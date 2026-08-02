import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image, ImageStat
from pytest import MonkeyPatch

from virector.config import Settings
from virector.models.omni_asset import OmniMediaType
from virector.models.shot_spec import ShotSpec
from virector.services.director_plan import compile_director_plan
from virector.workers.base import ReferenceAsset, RenderJob, RenderResult, VideoWorker
from virector.workers.factory import create_worker
from virector.workers.ltx import LtxWorker, LtxWorkerUnavailableError
from virector.workers.ltx_diffusers import (
    _postprocess_video,
    build_ltx_prompt,
    ltx_frame_count,
    ltx_segment_frame_counts,
)
from virector.workers.mock import MockWorker
from virector.workers.performance import PerformanceWorker, build_segment_prompt
from virector.workers.vace import VaceWorker, VaceWorkerUnavailableError
from virector.workers.vace_diffusers import (
    build_vace_prompt,
    evaluate_vace_hardware,
    vace_frame_count,
)
from virector.workers.wan_animate import WanAnimateWorker


class FakeLtxBackend:
    def render(self, job: RenderJob) -> Path:
        video = job.output_dir / "preview.mp4"
        video.write_bytes(b"fake video")
        return video


class FailingLtxBackend:
    def render(self, job: RenderJob) -> Path:
        raise RuntimeError("not enough GPU memory")


class FakeVaceBackend:
    def render(self, job: RenderJob) -> Path:
        video = job.output_dir / "vace-preview.mp4"
        video.write_bytes(b"fake multi-reference video")
        return video


class ReadyWanAnimateBackend:
    def ensure_available(self) -> None:
        pass

    def render(
        self,
        job: RenderJob,
        source_video: Path,
        driving_video: Path,
        output_path: Path,
    ) -> Path:
        output_path.write_bytes(b"wan video")
        return output_path


class CapturingSegmentWorker(VideoWorker):
    mode = "segment-test"
    requested_mode = "segment-test"

    def __init__(self, fail_index: int | None = None) -> None:
        self.jobs: list[RenderJob] = []
        self.fail_index = fail_index

    def render(self, job: RenderJob) -> RenderResult:
        self.jobs.append(job)
        index = len(self.jobs)
        if self.fail_index == index:
            return RenderResult(
                job_id=job.job_id,
                status="failed",
                start_frame=job.start_frame,
                message="performance generation failed",
            )
        video = job.output_dir / "preview.mp4"
        video.write_bytes(f"shot-{index}".encode())
        return RenderResult(
            job_id=job.job_id,
            status="completed",
            start_frame=job.start_frame,
            video=video,
            message="segment complete",
        )


class CapturingAssembler:
    def __init__(self) -> None:
        self.segments: list[Path] = []

    def assemble(self, segments: list[Path], output: Path) -> Path:
        self.segments = segments
        output.write_bytes(b"assembled-video")
        return output


PERFORMANCE_PROMPT = """CLIP 1
Duration: 4 seconds

Image References
@image1: Lead character
@image2: Designed world

0:00-0:02
@image1 walks toward camera.
Lead character: "We need to move."

0:02-0:04
Camera reveals @image2.
Transition: Hard cut to black.
"""


def performance_job(tmp_path: Path) -> RenderJob:
    character = tmp_path / "character.png"
    world = tmp_path / "world.png"
    character.write_bytes(b"character")
    world.write_bytes(b"world")
    plan = compile_director_plan(PERFORMANCE_PROMPT)
    return RenderJob(
        job_id="performance-job",
        output_dir=tmp_path,
        start_frame=character,
        spec=ShotSpec(
            prompt=PERFORMANCE_PROMPT,
            duration_seconds=4,
            director_plan=plan,
        ),
        reference_images=(character, world),
        reference_assets=(
            ReferenceAsset(index=1, tag="@image1", path=character),
            ReferenceAsset(index=2, tag="@image2", path=world),
        ),
    )


def test_factory_selects_mock_by_default(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, worker_mode="mock")

    worker = create_worker(settings)

    assert isinstance(worker, MockWorker)
    assert worker.requested_mode == "mock"
    assert worker.fallback_reason is None


def test_factory_selects_performance_worker_with_ltx_segments(
    tmp_path: Path,
) -> None:
    settings = Settings(
        data_dir=tmp_path,
        worker_mode="performance",
        performance_segment_worker="ltx",
    )

    worker = create_worker(settings, ltx_backend=FakeLtxBackend())

    assert isinstance(worker, PerformanceWorker)
    assert isinstance(worker.segment_worker, LtxWorker)


