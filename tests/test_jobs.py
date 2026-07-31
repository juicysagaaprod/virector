import json
from pathlib import Path

from PIL import Image

from virector.config import Settings
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
        "reference-01.png",
        "reference-02.jpg",
    ]
    assert [asset.tag for asset in worker.job.reference_assets] == [
        "@image1",
        "@image2",
    ]
    manifest = json.loads(
        (worker.job.output_dir / "shot_spec.json").read_text(encoding="utf-8")
    )
    assert len(manifest["assets"]["reference_images"]) == 2
    assert manifest["assets"]["references"][0]["tag"] == "@image1"
    assert manifest["assets"]["references"][1]["tag"] == "@image2"
