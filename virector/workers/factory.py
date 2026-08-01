from virector.config import Settings
from virector.workers.base import VideoWorker
from virector.workers.conditioning import ConditioningRouter, ConditioningTargets
from virector.workers.ltx import (
    LtxBackend,
    LtxWorker,
    LtxWorkerUnavailableError,
)
from virector.workers.mock import MockWorker
from virector.workers.performance import PerformanceWorker
from virector.workers.runpod import (
    HttpRunpodQueueClient,
    RunpodQueueClient,
    RunpodWorker,
)
from virector.workers.vace import (
    VaceBackend,
    VaceWorker,
    VaceWorkerUnavailableError,
)
from virector.workers.wan_animate import (
    SubprocessWanAnimateBackend,
    WanAnimateBackend,
    WanAnimateUnavailableError,
    WanAnimateWorker,
)


def _create_default_ltx_backend(settings: Settings) -> LtxBackend:
    from virector.workers.ltx_diffusers import DiffusersLtxBackend

    backend = DiffusersLtxBackend(settings=settings)
    backend.ensure_available()
    return backend


def _create_default_vace_backend(settings: Settings) -> VaceBackend:
    from virector.workers.vace_diffusers import DiffusersVaceBackend

    backend = DiffusersVaceBackend(settings=settings)
    backend.ensure_available()
    return backend


def _create_default_wan_animate_backend(settings: Settings) -> WanAnimateBackend:
    return SubprocessWanAnimateBackend(settings=settings)


def create_worker(
    settings: Settings,
    *,
    ltx_backend: LtxBackend | None = None,
    vace_backend: VaceBackend | None = None,
    wan_animate_backend: WanAnimateBackend | None = None,
    runpod_queue_client: RunpodQueueClient | None = None,
    runpod_s3_client: object | None = None,
) -> VideoWorker:
    """Select the configured worker and safely preserve the mock fallback."""

    if settings.worker_mode == "mock":
        return MockWorker()

    if settings.worker_mode == "runpod":
        try:
            settings.validate_runpod_configuration()
            if runpod_queue_client is None:
                assert settings.runpod_endpoint_id is not None
                assert settings.runpod_api_key is not None
                runpod_queue_client = HttpRunpodQueueClient(
                    endpoint_id=settings.runpod_endpoint_id,
                    api_key=settings.runpod_api_key.get_secret_value(),
                    base_url=settings.runpod_api_base_url,
                    request_timeout_seconds=settings.runpod_request_timeout_seconds,
                )
            if runpod_s3_client is None:
                from virector.services.storage import create_s3_client

                runpod_s3_client = create_s3_client(settings)
            return RunpodWorker(
                settings=settings,
                queue_client=runpod_queue_client,
                s3_client=runpod_s3_client,
            )
        except (RuntimeError, ValueError) as exc:
            return MockWorker(
                requested_mode="runpod",
                fallback_reason=str(exc),
            )

    if settings.worker_mode == "performance":
        segment_settings = settings.model_copy(
            update={"worker_mode": settings.performance_segment_worker}
        )
        segment_worker = create_worker(
            segment_settings,
            ltx_backend=ltx_backend,
            vace_backend=vace_backend,
            wan_animate_backend=wan_animate_backend,
        )
        if isinstance(segment_worker, MockWorker):
            return MockWorker(
                requested_mode="performance",
                fallback_reason=segment_worker.fallback_reason,
            )
        motion_worker = None
        conditioning_fallback_reason = None
        if settings.performance_motion_backend == "wan-animate":
            try:
                backend = wan_animate_backend or _create_default_wan_animate_backend(
                    settings
                )
                motion_worker = WanAnimateWorker(backend)
            except WanAnimateUnavailableError as exc:
                conditioning_fallback_reason = str(exc)
        return PerformanceWorker(
            segment_worker=segment_worker,
            conditioning_router=ConditioningRouter(
                generator_backend=segment_worker.mode,
                targets=ConditioningTargets(
                    motion=settings.performance_motion_backend,
                    speech=settings.performance_speech_backend,
                    audio=settings.performance_audio_backend,
                ),
            ),
            motion_worker=motion_worker,
            conditioning_fallback_reason=conditioning_fallback_reason,
        )

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
