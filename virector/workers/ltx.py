from pathlib import Path
from typing import Protocol

from virector.workers.base import RenderJob, RenderResult, VideoWorker


class LtxBackend(Protocol):
    """Inference implementation injected into the model-independent worker."""

    def render(self, job: RenderJob) -> str | Path:
        """Render a job and return the generated video path."""


class LtxWorkerUnavailableError(RuntimeError):
    """Raised when LTX mode is requested without a usable backend."""


class LtxWorker(VideoWorker):
    """LTX adapter scaffold; model loading stays behind ``LtxBackend``."""

    mode = "ltx"
    requested_mode = "ltx"
    fallback_reason = None

    def __init__(self, backend: LtxBackend | None = None) -> None:
        self._backend = backend

    @property
    def ready(self) -> bool:
        return self._backend is not None

    def ensure_ready(self) -> None:
        if not self.ready:
            raise LtxWorkerUnavailableError(
                "LTX mode was requested, but its inference backend is not "
                "configured yet."
            )

    def render(self, job: RenderJob) -> RenderResult:
        self.ensure_ready()
        assert self._backend is not None

        video = Path(self._backend.render(job))
        if not video.is_file():
            raise FileNotFoundError(
                f"The LTX backend did not create its declared output: {video}"
            )

        return RenderResult(
            job_id=job.job_id,
            status="completed",
            start_frame=job.start_frame,
            video=video,
            message="LTX preview rendered successfully.",
        )
