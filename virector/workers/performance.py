import json
import shutil
import subprocess
from pathlib import Path
from typing import Protocol

from virector.models.conditioning import (
    ConditioningPlan,
    ConditioningRouteStatus,
)
from virector.models.director_plan import DirectorPlan, DirectorSegment
from virector.models.omni_asset import BindingModality, OmniMediaType
from virector.models.shot_spec import ShotSpec
from virector.services.prompt_compiler import compile_model_prompt
from virector.workers.base import RenderJob, RenderResult, VideoWorker
from virector.workers.capability_router import ShotCapabilityRouter
from virector.workers.conditioning import ConditioningRouter
from virector.workers.wan_animate import WanAnimateUnavailableError, WanAnimateWorker


class PerformanceAssemblyError(RuntimeError):
    """Raised when rendered shot segments cannot be assembled."""


class VideoAssembler(Protocol):
    def assemble(self, segments: list[Path], output: Path) -> Path:
        """Combine ordered video segments into one final MP4."""


def build_segment_prompt(
    plan: DirectorPlan,
    segment: DirectorSegment,
    conditioning_plan: ConditioningPlan | None = None,
) -> str:
    """Convert one structured DirectorPlan segment into a worker prompt."""

    lines = [
        f"Shot {segment.index} of {len(plan.segments)}.",
        f"Timing: {segment.start_seconds:g}-{segment.end_seconds:g} seconds.",
        segment.action,
    ]
    if segment.reference_tags:
        lines.append("Visual references: " + ", ".join(segment.reference_tags) + ".")
    for binding in segment.reference_bindings:
        operations = ", ".join(operation.value for operation in binding.operations)
        controls = ", ".join(control.value for control in binding.controls)
        lines.append(
            f"ReferenceBinding [{binding.modality.value}] "
            f"({operations}; controls: {controls}): {binding.instruction}"
        )
    if conditioning_plan is not None:
        for route in conditioning_plan.routes:
            if route.segment_index != segment.index:
                continue
            lines.append(
                "ConditioningRoute "
                f"[{route.modality.value}]: {route.backend} ({route.status.value})."
            )
    for cue in segment.dialogue:
        speaker = (
            f"{cue.speaker_reference_tag} {cue.speaker}"
            if cue.speaker_reference_tag
            else cue.speaker
        )
        delivery = f" ({cue.delivery})" if cue.delivery else ""
        lines.append(f'Dialogue — {speaker}{delivery}: "{cue.text}"')
    for cue in segment.sound_cues:
        lines.append(f"Sound: {cue}")
    for text in segment.on_screen_text:
        lines.append(f"On-screen text: {text}")
    if segment.transition:
        lines.append(f"Transition: {segment.transition}")
    if segment.title_card:
        lines.append(f"Title card: {segment.title_card}")
    if plan.voice_direction:
        lines.append(f"Voice direction: {plan.voice_direction}")
    return "\n".join(lines)


