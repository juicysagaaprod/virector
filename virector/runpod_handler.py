import os

import runpod

from virector.config import get_settings
from virector.runpod_runtime import (
    RemoteFileClient,
    RunpodPerformanceRuntime,
    require_cuda_runtime,
)
from virector.workers.factory import create_worker


def create_runtime() -> RunpodPerformanceRuntime:
    settings = get_settings()
    if settings.worker_mode != "performance":
        raise RuntimeError(
            "RunPod requires VIRECTOR_WORKER_MODE=performance."
        )
    require_cuda_runtime()
    worker = create_worker(settings)
    if worker.mode != "performance":
        raise RuntimeError(
            "The configured performance worker is unavailable: "
            + (worker.fallback_reason or worker.mode)
        )
    return RunpodPerformanceRuntime(
        worker,
        file_client=RemoteFileClient(
            max_reference_bytes=int(
                os.environ.get(
                    "VIRECTOR_RUNPOD_MAX_REFERENCE_BYTES",
                    str(25 * 1024 * 1024),
                )
            ),
            max_video_bytes=int(
                os.environ.get(
                    "VIRECTOR_RUNPOD_MAX_VIDEO_BYTES",
                    str(100 * 1024 * 1024),
                )
            ),
            allowed_hosts={
                host.strip()
                for host in os.environ.get(
                    "VIRECTOR_RUNPOD_ALLOWED_ASSET_HOSTS",
                    "",
                ).split(",")
                if host.strip()
            },
        ),
    )


runtime = create_runtime()


def handler(job: dict) -> dict[str, str | int]:
    def progress(value: int, message: str) -> None:
        runpod.serverless.progress_update(job, f"{value}% · {message}")

    return runtime.handle(job, progress_callback=progress)


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
