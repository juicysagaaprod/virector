from dataclasses import replace
from pathlib import Path
from typing import Any

from virector.config import Settings
from virector.models.shot_spec import ShotSpec
from virector.services.director_plan import compile_director_plan
from virector.workers.base import ReferenceAsset, RenderJob
from virector.workers.factory import create_worker
from virector.workers.runpod import RunpodWorker

PROMPT = """CLOUD CLIP
Duration: 4 seconds

Image References
@image1: Lead character
@image2: Designed world

0:00-0:04
@image1 walks through @image2 as the camera tracks forward.
"""


class FakeS3Client:
    def __init__(self) -> None:
        self.uploads: list[tuple[str, str, str]] = []
        self.downloads: list[tuple[str, str, str]] = []
        self.presigned: list[tuple[str, dict[str, Any], int]] = []

    def upload_file(self, filename: str, bucket: str, key: str) -> None:
        self.uploads.append((filename, bucket, key))

    def generate_presigned_url(
        self,
        operation: str,
        *,
        Params: dict[str, Any],
        ExpiresIn: int,
    ) -> str:
        self.presigned.append((operation, Params, ExpiresIn))
        return f"https://assets.example.test/{Params['Key']}?operation={operation}"

    def download_file(self, bucket: str, key: str, filename: str) -> None:
        self.downloads.append((bucket, key, filename))
        Path(filename).write_bytes(b"cloud-video")


class FakeQueueClient:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self.documents = list(documents)
        self.payload: dict[str, Any] | None = None
        self.cancelled: list[str] = []

    def submit(self, payload: dict[str, Any]) -> str:
        self.payload = payload
        return "remote-job-1"

    def status(self, job_id: str) -> dict[str, Any]:
        assert job_id == "remote-job-1"
        return self.documents.pop(0)

    def cancel(self, job_id: str) -> None:
        self.cancelled.append(job_id)


def runpod_settings(tmp_path: Path, **updates: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "data_dir": tmp_path,
        "worker_mode": "runpod",
        "storage_backend": "s3",
        "s3_endpoint_url": "https://account.r2.cloudflarestorage.com",
        "s3_bucket": "virector-bucket",
        "s3_access_key_id": "access-key",
        "s3_secret_access_key": "secret-key",
        "runpod_endpoint_id": "endpoint-1",
        "runpod_api_key": "runpod-secret",
        "runpod_poll_interval_seconds": 0.1,
        "runpod_job_timeout_seconds": 60,
    }
    values.update(updates)
    return Settings(**values)


def render_job(tmp_path: Path) -> RenderJob:
    character = tmp_path / "character.png"
    world = tmp_path / "world.png"
    character.write_bytes(b"character")
    world.write_bytes(b"world")
    plan = compile_director_plan(PROMPT)
    return RenderJob(
        job_id="a" * 32,
        output_dir=tmp_path,
        start_frame=character,
        spec=ShotSpec(
            prompt=PROMPT,
            duration_seconds=4,
            director_plan=plan,
        ),
        reference_images=(character, world),
        reference_assets=(
            ReferenceAsset(index=1, tag="@image1", path=character),
            ReferenceAsset(index=2, tag="@image2", path=world),
        ),
    )


def test_runpod_worker_transfers_references_and_downloads_video(
    tmp_path: Path,
) -> None:
    job = render_job(tmp_path)
    expected_key = f"virector/renders/{job.job_id}/preview.mp4"
    queue = FakeQueueClient(
        [
            {"status": "IN_QUEUE"},
            {"status": "IN_PROGRESS", "output": "60% - Rendering shot 1."},
            {
                "status": "COMPLETED",
                "output": {
                    "job_id": job.job_id,
                    "status": "completed",
                    "output_object_key": expected_key,
                    "message": "Cloud performance render completed.",
                },
            },
        ]
    )
    s3 = FakeS3Client()
    progress: list[tuple[int, str]] = []
    job = replace(
        job,
        progress_callback=lambda value, message: progress.append(
            (value, message)
        ),
    )
    worker = RunpodWorker(
        settings=runpod_settings(tmp_path),
        queue_client=queue,
        s3_client=s3,
        sleep=lambda _: None,
    )

    result = worker.render(job)

    assert result.status == "completed"
    assert result.video == tmp_path / "preview.mp4"
    assert result.video.read_bytes() == b"cloud-video"
    assert len(s3.uploads) == 2
    assert queue.payload is not None
    assert [item["tag"] for item in queue.payload["references"]] == [
        "@image1",
        "@image2",
    ]
    assert queue.payload["output_object_key"] == expected_key
    assert s3.downloads == [
        ("virector-bucket", expected_key, str(tmp_path / "preview.mp4"))
    ]
    assert (60, "Rendering shot 1.") in progress
    assert progress[-1] == (95, "Downloading the completed cloud render.")


def test_runpod_worker_returns_remote_failure(tmp_path: Path) -> None:
    queue = FakeQueueClient(
        [{"status": "FAILED", "error": "Container could not load the model."}]
    )
    worker = RunpodWorker(
        settings=runpod_settings(tmp_path),
        queue_client=queue,
        s3_client=FakeS3Client(),
        sleep=lambda _: None,
    )

    result = worker.render(render_job(tmp_path))

    assert result.status == "failed"
    assert result.video is None
    assert "could not load" in result.message


def test_runpod_worker_cancels_after_timeout(tmp_path: Path) -> None:
    queue = FakeQueueClient([])
    clock = iter([0.0, 61.0])
    worker = RunpodWorker(
        settings=runpod_settings(tmp_path),
        queue_client=queue,
        s3_client=FakeS3Client(),
        sleep=lambda _: None,
        monotonic=lambda: next(clock),
    )

    result = worker.render(render_job(tmp_path))

    assert result.status == "failed"
    assert "timeout" in result.message
    assert queue.cancelled == ["remote-job-1"]


def test_factory_selects_injected_runpod_worker(tmp_path: Path) -> None:
    worker = create_worker(
        runpod_settings(tmp_path),
        runpod_queue_client=FakeQueueClient([]),
        runpod_s3_client=FakeS3Client(),
    )

    assert isinstance(worker, RunpodWorker)
