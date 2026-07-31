from pathlib import Path
from typing import Protocol

from virector.workers.base import RenderJob, RenderResult, VideoWorker


class VaceBackend(Protocol):
    """Self-hosted VACE inference implementation injected into the worker."""

    def render(self, job: RenderJob) -> str | Path:
        """Render a role-tagged multi-reference job and return its video path."""


class VaceWorkerUnavailableError(RuntimeError):
    """Raised when VACE mode is requested without its self-hosted runtime."""


class VaceWorker(VideoWorker):
    mode = "vace"

    def __init__(
        self,
        backend: VaceBackend | None = None,
        *,
        requested_mode: str = "vace",
        fallback_reason: str | None = None,
    ) -> None:
        self._backend = backend
        self.requested_mode = requested_mode
        self.fallback_reason = fallback_reason

    @property
    def ready(self) -> bool:
        return self._backend is not None

    def ensure_ready(self) -> None:
        if not self.ready:
            raise VaceWorkerUnavailableError(
                "VACE mode was requested, but its self-hosted inference backend "
                "is not configured yet."
            )

    def render(self, job: RenderJob) -> RenderResult:
        self.ensure_ready()
        assert self._backend is not None
        if not job.reference_assets:
            return RenderResult(
                job_id=job.job_id,
                status="failed",
                start_frame=job.start_frame,
                message="VACE render failed: no role-tagged references were supplied.",
            )

        try:
            video = Path(self._backend.render(job))
        except Exception as exc:
            return RenderResult(
                job_id=job.job_id,
                status="failed",
                start_frame=job.start_frame,
                message=f"VACE render failed: {exc}",
            )
        if not video.is_file():
            return RenderResult(
                job_id=job.job_id,
                status="failed",
                start_frame=job.start_frame,
                message=(
                    "VACE render failed: the backend did not create its "
                    f"declared output: {video}"
                ),
            )
        return RenderResult(
            job_id=job.job_id,
            status="completed",
            start_frame=job.start_frame,
            video=video,
            message="VACE multi-reference preview rendered successfully.",
        )
