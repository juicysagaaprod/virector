from virector.config import Settings
from virector.workers.base import VideoWorker
from virector.workers.ltx import (
    LtxBackend,
    LtxWorker,
    LtxWorkerUnavailableError,
)
from virector.workers.mock import MockWorker
from virector.workers.vace import (
    VaceBackend,
    VaceWorker,
    VaceWorkerUnavailableError,
)


def _create_default_ltx_backend(settings: Settings) -> LtxBackend:
    from virector.workers.ltx_diffusers import DiffusersLtxBackend

    backend = DiffusersLtxBackend(settings=settings)
    backend.ensure_available()
    return backend


def _create_default_vace_backend(settings: Settings) -> VaceBackend:
    raise VaceWorkerUnavailableError(
        f"The self-hosted {settings.vace_model_name} runtime is not installed."
    )


def create_worker(
    settings: Settings,
    *,
    ltx_backend: LtxBackend | None = None,
    vace_backend: VaceBackend | None = None,
) -> VideoWorker:
    """Select the configured worker and safely preserve the mock fallback."""

    if settings.worker_mode == "mock":
        return MockWorker()

    if settings.worker_mode == "vace":
        if vace_backend is not None:
            worker = VaceWorker(backend=vace_backend)
            worker.ensure_ready()
            return worker

        try:
            vace_backend = _create_default_vace_backend(settings)
        except VaceWorkerUnavailableError as vace_error:
            if ltx_backend is None:
                try:
                    ltx_backend = _create_default_ltx_backend(settings)
                except LtxWorkerUnavailableError as ltx_error:
                    return MockWorker(
                        requested_mode="vace",
                        fallback_reason=f"{vace_error} {ltx_error}",
                    )
            return LtxWorker(
                backend=ltx_backend,
                requested_mode="vace",
                fallback_reason=(
                    f"{vace_error} Falling back to the local LTX preview engine."
                ),
            )

        worker = VaceWorker(backend=vace_backend)
        worker.ensure_ready()
        return worker

    if ltx_backend is None:
        try:
            ltx_backend = _create_default_ltx_backend(settings)
        except LtxWorkerUnavailableError as exc:
            return MockWorker(
                requested_mode="ltx",
                fallback_reason=str(exc),
            )

    worker = LtxWorker(backend=ltx_backend)
    try:
        worker.ensure_ready()
    except LtxWorkerUnavailableError as exc:
        return MockWorker(
            requested_mode="ltx",
            fallback_reason=str(exc),
        )
    return worker
