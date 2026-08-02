from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from virector.models.shot_spec import ShotBeat


class ShotCapability(str, Enum):
    ACTION = "action"
    DIALOGUE = "dialogue"
    LIP_SYNC = "lip_sync"
    ASSEMBLY = "assembly"


class CapabilityStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    REQUIRES_INSTALL = "requires_install"


@dataclass(frozen=True)
class CapabilityRoute:
    capability: ShotCapability
    provider: str
    status: CapabilityStatus
    reason: str


class ShotCapabilityRouter:
    """Route only to capabilities verified as installed and explicitly enabled."""

    def __init__(
        self,
        *,
        action_provider: str | None = None,
        s2v_installed: bool = False,
        lipsync_provider: str | None = None,
    ) -> None:
        self.action_provider = action_provider
        self.s2v_installed = s2v_installed
        self.lipsync_provider = lipsync_provider

    def route(self, beat: ShotBeat, lip_sync_enabled: bool = True) -> list[CapabilityRoute]:
        routes = [
            CapabilityRoute(
                capability=ShotCapability.ACTION,
                provider=self.action_provider or "unassigned",
                status=(
                    CapabilityStatus.AVAILABLE
                    if self.action_provider
                    else CapabilityStatus.REQUIRES_INSTALL
                ),
                reason=(
                    "The active generation worker is explicitly configured for this shot."
                    if self.action_provider
                    else "No verified action-generation worker is configured."
                ),
            )
        ]
        if beat.dialogue:
            routes.append(
                CapabilityRoute(
                    capability=ShotCapability.DIALOGUE,
                    provider="wan2.2-s2v" if self.s2v_installed else "unassigned",
                    status=(
                        CapabilityStatus.AVAILABLE
                        if self.s2v_installed
                        else CapabilityStatus.REQUIRES_INSTALL
                    ),
                    reason=(
                        "Wan2.2-S2V is installed."
                        if self.s2v_installed
                        else "Wan2.2-S2V is not installed and no checkpoint download was approved."
                    ),
                )
            )
            if lip_sync_enabled:
                routes.append(
                    CapabilityRoute(
                        capability=ShotCapability.LIP_SYNC,
                        provider=self.lipsync_provider or "unassigned",
                        status=(
                            CapabilityStatus.AVAILABLE
                            if self.lipsync_provider
                            else CapabilityStatus.REQUIRES_INSTALL
                        ),
                        reason=(
                            "A configured lip-sync adapter will process only this speaking beat."
                            if self.lipsync_provider
                            else "No LatentSync or equivalent self-hosted adapter/checkpoint is installed."
                        ),
                    )
                )
        routes.append(
            CapabilityRoute(
                capability=ShotCapability.ASSEMBLY,
                provider="ffmpeg",
                status=CapabilityStatus.AVAILABLE,
                reason="FFmpeg assembles normalized video and audio streams.",
            )
        )
        return routes
