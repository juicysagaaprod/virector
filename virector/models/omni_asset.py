from enum import Enum

from pydantic import BaseModel, Field, model_validator


class OmniMediaType(str, Enum):
    image = "image"
    video = "video"
    audio = "audio"


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
