from virector.workers.base import RenderJob, RenderResult, VideoWorker


class MockWorker(VideoWorker):
    """Milestone 1A worker: validates the pipeline and returns the start frame."""

    mode = "mock"

    def __init__(
        self,
        *,
        requested_mode: str = "mock",
        fallback_reason: str | None = None,
    ) -> None:
        self.requested_mode = requested_mode
        self.fallback_reason = fallback_reason

    def render(self, job: RenderJob) -> RenderResult:
        if self.fallback_reason:
            message = (
                f"{self.fallback_reason} "
                "Using the mock worker to return the composed start frame."
            )
        else:
            message = (
                "Start frame composed successfully. "
                "LTX video rendering is added in Milestone 1B."
            )

        return RenderResult(
            job_id=job.job_id,
            status="composed",
            start_frame=job.start_frame,
            message=message,
        )
