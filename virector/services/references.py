import re

from virector.models.shot_spec import ReferenceDirective, ReferenceRole


ROLE_ALIASES: dict[str, ReferenceRole] = {
    "start": ReferenceRole.start_frame,
    "start_frame": ReferenceRole.start_frame,
    "opening": ReferenceRole.start_frame,
    "character": ReferenceRole.character,
    "identity": ReferenceRole.character,
    "person": ReferenceRole.character,
    "world": ReferenceRole.world,
    "background": ReferenceRole.world,
    "environment": ReferenceRole.world,
    "scene": ReferenceRole.world,
    "prop": ReferenceRole.prop,
    "object": ReferenceRole.prop,
    "wardrobe": ReferenceRole.wardrobe,
    "clothing": ReferenceRole.wardrobe,
    "costume": ReferenceRole.wardrobe,
    "style": ReferenceRole.style,
    "storyboard": ReferenceRole.storyboard,
    "pose": ReferenceRole.pose,
    "motion": ReferenceRole.pose,
    "camera": ReferenceRole.camera,
    "other": ReferenceRole.other,
    "reference": ReferenceRole.other,
}


def _slug(value: str) -> str:
    value = value.strip().lower().removeprefix("@")
    value = re.sub(r"[^a-z0-9_-]+", "_", value).strip("_-")
    return value or "reference"


def build_reference_directives(
    reference_count: int,
    role_text: str = "",
) -> list[ReferenceDirective]:
    """Build ordered, uniquely tagged directives from comma/newline role text."""

    if reference_count < 1:
        raise ValueError("Upload at least one reference image.")
    if reference_count > 9:
        raise ValueError("Virector currently accepts up to nine reference images.")

    entries = [
        item.strip()
        for item in re.split(r"[,\n]+", role_text or "")
        if item.strip()
    ]
    if len(entries) > reference_count:
        raise ValueError(
            "There are more reference roles than uploaded reference images."
        )

    directives: list[ReferenceDirective] = []
    used_tags: dict[str, int] = {}
    for offset in range(reference_count):
        entry = entries[offset] if offset < len(entries) else ""
        role_name, separator, description = entry.partition(":")
        if entry:
            slug = _slug(role_name)
            role = ROLE_ALIASES.get(slug, ReferenceRole.other)
        elif offset == 0:
            slug = "start_frame"
            role = ReferenceRole.start_frame
        else:
            slug = "reference"
            role = ReferenceRole.other

        occurrence = used_tags.get(slug, 0) + 1
        used_tags[slug] = occurrence
        tag = f"@{slug}" if occurrence == 1 else f"@{slug}_{occurrence}"
        directives.append(
            ReferenceDirective(
                index=offset + 1,
                tag=tag,
                role=role,
                description=description.strip() if separator else "",
            )
        )
    return directives
