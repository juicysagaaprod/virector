from dataclasses import dataclass

from virector.models.conditioning import (
    ConditioningPlan,
    ConditioningRoute,
    ConditioningRouteStatus,
)
from virector.models.director_plan import DirectorPlan
from virector.models.omni_asset import BindingModality


@dataclass(frozen=True)
class ConditioningTargets:
    """Selected specialist backends; ``disabled`` means not connected."""

    motion: str = "disabled"
    speech: str = "disabled"
    audio: str = "disabled"


class ConditioningRouter:
    """Compile ReferenceBindings into an explicit backend execution plan."""

    def __init__(
        self,
        generator_backend: str,
        targets: ConditioningTargets | None = None,
    ) -> None:
        self.generator_backend = generator_backend
        self.targets = targets or ConditioningTargets()

    def _route(
        self,
        modality: BindingModality,
        asset_count: int,
    ) -> tuple[str, ConditioningRouteStatus, str]:
        if modality == BindingModality.visual:
            if self.generator_backend == "vace":
                return (
                    "vace",
                    ConditioningRouteStatus.native,
                    "VACE accepts ordered multi-image visual conditioning.",
                )
            if self.generator_backend == "ltx":
                return (
                    "ltx",
                    ConditioningRouteStatus.limited,
                    "LTX uses the primary image as its start-frame condition; "
                    f"{asset_count} tagged visual reference(s) remain in the manifest.",
                )
            return (
                self.generator_backend,
                ConditioningRouteStatus.limited,
                "The selected generator receives the prompt and primary image only.",
            )

        if modality == BindingModality.motion:
            if self.targets.motion != "disabled":
                return (
                    self.targets.motion,
                    ConditioningRouteStatus.external,
                    "Route to the configured motion-performance stage after base generation.",
                )
            return (
                "unassigned",
                ConditioningRouteStatus.deferred,
                "No motion-performance worker is connected; the base generator sees "
                "this instruction as text only.",
            )

        if modality == BindingModality.camera:
            return (
                "unassigned",
                ConditioningRouteStatus.deferred,
                "Wan2.2-Animate transfers human pose and expression, not a "
                "dedicated camera trajectory; no camera-control worker is connected.",
            )

        if modality == BindingModality.voice:
            if self.targets.speech != "disabled":
                return (
                    self.targets.speech,
                    ConditioningRouteStatus.external,
                    "Route to the configured audio-driven performance and lip-sync stage.",
                )
            return (
                "unassigned",
                ConditioningRouteStatus.deferred,
                "No speech-performance worker is connected; voice and lip-sync are not applied.",
            )

        if modality == BindingModality.audio:
            if self.targets.audio != "disabled":
                return (
                    self.targets.audio,
                    ConditioningRouteStatus.external,
                    "Route to the configured soundtrack mixing stage.",
                )
            return (
                "unassigned",
                ConditioningRouteStatus.deferred,
                "No audio mixer is connected; the reference audio is not added to the video.",
            )

        return (
            self.generator_backend,
            ConditioningRouteStatus.limited,
            "Effect guidance is supplied to the base generator as text conditioning.",
        )

    def compile(self, director_plan: DirectorPlan) -> ConditioningPlan:
        routes: list[ConditioningRoute] = []
        for segment in director_plan.segments:
            for binding in segment.reference_bindings:
                backend, status, reason = self._route(
                    binding.modality,
                    len(binding.asset_tags),
                )
                routes.append(
                    ConditioningRoute(
                        segment_index=segment.index,
                        modality=binding.modality,
                        asset_tags=binding.asset_tags,
                        backend=backend,
                        status=status,
                        instruction=binding.instruction,
                        reason=reason,
                    )
                )

        plan = ConditioningPlan(
            generator_backend=self.generator_backend,
            routes=routes,
        )
        plan.refresh_warnings()
        return plan

    def describe(self) -> dict[str, str]:
        return {
            "generator": self.generator_backend,
            "motion": self.targets.motion,
            "speech": self.targets.speech,
            "audio": self.targets.audio,
        }
