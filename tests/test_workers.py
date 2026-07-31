from pathlib import Path

from pytest import MonkeyPatch

from virector.config import Settings
from virector.models.shot_spec import ShotSpec
from virector.workers.base import RenderJob
from virector.workers.factory import create_worker
from virector.workers.ltx import LtxWorker, LtxWorkerUnavailableError
from virector.workers.ltx_diffusers import (
    build_ltx_prompt,
    ltx_frame_count,
    ltx_segment_frame_counts,
)
from virector.workers.mock import MockWorker


class FakeLtxBackend:
    def render(self, job: RenderJob) -> Path:
        video = job.output_dir / "preview.mp4"
        video.write_bytes(b"fake video")
        return video


class FailingLtxBackend:
    def render(self, job: RenderJob) -> Path:
        raise RuntimeError("not enough GPU memory")


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
