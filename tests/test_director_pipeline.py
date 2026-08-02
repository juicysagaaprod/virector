import json
import shutil
import subprocess
import wave
from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError

from virector.models.omni_asset import ReferenceRole
from virector.models.shot_spec import ReferenceDirective, ShotBeat, ShotSpec
from virector.services.director_plan import compile_director_plan
from virector.services.prompt_compiler import compile_model_prompt
from virector.services.reference_resolution import (
    ReferenceResolutionError,
    resolve_reference_map,
)
from virector.services.timeline import timeline_from_director_plan
from virector.workers.audio import align_dialogue_beat_to_audio
from virector.workers.base import ReferenceAsset, RenderJob, RenderResult, VideoWorker
from virector.workers.capability_router import (
    CapabilityStatus,
    ShotCapability,
    ShotCapabilityRouter,
)
from virector.workers.dialogue import require_dialogue_provider
from virector.workers.performance import PerformanceWorker
from virector.workers.vace_diffusers import build_first_frame_condition


def _two_images(tmp_path: Path) -> tuple[Path, Path]:
    character = tmp_path / "character.png"
    world = tmp_path / "world.png"
    Image.new("RGB", (320, 480), "white").save(character)
    Image.new("RGB", (640, 360), (25, 75, 45)).save(world)
    return character, world


def _directives() -> list[ReferenceDirective]:
    return [
        ReferenceDirective(index=1, tag="@image1"),
        ReferenceDirective(index=2, tag="@image2"),
    ]


def test_reference_aliases_resolve_to_fixed_character_and_world_roles(
    tmp_path: Path,
) -> None:
    character, world = _two_images(tmp_path)

    resolved = resolve_reference_map(_directives(), [character, world])

    assert resolved.by_alias("@image1").role == ReferenceRole.CHARACTER_IDENTITY
    assert resolved.by_alias("@image2").role == ReferenceRole.WORLD_ENVIRONMENT
    assert resolved.by_alias("@image1").storage_uri.endswith("character.png")
    assert resolved.by_alias("@image2").storage_uri.endswith("world.png")


def test_reference_roles_never_follow_incorrect_prompt_heuristics(tmp_path: Path) -> None:
    character, world = _two_images(tmp_path)
    misleading = compile_director_plan(
        "Maintain the man's identity from @image1 and place him in the lane from @image2."
    )

    resolved = resolve_reference_map(_directives(), [character, world], misleading)

    assert resolved.by_alias("@image1").role == ReferenceRole.CHARACTER_IDENTITY
    assert resolved.by_alias("@image2").role == ReferenceRole.WORLD_ENVIRONMENT


def test_missing_and_duplicate_reference_resolution_is_rejected(tmp_path: Path) -> None:
    character, world = _two_images(tmp_path)
    duplicate = [
        ReferenceDirective(index=1, tag="@image1"),
        ReferenceDirective(index=1, tag="@image1"),
    ]

    with pytest.raises(ReferenceResolutionError, match="Duplicate"):
        resolve_reference_map(duplicate, [character, world])
    with pytest.raises(ReferenceResolutionError, match="exactly one directive"):
        resolve_reference_map(_directives(), [character])


def test_timeline_must_be_contiguous_and_match_total_duration() -> None:
    with pytest.raises(ValidationError, match="Timeline duration"):
        ShotSpec(
            prompt="A test shot timeline.",
            duration_seconds=4,
            timeline=[
                ShotBeat(
                    shot_id="one",
                    start_seconds=0,
                    duration_seconds=3,
                    subject_action="walk",
                )
            ],
        )


def test_silent_shot_bypasses_speech_and_lipsync_requirements() -> None:
    silent = ShotBeat(
        shot_id="walk",
        start_seconds=0,
        duration_seconds=2,
        subject_action="walk naturally",
    )

    require_dialogue_provider(
        silent,
        speech_provider=None,
        lip_sync_provider=None,
        generate_speech=True,
        lip_sync_enabled=True,
    )
    routes = ShotCapabilityRouter().route(silent)
    assert not any(route.capability == ShotCapability.LIP_SYNC for route in routes)


