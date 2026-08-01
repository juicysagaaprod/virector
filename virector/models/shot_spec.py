from enum import Enum

from pydantic import BaseModel, Field, model_validator

from virector.models.director_plan import DirectorPlan
from virector.models.omni_asset import OmniMediaType


class AspectRatio(str, Enum):
    portrait = "9:16"
    landscape = "16:9"
    square = "1:1"
    social = "4:5"


class OutputResolution(str, Enum):
    preview = "Preview"
    p720 = "720p"
    p1080 = "1080p"


class ReferenceDirective(BaseModel):
    index: int = Field(ge=1, le=9)
    tag: str = Field(pattern=r"^@(image[1-9]|video[1-3]|audio[1-3])$")
    media_type: OmniMediaType = OmniMediaType.image
    strength: float = Field(default=0.9, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_tag(self) -> "ReferenceDirective":
        limit = 9 if self.media_type == OmniMediaType.image else 3
        if self.index > limit:
            raise ValueError(
                f"{self.media_type.value} references support indexes 1-{limit}"
            )
        if self.tag != f"@{self.media_type.value}{self.index}":
            raise ValueError("Reference tag must match its media type and index")
        return self


class CameraDirection(BaseModel):
    shot_size: str = Field(default="medium", max_length=40)
    movement: str = Field(default="static", max_length=80)
    lens_mm: int = Field(default=50, ge=14, le=200)
    movement_strength: float = Field(default=0.25, ge=0.0, le=1.0)
    handheld_strength: float = Field(default=0.0, ge=0.0, le=1.0)
    focus_target: str = Field(default="character eyes", max_length=100)


class CharacterDirection(BaseModel):
    name: str = Field(default="Character", min_length=1, max_length=80)
    action: str = Field(default="stands naturally", max_length=500)
    expression: str = Field(default="neutral", max_length=200)
    facing: str = Field(default="camera", max_length=80)
    position_x: float = Field(default=0.5, ge=0.0, le=1.0)
    position_y: float = Field(default=0.92, ge=0.0, le=1.0)
    scale: float = Field(default=0.72, ge=0.1, le=1.5)
    reference_strength: float = Field(default=0.85, ge=0.0, le=1.0)


class LightingDirection(BaseModel):
    style: str = Field(default="cinematic natural light", max_length=200)
    time_of_day: str = Field(default="day", max_length=80)
    colour_grade: str = Field(default="natural cinematic contrast", max_length=200)


class ShotSpec(BaseModel):
    """Portable contract passed to local or cloud generation workers."""

    title: str = Field(default="Untitled shot", min_length=1, max_length=120)
    prompt: str = Field(min_length=3, max_length=20_000)
    director_plan: DirectorPlan | None = None
    negative_prompt: str = Field(
        default=(
            "identity drift, face distortion, extra fingers, duplicate person, "
            "warped anatomy, flicker, jitter, text, watermark"
        ),
        max_length=2000,
    )
    video_model: str = Field(default="ltx-video-2b-distilled", max_length=120)
    reference_mode: str = Field(default="omni", pattern="^(omni|layered)$")
    references: list[ReferenceDirective] = Field(default_factory=list, max_length=12)
    aspect_ratio: AspectRatio = AspectRatio.portrait
    output_resolution: OutputResolution = OutputResolution.preview
    width: int = Field(default=480, ge=256, le=4096)
    height: int = Field(default=832, ge=256, le=4096)
    duration_seconds: float = Field(default=4.0, ge=1.0, le=15.0)
    fps: int = Field(default=24, ge=8, le=60)
    seed: int = Field(default=42, ge=0, le=2_147_483_647)
    character: CharacterDirection = Field(default_factory=CharacterDirection)
    camera: CameraDirection = Field(default_factory=CameraDirection)
    lighting: LightingDirection = Field(default_factory=LightingDirection)

    @model_validator(mode="after")
    def validate_aspect_orientation(self) -> "ShotSpec":
        if self.aspect_ratio == AspectRatio.portrait and self.width >= self.height:
            raise ValueError("9:16 output requires height greater than width")
        if self.aspect_ratio == AspectRatio.landscape and self.width <= self.height:
            raise ValueError("16:9 output requires width greater than height")
        if self.aspect_ratio == AspectRatio.square and self.width != self.height:
            raise ValueError("1:1 output requires equal width and height")
        if self.aspect_ratio == AspectRatio.social and self.width >= self.height:
            raise ValueError("4:5 output requires height greater than width")
        if len({reference.tag for reference in self.references}) != len(
            self.references
        ):
            raise ValueError("Reference tags must be unique")
        media_order = {
            OmniMediaType.image: 0,
            OmniMediaType.video: 1,
            OmniMediaType.audio: 2,
        }
        expected_order = sorted(
            self.references,
            key=lambda reference: (media_order[reference.media_type], reference.index),
        )
        if self.references != expected_order:
            raise ValueError("References must be ordered as images, videos, then audio")
        for media_type in OmniMediaType:
            references = [
                reference
                for reference in self.references
                if reference.media_type == media_type
            ]
            indexes = [reference.index for reference in references]
            if indexes and indexes != list(range(1, len(indexes) + 1)):
                raise ValueError(
                    f"{media_type.value} reference indexes must be contiguous "
                    "and start at one"
                )
        return self


RESOLUTION_PRESETS: dict[AspectRatio, tuple[int, int]] = {
    AspectRatio.portrait: (480, 832),
    AspectRatio.landscape: (832, 480),
    AspectRatio.square: (640, 640),
    AspectRatio.social: (512, 640),
}

OUTPUT_RESOLUTION_PRESETS: dict[
    OutputResolution, dict[AspectRatio, tuple[int, int]]
] = {
    OutputResolution.preview: RESOLUTION_PRESETS,
    OutputResolution.p720: {
        AspectRatio.portrait: (720, 1280),
        AspectRatio.landscape: (1280, 720),
        AspectRatio.square: (720, 720),
        AspectRatio.social: (720, 900),
    },
    OutputResolution.p1080: {
        AspectRatio.portrait: (1080, 1920),
        AspectRatio.landscape: (1920, 1080),
        AspectRatio.square: (1080, 1080),
        AspectRatio.social: (1080, 1350),
    },
}
