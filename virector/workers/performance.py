import shutil
import subprocess
from pathlib import Path
from typing import Protocol

from virector.models.director_plan import DirectorPlan, DirectorSegment
from virector.models.omni_asset import BindingModality
from virector.models.shot_spec import ShotSpec
from virector.workers.base import RenderJob, RenderResult, VideoWorker


class PerformanceAssemblyError(RuntimeError):
    """Raised when rendered shot segments cannot be assembled."""


class VideoAssembler(Protocol):
    def assemble(self, segments: list[Path], output: Path) -> Path:
        """Combine ordered video segments into one final MP4."""


def build_segment_prompt(plan: DirectorPlan, segment: DirectorSegment) -> str:
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


class PerformanceWorker(VideoWorker):
    """Execute a DirectorPlan shot-by-shot, then assemble one final video."""

    mode = "performance"
    requested_mode = "performance"

    def __init__(
        self,
        segment_worker: VideoWorker,
        assembler: VideoAssembler | None = None,
    ) -> None:
        if isinstance(segment_worker, PerformanceWorker):
            raise ValueError("A PerformanceWorker cannot delegate to itself.")
        self.segment_worker = segment_worker
        self.assembler = assembler or FfmpegVideoAssembler()
        self.fallback_reason = segment_worker.fallback_reason

    @staticmethod
    def _segment_job(
        job: RenderJob,
        plan: DirectorPlan,
        segment: DirectorSegment,
        output_dir: Path,
    ) -> RenderJob:
        visual_binding_tags = {
            tag
            for binding in segment.reference_bindings
            if binding.modality == BindingModality.visual and binding.visible
            for tag in binding.asset_tags
        }
        selected_tags = visual_binding_tags or set(segment.reference_tags)
        selected_assets = tuple(
            asset
            for asset in job.reference_assets
            if not selected_tags or asset.tag in selected_tags
        )
        if not selected_assets:
            selected_assets = job.reference_assets
        selected_images = tuple(asset.path for asset in selected_assets)
        if not selected_images:
            selected_images = job.reference_images
        start_frame = selected_images[0] if selected_images else job.start_frame
        segment_prompt = build_segment_prompt(plan, segment)[:20_000]
        segment_title = f"{job.spec.title} — Shot {segment.index}"[:120]
        spec = ShotSpec.model_validate(
            {
                **job.spec.model_dump(),
                "title": segment_title,
                "prompt": segment_prompt,
                "director_plan": None,
                "duration_seconds": segment.duration_seconds,
                "seed": (job.spec.seed + segment.index - 1) % 2_147_483_648,
            }
        )
        return RenderJob(
            job_id=f"{job.job_id}-shot-{segment.index:02d}",
            output_dir=output_dir,
            start_frame=start_frame,
            spec=spec,
            reference_images=selected_images,
            reference_assets=selected_assets,
            progress_callback=job.progress_callback,
        )

    def render(self, job: RenderJob) -> RenderResult:
        plan = job.spec.director_plan
        if plan is None or len(plan.segments) == 1:
            return self.segment_worker.render(job)

        segments_dir = job.output_dir / "segments"
        segments_dir.mkdir(parents=True, exist_ok=True)
        rendered_segments: list[Path] = []
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
            result = self.segment_worker.render(
                self._segment_job(job, plan, segment, output_dir)
            )
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
        return RenderResult(
            job_id=job.job_id,
            status="completed",
            start_frame=job.start_frame,
            video=output,
            message=f"Multi-shot performance video assembled from {total} shots.",
        )
