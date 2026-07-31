import pytest

from virector.services.references import (
    build_reference_directives,
    validate_prompt_reference_tags,
)


def test_references_receive_image_tags_in_upload_order() -> None:
    directives = build_reference_directives(4)

    assert [item.tag for item in directives] == [
        "@image1",
        "@image2",
        "@image3",
        "@image4",
    ]


def test_reference_roles_cap_uploads_at_nine_images() -> None:
    with pytest.raises(ValueError, match="up to nine"):
        build_reference_directives(10)


def test_prompt_accepts_every_uploaded_image_tag() -> None:
    validate_prompt_reference_tags(
        "@image1 walks through the world shown in @image2.",
        2,
    )


def test_prompt_rejects_missing_uploaded_image_tag() -> None:
    with pytest.raises(ValueError, match="@image2"):
        validate_prompt_reference_tags("@image1 walks forward.", 2)


def test_prompt_rejects_unknown_image_tag() -> None:
    with pytest.raises(ValueError, match="not uploaded: @image3"):
        validate_prompt_reference_tags(
            "@image1 enters @image2 while @image3 watches.",
            2,
        )
