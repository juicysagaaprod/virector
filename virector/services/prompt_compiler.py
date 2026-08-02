from __future__ import annotations

from typing import TYPE_CHECKING

from virector.models.omni_asset import ReferenceRole
from virector.models.shot_spec import ShotBeat, ShotSpec
from virector.services.action_prompt import compile_action_prompt

if TYPE_CHECKING:
    from virector.workers.base import ReferenceAsset


def _asset_position(assets: tuple[ReferenceAsset, ...], role: ReferenceRole) -> int:
    images = [asset for asset in assets if asset.media_type.value == "image"]
    matches = [index for index, asset in enumerate(images, start=1) if asset.role == role]
    if len(matches) != 1:
        raise ValueError(
            f"Compiled prompt requires exactly one {role.value} image; found {len(matches)}"
        )
    return matches[0]


def compile_model_prompt(
    spec: ShotSpec,
    beat: ShotBeat,
    assets: tuple[ReferenceAsset, ...],
) -> str:
    """Compile one beat without leaking UI aliases to an unaware backend."""

    character_position = _asset_position(assets, ReferenceRole.CHARACTER_IDENTITY)
    world_position = _asset_position(assets, ReferenceRole.WORLD_ENVIRONMENT)
    dialogue = beat.dialogue if beat.dialogue is not None else spec.dialogue_text
    sections = [
        "CHARACTER REFERENCE:\n"
        f"Use conditioning image {character_position} as the sole character identity. "
        "Preserve facial structure, hairstyle, skin tone, body proportions and complete "
        "wardrobe. Generate exactly one person.",
        "ENVIRONMENT REFERENCE:\n"
        f"Use conditioning image {world_position} as the location and environmental "
        "design. Preserve its architecture, materials, spatial layout, lighting direction "
        "and colour palette. Place the character naturally inside this environment with "
        "matching perspective, scale, light and floor contact.",
        f"SUBJECT PLACEMENT:\n{beat.framing}; keep the subject grounded and correctly scaled.",
        "PHYSICAL ACTION:\n"
        + compile_action_prompt(
            beat.subject_action,
            duration_seconds=beat.duration_seconds,
            framing=beat.framing,
            camera_motion=beat.camera_motion,
        ),
        f"PERFORMANCE:\n{beat.expression}; natural blinking, breathing and small head motion.",
    ]
    if dialogue:
        sections.append(
            "EXACT DIALOGUE:\n"
            f'The visible character says exactly: "{dialogue}". Only this character speaks; '
            "no narrator and no additional words. Mouth motion must be timed by the supplied "
            "speech audio in the dedicated dialogue stage, not inferred from this text prompt."
        )
    else:
        sections.append("EXACT DIALOGUE:\nSilent shot. Keep the mouth naturally at rest.")
    sections.extend(
        [
            "CAMERA:\n"
            f"{beat.framing}; {beat.camera_motion}; {spec.camera.lens_mm}mm cinematic lens. "
            "No unrequested cuts within this shot.",
            "LIGHTING AND ATMOSPHERE:\n"
            f"{spec.lighting.style}; {spec.lighting.time_of_day}; "
            f"{spec.lighting.colour_grade}.",
            "AUDIO:\nDialogue, footsteps and ambience are separate post-generation stems. "
            "No background music for validation.",
            "CONTINUITY:\nMaintain one character, unchanged wardrobe and the same environment. "
            "Preserve facial identity during head turns and continue from the prior final "
            "frame when a continuity anchor is supplied.",
            "AVOID:\nStatic pose, floating feet, sliding, duplicated people, changing clothes, "
            "changing location, distorted hands, warped face, frozen background, unintended "
            "mouth movement and sudden camera jumps. "
            + spec.negative_prompt,
        ]
    )
    return "\n\n".join(sections)
