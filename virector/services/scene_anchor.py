from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from virector.models.omni_asset import ReferenceRole
from virector.models.shot_spec import ShotSpec

if TYPE_CHECKING:
    from virector.workers.base import ReferenceAsset


class SceneAnchorError(RuntimeError):
    """Raised when required character/world inputs cannot form a scene anchor."""


@dataclass(frozen=True)
class SceneAnchorResult:
    path: Path
    provider: str
    fallback: bool
    notes: tuple[str, ...] = ()


class SceneAnchorProvider(ABC):
    """Create one spatially coherent first-frame condition from typed references."""

    name: str

    @abstractmethod
    def generate(
        self,
        assets: tuple[ReferenceAsset, ...],
        spec: ShotSpec,
        output_path: Path,
    ) -> SceneAnchorResult:
        """Generate and persist one first-frame continuity anchor."""


def _cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    target_width, target_height = size
    scale = max(target_width / image.width, target_height / image.height)
    resized = image.resize(
        (round(image.width * scale), round(image.height * scale)),
        Image.Resampling.LANCZOS,
    )
    left = max(0, (resized.width - target_width) // 2)
    top = max(0, (resized.height - target_height) // 2)
    return resized.crop((left, top, left + target_width, top + target_height))


def _matte_character(source: Image.Image) -> Image.Image:
    """Build a soft alpha matte for white-backed refs; preserve existing alpha."""

    rgba = source.convert("RGBA")
    existing = np.asarray(rgba.getchannel("A"), dtype=np.float32) / 255.0
    if existing.min() < 0.98:
        alpha = existing
    else:
        rgb = np.asarray(rgba.convert("RGB"), dtype=np.float32)
        distance = np.linalg.norm(255.0 - rgb, axis=2)
        # Transparent near white, opaque beyond a soft anti-aliased transition.
        # Studio refs commonly use a light-gray radial gradient rather than pure
        # white. Keep that connected neutral field transparent while retaining
        # skin, hair, dark wardrobe and anti-aliased subject edges.
        alpha = np.clip((distance - 38.0) / 52.0, 0.0, 1.0)
        alpha_image = Image.fromarray(np.uint8(alpha * 255), mode="L")
        alpha_image = alpha_image.filter(ImageFilter.MedianFilter(size=3))
        alpha_image = alpha_image.filter(ImageFilter.GaussianBlur(radius=0.75))
        alpha = np.asarray(alpha_image, dtype=np.float32) / 255.0
    output = rgba.copy()
    output.putalpha(Image.fromarray(np.uint8(alpha * 255), mode="L"))
    bbox = output.getbbox()
    return output.crop(bbox) if bbox else output


def _harmonise(character: Image.Image, world: Image.Image) -> Image.Image:
    """Apply restrained luminance/colour matching without replacing identity."""

    world_rgb = np.asarray(world.convert("RGB"), dtype=np.float32)
    character_rgb = np.asarray(character.convert("RGB"), dtype=np.float32)
    alpha = np.asarray(character.getchannel("A"), dtype=np.float32) / 255.0
    visible = alpha > 0.65
    if not visible.any():
        return character
    world_mean = world_rgb.reshape(-1, 3).mean(axis=0)
    character_mean = character_rgb[visible].mean(axis=0)
    gain = np.clip(world_mean / np.maximum(character_mean, 1.0), 0.82, 1.18)
    mixed_gain = 1.0 + (gain - 1.0) * 0.35
    adjusted = np.clip(character_rgb * mixed_gain, 0, 255).astype(np.uint8)
    result = Image.fromarray(adjusted, mode="RGB").convert("RGBA")
    result.putalpha(character.getchannel("A"))
    return ImageEnhance.Contrast(result).enhance(0.98)


class CompositorSceneAnchorProvider(SceneAnchorProvider):
    """Deterministic fallback anchor with matting, harmonisation and floor contact."""

    name = "fallback-compositor-v2"

    @staticmethod
    def _one_role(
        assets: tuple[ReferenceAsset, ...], role: ReferenceRole, *, required: bool = True
    ) -> ReferenceAsset | None:
        matches = [asset for asset in assets if asset.role == role]
        if not matches and not required:
            return None
        if len(matches) != 1:
            raise SceneAnchorError(
                f"Scene anchor requires exactly one {role.value} reference; "
                f"found {len(matches)}."
            )
        return matches[0]

    def generate(
        self,
        assets: tuple[ReferenceAsset, ...],
        spec: ShotSpec,
        output_path: Path,
    ) -> SceneAnchorResult:
        character_asset = self._one_role(assets, ReferenceRole.CHARACTER_IDENTITY)
        world_asset = self._one_role(
            assets, ReferenceRole.WORLD_ENVIRONMENT, required=False
        )
        assert character_asset is not None
        if world_asset is None:
            with Image.open(character_asset.path) as source:
                frame = _cover(source.convert("RGB"), (spec.width, spec.height))
            output_path.parent.mkdir(parents=True, exist_ok=True)
            frame.save(output_path, "PNG", optimize=True)
            return SceneAnchorResult(
                path=output_path,
                provider="legacy-primary-image-fallback",
                fallback=True,
                notes=(
                    "No world_environment reference was supplied; preserving the "
                    "legacy one-image API without claiming scene grounding.",
                ),
            )
        assert world_asset is not None
        with Image.open(world_asset.path) as source_world:
            world = _cover(source_world.convert("RGB"), (spec.width, spec.height))
        with Image.open(character_asset.path) as source_character:
            character = _matte_character(source_character)

        framing_scale = {
            "full": 0.78,
            "wide": 0.67,
            "medium": 1.0,
            "close": 1.35,
        }
        shot_size = spec.camera.shot_size.lower()
        framing = next(
            (value for key, value in framing_scale.items() if key in shot_size),
            0.78,
        )
        target_height = round(spec.height * spec.character.scale * framing)
        scale = target_height / max(character.height, 1)
        character = character.resize(
            (max(1, round(character.width * scale)), max(1, target_height)),
            Image.Resampling.LANCZOS,
        )
        character = _harmonise(character, world)

        canvas = world.convert("RGBA")
        center_x = round(spec.character.position_x * spec.width)
        baseline_y = min(
            spec.height - 2,
            round(spec.character.position_y * spec.height),
        )
        left = center_x - character.width // 2
        top = baseline_y - character.height

        # An elliptical floor-contact shadow is distinct from a silhouette drop
        # shadow and gives the generator an explicit grounding cue.
        shadow_width = max(8, round(character.width * 0.58))
        shadow_height = max(3, round(character.height * 0.035))
        shadow = Image.new("RGBA", (shadow_width, shadow_height), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).ellipse(
            (1, 1, shadow_width - 2, shadow_height - 2),
            fill=(0, 0, 0, 90),
        )
        shadow = shadow.filter(ImageFilter.GaussianBlur(radius=max(2, shadow_height / 2)))
        canvas.alpha_composite(
            shadow,
            (center_x - shadow_width // 2, baseline_y - shadow_height // 2),
        )
        canvas.alpha_composite(character, (left, top))

        output_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.convert("RGB").save(output_path, "PNG", optimize=True)
        return SceneAnchorResult(
            path=output_path,
            provider=self.name,
            fallback=True,
            notes=(
                "Neural scene-anchor generation is not installed; this is the "
                "explicit fallback/debug compositor.",
                "The VACE worker still receives character and world references "
                "independently in addition to this first-frame condition.",
            ),
        )
