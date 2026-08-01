from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from pydantic import ValidationError

from virector.models.shot_spec import ShotSpec
from virector.models.omni_asset import OmniMediaType
from virector.runpod_runtime import (
    RemoteFileClient,
    RunpodPayloadError,
    RunpodPerformanceRuntime,
    RunpodRenderInput,
    require_cuda_runtime,
)
from virector.services.director_plan import compile_director_plan
from virector.workers.base import RenderJob, RenderResult, VideoWorker


PROMPT = """CLIP 1
Duration: 4 seconds

Image References
@image1: Lead character
@image2: Designed world

0:00-0:02
@image1 enters the room.

0:02-0:04
@image1 crosses @image2.
"""


def test_require_cuda_runtime_accepts_visible_gpu() -> None:
    torch_module = SimpleNamespace(
        __version__="2.11.0+cu128",
        version=SimpleNamespace(cuda="12.8"),
        cuda=SimpleNamespace(is_available=lambda: True),
    )

    require_cuda_runtime(torch_module)


def test_require_cuda_runtime_reports_incompatible_wheel() -> None:
    torch_module = SimpleNamespace(
        __version__="2.12.0+cu130",
        version=SimpleNamespace(cuda="13.0"),
        cuda=SimpleNamespace(is_available=lambda: False),
    )

    with pytest.raises(
        RuntimeError,
        match=r"PyTorch 2\.12\.0\+cu130 \(CUDA build: 13\.0\)",
    ):
        require_cuda_runtime(torch_module)


class FakeRemoteFiles:
    def __init__(self) -> None:
        self.downloads: list[str] = []
        self.upload_url: str | None = None
        self.uploaded = b""

    def download_reference(
        self,
        url: str,
        destination: Path,
        media_type: OmniMediaType = OmniMediaType.image,
    ) -> None:
        self.downloads.append(url)
        if media_type == OmniMediaType.image:
            Image.new("RGB", (32, 32), (80, 40, 120)).save(destination)
        else:
            destination.write_bytes(b"reference-media")

    def upload_video(self, url: str, video: Path) -> None:
        self.upload_url = url
        self.uploaded = video.read_bytes()


class FakeCloudWorker(VideoWorker):
    mode = "performance"
    requested_mode = "performance"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.job: RenderJob | None = None

    def render(self, job: RenderJob) -> RenderResult:
        self.job = job
        if job.progress_callback:
            job.progress_callback(50, "Rendering cloud shot.")
        if self.fail:
            return RenderResult(
                job_id=job.job_id,
                status="failed",
                start_frame=job.start_frame,
                message="GPU render failed",
            )
        video = job.output_dir / "preview.mp4"
        video.write_bytes(b"cloud-video")
        return RenderResult(
            job_id=job.job_id,
            status="completed",
            start_frame=job.start_frame,
            video=video,
            message="Cloud performance render completed.",
        )


def payload() -> dict:
    plan = compile_director_plan(PROMPT)
    spec = ShotSpec(
        prompt=PROMPT,
        duration_seconds=4,
        director_plan=plan,
    )
    return {
        "job_id": "a" * 32,
        "shot_spec": spec.model_dump(mode="json"),
        "references": [
            {
                "index": 1,
                "tag": "@image1",
                "download_url": "https://objects.example/character.png?signature=one",
            },
            {
                "index": 2,
                "tag": "@image2",
                "download_url": "https://objects.example/world.png?signature=two",
            },
        ],
        "output_upload_url": "https://objects.example/preview.mp4?signature=put",
        "output_object_key": "renders/aaaaaaaa/preview.mp4",
    }


def test_runpod_runtime_downloads_renders_and_uploads(tmp_path: Path) -> None:
    files = FakeRemoteFiles()
    worker = FakeCloudWorker()
    progress: list[tuple[int, str]] = []
    runtime = RunpodPerformanceRuntime(worker, files, temp_root=tmp_path)

    result = runtime.handle(
        {"id": "runpod-job", "input": payload()},
        progress_callback=lambda value, message: progress.append((value, message)),
    )

    assert result == {
        "job_id": "a" * 32,
        "status": "completed",
        "output_object_key": "renders/aaaaaaaa/preview.mp4",
        "size_bytes": 11,
        "message": "Cloud performance render completed.",
    }
    assert files.downloads == [
        "https://objects.example/character.png?signature=one",
        "https://objects.example/world.png?signature=two",
    ]
    assert files.upload_url == "https://objects.example/preview.mp4?signature=put"
    assert files.uploaded == b"cloud-video"
    assert progress == [(50, "Rendering cloud shot.")]
    assert worker.job is not None
    assert [asset.tag for asset in worker.job.reference_assets] == [
        "@image1",
        "@image2",
    ]
    assert "signature=put" not in str(result)


def test_runpod_runtime_transports_multimodal_references(tmp_path: Path) -> None:
    request = payload()
    request["references"].extend(
        [
            {
                "index": 1,
                "tag": "@video1",
                "media_type": "video",
                "download_url": "https://objects.example/action.mp4?signature=three",
            },
            {
                "index": 1,
                "tag": "@audio1",
                "media_type": "audio",
                "download_url": "https://objects.example/voice.wav?signature=four",
            },
        ]
    )
    request["shot_spec"]["references"] = [
        {"index": 1, "tag": "@image1", "media_type": "image"},
        {"index": 2, "tag": "@image2", "media_type": "image"},
        {"index": 1, "tag": "@video1", "media_type": "video"},
        {"index": 1, "tag": "@audio1", "media_type": "audio"},
    ]
    files = FakeRemoteFiles()
    worker = FakeCloudWorker()
    runtime = RunpodPerformanceRuntime(worker, files, temp_root=tmp_path)

    runtime.handle({"input": request})

    assert worker.job is not None
    assert [path.name for path in worker.job.reference_videos] == ["video-01.mp4"]
    assert [path.name for path in worker.job.reference_audio] == ["audio-01.wav"]
    assert [asset.tag for asset in worker.job.reference_assets] == [
        "@image1",
        "@image2",
        "@video1",
        "@audio1",
    ]


def test_runpod_payload_requires_internal_director_plan() -> None:
    request = payload()
    request["shot_spec"]["director_plan"] = None

    with pytest.raises(ValidationError, match="require a DirectorPlan"):
        RunpodRenderInput.model_validate(request)


def test_runpod_runtime_propagates_worker_failure(tmp_path: Path) -> None:
    runtime = RunpodPerformanceRuntime(
        FakeCloudWorker(fail=True),
        FakeRemoteFiles(),
        temp_root=tmp_path,
    )

    with pytest.raises(RuntimeError, match="GPU render failed"):
        runtime.handle({"input": payload()})


def test_remote_file_client_rejects_non_https_url(tmp_path: Path) -> None:
    client = RemoteFileClient()

    with pytest.raises(RunpodPayloadError, match="HTTPS"):
        client.download_reference(
            "http://objects.example/reference.png",
            tmp_path / "reference.png",
        )


def test_remote_file_client_rejects_unapproved_host(tmp_path: Path) -> None:
    client = RemoteFileClient(allowed_hosts={"approved.example"})

    with pytest.raises(RunpodPayloadError, match="not approved"):
        client.download_reference(
            "https://unapproved.example/reference.png",
            tmp_path / "reference.png",
        )
