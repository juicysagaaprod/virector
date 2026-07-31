from virector.config import Settings
from virector.workers.base import VideoWorker
from virector.workers.ltx import (
    LtxBackend,
    LtxWorker,
    LtxWorkerUnavailableError,
)
from virector.workers.mock import MockWorker


def create_worker(
    settings: Settings,
    *,
    ltx_backend: LtxBackend | None = None,
) -> VideoWorker:
    """Select the configured worker and safely preserve the mock fallback."""

    if settings.worker_mode == "mock":
        return MockWorker()

    worker = LtxWorker(backend=ltx_backend)
    try:
        worker.ensure_ready()
    except LtxWorkerUnavailableError as exc:
        return MockWorker(
            requested_mode="ltx",
            fallback_reason=str(exc),
        )
    return worker
