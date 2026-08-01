from dataclasses import replace
from pathlib import Path

from pytest import MonkeyPatch

from virector.config import Settings
from virector.models.shot_spec import ShotSpec
from virector.services.director_plan import compile_director_plan
from virector.workers.base import ReferenceAsset, RenderJob, RenderResult, VideoWorker
from virector.workers.factory import create_worker
from virector.workers.ltx import LtxWorker, LtxWorkerUnavailableError
from virector.workers.ltx_diffusers import (
    build_ltx_prompt,
    ltx_frame_count,
    ltx_segment_frame_counts,
)
from virector.workers.mock import MockWorker
from virector.workers.performance import PerformanceWorker, build_segment_prompt
from virector.workers.vace import VaceWorker, VaceWorkerUnavailableError
from virector.workers.vace_diffusers import (
    evaluate_vace_hardware,
    vace_frame_count,
)


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


def test_segment_prompt_preserves_director_cues(tmp_path: Path) -> None:
    plan = performance_job(tmp_path).spec.director_plan
    assert plan is not None

    prompt = build_segment_prompt(plan, plan.segments[1])

    assert "Visual references: @image2." in prompt
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
