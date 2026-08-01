from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlparse

import requests
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field, ValidationError, model_validator

from virector.models.omni_asset import OmniMediaType
from virector.models.shot_spec import ShotSpec
from virector.workers.base import ReferenceAsset, RenderJob, VideoWorker


class RunpodPayloadError(ValueError):
    """Raised when a RunPod job does not satisfy the Virector contract."""


def require_cuda_runtime(torch_module: object | None = None) -> None:
    """Fail before model downloads when the RunPod image cannot see its GPU."""

    if torch_module is None:
        import torch as torch_module

    cuda = getattr(torch_module, "cuda", None)
    cuda_available = bool(cuda and cuda.is_available())
    if cuda_available:
        return

    version = getattr(torch_module, "__version__", "unknown")
    torch_version = getattr(torch_module, "version", None)
    cuda_build = getattr(torch_version, "cuda", None) or "CPU-only"
    raise RuntimeError(
        "RunPod GPU is not available to PyTorch "
        f"{version} (CUDA build: {cuda_build})."
    )


class RemoteReference(BaseModel):
    index: int = Field(ge=1, le=9)
    tag: str = Field(pattern=r"^@(image[1-9]|video[1-3]|audio[1-3])$")
    media_type: OmniMediaType = OmniMediaType.image
    download_url: str = Field(min_length=8, max_length=4096)
    strength: float = Field(default=0.9, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_tag(self) -> "RemoteReference":
        limit = 9 if self.media_type == OmniMediaType.image else 3
        if self.index > limit or self.tag != f"@{self.media_type.value}{self.index}":
            raise ValueError("Remote reference tag must match media type and index")
        return self


class RunpodRenderInput(BaseModel):
    job_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    shot_spec: ShotSpec
    references: list[RemoteReference] = Field(min_length=1, max_length=12)
    output_upload_url: str = Field(min_length=8, max_length=4096)
    output_object_key: str = Field(min_length=1, max_length=1024)

    @model_validator(mode="after")
    def validate_references(self) -> "RunpodRenderInput":
        media_order = {
            OmniMediaType.image: 0,
            OmniMediaType.video: 1,
            OmniMediaType.audio: 2,
        }
        expected = sorted(
            self.references,
            key=lambda reference: (media_order[reference.media_type], reference.index),
        )
        if self.references != expected:
            raise ValueError("RunPod references must be ordered by media and index")
        for media_type in OmniMediaType:
            indexes = [
                reference.index
                for reference in self.references
                if reference.media_type == media_type
            ]
            if indexes and indexes != list(range(1, len(indexes) + 1)):
                raise ValueError(
                    f"RunPod {media_type.value} references must start at index 1"
                )
        if not any(
            reference.media_type == OmniMediaType.image
            for reference in self.references
        ):
            raise ValueError("RunPod performance jobs require an image reference")
        if self.shot_spec.director_plan is None:
            raise ValueError("RunPod performance jobs require a DirectorPlan")
        return self


class RemoteFileClient:
    """Transfer render assets with short-lived object-storage URLs."""

    def __init__(
        self,
        *,
        max_reference_bytes: int = 25 * 1024 * 1024,
        max_video_bytes: int = 100 * 1024 * 1024,
        allow_http: bool = False,
        allowed_hosts: set[str] | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.max_reference_bytes = max_reference_bytes
        self.max_video_bytes = max_video_bytes
        self.allow_http = allow_http
        self.allowed_hosts = {host.lower() for host in (allowed_hosts or set())}
        self.session = session or requests.Session()

    def _validate_url(self, value: str) -> None:
        parsed = urlparse(value)
        allowed_schemes = {"https"} | ({"http"} if self.allow_http else set())
        if parsed.scheme not in allowed_schemes or not parsed.netloc:
            raise RunpodPayloadError(
                "Asset URLs must be absolute HTTPS URLs."
            )
        hostname = (parsed.hostname or "").lower()
        if self.allowed_hosts and hostname not in self.allowed_hosts:
            raise RunpodPayloadError("Asset URL host is not approved.")

    def download_reference(
        self,
        url: str,
        destination: Path,
        media_type: OmniMediaType = OmniMediaType.image,
    ) -> None:
        self._validate_url(url)
        max_bytes = (
            self.max_video_bytes
            if media_type == OmniMediaType.video
            else self.max_reference_bytes
        )
        with self.session.get(
            url,
            stream=True,
            timeout=(10, 180),
        ) as response:
            self._validate_url(response.url)
            response.raise_for_status()
            declared_size = int(response.headers.get("content-length", "0") or 0)
            if declared_size > max_bytes:
                raise RunpodPayloadError("Reference asset exceeds the size limit.")
            size = 0
            with destination.open("wb") as target:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > max_bytes:
                        raise RunpodPayloadError(
                            "Reference asset exceeds the size limit."
                        )
                    target.write(chunk)
        if size == 0:
            raise RunpodPayloadError("Downloaded reference asset is empty.")
        if media_type == OmniMediaType.image:
            try:
                with Image.open(destination) as image:
                    image.verify()
            except (OSError, UnidentifiedImageError) as exc:
                raise RunpodPayloadError(
                    "Downloaded reference is not a valid image."
                ) from exc

    def upload_video(self, url: str, video: Path) -> None:
        self._validate_url(url)
        with video.open("rb") as source:
            response = self.session.put(
                url,
                data=source,
                headers={"Content-Type": "video/mp4"},
                timeout=(10, 600),
            )
        try:
            self._validate_url(response.url)
            response.raise_for_status()
        finally:
            response.close()


class RunpodPerformanceRuntime:
    """Adapt RunPod queue jobs to Virector's PerformanceWorker contract."""

    def __init__(
        self,
        worker: VideoWorker,
        file_client: RemoteFileClient | None = None,
        temp_root: Path | None = None,
    ) -> None:
        self.worker = worker
        self.file_client = file_client or RemoteFileClient()
        self.temp_root = temp_root

    def handle(
        self,
        job: dict,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> dict[str, str | int]:
        raw_input = job.get("input")
        if not isinstance(raw_input, dict):
            raise RunpodPayloadError("RunPod job input must be a JSON object.")
        try:
            payload = RunpodRenderInput.model_validate(raw_input)
        except ValidationError as exc:
            details = "; ".join(
                f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                for error in exc.errors(include_input=False)
            )
            raise RunpodPayloadError(
                f"RunPod input validation failed: {details}"
            ) from exc

        temp_parent = str(self.temp_root) if self.temp_root else None
        with TemporaryDirectory(prefix="virector-", dir=temp_parent) as temp:
            job_dir = Path(temp)
            references_dir = job_dir / "references"
            references_dir.mkdir()
            assets: list[ReferenceAsset] = []
            for reference in payload.references:
                url_suffix = Path(urlparse(reference.download_url).path).suffix.lower()
                default_suffix = {
                    OmniMediaType.image: ".png",
                    OmniMediaType.video: ".mp4",
                    OmniMediaType.audio: ".wav",
                }[reference.media_type]
                path = references_dir / (
                    f"{reference.media_type.value}-{reference.index:02d}"
                    f"{url_suffix or default_suffix}"
                )
                self.file_client.download_reference(
                    reference.download_url,
                    path,
                    reference.media_type,
                )
                assets.append(
                    ReferenceAsset(
                        index=reference.index,
                        tag=reference.tag,
                        path=path,
                        media_type=reference.media_type,
                        strength=reference.strength,
                    )
                )

            image_assets = tuple(
                asset
                for asset in assets
                if asset.media_type == OmniMediaType.image
            )
            video_assets = tuple(
                asset
                for asset in assets
                if asset.media_type == OmniMediaType.video
            )
            audio_assets = tuple(
                asset
                for asset in assets
                if asset.media_type == OmniMediaType.audio
            )

            result = self.worker.render(
                RenderJob(
                    job_id=payload.job_id,
                    output_dir=job_dir,
                    start_frame=image_assets[0].path,
                    spec=payload.shot_spec,
                    reference_images=tuple(asset.path for asset in image_assets),
                    reference_videos=tuple(asset.path for asset in video_assets),
                    reference_audio=tuple(asset.path for asset in audio_assets),
                    reference_assets=tuple(assets),
                    progress_callback=progress_callback,
                )
            )
            if result.status not in {"complete", "completed"} or result.video is None:
                raise RuntimeError(result.message or "RunPod render failed.")
            if not result.video.is_file():
                raise RuntimeError("RunPod worker returned a missing video file.")
            size_bytes = result.video.stat().st_size
            self.file_client.upload_video(payload.output_upload_url, result.video)
            return {
                "job_id": payload.job_id,
                "status": "completed",
                "output_object_key": payload.output_object_key,
                "size_bytes": size_bytes,
                "message": result.message or "Cloud performance render completed.",
            }
