from io import BytesIO
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from virector.api import build_api
from virector.config import Settings
from virector.services.jobs import JobService
from virector.workers.base import RenderJob, RenderResult, VideoWorker


class ApiWorker(VideoWorker):
    mode = "test"
    requested_mode = "test"

    def __init__(self, create_video: bool = False) -> None:
        self.create_video = create_video
        self.job: RenderJob | None = None

    def render(self, job: RenderJob) -> RenderResult:
        self.job = job
        video = None
        if self.create_video:
            video = job.output_dir / "preview.mp4"
            video.write_bytes(b"test-video")
        return RenderResult(
            job_id=job.job_id,
            status="complete" if video else "composed",
            start_frame=job.start_frame,
            video=video,
            message="Render accepted.",
        )


def image_bytes(colour: tuple[int, int, int]) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (80, 120), colour).save(buffer, format="PNG")
    return buffer.getvalue()


def make_client(tmp_path: Path, worker: ApiWorker) -> TestClient:
    settings = Settings(_env_file=None, data_dir=tmp_path / "data")
    app = FastAPI()
    app.include_router(build_api(JobService(settings, worker)))
    return TestClient(app)


def test_render_api_accepts_ordered_omni_references(tmp_path: Path) -> None:
    worker = ApiWorker(create_video=True)
    client = make_client(tmp_path, worker)

    response = client.post(
        "/api/renders",
        files=[
            (
                "reference_images",
                ("character.png", image_bytes((120, 40, 60)), "image/png"),
            ),
            (
                "reference_images",
                ("world.png", image_bytes((20, 80, 120)), "image/png"),
            ),
        ],
        data={
            "direction_prompt": (
                "@image1 walks naturally through @image2 while the camera tracks."
            ),
            "title": "Tracking shot",
            "aspect_ratio": "16:9",
            "output_resolution": "720p",
            "duration_seconds": "5",
            "seed": "91",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "complete"
    assert payload["video_url"].endswith("/video")
    assert "start_frame" not in payload
    assert worker.job is not None
    assert [asset.tag for asset in worker.job.reference_assets] == [
        "@image1",
        "@image2",
    ]
    assert worker.job.spec.duration_seconds == 5
    assert worker.job.spec.aspect_ratio == "16:9"
    assert worker.job.spec.output_resolution == "720p"

    video_response = client.get(payload["video_url"])
    assert video_response.status_code == 200
    assert video_response.content == b"test-video"
    assert video_response.headers["content-type"] == "video/mp4"


def test_render_api_requires_prompt_to_use_every_reference(tmp_path: Path) -> None:
    client = make_client(tmp_path, ApiWorker())

    response = client.post(
        "/api/renders",
        files=[
            ("reference_images", ("one.png", image_bytes((0, 0, 0)), "image/png")),
            ("reference_images", ("two.png", image_bytes((1, 1, 1)), "image/png")),
        ],
        data={"direction_prompt": "Only move @image1."},
    )

    assert response.status_code == 422
    assert "@image2" in response.json()["detail"]


def test_render_video_rejects_invalid_job_id(tmp_path: Path) -> None:
    client = make_client(tmp_path, ApiWorker())

    response = client.get("/api/renders/not-a-job/video")

    assert response.status_code == 404


def test_health_reports_job_repository(tmp_path: Path) -> None:
    client = make_client(tmp_path, ApiWorker())

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["job_repository"] == "local"
