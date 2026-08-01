from enum import Enum

from pydantic import BaseModel, Field, model_validator

from virector.models.omni_asset import BindingModality


class ConditioningRouteStatus(str, Enum):
    """How completely a render stage can honor one reference binding."""

    native = "native"
    limited = "limited"
    external = "external"
    applied = "applied"
    deferred = "deferred"


class ConditioningRoute(BaseModel):
    """One backend assignment for a compiled ReferenceBinding."""

    segment_index: int = Field(ge=1, le=20)
    modality: BindingModality
    asset_tags: list[str] = Field(min_length=1, max_length=12)
    backend: str = Field(min_length=1, max_length=80)
    status: ConditioningRouteStatus
    instruction: str = Field(min_length=1, max_length=2000)
    reason: str = Field(min_length=1, max_length=500)


class ConditioningPlan(BaseModel):
    """Capability-aware routing manifest used by local and cloud workers."""

    version: int = 1
    generator_backend: str = Field(min_length=1, max_length=80)
    routes: list[ConditioningRoute] = Field(default_factory=list, max_length=480)
    warnings: list[str] = Field(default_factory=list, max_length=50)

    @property
    def deferred_modalities(self) -> list[BindingModality]:
        return sorted(
            {
                route.modality
                for route in self.routes
                if route.status == ConditioningRouteStatus.deferred
            },
            key=lambda modality: modality.value,
        )

    @property
    def has_deferred_routes(self) -> bool:
        return bool(self.deferred_modalities)

    @property
    def external_backends(self) -> list[str]:
        return sorted(
            {
                route.backend
                for route in self.routes
                if route.status == ConditioningRouteStatus.external
            }
        )

    def refresh_warnings(self) -> None:
        warnings = []
        deferred = [modality.value for modality in self.deferred_modalities]
        if deferred:
            warnings.append(
                "Deferred controls are preserved but not executed: "
                + ", ".join(deferred)
                + "."
            )
        if self.external_backends:
            warnings.append(
                "External targets are selected but require execution adapters: "
                + ", ".join(self.external_backends)
                + "."
            )
        self.warnings = warnings

    @model_validator(mode="after")
    def validate_routes(self) -> "ConditioningPlan":
        route_keys = [
            (
                route.segment_index,
                route.modality,
                tuple(route.asset_tags),
                route.instruction,
            )
            for route in self.routes
        ]
        if len(set(route_keys)) != len(route_keys):
            raise ValueError("Conditioning routes must be unique per segment")
        return self