def test_factory_configures_specialized_conditioning_targets(
    tmp_path: Path,
) -> None:
    settings = Settings(
        data_dir=tmp_path,
        worker_mode="performance",
        performance_segment_worker="ltx",
        performance_motion_backend="wan-animate",
        performance_speech_backend="hunyuan-avatar",
        performance_audio_backend="ffmpeg",
    )

    worker = create_worker(
        settings,
        ltx_backend=FakeLtxBackend(),
        wan_animate_backend=ReadyWanAnimateBackend(),
    )

    assert isinstance(worker, PerformanceWorker)
    assert worker.conditioning_router.describe() == {
        "generator": "ltx",
        "motion": "wan-animate",
        "speech": "hunyuan-avatar",
        "audio": "ffmpeg",
    }
    assert isinstance(worker.motion_worker, WanAnimateWorker)
    assert worker.conditioning_fallback_reason is None


def test_performance_worker_renders_referenced_shots_and_assembles(
    tmp_path: Path,
) -> None:
    segment_worker = CapturingSegmentWorker()
    assembler = CapturingAssembler()
    progress: list[tuple[int, str]] = []
    job = performance_job(tmp_path)
    job = replace(
        job,
        progress_callback=lambda value, message: progress.append((value, message)),
    )

    result = PerformanceWorker(segment_worker, assembler).render(job)

    assert result.status == "completed"
    assert result.video == tmp_path / "preview.mp4"
    assert result.video.read_bytes() == b"assembled-video"
    assert len(segment_worker.jobs) == 2
    first, second = segment_worker.jobs
    assert [asset.tag for asset in first.reference_assets] == ["@image1"]
    assert [asset.tag for asset in second.reference_assets] == ["@image2"]
    assert first.spec.duration_seconds == 2
    assert second.spec.duration_seconds == 2
    assert first.spec.seed == 42
    assert second.spec.seed == 43
    assert first.spec.director_plan is None
    assert "We need to move." in first.spec.prompt
    assert assembler.segments == [
        first.output_dir / "preview.mp4",
        second.output_dir / "preview.mp4",
    ]
    assert progress[-1] == (90, "Assembling the final multi-shot video.")


def test_performance_worker_stops_when_a_segment_fails(tmp_path: Path) -> None:
    segment_worker = CapturingSegmentWorker(fail_index=2)
    assembler = CapturingAssembler()

    result = PerformanceWorker(segment_worker, assembler).render(
        performance_job(tmp_path)
    )

    assert result.status == "failed"
    assert result.video is None
    assert "Shot 2 failed" in result.message
    assert not assembler.segments


def test_performance_worker_routes_nonvisual_assets_to_referenced_segment(
    tmp_path: Path,
) -> None:
    prompt = """MULTIMODAL CLIP
Duration: 4 seconds

Image References
@image1: Lead character
@image2: Designed world
Video References
@video1: Walking motion and camera movement
Audio References
@audio1: Lead voice

0:00-0:02
@image1 follows @video1 and speaks with @audio1.

0:02-0:04
Camera reveals @image2.
"""
    character = tmp_path / "character.png"
    world = tmp_path / "world.png"
    motion = tmp_path / "motion.mp4"
    voice = tmp_path / "voice.wav"
    for path in (character, world, motion, voice):
        path.write_bytes(path.name.encode())
    plan = compile_director_plan(prompt)
    job = RenderJob(
        job_id="multimodal-performance-job",
        output_dir=tmp_path,
        start_frame=character,
        spec=ShotSpec(prompt=prompt, duration_seconds=4, director_plan=plan),
        reference_images=(character, world),
        reference_videos=(motion,),
        reference_audio=(voice,),
        reference_assets=(
            ReferenceAsset(index=1, tag="@image1", path=character),
            ReferenceAsset(index=2, tag="@image2", path=world),
            ReferenceAsset(
                index=1,
                tag="@video1",
                path=motion,
                media_type=OmniMediaType.video,
            ),
            ReferenceAsset(
                index=1,
                tag="@audio1",
                path=voice,
                media_type=OmniMediaType.audio,
            ),
        ),
    )
    segment_worker = CapturingSegmentWorker()

    PerformanceWorker(segment_worker, CapturingAssembler()).render(job)

    first, second = segment_worker.jobs
    assert [asset.tag for asset in first.reference_assets] == [
        "@image1",
        "@video1",
        "@audio1",
    ]
    assert first.reference_videos == (motion,)
    assert first.reference_audio == (voice,)
    assert [asset.tag for asset in second.reference_assets] == ["@image2"]


