from pathlib import Path

import pytest

from virector.config import Settings
from virector.services.storage import (
    LocalArtifactStore,
    S3ArtifactStore,
    create_artifact_store,
)


class FakeS3Client:
    def __init__(self) -> None:
        self.uploads: list[tuple[str, str, str]] = []
        self.objects: set[tuple[str, str]] = set()

    def upload_file(self, filename: str, bucket: str, key: str) -> None:
        self.uploads.append((filename, bucket, key))
        self.objects.add((bucket, key))

    def head_object(self, *, Bucket: str, Key: str) -> None:
        if (Bucket, Key) not in self.objects:
            raise FileNotFoundError(Key)

    def generate_presigned_url(
        self,
        operation: str,
        *,
        Params: dict[str, str],
        ExpiresIn: int,
    ) -> str:
        return (
            f"https://private.example/{Params['Bucket']}/{Params['Key']}"
            f"?ttl={ExpiresIn}&operation={operation}"
        )


class BrokenS3Client(FakeS3Client):
    def head_object(self, *, Bucket: str, Key: str) -> None:
        raise ConnectionError("object store unavailable")


def test_local_artifact_store_returns_generated_video(tmp_path: Path) -> None:
    video = tmp_path / "job-1" / "preview.mp4"
    video.parent.mkdir()
    video.write_bytes(b"video")

    location = LocalArtifactStore(tmp_path).get_video_location("job-1")

    assert location is not None
    assert location.path == video
    assert location.url is None


def test_s3_artifact_store_publishes_private_job_tree(tmp_path: Path) -> None:
    job_dir = tmp_path / "job-1"
    references = job_dir / "references"
    references.mkdir(parents=True)
    (references / "reference-01.png").write_bytes(b"image")
    (job_dir / "shot_spec.json").write_text("{}", encoding="utf-8")
    (job_dir / "preview.mp4").write_bytes(b"video")
    client = FakeS3Client()
    store = S3ArtifactStore(
        bucket="virector-staging",
        key_prefix="tenant-a",
        presigned_url_ttl_seconds=900,
        client=client,
    )

    store.publish_job("job-1", job_dir)
    location = store.get_video_location("job-1")

    assert [item[2] for item in client.uploads] == [
        "tenant-a/renders/job-1/preview.mp4",
        "tenant-a/renders/job-1/references/reference-01.png",
        "tenant-a/renders/job-1/shot_spec.json",
    ]
    assert location is not None
    assert location.path is None
    assert location.url is not None
    assert "ttl=900" in location.url


def test_s3_configuration_fails_fast_when_secrets_are_missing(
    tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        storage_backend="s3",
        s3_bucket="virector-staging",
    )

    with pytest.raises(ValueError, match="VIRECTOR_S3_ENDPOINT_URL"):
        create_artifact_store(settings, s3_client=FakeS3Client())


def test_s3_configuration_rejects_blank_secret_values(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        storage_backend="s3",
        s3_endpoint_url="https://example.r2.cloudflarestorage.com",
        s3_bucket="virector-staging",
        s3_access_key_id="",
        s3_secret_access_key="",
    )

    with pytest.raises(ValueError, match="VIRECTOR_S3_ACCESS_KEY_ID"):
        create_artifact_store(settings, s3_client=FakeS3Client())


def test_s3_artifact_store_surfaces_connectivity_failures() -> None:
    store = S3ArtifactStore(
        bucket="virector-staging",
        key_prefix="staging",
        presigned_url_ttl_seconds=900,
        client=BrokenS3Client(),
    )

    with pytest.raises(RuntimeError, match="object store unavailable"):
        store.get_video_location("job-1")
