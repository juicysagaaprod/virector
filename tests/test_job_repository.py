import json
from pathlib import Path

import pytest

from virector.services.job_repository import (
    JobAssetRecord,
    JobIdentityRequiredError,
    JobRecord,
    LocalJobRepository,
    PostgresJobRepository,
)


def make_record() -> JobRecord:
    return JobRecord(
        job_id="a" * 32,
        title="Tracking shot",
        direction_prompt="@image1 walks through @image2.",
        shot_spec={"title": "Tracking shot", "seed": 42},
        worker_mode="ltx",
    )


def test_local_repository_persists_lifecycle_and_assets(tmp_path: Path) -> None:
    repository = LocalJobRepository(tmp_path)
    record = make_record()

    repository.create_job(record)
    repository.add_assets(
        record.job_id,
        [
            JobAssetRecord(
                kind="reference",
                image_tag="@image1",
                ordinal=1,
                object_key=f"renders/{record.job_id}/references/one.png",
                content_type="image/png",
                size_bytes=123,
                metadata={"strength": 0.9},
            )
        ],
    )
    repository.transition(
        record.job_id,
        status="rendering",
        progress=25,
        message="Worker started.",
    )
    repository.transition(
        record.job_id,
        status="completed",
        progress=100,
        message="Preview complete.",
        output_object_key=f"renders/{record.job_id}/preview.mp4",
    )

    payload = json.loads(
        (tmp_path / record.job_id / "job_state.json").read_text(encoding="utf-8")
    )
    assert payload["status"] == "completed"
    assert payload["progress"] == 100
    assert payload["output_object_key"].endswith("preview.mp4")
    assert payload["assets"][0]["image_tag"] == "@image1"
    assert [event["event_type"] for event in payload["events"]] == [
        "accepted",
        "progress",
        "completed",
    ]


class UnusedPool:
    def connection(self):
        raise AssertionError("A missing identity must fail before database access.")

    def close(self) -> None:
        return None


def test_postgres_repository_requires_authenticated_identity() -> None:
    repository = PostgresJobRepository(
        database_url="postgresql://unused",
        pool_min_size=1,
        pool_max_size=1,
        pool_timeout_seconds=1,
        pool=UnusedPool(),
    )

    with pytest.raises(JobIdentityRequiredError, match="authenticated owner"):
        repository.create_job(make_record())
