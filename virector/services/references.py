import re

from virector.models.shot_spec import ReferenceDirective


IMAGE_TAG_PATTERN = re.compile(r"(?<![a-z0-9_])@image(\d+)\b", re.IGNORECASE)


def build_reference_directives(
    reference_count: int,
) -> list[ReferenceDirective]:
    """Assign deterministic @image1...@image9 tags in upload order."""

    if reference_count < 1:
        raise ValueError("Upload at least one reference image.")
    if reference_count > 9:
        raise ValueError("Virector currently accepts up to nine reference images.")

    return [
        ReferenceDirective(index=index, tag=f"@image{index}")
        for index in range(1, reference_count + 1)
    ]


def validate_prompt_reference_tags(prompt: str, reference_count: int) -> None:
    """Ensure the direction prompt uses each upload and no unknown image tag."""

    expected = set(range(1, reference_count + 1))
    mentioned = {int(value) for value in IMAGE_TAG_PATTERN.findall(prompt)}
    unknown = sorted(mentioned - expected)
    if unknown:
        tags = ", ".join(f"@image{index}" for index in unknown)
        raise ValueError(f"Direction prompt references image tags not uploaded: {tags}.")

    missing = sorted(expected - mentioned)
    if missing:
        tags = ", ".join(f"@image{index}" for index in missing)
        raise ValueError(f"Mention every uploaded image in the direction prompt: {tags}.")
