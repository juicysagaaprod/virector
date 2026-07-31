from pathlib import Path

from virector.config import Settings
from virector.models.shot_spec import ShotSpec
from virector.workers.base import RenderJob
from virector.workers.factory import create_worker
from virector.workers.ltx import LtxWorker
from virector.workers.mock import MockWorker


class FakeLtxBackend:
    def render(self, job: RenderJob) -> Path:
        video = job.output_dir / "preview.mp4"
        video.write_bytes(b"fake video")
        return video


def test_factory_selects_mock_by_default(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, worker_mode="mock")

    worker = create_worker(settings)

    assert isinstance(worker, MockWorker)
    assert worker.requested_mode == "mock"
    assert worker.fallback_reason is None


def test_factory_falls_back_when_ltx_backend_is_missing(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, worker_mode="ltx")

    worker = create_worker(settings)

    assert isinstance(worker, MockWorker)
    assert worker.requested_mode == "ltx"
    assert worker.fallback_reason is not None
    assert "not configured" in worker.fallback_reason


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
