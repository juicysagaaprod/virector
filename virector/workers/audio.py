from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from virector.models.shot_spec import ShotBeat


class AudioAssemblyError(RuntimeError):
    """Raised when timed dialogue or final audio mixing cannot be verified."""


def _binary(name: str) -> str:
    executable = shutil.which(name)
    if executable:
        return executable
    if name == "ffmpeg":
        try:
            import imageio_ffmpeg

            return imageio_ffmpeg.get_ffmpeg_exe()
        except (ImportError, RuntimeError):
            pass
    raise AudioAssemblyError(f"{name} is required for audio assembly")


def media_duration(path: Path) -> float:
    completed = subprocess.run(
        [
            _binary("ffprobe"),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AudioAssemblyError(f"Could not probe audio duration for {path.name}")
    try:
        return float(json.loads(completed.stdout)["format"]["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AudioAssemblyError("FFprobe returned no valid media duration") from exc


def align_dialogue_beat_to_audio(beat: ShotBeat, audio: Path) -> ShotBeat:
    """Use supplied/synthesized speech as the timing authority for a speaking beat."""

    if not beat.dialogue:
        return beat
    duration = media_duration(audio)
    if duration <= 0:
        raise AudioAssemblyError("Dialogue audio duration must be positive")
    return beat.model_copy(
        update={
            "duration_seconds": duration,
            "dialogue_audio_uri": audio.resolve().as_uri(),
        }
    )


class FfmpegAudioMixer:
    """Mix dialogue, footsteps and ambience as separate inputs into an MP4."""

    name = "ffmpeg"

    def mix(
        self,
        video: Path,
        output: Path,
        *,
        dialogue: Path | None = None,
        footsteps: Path | None = None,
        ambience: Path | None = None,
    ) -> Path:
        stems = [stem for stem in (dialogue, footsteps, ambience) if stem is not None]
        if not stems:
            raise AudioAssemblyError("At least one audio stem is required")
        missing = [stem.name for stem in stems if not stem.is_file()]
        if missing:
            raise AudioAssemblyError("Missing audio stems: " + ", ".join(missing))
        command = [_binary("ffmpeg"), "-y", "-i", str(video)]
        for stem in stems:
            command.extend(["-i", str(stem)])
        audio_inputs = "".join(f"[{index}:a]" for index in range(1, len(stems) + 1))
        command.extend(
            [
                "-filter_complex",
                f"{audio_inputs}amix=inputs={len(stems)}:duration=longest:normalize=0[a]",
                "-map",
                "0:v:0",
                "-map",
                "[a]",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-shortest",
                "-movflags",
                "+faststart",
                str(output),
            ]
        )
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0 or not output.is_file():
            detail = completed.stderr.strip().splitlines()
            raise AudioAssemblyError(
                "Audio mix failed: "
                + (detail[-1] if detail else "FFmpeg returned no output")
            )
        return output
