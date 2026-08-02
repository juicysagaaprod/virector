from enum import Enum

from pydantic import BaseModel, Field, model_validator


class OmniMediaType(str, Enum):
    image = "image"
    video = "video"
    audio = "audio"


class ReferenceRole(str, Enum):
    """Stable semantic role carried from upload through model invocation."""

    CHARACTER_IDENTITY = "character_identity"
    WORLD_ENVIRONMENT = "world_environment"
    START_FRAME = "start_frame"
    END_FRAME = "end_frame"
    PROP = "prop"
    MOTION = "motion"
    CAMERA = "camera"
    AUDIO = "audio"


class AssetRole(str, Enum):
    character_identity = "character_identity"
    wardrobe = "wardrobe"
    environment = "environment"
    prop = "prop"
    readable_text = "readable_text"
    composition = "composition"
    storyboard = "storyboard"
    motion = "motion"
    camera = "camera"
    effect = "effect"
    voice = "voice"
    audio = "audio"
    style = "style"


class ReferenceOperation(str, Enum):
    reference = "reference"
    extract = "extract"
    combine = "combine"
    follow = "follow"
    replace = "replace"
    generate = "generate"
    maintain = "maintain"


class BindingModality(str, Enum):
    visual = "visual"
    voice = "voice"
    motion = "motion"
    camera = "camera"
    effect = "effect"
    audio = "audio"


class OmniAsset(BaseModel):
    """One ordered multimodal source available to the internal director."""

    index: int = Field(ge=1, le=12)
    tag: str = Field(pattern=r"^@(image[1-9]|video[1-3]|audio[1-3])$")
    media_type: OmniMediaType
    description: str = Field(min_length=1, max_length=300)
    roles: list[AssetRole] = Field(default_factory=list, max_length=12)
    reference_role: ReferenceRole | None = None
    identity_group: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def validate_tag(self) -> "OmniAsset":
        prefix = f"@{self.media_type.value}"
        if not self.tag.startswith(prefix):
            raise ValueError("OmniAsset tag must match its media type")
        tag_index = int(self.tag.removeprefix(prefix))
        if tag_index != self.index:
            raise ValueError("OmniAsset tag must match its media index")
        if len(set(self.roles)) != len(self.roles):
            raise ValueError("OmniAsset roles must be unique")
        return self


class ResolvedReferenceAsset(BaseModel):
    """A validated alias-to-file mapping safe to pass between workers."""

    asset_id: str = Field(min_length=1, max_length=160)
    role: ReferenceRole
    storage_uri: str = Field(min_length=1, max_length=4096)
    prompt_alias: str = Field(pattern=r"^@(image[1-9]|video[1-3]|audio[1-3])$")
    media_type: OmniMediaType
    ordinal: int = Field(ge=1, le=12)
    priority: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_role_modality(self) -> "ResolvedReferenceAsset":
        if self.role == ReferenceRole.AUDIO and self.media_type != OmniMediaType.audio:
            raise ValueError("The audio role requires an audio asset")
        if self.role in {ReferenceRole.MOTION, ReferenceRole.CAMERA} and (
            self.media_type == OmniMediaType.audio
        ):
            raise ValueError("Motion and camera roles cannot use audio assets")
        return self


class ResolvedReferenceMap(BaseModel):
    """Debuggable, immutable reference resolution document for one job."""

    version: int = 1
    assets: list[ResolvedReferenceAsset] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def validate_unique_identifiers(self) -> "ResolvedReferenceMap":
        for label, values in {
            "asset IDs": [asset.asset_id for asset in self.assets],
            "prompt aliases": [asset.prompt_alias for asset in self.assets],
        }.items():
            if len(values) != len(set(values)):
                raise ValueError(f"Resolved reference {label} must be unique")
        return self

    def by_alias(self, alias: str) -> ResolvedReferenceAsset:
        matches = [asset for asset in self.assets if asset.prompt_alias == alias]
        if len(matches) != 1:
            raise ValueError(f"Reference alias {alias} did not resolve exactly once")
        return matches[0]


class ReferenceBinding(BaseModel):
    """How one or more assets control a specific generated shot."""

    asset_tags: list[str] = Field(min_length=1, max_length=12)
    modality: BindingModality
    operations: list[ReferenceOperation] = Field(min_length=1, max_length=6)
    controls: list[AssetRole] = Field(min_length=1, max_length=12)
    target: str = Field(min_length=1, max_length=300)
    instruction: str = Field(min_length=1, max_length=2000)
    visible: bool = True
    strength: float = Field(default=0.9, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_binding(self) -> "ReferenceBinding":
        if len(set(self.asset_tags)) != len(self.asset_tags):
            raise ValueError("ReferenceBinding asset tags must be unique")
        if len(set(self.operations)) != len(self.operations):
            raise ValueError("ReferenceBinding operations must be unique")
        if len(set(self.controls)) != len(self.controls):
            raise ValueError("ReferenceBinding controls must be unique")
        if self.modality != BindingModality.visual and self.visible:
            raise ValueError("Only visual reference bindings may be visible")
        return self
