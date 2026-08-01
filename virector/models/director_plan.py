from pydantic import AliasChoices, BaseModel, Field, model_validator

from virector.models.omni_asset import OmniAsset, ReferenceBinding


class DirectorPlanRequest(BaseModel):
    direction_prompt: str = Field(min_length=10, max_length=20_000)


PlanReference = OmniAsset


class DialogueCue(BaseModel):
    speaker: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=1000)
    delivery: str | None = Field(default=None, max_length=200)
    speaker_reference_tag: str | None = Field(
        default=None,
        pattern=r"^@image[1-9]$",
    )


class DirectorSegment(BaseModel):
    index: int = Field(ge=1, le=20)
    start_seconds: float = Field(ge=0, le=15)
    end_seconds: float = Field(gt=0, le=15)
    duration_seconds: float = Field(gt=0, le=15)
    action: str = Field(min_length=1, max_length=5000)
    reference_tags: list[str] = Field(default_factory=list, max_length=9)
    asset_tags: list[str] = Field(default_factory=list, max_length=12)
    reference_bindings: list[ReferenceBinding] = Field(
        default_factory=list,
        max_length=24,
    )
    dialogue: list[DialogueCue] = Field(default_factory=list, max_length=20)
    sound_cues: list[str] = Field(default_factory=list, max_length=20)
    on_screen_text: list[str] = Field(default_factory=list, max_length=20)
    transition: str | None = Field(default=None, max_length=500)
    title_card: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_timing(self) -> "DirectorSegment":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("Segment end time must be after its start time")
        expected_duration = self.end_seconds - self.start_seconds
        if abs(self.duration_seconds - expected_duration) > 0.001:
            raise ValueError("Segment duration must match its time range")
        if len(set(self.reference_tags)) != len(self.reference_tags):
            raise ValueError("Segment reference tags must be unique")
        return self


class DirectorPlan(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    requested_model: str | None = Field(default=None, max_length=200)
    method: str | None = Field(default=None, max_length=300)
    purpose: str | None = Field(default=None, max_length=500)
    voice_direction: str | None = Field(default=None, max_length=500)
    duration_seconds: float = Field(gt=0, le=15)
    omni_assets: list[OmniAsset] = Field(
        default_factory=list,
        max_length=12,
        validation_alias=AliasChoices("omni_assets", "references"),
    )
    segments: list[DirectorSegment] = Field(min_length=1, max_length=20)
    warnings: list[str] = Field(default_factory=list, max_length=50)

    @property
    def references(self) -> list[OmniAsset]:
        """Backward-compatible accessor for pre-OmniAsset integrations."""

        return self.omni_assets

    @model_validator(mode="after")
    def validate_plan(self) -> "DirectorPlan":
        reference_tags = [asset.tag for asset in self.omni_assets]
        if len(set(reference_tags)) != len(reference_tags):
            raise ValueError("DirectorPlan reference tags must be unique")

        previous_end = 0.0
        known_tags = set(reference_tags)
        for expected_index, segment in enumerate(self.segments, start=1):
            if segment.index != expected_index:
                raise ValueError("DirectorPlan segment indexes must be contiguous")
            if segment.start_seconds < previous_end:
                raise ValueError("DirectorPlan segments must not overlap")
            if segment.end_seconds > self.duration_seconds:
                raise ValueError("A segment extends beyond the plan duration")
            unknown_tags = set(segment.reference_tags) - known_tags
            if unknown_tags:
                raise ValueError(
                    "Segment references images not defined by the plan: "
                    + ", ".join(sorted(unknown_tags))
                )
            unknown_asset_tags = set(segment.asset_tags) - known_tags
            if unknown_asset_tags:
                raise ValueError(
                    "Segment references assets not defined by the plan: "
                    + ", ".join(sorted(unknown_asset_tags))
                )
            binding_tags = {
                tag
                for binding in segment.reference_bindings
                for tag in binding.asset_tags
            }
            unknown_binding_tags = binding_tags - known_tags
            if unknown_binding_tags:
                raise ValueError(
                    "ReferenceBinding uses undefined assets: "
                    + ", ".join(sorted(unknown_binding_tags))
                )
            previous_end = segment.end_seconds
        return self
