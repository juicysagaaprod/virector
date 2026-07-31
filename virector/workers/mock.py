from virector.workers.base import RenderJob, RenderResult, VideoWorker


class MockWorker(VideoWorker):
    """Milestone 1A worker: validates the pipeline and returns the start frame."""

    def render(self, job: RenderJob) -> RenderResult:
        return RenderResult(
            job_id=job.job_id,
            status="composed",
            start_frame=job.start_frame,
            message=(
                "Start frame composed successfully. "
                "LTX video rendering is added in Milestone 1B."
            ),
        )

