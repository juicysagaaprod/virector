import pytest

from virector.models.omni_asset import OmniMediaType
from virector.services.references import (
    build_omni_reference_directives,
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
    with pytest.raises(ValueError, match="up to 9"):
        build_reference_directives(10)


def test_prompt_accepts_every_uploaded_image_tag() -> None:
    validate_prompt_reference_tags(
        "@image1 walks through the world shown in @image2.",
        2,
    )


def test_prompt_accepts_human_readable_image_numbers() -> None:
    validate_prompt_reference_tags(
        "Reference the character from Image 1 inside the world from Image 2.",
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


def test_omni_references_receive_independent_media_indexes() -> None:
    directives = build_omni_reference_directives(2, 2, 1)

    assert [directive.tag for directive in directives] == [
        "@image1",
        "@image2",
        "@video1",
        "@video2",
        "@audio1",
    ]
    assert [directive.media_type for directive in directives] == [
        OmniMediaType.image,
        OmniMediaType.image,
        OmniMediaType.video,
        OmniMediaType.video,
        OmniMediaType.audio,
    ]


def test_omni_references_are_capped_at_twelve_total_assets() -> None:
    with pytest.raises(ValueError, match="up to 12 total"):
        build_omni_reference_directives(9, 3, 1)


def test_prompt_validates_video_and_audio_upload_tags() -> None:
    directives = build_omni_reference_directives(1, 1, 1)

    validate_prompt_reference_tags(
        "@image1 follows @video1 and speaks with @audio1.",
        directives,
    )

    with pytest.raises(ValueError, match="@audio1"):
        validate_prompt_reference_tags(
            "@image1 follows @video1.",
            directives,
        )


def test_prompt_rejects_unuploaded_video_tag() -> None:
    with pytest.raises(ValueError, match="not uploaded: @video1"):
        validate_prompt_reference_tags(
            "@image1 follows @video1.",
            build_omni_reference_directives(1),
        )
