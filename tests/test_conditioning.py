import json
from pathlib import Path

from virector.models.conditioning import ConditioningRouteStatus
from virector.models.omni_asset import BindingModality, OmniMediaType
from virector.models.shot_spec import ShotSpec
from virector.services.director_plan import compile_director_plan
from virector.workers.base import ReferenceAsset, RenderJob, RenderResult, VideoWorker
from virector.workers.conditioning import ConditioningRouter, ConditioningTargets
from virector.workers.performance import PerformanceWorker
from virector.workers.wan_animate import WanAnimateWorker


PROMPT = """PERFORMANCE TEST
Duration: 4 seconds

Image References
@image1: Lead character
Video References
@video1: Walking performance and camera movement
Audio References
@audio1: Lead character voice

0:00-0:04
@image1 follows the movement and camera from @video1 and speaks with @audio1.
"""


class CapturingWorker(VideoWorker):
    mode = "ltx"
    requested_mode = "ltx"

    def __init__(self) -> None:
        self.job: RenderJob | None = None

    def render(self, job: RenderJob) -> RenderResult:
        self.job = job
        video = job.output_dir / "preview.mp4"
        video.write_bytes(b"video")
        return RenderResult(
            job_id=job.job_id,
            status="completed",
            start_frame=job.start_frame,
            video=video,
            message="Base generation complete.",
        )


class FakeWanAnimateBackend:
    def __init__(self) -> None:
        self.ready_checks = 0
        self.source_video: Path | None = None
        self.driving_video: Path | None = None

    def ensure_available(self) -> None:
        self.ready_checks += 1

    def render(
        self,
        job: RenderJob,
        source_video: Path,
        driving_video: Path,
        output_path: Path,
    ) -> Path:
        self.source_video = source_video
        self.driving_video = driving_video
        output_path.write_bytes(b"wan-motion-video")
        return output_path


def test_router_marks_unconnected_specialists_as_deferred() -> None:
    plan = compile_director_plan(PROMPT)

    result = ConditioningRouter("ltx").compile(plan)

    by_modality = {route.modality: route for route in result.routes}
    assert by_modality[BindingModality.visual].status == (
        ConditioningRouteStatus.limited
    )
    assert by_modality[BindingModality.motion].status == (
        ConditioningRouteStatus.deferred
    )
    assert by_modality[BindingModality.camera].status == (
        ConditioningRouteStatus.deferred
    )
    assert by_modality[BindingModality.voice].status == (
        ConditioningRouteStatus.deferred
    )
    assert result.has_deferred_routes


def test_router_assigns_configured_cloud_targets() -> None:
    plan = compile_director_plan(PROMPT)
    router = ConditioningRouter(
        "vace",
        ConditioningTargets(
            motion="wan-animate",
            speech="infinitetalk",
            audio="ffmpeg",
        ),
    )

    result = router.compile(plan)

    routes = {(route.modality, route.backend): route for route in result.routes}
    assert routes[(BindingModality.visual, "vace")].status == (
        ConditioningRouteStatus.native
    )
    assert routes[(BindingModality.motion, "wan-animate")].status == (
        ConditioningRouteStatus.external
    )
    assert routes[(BindingModality.voice, "infinitetalk")].status == (
        ConditioningRouteStatus.external
    )
    assert result.external_backends == ["infinitetalk", "wan-animate"]
    assert "require execution adapters" in result.warnings[-1]


def test_performance_worker_persists_and_reports_conditioning_plan(
    tmp_path: Path,
) -> None:
    image = tmp_path / "character.png"
    motion = tmp_path / "motion.mp4"
    voice = tmp_path / "voice.wav"
    for path in (image, motion, voice):
        path.write_bytes(path.name.encode())
    plan = compile_director_plan(PROMPT)
    job = RenderJob(
        job_id="conditioning-job",
        output_dir=tmp_path,
        start_frame=image,
        spec=ShotSpec(prompt=PROMPT, duration_seconds=4, director_plan=plan),
        reference_images=(image,),
        reference_videos=(motion,),
        reference_audio=(voice,),
        reference_assets=(
            ReferenceAsset(index=1, tag="@image1", path=image),
            ReferenceAsset(
                index=1,
                tag="@video1",
                path=motion,
                media_type=OmniMediaType.video,
            ),
            ReferenceAsset(
                index=1,
                tag="@audio1",
                path=voice,
                media_type=OmniMediaType.audio,
            ),
        ),
    )
    segment_worker = CapturingWorker()

    result = PerformanceWorker(segment_worker).render(job)

    manifest = json.loads((tmp_path / "conditioning_plan.json").read_text())
    assert manifest["generator_backend"] == "ltx"
    assert any(route["status"] == "deferred" for route in manifest["routes"])
    assert result.job_id == "conditioning-job"
    assert "Deferred conditioning:" in result.message
    assert segment_worker.job is not None
    assert "ConditioningRoute [motion]: unassigned (deferred)." in (
        segment_worker.job.spec.prompt
    )


def test_performance_worker_executes_wan_motion_stage(tmp_path: Path) -> None:
    image = tmp_path / "character.png"
    motion = tmp_path / "motion.mp4"
    voice = tmp_path / "voice.wav"
    for path in (image, motion, voice):
        path.write_bytes(path.name.encode())
    plan = compile_director_plan(PROMPT)
    job = RenderJob(
        job_id="wan-conditioning-job",
        output_dir=tmp_path,
        start_frame=image,
        spec=ShotSpec(prompt=PROMPT, duration_seconds=4, director_plan=plan),
        reference_images=(image,),
        reference_videos=(motion,),
        reference_audio=(voice,),
        reference_assets=(
            ReferenceAsset(index=1, tag="@image1", path=image),
            ReferenceAsset(
                index=1,
                tag="@video1",
                path=motion,
                media_type=OmniMediaType.video,
            ),
            ReferenceAsset(
                index=1,
                tag="@audio1",
                path=voice,
                media_type=OmniMediaType.audio,
            ),
        ),
    )
    backend = FakeWanAnimateBackend()
    router = ConditioningRouter(
        "ltx",
        ConditioningTargets(motion="wan-animate"),
    )

    result = PerformanceWorker(
        CapturingWorker(),
        conditioning_router=router,
        motion_worker=WanAnimateWorker(backend),
    ).render(job)

    manifest = json.loads((tmp_path / "conditioning_plan.json").read_text())
    motion_routes = [
        route
        for route in manifest["routes"]
        if route["modality"] == "motion"
    ]
    camera_routes = [
        route for route in manifest["routes"] if route["modality"] == "camera"
    ]
    assert result.status == "completed"
    assert result.video == tmp_path / "wan-animate-preview.mp4"
    assert result.video.read_bytes() == b"wan-motion-video"
    assert backend.ready_checks == 1
    assert backend.source_video == tmp_path / "preview.mp4"
    assert backend.driving_video == motion
    assert all(route["status"] == "applied" for route in motion_routes)
    assert all(route["status"] == "deferred" for route in camera_routes)
    assert "pending execution adapters: wan-animate" not in result.message
    assert "Wan2.2 motion stage applied." in result.message