def test_segment_prompt_preserves_director_cues(tmp_path: Path) -> None:
    plan = performance_job(tmp_path).spec.director_plan
    assert plan is not None

    prompt = build_segment_prompt(plan, plan.segments[1])

    assert "Visual references: @image2." in prompt
    assert "ReferenceBinding [visual]" in prompt
    assert "controls: environment, composition" in prompt
    assert "Transition: Hard cut to black." in prompt


def test_factory_falls_back_when_ltx_backend_is_missing(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    def unavailable_backend(settings: Settings):
        raise LtxWorkerUnavailableError("The LTX runtime is not installed.")

    monkeypatch.setattr(
        "virector.workers.factory._create_default_ltx_backend",
        unavailable_backend,
    )
    settings = Settings(data_dir=tmp_path, worker_mode="ltx")

    worker = create_worker(settings)

    assert isinstance(worker, MockWorker)
    assert worker.requested_mode == "ltx"
    assert worker.fallback_reason is not None
    assert "not installed" in worker.fallback_reason


def test_ltx_worker_uses_injected_backend(tmp_path: Path) -> None:
    start_frame = tmp_path / "start_frame.png"
    start_frame.write_bytes(b"fake image")
    job = RenderJob(
        job_id="test-job",
        output_dir=tmp_path,
        start_frame=start_frame,
        spec=ShotSpec(prompt="A controlled cinematic character entrance."),
    )
    settings = Settings(data_dir=tmp_path, worker_mode="ltx")

    worker = create_worker(settings, ltx_backend=FakeLtxBackend())
    result = worker.render(job)

    assert isinstance(worker, LtxWorker)
    assert result.status == "completed"
    assert result.video == tmp_path / "preview.mp4"
    assert result.video.is_file()


def test_ltx_worker_returns_backend_failure(tmp_path: Path) -> None:
    start_frame = tmp_path / "start_frame.png"
    start_frame.write_bytes(b"fake image")
    job = RenderJob(
        job_id="failed-job",
        output_dir=tmp_path,
        start_frame=start_frame,
        spec=ShotSpec(prompt="A controlled cinematic character entrance."),
    )
    worker = LtxWorker(backend=FailingLtxBackend())

    result = worker.render(job)

    assert result.status == "failed"
    assert result.video is None
    assert "not enough GPU memory" in result.message


def test_ltx_preview_uses_compatible_four_second_frame_count() -> None:
    assert ltx_frame_count(4.0, 24, max_frames=97) == 97
    assert ltx_frame_count(15.0, 24, max_frames=96) == 89


def test_ltx_duration_is_chained_through_fifteen_seconds() -> None:
    assert ltx_segment_frame_counts(1.0, 24, max_frames=97) == [25]
    assert ltx_segment_frame_counts(4.0, 24, max_frames=97) == [97]
    assert ltx_segment_frame_counts(15.0, 24, max_frames=97) == [
        97,
        97,
        97,
        73,
    ]


def test_ltx_prompt_uses_single_direction_box_verbatim() -> None:
    spec = ShotSpec(prompt="The lead crosses the room.")

    prompt = build_ltx_prompt(spec)

    assert prompt == "The lead crosses the room."


def test_factory_selects_injected_vace_backend(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, worker_mode="vace")

    worker = create_worker(settings, vace_backend=FakeVaceBackend())

    assert isinstance(worker, VaceWorker)
    assert worker.requested_mode == "vace"


def test_vace_mode_falls_back_to_injected_ltx_backend(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    def unavailable_backend(settings: Settings):
        raise VaceWorkerUnavailableError("The VACE worker is unavailable.")

    monkeypatch.setattr(
        "virector.workers.factory._create_default_vace_backend",
        unavailable_backend,
    )
    settings = Settings(data_dir=tmp_path, worker_mode="vace")

    worker = create_worker(settings, ltx_backend=FakeLtxBackend())

    assert isinstance(worker, LtxWorker)
    assert worker.requested_mode == "vace"
    assert worker.fallback_reason is not None
    assert "VACE worker" in worker.fallback_reason


def test_vace_worker_passes_indexed_references_to_backend(
    tmp_path: Path,
) -> None:
    start_frame = tmp_path / "start_frame.png"
    start_frame.write_bytes(b"fake image")
    character = tmp_path / "character.png"
    character.write_bytes(b"fake character")
    job = RenderJob(
        job_id="vace-job",
        output_dir=tmp_path,
        start_frame=start_frame,
        spec=ShotSpec(prompt="The lead crosses the designed world."),
        reference_images=(character,),
        reference_assets=(
            ReferenceAsset(
                index=1,
                tag="@image1",
                path=character,
            ),
        ),
    )
    worker = VaceWorker(backend=FakeVaceBackend())

    result = worker.render(job)

    assert result.status == "completed"
    assert result.video == tmp_path / "vace-preview.mp4"


def test_vace_frame_count_is_wan_compatible() -> None:
    assert vace_frame_count(1, fps=16, max_frames=81) == 17
    assert vace_frame_count(4, fps=16, max_frames=81) == 65
    assert vace_frame_count(15, fps=16, max_frames=81) == 81


def test_vace_prompt_leads_with_compiled_image_reference_contract() -> None:
    prompt = """WORLD TEST
Duration: 4 seconds

Image References
Image 1: Lead character
Image 2: House exterior

0:00-0:04
Combine Image 1 and Image 2. Generate the character walking through the house
environment while maintaining both designs.
"""
    spec = ShotSpec(
        prompt=prompt,
        director_plan=compile_director_plan(prompt),
    )

    compiled = build_vace_prompt(spec)

    assert compiled.startswith("Ordered image-reference contract:")
    assert "Image 1: reference, combine, generate, maintain" in compiled
    assert "controls character_identity, wardrobe" in compiled
    assert "Image 2: reference, combine, generate, maintain" in compiled
    assert "controls environment, composition" in compiled
    assert compiled.endswith(prompt.strip())


def test_postprocess_video_interpolates_to_delivery_fps(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    monkeypatch.setattr(
        "virector.workers.ltx_diffusers.shutil.which",
        lambda _: "ffmpeg",
    )
    monkeypatch.setattr(
        "virector.workers.ltx_diffusers.subprocess.run",
        lambda command, **_: commands.append(command),
    )

    _postprocess_video(
        tmp_path / "native.mp4",
        tmp_path / "preview.mp4",
        width=480,
        height=832,
        fps=24,
        interpolate=True,
    )

    filter_value = commands[0][commands[0].index("-vf") + 1]
    assert filter_value.startswith("minterpolate=fps=24:")
    assert filter_value.endswith(",scale=480:832:flags=lanczos")


def test_postprocess_video_overlays_anchor_on_delivery_frame_zero(
    tmp_path: Path,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("FFmpeg is required for the delivery-frame retention contract.")

    source = tmp_path / "native.mp4"
    anchor = tmp_path / "anchor.png"
    output = tmp_path / "preview.mp4"
    frame_pattern = tmp_path / "frame-%02d.png"
    Image.new("RGB", (64, 64), (0, 255, 0)).save(anchor)
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=64x64:r=16:d=0.25",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
        capture_output=True,
    )

    _postprocess_video(
        source,
        output,
        fps=16,
        first_frame=anchor,
    )
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(output),
            "-frames:v",
            "2",
            str(frame_pattern),
        ],
        check=True,
        capture_output=True,
    )

    first_mean = ImageStat.Stat(Image.open(tmp_path / "frame-01.png")).mean
    second_mean = ImageStat.Stat(Image.open(tmp_path / "frame-02.png")).mean
    assert first_mean[1] > 220
    assert first_mean[0] < 30
    assert second_mean[0] > 220
    assert second_mean[1] < 30


def test_vace_preflight_blocks_insufficient_worker_ram() -> None:
    report = evaluate_vace_hardware(
        cuda_available=True,
        gpu_name="Test GPU",
        gpu_total_gb=8.15,
        gpu_free_gb=6.5,
        system_total_gb=7.56,
        system_available_gb=6.0,
        disk_free_gb=100,
        checkpoint_present=False,
    )

    assert not report.supported
    assert "system RAM" in report.blockers[0]


def test_vace_preflight_accepts_guarded_quantized_worker() -> None:
    report = evaluate_vace_hardware(
        cuda_available=True,
        gpu_name="Test GPU",
        gpu_total_gb=24,
        gpu_free_gb=23,
        system_total_gb=64,
        system_available_gb=48,
        disk_free_gb=100,
        checkpoint_present=False,
    )

    assert report.supported
    assert not report.blockers
