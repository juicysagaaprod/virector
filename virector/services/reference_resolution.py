from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from virector.models.director_plan import DirectorPlan
from virector.models.omni_asset import (
    AssetRole,
    OmniMediaType,
    ReferenceRole,
    ResolvedReferenceAsset,
    ResolvedReferenceMap,
)
from virector.models.shot_spec import ReferenceDirective


class ReferenceResolutionError(ValueError):
    """Raised when a prompt alias cannot be resolved safely and unambiguously."""


def _plan_role(tag: str, plan: DirectorPlan | None) -> ReferenceRole | None:
    if plan is None:
        return None
    matches = [asset for asset in plan.omni_assets if asset.tag == tag]
    if len(matches) > 1:
        raise ReferenceResolutionError(f"Reference {tag} is defined more than once")
    if not matches:
        return None
    asset = matches[0]
    if asset.reference_role is not None:
        return asset.reference_role
    roles = set(asset.roles)
    if AssetRole.environment in roles:
        return ReferenceRole.WORLD_ENVIRONMENT
    if AssetRole.character_identity in roles:
        return ReferenceRole.CHARACTER_IDENTITY
    if AssetRole.prop in roles:
        return ReferenceRole.PROP
    if AssetRole.motion in roles:
        return ReferenceRole.MOTION
    if AssetRole.camera in roles:
        return ReferenceRole.CAMERA
    if AssetRole.audio in roles or AssetRole.voice in roles:
        return ReferenceRole.AUDIO
    return None


def infer_reference_role(
    directive: ReferenceDirective,
    plan: DirectorPlan | None = None,
) -> ReferenceRole:
    """Resolve a role with explicit metadata first and safe legacy defaults last."""

    if directive.role is not None:
        return directive.role
    # The initial production contract is deliberately positional and must not be
    # overridden by an unreliable natural-language heuristic.
    if directive.media_type == OmniMediaType.image and directive.index == 1:
        return ReferenceRole.CHARACTER_IDENTITY
    if directive.media_type == OmniMediaType.image and directive.index == 2:
        return ReferenceRole.WORLD_ENVIRONMENT
    planned = _plan_role(directive.tag, plan)
    if planned is not None:
        return planned
    if directive.media_type == OmniMediaType.audio:
        return ReferenceRole.AUDIO
    if directive.media_type == OmniMediaType.video:
        return ReferenceRole.MOTION
    return ReferenceRole.PROP


def resolve_reference_map(
    directives: Sequence[ReferenceDirective],
    paths: Sequence[str | Path],
    plan: DirectorPlan | None = None,
) -> ResolvedReferenceMap:
    """Resolve every stable prompt alias to exactly one existing stored asset."""

    if not directives or not paths:
        raise ReferenceResolutionError("At least one reference is required")
    if len(directives) != len(paths):
        raise ReferenceResolutionError(
            "Every uploaded reference must have exactly one directive"
        )
    aliases = [directive.tag for directive in directives]
    if len(aliases) != len(set(aliases)):
        raise ReferenceResolutionError("Duplicate reference aliases are not allowed")

    assets: list[ResolvedReferenceAsset] = []
    for ordinal, (directive, path_value) in enumerate(
        zip(directives, paths, strict=True),
        start=1,
    ):
        path = Path(path_value)
        if not path.is_file():
            raise ReferenceResolutionError(
                f"Reference {directive.tag} could not be resolved to a stored file"
            )
        alias = directive.prompt_alias or directive.tag
        asset_id = directive.asset_id or (
            f"{directive.media_type.value}-{directive.index:02d}"
        )
        assets.append(
            ResolvedReferenceAsset(
                asset_id=asset_id,
                role=infer_reference_role(directive, plan),
                storage_uri=path.resolve().as_uri(),
                prompt_alias=alias,
                media_type=directive.media_type,
                ordinal=ordinal,
                priority=directive.priority,
            )
        )

    resolved = ResolvedReferenceMap(assets=assets)
    expected = set(aliases)
    actual = {asset.prompt_alias for asset in resolved.assets}
    unresolved = sorted(expected - actual)
    if unresolved:
        raise ReferenceResolutionError(
            "Unresolved reference aliases: " + ", ".join(unresolved)
        )
    return resolved


def require_role(
    resolved: ResolvedReferenceMap,
    role: ReferenceRole,
) -> ResolvedReferenceAsset:
    matches = [asset for asset in resolved.assets if asset.role == role]
    if len(matches) != 1:
        raise ReferenceResolutionError(
            f"Expected exactly one {role.value} reference, found {len(matches)}"
        )
    return matches[0]
