import pytest

from virector.models.shot_spec import ReferenceRole
from virector.services.references import build_reference_directives


def test_reference_role_text_builds_unique_ordered_tags() -> None:
    directives = build_reference_directives(
        4,
        "character: lead identity, world: city, prop, character: stunt double",
    )

    assert [item.tag for item in directives] == [
        "@character",
        "@world",
        "@prop",
        "@character_2",
    ]
    assert [item.role for item in directives] == [
        ReferenceRole.character,
        ReferenceRole.world,
        ReferenceRole.prop,
        ReferenceRole.character,
    ]
    assert directives[0].description == "lead identity"
    assert directives[3].description == "stunt double"


def test_reference_roles_default_to_start_frame_then_other() -> None:
    directives = build_reference_directives(3)

    assert [item.tag for item in directives] == [
        "@start_frame",
        "@reference",
        "@reference_2",
    ]
    assert directives[0].role == ReferenceRole.start_frame
    assert directives[1].role == ReferenceRole.other


def test_reference_roles_reject_more_entries_than_images() -> None:
    with pytest.raises(ValueError, match="more reference roles"):
        build_reference_directives(1, "character, world")


def test_reference_roles_cap_uploads_at_nine_images() -> None:
    with pytest.raises(ValueError, match="up to nine"):
        build_reference_directives(10)
