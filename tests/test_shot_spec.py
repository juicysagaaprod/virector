import pytest
from pydantic import ValidationError

from virector.models.shot_spec import (
    OUTPUT_RESOLUTION_PRESETS,
    AspectRatio,
    OutputResolution,
    ReferenceDirective,
    ShotSpec,
)


def test_default_portrait_shot_is_valid() -> None:
    shot = ShotSpec(prompt="A controlled cinematic character entrance.")
    assert shot.aspect_ratio == AspectRatio.portrait
    assert (shot.width, shot.height) == (480, 832)
    assert shot.duration_seconds == 4.0
    assert shot.output_resolution == OutputResolution.preview
    assert shot.reference_mode == "omni"


def test_portrait_rejects_landscape_dimensions() -> None:
    with pytest.raises(ValidationError):
        ShotSpec(
            prompt="A controlled cinematic character entrance.",
            aspect_ratio=AspectRatio.portrait,
            width=832,
            height=480,
        )


def test_clip_duration_is_capped_at_fifteen_seconds() -> None:
    with pytest.raises(ValidationError):
        ShotSpec(
            prompt="A controlled cinematic character entrance.",
            duration_seconds=16,
        )


def test_output_resolution_presets_cover_every_aspect_ratio() -> None:
    assert OUTPUT_RESOLUTION_PRESETS[OutputResolution.p720][
        AspectRatio.portrait
    ] == (720, 1280)
    assert OUTPUT_RESOLUTION_PRESETS[OutputResolution.p1080][
        AspectRatio.landscape
    ] == (1920, 1080)


def test_shot_accepts_contiguous_unique_reference_directives() -> None:
    shot = ShotSpec(
        prompt="The lead crosses the designed world.",
        references=[
            ReferenceDirective(
                index=1,
                tag="@image1",
            ),
            ReferenceDirective(
                index=2,
                tag="@image2",
            ),
        ],
    )

    assert [reference.tag for reference in shot.references] == [
        "@image1",
        "@image2",
    ]


def test_shot_rejects_reference_tags_outside_upload_order() -> None:
    with pytest.raises(ValidationError, match="must match upload order"):
        ShotSpec(
            prompt="The lead crosses the designed world.",
            references=[
                ReferenceDirective(
                    index=1,
                    tag="@image1",
                ),
                ReferenceDirective(
                    index=2,
                    tag="@image1",
                ),
            ],
        )


def test_shot_rejects_noncontiguous_reference_indexes() -> None:
    with pytest.raises(ValidationError, match="indexes must be contiguous"):
        ShotSpec(
            prompt="The lead crosses the designed world.",
            references=[
                ReferenceDirective(
                    index=2,
                    tag="@image2",
                )
            ],
        )