class FfmpegVideoAssembler:
    """Join compatible MP4 segments without re-encoding."""

    @staticmethod
    def _executable() -> str:
        executable = shutil.which("ffmpeg")
        if executable:
            return executable
        try:
            import imageio_ffmpeg

            return imageio_ffmpeg.get_ffmpeg_exe()
        except (ImportError, RuntimeError) as exc:
            raise PerformanceAssemblyError(
                "FFmpeg is required to assemble multi-shot video."
            ) from exc

    def assemble(self, segments: list[Path], output: Path) -> Path:
        if not segments:
            raise PerformanceAssemblyError("No rendered segments were supplied.")
        missing = [str(path) for path in segments if not path.is_file()]
        if missing:
            raise PerformanceAssemblyError(
                "Rendered segments are missing: " + ", ".join(missing)
            )

        output.parent.mkdir(parents=True, exist_ok=True)
        manifest = output.parent / "segment-manifest.txt"
        manifest.write_text(
            "".join(
                "file '"
                + path.resolve().as_posix().replace("'", "'\\''")
                + "'\n"
                for path in segments
            ),
            encoding="utf-8",
        )
        command = [
            self._executable(),
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(manifest),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(output),
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            raise PerformanceAssemblyError(
                f"Could not start FFmpeg: {exc}"
            ) from exc
        if completed.returncode != 0 or not output.is_file():
            detail = completed.stderr.strip().splitlines()
            message = detail[-1] if detail else "FFmpeg returned no output."
            raise PerformanceAssemblyError(
                f"Multi-shot assembly failed: {message}"
            )
        return output


class FfmpegFrameExtractor:
    """Extract the actual final decoded frame for inter-shot continuity."""

    @staticmethod
    def extract(video: Path, output: Path) -> Path:
        executable = FfmpegVideoAssembler._executable()
        output.parent.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(
            [
                executable,
                "-y",
                "-sseof",
                "-0.08",
                "-i",
                str(video),
                "-frames:v",
                "1",
                str(output),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0 or not output.is_file():
            detail = completed.stderr.strip().splitlines()
            raise PerformanceAssemblyError(
                "Could not extract the previous shot continuity frame: "
                + (detail[-1] if detail else "FFmpeg returned no output.")
            )
        return output


class PerformanceWorker(VideoWorker):
    """Execute a DirectorPlan shot-by-shot, then assemble one final video."""

    mode = "performance"
    requested_mode = "performance"

    def __init__(
        self,
        segment_worker: VideoWorker,
        assembler: VideoAssembler | None = None,
        conditioning_router: ConditioningRouter | None = None,
        motion_worker: WanAnimateWorker | None = None,
        conditioning_fallback_reason: str | None = None,
    ) -> None:
        if isinstance(segment_worker, PerformanceWorker):
            raise ValueError("A PerformanceWorker cannot delegate to itself.")
        self.segment_worker = segment_worker
        self.assembler = assembler or FfmpegVideoAssembler()
        self.conditioning_router = conditioning_router or ConditioningRouter(
            generator_backend=segment_worker.mode
        )
        self.motion_worker = motion_worker
        self.conditioning_fallback_reason = conditioning_fallback_reason
        self.fallback_reason = segment_worker.fallback_reason

    @staticmethod
    def _write_conditioning_plan(job: RenderJob, plan: ConditioningPlan) -> None:
        (job.output_dir / "conditioning_plan.json").write_text(
            plan.model_dump_json(indent=2),
            encoding="utf-8",
        )

    def _write_capability_plan(self, job: RenderJob) -> None:
        action_provider = {
            "vace": "vace-r2v",
            "ltx": "ltx-image-to-video",
        }.get(self.segment_worker.mode)
        router = ShotCapabilityRouter(action_provider=action_provider)
        beats = job.spec.timeline
        if not beats:
            return
        payload = {
            "version": 1,
            "shots": [
                {
                    "shot_id": beat.shot_id,
                    "routes": [
                        {
                            "capability": route.capability.value,
                            "provider": route.provider,
                            "status": route.status.value,
                            "reason": route.reason,
                        }
                        for route in router.route(
                            beat,
                            lip_sync_enabled=job.spec.lip_sync_enabled,
                        )
                    ],
                }
                for beat in beats
            ],
        }
        (job.output_dir / "capability_plan.json").write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )

    def _apply_motion_stage(
        self,
        job: RenderJob,
        segment_index: int,
        conditioning_plan: ConditioningPlan,
        result: RenderResult,
        progress: int,
    ) -> RenderResult:
        if self.motion_worker is None or result.video is None:
            return result
        routes = [
            route
            for route in conditioning_plan.routes
            if route.segment_index == segment_index
            and route.backend == self.motion_worker.mode
            and route.status == ConditioningRouteStatus.external
        ]
        if not routes:
            return result
        if job.progress_callback:
            job.progress_callback(progress, "Applying Wan2.2 character motion.")
        try:
            output = self.motion_worker.apply(job, result.video, routes)
        except WanAnimateUnavailableError as exc:
            return RenderResult(
                job_id=result.job_id,
                status="failed",
                start_frame=result.start_frame,
                message=f"Wan2.2 motion stage failed: {exc}",
            )
        for route in routes:
            route.status = ConditioningRouteStatus.applied
            route.reason = (
                "Wan2.2-Animate applied the tagged driving-video motion to the "
                "composed/base shot."
            )
        conditioning_plan.refresh_warnings()
        return RenderResult(
            job_id=result.job_id,
            status="completed",
            start_frame=result.start_frame,
            video=output,
            message=(result.message.rstrip() + " Wan2.2 motion stage applied.").strip(),
        )

    @staticmethod
    def _segment_job(
        job: RenderJob,
        plan: DirectorPlan,
        segment: DirectorSegment,
        output_dir: Path,
        conditioning_plan: ConditioningPlan | None = None,
        continuity_frame: Path | None = None,
    ) -> RenderJob:
        visual_binding_tags = {
            tag
            for binding in segment.reference_bindings
            if binding.modality == BindingModality.visual and binding.visible
            for tag in binding.asset_tags
        }
        conditioning_tags = {
            tag
            for binding in segment.reference_bindings
            for tag in binding.asset_tags
        }
        selected_tags = visual_binding_tags or set(segment.reference_tags)
        selected_assets = tuple(
            asset
            for asset in job.reference_assets
            if (
                not selected_tags
                or asset.tag in selected_tags
                or asset.role is not None
                and asset.role.value in {"character_identity", "world_environment"}
                or (
                    asset.media_type != OmniMediaType.image
                    and asset.tag in conditioning_tags
                )
            )
        )
        if not selected_assets:
            selected_assets = job.reference_assets
        selected_images = tuple(
            asset.path
            for asset in selected_assets
            if asset.media_type == OmniMediaType.image
        )
        selected_videos = tuple(
            asset.path
            for asset in selected_assets
            if asset.media_type == OmniMediaType.video
        )
        selected_audio = tuple(
            asset.path
            for asset in selected_assets
            if asset.media_type == OmniMediaType.audio
        )
        if not selected_images:
            selected_images = job.reference_images
        start_frame = continuity_frame or job.continuity_frame or (
            selected_images[0] if selected_images else job.start_frame
        )
        beat = (
            job.spec.timeline[segment.index - 1]
            if len(job.spec.timeline) >= segment.index
            else None
        )
        if beat is not None:
            segment_prompt = compile_model_prompt(
                job.spec,
                beat,
                selected_assets,
            )[:20_000]
        else:
            segment_prompt = build_segment_prompt(
                plan,
                segment,
                conditioning_plan,
            )[:20_000]
        segment_title = f"{job.spec.title} — Shot {segment.index}"[:120]
        spec = ShotSpec.model_validate(
            {
                **job.spec.model_dump(),
                "title": segment_title,
                "prompt": segment_prompt,
                "director_plan": None,
                "timeline": [],
                "duration_seconds": segment.duration_seconds,
                "seed": (job.spec.seed + segment.index - 1) % 2_147_483_648,
            }
        )
        compiled_prompt_path = output_dir / "compiled_model_prompt.txt"
        compiled_prompt_path.write_text(segment_prompt, encoding="utf-8")
        return RenderJob(
            job_id=f"{job.job_id}-shot-{segment.index:02d}",
            output_dir=output_dir,
            start_frame=start_frame,
            spec=spec,
            reference_images=selected_images,
            reference_videos=selected_videos,
            reference_audio=selected_audio,
            reference_assets=selected_assets,
            continuity_frame=start_frame,
            compiled_prompt_path=compiled_prompt_path,
            progress_callback=job.progress_callback,
        )

    def render(self, job: RenderJob) -> RenderResult:
        plan = job.spec.director_plan
        if plan is None:
            return self.segment_worker.render(job)

        conditioning_plan = self.conditioning_router.compile(plan)
        self._write_conditioning_plan(job, conditioning_plan)
        self._write_capability_plan(job)
        if len(plan.segments) == 1:
            segment_job = self._segment_job(
                job,
                plan,
                plan.segments[0],
                job.output_dir,
                conditioning_plan,
            )
            result = self.segment_worker.render(segment_job)
            result = self._apply_motion_stage(
                segment_job,
                plan.segments[0].index,
                conditioning_plan,
                result,
                70,
            )
            self._write_conditioning_plan(job, conditioning_plan)
            result = RenderResult(
                job_id=job.job_id,
                status=result.status,
                start_frame=result.start_frame,
                video=result.video,
                message=result.message,
            )
            return self._with_conditioning_summary(result, conditioning_plan)

        segments_dir = job.output_dir / "segments"
        segments_dir.mkdir(parents=True, exist_ok=True)
        rendered_segments: list[Path] = []
        continuity_frame = job.continuity_frame
        total = len(plan.segments)

        for segment in plan.segments:
            if job.progress_callback:
                progress = 25 + round(60 * (segment.index - 1) / total)
                job.progress_callback(
                    progress,
                    f"Rendering shot {segment.index} of {total}.",
                )
            output_dir = segments_dir / f"shot-{segment.index:02d}"
            output_dir.mkdir(parents=True, exist_ok=False)
            segment_job = self._segment_job(
                job,
                plan,
                segment,
                output_dir,
                conditioning_plan,
                continuity_frame,
            )
            result = self.segment_worker.render(segment_job)
            result = self._apply_motion_stage(
                segment_job,
                segment.index,
                conditioning_plan,
                result,
                25 + round(60 * (segment.index - 0.5) / total),
            )
            self._write_conditioning_plan(job, conditioning_plan)
            if result.status not in {"complete", "completed"} or result.video is None:
                return RenderResult(
                    job_id=job.job_id,
                    status="failed",
                    start_frame=job.start_frame,
                    message=(
                        f"Shot {segment.index} failed: "
                        f"{result.message or 'segment worker produced no video.'}"
                    ),
                )
            rendered_segments.append(result.video)
            if not isinstance(self.assembler, FfmpegVideoAssembler):
                # Injected assemblers are used by GPU-free contract tests; their
                # placeholder segment bytes are intentionally not decodable.
                continuity_frame = result.start_frame
                continue
            try:
                continuity_frame = FfmpegFrameExtractor.extract(
                    result.video,
                    output_dir / "continuity-final-frame.png",
                )
            except PerformanceAssemblyError as exc:
                return RenderResult(
                    job_id=job.job_id,
                    status="failed",
                    start_frame=job.start_frame,
                    message=str(exc),
                )

        if job.progress_callback:
            job.progress_callback(90, "Assembling the final multi-shot video.")
        try:
            output = self.assembler.assemble(
                rendered_segments,
                job.output_dir / "preview.mp4",
            )
        except PerformanceAssemblyError as exc:
            return RenderResult(
                job_id=job.job_id,
                status="failed",
                start_frame=job.start_frame,
                message=str(exc),
            )
        result = RenderResult(
            job_id=job.job_id,
            status="completed",
            start_frame=job.start_frame,
            video=output,
            message=f"Multi-shot performance video assembled from {total} shots.",
        )
        self._write_conditioning_plan(job, conditioning_plan)
        return self._with_conditioning_summary(result, conditioning_plan)

    @staticmethod
    def _with_conditioning_summary(
        result: RenderResult,
        plan: ConditioningPlan,
    ) -> RenderResult:
        deferred = ", ".join(
            modality.value for modality in plan.deferred_modalities
        )
        external = ", ".join(plan.external_backends)
        if not deferred and not external:
            return result
        message = result.message.rstrip()
        if message and not message.endswith("."):
            message += "."
        details = []
        if deferred:
            details.append(
                f"Deferred conditioning: {deferred}; specialized workers "
                "are not connected."
            )
        if external:
            details.append(
                f"External conditioning targets pending execution adapters: {external}."
            )
        return RenderResult(
            job_id=result.job_id,
            status=result.status,
            start_frame=result.start_frame,
            video=result.video,
            message=f"{message} {' '.join(details)}".strip(),
        )
