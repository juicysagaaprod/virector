from pathlib import Path

import pytest

from virector.config import Settings
from virector.models.conditioning import ConditioningRoute
from virector.models.omni_asset import BindingModality
from virector.models.shot_spec import ShotSpec
from virector.wan_animate_preflight import hardware_report
from virector.workers.base import ReferenceAsset, RenderJob
from virector.workers.wan_animate import (
    SubprocessWanAnimateBackend,
    WanAnimateUnavailableError,
    WanAnimateWorker,
)


class FakeBackend:
    def ensure_available(self) -> None:
        pass

    def render(
        self,
        job: RenderJob,
        source_video: Path,
        driving_video: Path,
        output_path: Path,
    ) -> Path:
        output_path.write_bytes(b"motion")
        return output_path


def motion_route() -> ConditioningRoute:
    return ConditioningRoute(
        segment_index=1,
        modality=BindingModality.motion,
        asset_tags=["@video1"],
        backend="wan-animate",
        status="external",
        instruction="Follow @video1.",
        reason="test",
    )


def test_wan_worker_requires_tagged_driving_video(tmp_path: Path) -> None:
    source = tmp_path / "base.mp4"
    image = tmp_path / "character.png"
    source.write_bytes(b"base")
    image.write_bytes(b"image")
    job = RenderJob(
        job_id="wan-test",
        output_dir=tmp_path,
        start_frame=image,
        spec=ShotSpec(prompt="Follow the reference movement."),
        reference_assets=(ReferenceAsset(1, "@image1", image),),
    )

    with pytest.raises(WanAnimateUnavailableError, match="driving video"):
        WanAnimateWorker(FakeBackend()).apply(job, source, [motion_route()])


def test_subprocess_backend_rejects_missing_isolated_runtime(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        wan_animate_repo_dir=tmp_path / "missing-repo",
        wan_animate_python=str(tmp_path / "missing-python"),
    )

    with pytest.raises(WanAnimateUnavailableError, match="runtime files"):
        SubprocessWanAnimateBackend(settings).ensure_available()


def test_wan_preflight_blocks_local_hardware() -> None:
    report = hardware_report(
        vram_gb=8,
        ram_gb=16,
        storage_gb=500,
        runtime_ready=True,
    )

    assert report["supported"] is False
    assert len(report["blockers"]) == 2


def test_wan_preflight_accepts_cloud_profile() -> None:
    report = hardware_report(
        vram_gb=80,
        ram_gb=128,
        storage_gb=250,
        runtime_ready=True,
    )

    assert report["supported"] is True
    assert report["blockers"] == []
