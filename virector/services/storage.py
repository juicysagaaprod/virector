from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from virector.config import Settings


class ArtifactStorageError(RuntimeError):
    """Raised when durable render storage cannot be reached."""


@dataclass(frozen=True)
class ArtifactLocation:
    path: Path | None = None
    url: str | None = None


class ArtifactStore(Protocol):
    def publish_job(self, job_id: str, job_dir: Path) -> None:
        """Persist every artifact produced for a render job."""

    def get_video_location(self, job_id: str) -> ArtifactLocation | None:
        """Return a local path or short-lived remote URL for a render."""

    def object_key(self, job_id: str, relative_path: str) -> str:
        """Return the durable key recorded in render metadata."""


class LocalArtifactStore:
    def __init__(self, outputs_dir: Path) -> None:
        self.outputs_dir = outputs_dir

    def publish_job(self, job_id: str, job_dir: Path) -> None:
        # The worker already writes into the persistent output directory.
        return None

    def get_video_location(self, job_id: str) -> ArtifactLocation | None:
        video_path = self.outputs_dir / job_id / "preview.mp4"
        if not video_path.is_file():
            return None
        return ArtifactLocation(path=video_path)

    def object_key(self, job_id: str, relative_path: str) -> str:
        return f"renders/{job_id}/{relative_path}"


class S3ArtifactStore:
    """Private S3-compatible storage, including Cloudflare R2."""

    def __init__(
        self,
        *,
        bucket: str,
        key_prefix: str,
        presigned_url_ttl_seconds: int,
        client: Any,
    ) -> None:
        self.bucket = bucket
        self.key_prefix = key_prefix.strip("/")
        self.presigned_url_ttl_seconds = presigned_url_ttl_seconds
        self.client = client

    def _key(self, job_id: str, relative_path: str) -> str:
        parts = [
            part for part in (self.key_prefix, "renders", job_id, relative_path) if part
        ]
        return "/".join(parts)

    def object_key(self, job_id: str, relative_path: str) -> str:
        return self._key(job_id, relative_path)

    def publish_job(self, job_id: str, job_dir: Path) -> None:
        try:
            for artifact in sorted(
                path for path in job_dir.rglob("*") if path.is_file()
            ):
                relative_path = artifact.relative_to(job_dir).as_posix()
                self.client.upload_file(
                    str(artifact),
                    self.bucket,
                    self._key(job_id, relative_path),
                )
        except Exception as exc:
            raise ArtifactStorageError(
                f"Could not persist render {job_id} to object storage: {exc}"
            ) from exc

    def get_video_location(self, job_id: str) -> ArtifactLocation | None:
        key = self._key(job_id, "preview.mp4")
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            url = self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=self.presigned_url_ttl_seconds,
            )
        except FileNotFoundError:
            return None
        except Exception as exc:
            error = getattr(exc, "response", {}).get("Error", {})
            if str(error.get("Code", "")) in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise ArtifactStorageError(
                f"Could not read render {job_id} from object storage: {exc}"
            ) from exc
        return ArtifactLocation(url=url)


def create_s3_client(settings: Settings) -> Any:
    """Create the configured private S3-compatible client."""

    settings.validate_cloud_configuration()
    if settings.storage_backend != "s3":
        raise ArtifactStorageError(
            "S3 client creation requires VIRECTOR_STORAGE_BACKEND=s3."
        )
    try:
        import boto3
    except ImportError as exc:
        raise ArtifactStorageError(
            "S3-compatible storage requires the boto3 runtime dependency."
        ) from exc

    assert settings.s3_access_key_id is not None
    assert settings.s3_secret_access_key is not None
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id.get_secret_value(),
        aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
    )


def create_artifact_store(
    settings: Settings,
    *,
    s3_client: Any | None = None,
) -> ArtifactStore:
    settings.validate_cloud_configuration()
    if settings.storage_backend == "local":
        return LocalArtifactStore(settings.outputs_dir)

    if s3_client is None:
        s3_client = create_s3_client(settings)

    assert settings.s3_bucket is not None
    return S3ArtifactStore(
        bucket=settings.s3_bucket,
        key_prefix=settings.s3_key_prefix,
        presigned_url_ttl_seconds=settings.s3_presigned_url_ttl_seconds,
        client=s3_client,
    )