def test_dialogue_route_is_not_claimed_without_installed_backends() -> None:
    speaking = ShotBeat(
        shot_id="dialogue",
        start_seconds=0,
        duration_seconds=2,
        subject_action="turn toward camera",
        dialogue="Tell me the truth.",
    )

    routes = ShotCapabilityRouter().route(speaking)

    dialogue = next(route for route in routes if route.capability == ShotCapability.DIALOGUE)
    lipsync = next(route for route in routes if route.capability == ShotCapability.LIP_SYNC)
    assert dialogue.status == CapabilityStatus.REQUIRES_INSTALL
    assert lipsync.status == CapabilityStatus.REQUIRES_INSTALL


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="FFprobe unavailable")
def test_dialogue_audio_is_the_shot_timing_authority(tmp_path: Path) -> None:
    audio = tmp_path / "dialogue.wav"
    with wave.open(str(audio), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(16_000)
        target.writeframes(b"\x00\x00" * 20_000)
    beat = ShotBeat(
        shot_id="speaking",
        start_seconds=0,
        duration_seconds=2,
        subject_action="speak to camera",
        dialogue="Exact words.",
    )

    aligned = align_dialogue_beat_to_audio(beat, audio)

    assert aligned.duration_seconds == pytest.approx(1.25, abs=0.02)
    assert aligned.dialogue_audio_uri == audio.resolve().as_uri()


def test_vace_first_frame_condition_retains_anchor_and_generates_future_frames(
    tmp_path: Path,
) -> None:
    anchor = tmp_path / "anchor.png"
    Image.new("RGB", (640, 360), (10, 20, 30)).save(anchor)

    video, mask = build_first_frame_condition(
        anchor,
        width=480,
        height=272,
        frame_count=5,
    )

    assert len(video) == len(mask) == 5
    assert video[0].size == (480, 272)
    assert mask[0].getextrema() == (0, 0)
    assert all(frame.getextrema() == (255, 255) for frame in mask[1:])


def test_compiled_model_prompt_resolves_aliases_to_conditioning_positions(
    tmp_path: Path,
) -> None:
    character, world = _two_images(tmp_path)
    assets = (
        ReferenceAsset(
            1,
            "@image1",
            character,
            role=ReferenceRole.CHARACTER_IDENTITY,
        ),
        ReferenceAsset(
            2,
            "@image2",
            world,
            role=ReferenceRole.WORLD_ENVIRONMENT,
        ),
    )
    beat = ShotBeat(
        shot_id="walk",
        start_seconds=0,
        duration_seconds=2,
        framing="wide full-body",
        camera_motion="tracking",
        subject_action="walk six natural steps",
        expression="focused",
    )

    prompt = compile_model_prompt(ShotSpec(prompt="UI @image1 @image2"), beat, assets)

    assert "conditioning image 1" in prompt
    assert "conditioning image 2" in prompt
    assert "@image1" not in prompt
    assert "@image2" not in prompt


class SyntheticSegmentWorker(VideoWorker):
    mode = "synthetic"
    requested_mode = "synthetic"

    def __init__(self) -> None:
        self.jobs: list[RenderJob] = []

    def render(self, job: RenderJob) -> RenderResult:
        self.jobs.append(job)
        output = job.output_dir / "preview.mp4"
        colour = "blue" if len(self.jobs) == 1 else "green"
        completed = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c={colour}:s=320x240:r=24:d={job.spec.duration_seconds}",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=440:duration={job.spec.duration_seconds}",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                str(output),
            ],
            capture_output=True,
            check=False,
        )
        assert completed.returncode == 0
        return RenderResult(
            job_id=job.job_id,
            status="completed",
            start_frame=job.start_frame,
            video=output,
        )


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg unavailable")
def test_mocked_multi_shot_pipeline_preserves_continuity_and_audio_video_streams(
    tmp_path: Path,
) -> None:
    prompt = """TEST\nDuration: 2 seconds\n\nImage References\n@image1: Lead character\n@image2: World environment\n\n0:00-0:01\n@image1 walks in @image2.\n\n0:01-0:02\n@image1 turns in @image2.\n"""
    character, world = _two_images(tmp_path)
    plan = compile_director_plan(prompt)
    timeline = timeline_from_director_plan(plan)
    assets = (
        ReferenceAsset(
            1, "@image1", character, role=ReferenceRole.CHARACTER_IDENTITY
        ),
        ReferenceAsset(
            2, "@image2", world, role=ReferenceRole.WORLD_ENVIRONMENT
        ),
    )
    worker = SyntheticSegmentWorker()
    result = PerformanceWorker(worker).render(
        RenderJob(
            job_id="mocked-integration",
            output_dir=tmp_path,
            start_frame=world,
            continuity_frame=world,
            spec=ShotSpec(
                prompt=prompt,
                director_plan=plan,
                timeline=timeline,
                duration_seconds=2,
                aspect_ratio="16:9",
                width=480,
                height=272,
            ),
            reference_images=(character, world),
            reference_assets=assets,
        )
    )

    assert result.status == "completed"
    assert result.video is not None and result.video.is_file()
    assert worker.jobs[0].continuity_frame == world
    assert worker.jobs[1].continuity_frame is not None
    assert worker.jobs[1].continuity_frame.name == "continuity-final-frame.png"
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "json",
            str(result.video),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    stream_types = {stream["codec_type"] for stream in json.loads(probe.stdout)["streams"]}
    assert stream_types == {"video", "audio"}
