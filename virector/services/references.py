import re
from collections.abc import Sequence

from virector.models.omni_asset import OmniMediaType
from virector.models.shot_spec import ReferenceDirective

REFERENCE_TAG_PATTERN = re.compile(
    r"(?<![a-z0-9_])@(image|video|audio)(\d+)\b",
    re.IGNORECASE,
)
HUMAN_REFERENCE_PATTERN = re.compile(
    r"(?<![@a-z0-9_])\b(image|video|audio)\s*#?\s*(\d+)\b",
    re.IGNORECASE,
)
REFERENCE_LIMITS = {
    OmniMediaType.image: 9,
    OmniMediaType.video: 3,
    OmniMediaType.audio: 3,
}
MAX_OMNI_REFERENCES = 12


def normalize_reference_mentions(prompt: str) -> str:
    """Normalize ``Image 1``-style references to Virector's stable tags."""

    return HUMAN_REFERENCE_PATTERN.sub(
        lambda match: f"@{match.group(1).lower()}{int(match.group(2))}",
        prompt,
    )


def extract_reference_tags(prompt: str) -> set[str]:
    """Return normalized omni tags from either supported prompt spelling."""

    normalized = normalize_reference_mentions(prompt)
    return {
        f"@{media_type.lower()}{int(index)}"
        for media_type, index in REFERENCE_TAG_PATTERN.findall(normalized)
    }


def build_reference_directives(
    reference_count: int,
    media_type: OmniMediaType = OmniMediaType.image,
) -> list[ReferenceDirective]:
    """Assign deterministic per-media tags in upload order."""

    if reference_count < 1:
        raise ValueError(f"Upload at least one reference {media_type.value}.")
    limit = REFERENCE_LIMITS[media_type]
    if reference_count > limit:
        label = "images" if media_type == OmniMediaType.image else f"{media_type.value}s"
        raise ValueError(
            f"Virector currently accepts up to {limit} reference {label}."
        )
    return [
        ReferenceDirective(
            index=index,
            tag=f"@{media_type.value}{index}",
            media_type=media_type,
        )
        for index in range(1, reference_count + 1)
    ]


def build_omni_reference_directives(
    image_count: int,
    video_count: int = 0,
    audio_count: int = 0,
) -> list[ReferenceDirective]:
    """Build one ordered image/video/audio conditioning contract."""

    counts = {
        OmniMediaType.image: image_count,
        OmniMediaType.video: video_count,
        OmniMediaType.audio: audio_count,
    }
    if image_count < 1:
        raise ValueError("Upload at least one omni reference image.")
    if sum(counts.values()) > MAX_OMNI_REFERENCES:
        raise ValueError(
            f"Virector accepts up to {MAX_OMNI_REFERENCES} total omni references."
        )
    directives: list[ReferenceDirective] = []
    for media_type, count in counts.items():
        if count:
            directives.extend(build_reference_directives(count, media_type))
    return directives


def validate_prompt_reference_tags(
    prompt: str,
    references: int | Sequence[ReferenceDirective],
) -> None:
    """Ensure the prompt uses each upload and no unbound omni tag."""

    if isinstance(references, int):
        directives = build_reference_directives(references)
    else:
        directives = list(references)
    expected = {directive.tag for directive in directives}
    mentioned = extract_reference_tags(prompt)
    unknown = sorted(mentioned - expected)
    if unknown:
        raise ValueError(
            "Direction prompt references assets not uploaded: "
            + ", ".join(unknown)
            + "."
        )
    missing = sorted(expected - mentioned)
    if missing:
        raise ValueError(
            "Mention every uploaded reference in the direction prompt: "
            + ", ".join(missing)
            + "."
        )
