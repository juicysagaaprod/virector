from __future__ import annotations

from pathlib import Path
from typing import Protocol

from virector.models.shot_spec import ShotBeat, ShotSpec


class SpeechProvider(Protocol):
    name: str

    def synthesize(self, text: str, spec: ShotSpec, output: Path) -> Path:
        """Synthesize exact text and return a timed audio file."""


class LipSyncProvider(Protocol):
    name: str

    def apply(self, video: Path, audio: Path, beat: ShotBeat, output: Path) -> Path:
        """Synchronize visible mouth motion to the supplied timing-authority audio."""


class DialogueProviderUnavailableError(RuntimeError):
    """Raised rather than pretending prompt-only dialogue is synchronized."""


def require_dialogue_provider(
    beat: ShotBeat,
    *,
    speech_provider: SpeechProvider | None,
    lip_sync_provider: LipSyncProvider | None,
    generate_speech: bool,
    lip_sync_enabled: bool,
) -> None:
    if not beat.dialogue:
        return
    if generate_speech and beat.dialogue_audio_uri is None and speech_provider is None:
        raise DialogueProviderUnavailableError(
            "This speaking shot needs a self-hosted speech provider, but none is installed."
        )
    if lip_sync_enabled and lip_sync_provider is None:
        raise DialogueProviderUnavailableError(
            "This speaking shot needs a self-hosted lip-sync provider, but none is installed."
        )
