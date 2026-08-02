import re
import time
from collections.abc import Callable
from typing import Any, ClassVar, Protocol

import requests

from virector.config import Settings
from virector.workers.base import RenderJob, RenderResult, VideoWorker


class RunpodWorkerUnavailableError(RuntimeError):
    """Raised when the API-side RunPod worker cannot be configured."""


class RunpodQueueError(RuntimeError):
    """Raised when RunPod rejects or loses a queued render."""


class RunpodQueueClient(Protocol):
    def submit(self, payload: dict[str, Any]) -> str:
        """Submit one asynchronous RunPod job and return its remote ID."""

    def status(self, job_id: str) -> dict[str, Any]:
        """Return the current RunPod job status document."""

    def cancel(self, job_id: str) -> None:
        """Best-effort cancellation for an unfinished remote job."""


class HttpRunpodQueueClient:
    """Small HTTP client for RunPod's queue-based endpoint API."""

    def __init__(
        self,
        *,
        endpoint_id: str,
        api_key: str,
        base_url: str = "https://api.runpod.ai/v2",
        request_timeout_seconds: float = 30.0,
        session: requests.Session | None = None,
    ) -> None:
        self.endpoint_id = endpoint_id.strip()
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.request_timeout_seconds = request_timeout_seconds
        self.session = session or requests.Session()
        if not self.endpoint_id or not self.api_key:
            raise RunpodWorkerUnavailableError(
                "RunPod endpoint ID and API key must be configured."
            )

    @property
    def _endpoint_url(self) -> str:
        return f"{self.base_url}/{self.endpoint_id}"

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _json_object(response: requests.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise RunpodQueueError("RunPod returned a non-JSON response.") from exc
        if not isinstance(payload, dict):
            raise RunpodQueueError("RunPod returned an invalid response object.")
        return payload

    def submit(self, payload: dict[str, Any]) -> str:
        try:
            response = self.session.post(
                f"{self._endpoint_url}/run",
                headers=self._headers,
                json={"input": payload},
                timeout=(10, self.request_timeout_seconds),
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RunpodQueueError(f"RunPod job submission failed: {exc}") from exc
        document = self._json_object(response)
        job_id = document.get("id")
        if not isinstance(job_id, str) or not job_id.strip():
            raise RunpodQueueError("RunPod did not return a job ID.")
        return job_id

    def status(self, job_id: str) -> dict[str, Any]:
        try:
            response = self.session.get(
                f"{self._endpoint_url}/status/{job_id}",
                headers=self._headers,
                timeout=(10, self.request_timeout_seconds),
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RunpodQueueError(f"RunPod status check failed: {exc}") from exc
        return self._json_object(response)

    def cancel(self, job_id: str) -> None:
        try:
            response = self.session.post(
                f"{self._endpoint_url}/cancel/{job_id}",
                headers=self._headers,
                timeout=(10, self.request_timeout_seconds),
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RunpodQueueError(f"RunPod cancellation failed: {exc}") from exc


class RunpodWorker(VideoWorker):
    """Submit Virector RenderJobs to the cloud PerformanceWorker endpoint."""

    mode = "runpod"
    requested_mode = "runpod"
    fallback_reason = None

    _terminal_failures: ClassVar[frozenset[str]] = frozenset(
        {"FAILED", "CANCELLED", "TIMED_OUT", "ERROR"}
    )
    _progress_pattern: ClassVar[re.Pattern[str]] = re.compile(
        r"^(\d{1,3})%\s*(?:\N{MIDDLE DOT}|-)?\s*(.*)$"
    )

    def __init__(
        self,
        *,
        settings: Settings,
        queue_client: RunpodQueueClient,
        s3_client: Any,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        settings.validate_runpod_configuration()
        self.settings = settings
        self.queue_client = queue_client
        self.s3_client = s3_client
        self.sleep = sleep
        self.monotonic = monotonic

    def _key(self, job_id: str, relative_path: str) -> str:
        parts = [
            part
            for part in (
                self.settings.s3_key_prefix.strip("/"),
                "renders",
                job_id,
                relative_path,
            )
            if part
        ]
        return "/".join(parts)

    def _presigned_get(self, key: str) -> str:
        assert self.settings.s3_bucket is not None
        return self.s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.settings.s3_bucket, "Key": key},
            ExpiresIn=self.settings.s3_presigned_url_ttl_seconds,
        )

    def _presigned_put(self, key: str) -> str:
        assert self.settings.s3_bucket is not None
        return self.s3_client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self.settings.s3_bucket,
                "Key": key,
                "ContentType": "video/mp4",
            },
            ExpiresIn=self.settings.s3_presigned_url_ttl_seconds,
        )

    def _build_payload(self, job: RenderJob) -> tuple[dict[str, Any], str]:
        if not job.reference_assets:
            raise RunpodQueueError("RunPod renders require tagged omni references.")
        assert self.settings.s3_bucket is not None
        references = []
        for asset in job.reference_assets:
            if not asset.path.is_file():
                raise RunpodQueueError(f"Reference asset is missing: {asset.path.name}")
            suffix = asset.path.suffix.lower() or {
                "image": ".png",
                "video": ".mp4",
                "audio": ".wav",
            }[asset.media_type.value]
            key = self._key(
                job.job_id,
                f"references/{asset.media_type.value}-{asset.index:02d}{suffix}",
            )
            self.s3_client.upload_file(
                str(asset.path),
                self.settings.s3_bucket,
                key,
            )
            references.append(
                {
                    "index": asset.index,
                    "tag": asset.tag,
                    "media_type": asset.media_type.value,
                    "download_url": self._presigned_get(key),
                    "strength": asset.strength,
                    "asset_id": asset.asset_id,
                    "role": asset.role.value if asset.role else None,
                    "prompt_alias": asset.prompt_alias,
                    "priority": asset.priority,
                }
            )

        output_key = self._key(job.job_id, "preview.mp4")
        payload = {
                "job_id": job.job_id,
                "shot_spec": job.spec.model_dump(mode="json"),
                "references": references,
                "output_upload_url": self._presigned_put(output_key),
                "output_object_key": output_key,
        }
        if job.continuity_frame is not None and job.continuity_frame.is_file():
            anchor_key = self._key(job.job_id, "scene_anchor.png")
            self.s3_client.upload_file(
                str(job.continuity_frame),
                self.settings.s3_bucket,
                anchor_key,
            )
            payload["scene_anchor_download_url"] = self._presigned_get(anchor_key)
        return payload, output_key

    @classmethod
    def _progress_update(cls, document: dict[str, Any]) -> tuple[int, str] | None:
        value = document.get("output")
        if not isinstance(value, str):
            return None
        match = cls._progress_pattern.match(value.strip())
        if match is None:
            return None
        progress = max(35, min(95, int(match.group(1))))
        message = match.group(2).strip() or "Cloud render in progress."
        return progress, message

    @staticmethod
    def _failure_message(document: dict[str, Any], status: str) -> str:
        error = document.get("error")
        if isinstance(error, str) and error.strip():
            return error.strip()
        output = document.get("output")
        if isinstance(output, str) and output.strip():
            return output.strip()
        if isinstance(output, dict):
            message = output.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        return f"RunPod render ended with status {status}."

    def _download_result(
        self,
        job: RenderJob,
        document: dict[str, Any],
        expected_key: str,
    ) -> RenderResult:
        output = document.get("output")
        if not isinstance(output, dict):
            raise RunpodQueueError("RunPod completed without output metadata.")
        if output.get("job_id") != job.job_id:
            raise RunpodQueueError("RunPod returned output for a different job.")
        output_key = output.get("output_object_key")
        if output_key != expected_key:
            raise RunpodQueueError("RunPod returned an unexpected output object key.")
        assert self.settings.s3_bucket is not None
        video = job.output_dir / "preview.mp4"
        self.s3_client.download_file(
            self.settings.s3_bucket,
            expected_key,
            str(video),
        )
        if not video.is_file() or video.stat().st_size == 0:
            raise RunpodQueueError("RunPod output video is missing or empty.")
        return RenderResult(
            job_id=job.job_id,
            status="completed",
            start_frame=job.start_frame,
            video=video,
            message=str(output.get("message") or "Cloud render completed."),
        )

    def render(self, job: RenderJob) -> RenderResult:
        remote_job_id: str | None = None
        try:
            if job.progress_callback:
                job.progress_callback(30, "Uploading omni references to cloud storage.")
            payload, output_key = self._build_payload(job)
            remote_job_id = self.queue_client.submit(payload)
            if job.progress_callback:
                job.progress_callback(35, "RunPod render queued.")

            deadline = self.monotonic() + self.settings.runpod_job_timeout_seconds
            while self.monotonic() < deadline:
                document = self.queue_client.status(remote_job_id)
                status = str(document.get("status", "")).upper()
                if status == "COMPLETED":
                    if job.progress_callback:
                        job.progress_callback(95, "Downloading the completed cloud render.")
                    return self._download_result(job, document, output_key)
                if status in self._terminal_failures:
                    raise RunpodQueueError(self._failure_message(document, status))
                if status not in {"IN_QUEUE", "IN_PROGRESS", "RUNNING"}:
                    raise RunpodQueueError(
                        f"RunPod returned an unknown job status: {status or 'missing'}."
                    )
                if job.progress_callback:
                    update = self._progress_update(document)
                    if update is not None:
                        job.progress_callback(*update)
                    elif status == "IN_QUEUE":
                        job.progress_callback(35, "Waiting for a RunPod GPU worker.")
                    else:
                        job.progress_callback(50, "Cloud performance render in progress.")
                self.sleep(self.settings.runpod_poll_interval_seconds)

            try:
                self.queue_client.cancel(remote_job_id)
            except RunpodQueueError:
                pass
            raise RunpodQueueError(
                "RunPod render exceeded the configured job timeout and was cancelled."
            )
        except Exception as exc:
            return RenderResult(
                job_id=job.job_id,
                status="failed",
                start_frame=job.start_frame,
                message=str(exc),
            )
