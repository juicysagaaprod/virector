from enum import Enum

from pydantic import BaseModel, Field, model_validator


class AspectRatio(str, Enum):
    portrait = "9:16"
    landscape = "16:9"
    square = "1:1"
    social = "4:5"


class OutputResolution(str, Enum):
    preview = "Preview"
    p720 = "720p"
    p1080 = "1080p"


class ReferenceRole(str, Enum):
    start_frame = "start_frame"
    character = "character"
    world = "world"
    prop = "prop"
    wardrobe = "wardrobe"
    style = "style"
    storyboard = "storyboard"
    pose = "pose"
    camera = "camera"
    other = "other"


class ReferenceDirective(BaseModel):
    index: int = Field(ge=1, le=15)
    tag: str = Field(pattern=r"^@[a-z][a-z0-9_-]{0,39}$")
    role: ReferenceRole
    description: str = Field(default="", max_length=240)
    strength: float = Field(default=0.9, ge=0.0, le=1.0)


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
    prompt: str = Field(min_length=3, max_length=4000)
    negative_prompt: str = Field(
        default=(
            "identity drift, face distortion, extra fingers, duplicate person, "
            "warped anatomy, flicker, jitter, text, watermark"
        ),
        max_length=2000,
    )
    video_model: str = Field(default="ltx-video-2b-distilled", max_length=120)
    reference_mode: str = Field(default="omni", pattern="^(omni|layered)$")
    references: list[ReferenceDirective] = Field(default_factory=list, max_length=15)
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
        indexes = [reference.index for reference in self.references]
        if indexes and sorted(indexes) != list(range(1, len(indexes) + 1)):
            raise ValueError("Reference indexes must be contiguous and start at one")
        tags = [reference.tag for reference in self.references]
        if len(tags) != len(set(tags)):
            raise ValueError("Reference tags must be unique")
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
