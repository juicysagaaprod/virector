import json
from pathlib import Path

from PIL import Image

from virector.config import Settings
from virector.models.omni_asset import OmniMediaType, ReferenceRole
from virector.models.shot_spec import ReferenceDirective, ShotSpec
from virector.services.jobs import JobService
from virector.workers.base import RenderJob, RenderResult, VideoWorker


class CapturingWorker(VideoWorker):
    mode = "test"
    requested_mode = "test"

    def __init__(self) -> None:
        self.job: RenderJob | None = None

    def render(self, job: RenderJob) -> RenderResult:
        self.job = job
        return RenderResult(
            job_id=job.job_id,
            status="composed",
            start_frame=job.start_frame,
        )


def test_job_service_retains_ordered_omni_references(tmp_path: Path) -> None:
    first = tmp_path / "character.png"
    second = tmp_path / "world.jpg"
    Image.new("RGB", (800, 1200), (120, 40, 60)).save(first)
    Image.new("RGB", (1600, 900), (20, 80, 120)).save(second)
    worker = CapturingWorker()
    service = JobService(Settings(data_dir=tmp_path / "data"), worker)

    result = service.create_from_references(
        [first, second],
        ShotSpec(prompt="The character walks through the designed world."),
        reference_directives=[
            ReferenceDirective(
                index=1,
                tag="@image1",
            ),
            ReferenceDirective(
                index=2,
                tag="@image2",
            ),
        ],
    )

    assert result.start_frame.is_file()
    assert worker.job is not None
    assert [path.name for path in worker.job.reference_images] == [
        "image-01.png",
        "image-02.jpg",
    ]
    assert [asset.tag for asset in worker.job.reference_assets] == [
        "@image1",
        "@image2",
    ]
    assert [asset.role for asset in worker.job.reference_assets] == [
        ReferenceRole.CHARACTER_IDENTITY,
        ReferenceRole.WORLD_ENVIRONMENT,
    ]
    resolved = json.loads(
        (worker.job.output_dir / "resolved_reference_map.json").read_text(
            encoding="utf-8"
        )
    )
    assert [asset["role"] for asset in resolved["assets"]] == [
        "character_identity",
        "world_environment",
    ]
    assert (worker.job.output_dir / "scene_anchor.png").is_file()
    assert (worker.job.output_dir / "compiled_model_prompt.txt").is_file()
    manifest = json.loads(
        (worker.job.output_dir / "shot_spec.json").read_text(encoding="utf-8")
    )
    assert len(manifest["assets"]["reference_images"]) == 2
    assert manifest["assets"]["references"][0]["tag"] == "@image1"
    assert manifest["assets"]["references"][1]["tag"] == "@image2"
    state = json.loads(
        (worker.job.output_dir / "job_state.json").read_text(encoding="utf-8")
    )
    assert state["status"] == "composed"
    assert state["progress"] == 100
    assert [event["status"] for event in state["events"]] == [
        "queued",
        "validating",
        "rendering",
        "composed",
    ]
    assert [asset["image_tag"] for asset in state["assets"][:2]] == [
        "@image1",
        "@image2",
    ]


def test_job_service_persists_video_and_audio_references(tmp_path: Path) -> None:
    image = tmp_path / "character.png"
    video = tmp_path / "motion.mp4"
    audio = tmp_path / "voice.wav"
    Image.new("RGB", (480, 832), (30, 60, 90)).save(image)
    video.write_bytes(b"video-reference")
    audio.write_bytes(b"audio-reference")
    worker = CapturingWorker()
    service = JobService(Settings(data_dir=tmp_path / "data"), worker)
    directives = [
        ReferenceDirective(index=1, tag="@image1"),
        ReferenceDirective(
            index=1,
            tag="@video1",
            media_type=OmniMediaType.video,
        ),
        ReferenceDirective(
            index=1,
            tag="@audio1",
            media_type=OmniMediaType.audio,
        ),
    ]

    service.create_from_references(
        [image, video, audio],
        ShotSpec(prompt="Use @image1, @video1 and @audio1.", references=directives),
        reference_directives=directives,
    )

    assert worker.job is not None
    assert [path.name for path in worker.job.reference_images] == ["image-01.png"]
    assert [path.name for path in worker.job.reference_videos] == ["video-01.mp4"]
    assert [path.name for path in worker.job.reference_audio] == ["audio-01.wav"]
    assert [asset.tag for asset in worker.job.reference_assets] == [
        "@image1",
        "@video1",
        "@audio1",
    ]
    manifest = json.loads(
        (worker.job.output_dir / "shot_spec.json").read_text(encoding="utf-8")
    )
    assert len(manifest["assets"]["reference_videos"]) == 1
    assert len(manifest["assets"]["reference_audio"]) == 1
    assert manifest["assets"]["references"][1]["media_type"] == "video"
