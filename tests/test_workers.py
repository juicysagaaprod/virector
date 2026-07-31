from pathlib import Path

from pytest import MonkeyPatch

from virector.config import Settings
from virector.models.shot_spec import ShotSpec
from virector.workers.base import ReferenceAsset, RenderJob
from virector.workers.factory import create_worker
from virector.workers.ltx import LtxWorker, LtxWorkerUnavailableError
from virector.workers.ltx_diffusers import (
    build_ltx_prompt,
    ltx_frame_count,
    ltx_segment_frame_counts,
)
from virector.workers.mock import MockWorker
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


def test_factory_selects_mock_by_default(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, worker_mode="mock")

    worker = create_worker(settings)

    assert isinstance(worker, MockWorker)
    assert worker.requested_mode == "mock"
    assert worker.fallback_reason is None


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
