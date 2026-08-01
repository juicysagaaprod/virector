import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from virector.config import Settings


class JobRepositoryError(RuntimeError):
    """Raised when render metadata cannot be persisted."""


class JobIdentityRequiredError(JobRepositoryError):
    """Raised when Postgres persistence lacks an authenticated owner/project."""


@dataclass(frozen=True)
class JobIdentity:
    owner_id: str
    project_id: str


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    title: str
    direction_prompt: str
    shot_spec: dict[str, Any]
    worker_mode: str
    identity: JobIdentity | None = None


@dataclass(frozen=True)
class JobAssetRecord:
    kind: str
    object_key: str
    image_tag: str | None = None
    ordinal: int | None = None
    content_type: str | None = None
    size_bytes: int | None = None
    metadata: dict[str, Any] | None = None


class JobRepository(Protocol):
    backend: str
    requires_identity: bool

    def healthcheck(self) -> None:
        """Verify that the configured repository is available."""

    def create_job(self, record: JobRecord) -> None:
        """Persist a newly accepted render job."""

    def add_assets(self, job_id: str, assets: list[JobAssetRecord]) -> None:
        """Persist reference and output artifact metadata."""

    def transition(
        self,
        job_id: str,
        *,
        status: str,
        progress: int,
        message: str = "",
        output_object_key: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Update job state and append a lifecycle event."""

    def close(self) -> None:
        """Release repository resources."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _event_type(status: str) -> str:
    return {
        "queued": "accepted",
        "completed": "completed",
        "failed": "failed",
        "cancelled": "cancelled",
    }.get(status, "progress")


class LocalJobRepository:
    """JSON-backed render state used by local development and tests."""

    backend = "local"
    requires_identity = False

    def __init__(self, outputs_dir: Path) -> None:
        self.outputs_dir = outputs_dir

    def _path(self, job_id: str) -> Path:
        return self.outputs_dir / job_id / "job_state.json"

    def _read(self, job_id: str) -> dict[str, Any]:
        path = self._path(job_id)
        if not path.is_file():
            raise JobRepositoryError(f"Local render state not found: {job_id}.")
        return json.loads(path.read_text(encoding="utf-8"))

    def _write(self, job_id: str, payload: dict[str, Any]) -> None:
        path = self._path(job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_suffix(".json.tmp")
        temporary_path.write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(path)

    def healthcheck(self) -> None:
        self.outputs_dir.mkdir(parents=True, exist_ok=True)

    def create_job(self, record: JobRecord) -> None:
        now = _utc_now()
        payload = {
            "job_id": record.job_id,
            "owner_id": record.identity.owner_id if record.identity else None,
            "project_id": record.identity.project_id if record.identity else None,
            "title": record.title,
            "direction_prompt": record.direction_prompt,
            "shot_spec": record.shot_spec,
            "worker_mode": record.worker_mode,
            "status": "queued",
            "progress": 0,
            "output_object_key": None,
            "error_message": None,
            "assets": [],
            "events": [
                {
                    "event_type": "accepted",
                    "status": "queued",
                    "progress": 0,
                    "message": "Render job accepted.",
                    "created_at": now,
                }
            ],
            "created_at": now,
            "updated_at": now,
        }
        self._write(record.job_id, payload)

    def add_assets(self, job_id: str, assets: list[JobAssetRecord]) -> None:
        if not assets:
            return
        payload = self._read(job_id)
        payload["assets"].extend(asdict(asset) for asset in assets)
        payload["updated_at"] = _utc_now()
        self._write(job_id, payload)

    def transition(
        self,
        job_id: str,
        *,
        status: str,
        progress: int,
        message: str = "",
        output_object_key: str | None = None,
        error_message: str | None = None,
    ) -> None:
        payload = self._read(job_id)
        now = _utc_now()
        payload.update(
            {
                "status": status,
                "progress": progress,
                "updated_at": now,
                "error_message": error_message,
            }
        )
        if output_object_key is not None:
            payload["output_object_key"] = output_object_key
        payload["events"].append(
            {
                "event_type": _event_type(status),
                "status": status,
                "progress": progress,
                "message": message,
                "created_at": now,
            }
        )
        self._write(job_id, payload)

    def close(self) -> None:
        return None


class PostgresJobRepository:
    """Supabase Postgres repository for authenticated production jobs."""

    backend = "postgres"
    requires_identity = True

    def __init__(
        self,
        *,
        database_url: str,
        pool_min_size: int,
        pool_max_size: int,
        pool_timeout_seconds: float,
        pool: Any | None = None,
    ) -> None:
        if pool is None:
            try:
                from psycopg_pool import ConnectionPool
            except ImportError as exc:
                raise JobRepositoryError(
                    "Postgres persistence requires psycopg and psycopg-pool."
                ) from exc
            pool = ConnectionPool(
                conninfo=database_url,
                min_size=pool_min_size,
                max_size=pool_max_size,
                timeout=pool_timeout_seconds,
                open=True,
            )
        self.pool = pool

    def healthcheck(self) -> None:
        try:
            with self.pool.connection() as connection:
                connection.execute("select 1")
        except Exception as exc:
            raise JobRepositoryError(
                f"Postgres job repository is unavailable: {exc}"
            ) from exc

    def create_job(self, record: JobRecord) -> None:
        if record.identity is None:
            raise JobIdentityRequiredError(
                "Postgres job persistence requires an authenticated owner and project."
            )
        try:
            from psycopg.types.json import Jsonb

            with self.pool.connection() as connection:
                connection.execute(
                    """
                    insert into public.render_jobs (
                        id, project_id, owner_id, title, direction_prompt,
                        shot_spec, status, progress, worker_mode
                    ) values (%s, %s, %s, %s, %s, %s, 'queued', 0, %s)
                    """,
                    (
                        record.job_id,
                        record.identity.project_id,
                        record.identity.owner_id,
                        record.title,
                        record.direction_prompt,
                        Jsonb(record.shot_spec),
                        record.worker_mode,
                    ),
                )
                connection.execute(
                    """
                    insert into public.render_events (
                        render_job_id, event_type, stage, progress, message
                    ) values (%s, 'accepted', 'queued', 0, %s)
                    """,
                    (record.job_id, "Render job accepted."),
                )
        except JobRepositoryError:
            raise
        except Exception as exc:
            raise JobRepositoryError(
                f"Could not create render job {record.job_id}: {exc}"
            ) from exc

    def add_assets(self, job_id: str, assets: list[JobAssetRecord]) -> None:
        if not assets:
            return
        try:
            from psycopg.types.json import Jsonb

            rows = [
                (
                    job_id,
                    asset.kind,
                    asset.image_tag,
                    asset.ordinal,
                    asset.object_key,
                    asset.content_type,
                    asset.size_bytes,
                    Jsonb(asset.metadata or {}),
                )
                for asset in assets
            ]
            with (
                self.pool.connection() as connection,
                connection.cursor() as cursor,
            ):
                cursor.executemany(
                    """
                    insert into public.render_assets (
                        render_job_id, kind, image_tag, ordinal, object_key,
                        content_type, size_bytes, metadata
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s)
                    on conflict (render_job_id, object_key) do nothing
                    """,
                    rows,
                )
        except Exception as exc:
            raise JobRepositoryError(
                f"Could not persist assets for render {job_id}: {exc}"
            ) from exc

    def transition(
        self,
        job_id: str,
        *,
        status: str,
        progress: int,
        message: str = "",
        output_object_key: str | None = None,
        error_message: str | None = None,
    ) -> None:
        event_type = _event_type(status)
        try:
            with self.pool.connection() as connection:
                updated = connection.execute(
                    """
                    update public.render_jobs
                    set status = %s,
                        progress = %s,
                        output_object_key = coalesce(%s, output_object_key),
                        error_message = %s,
                        started_at = case
                            when %s = 'rendering' and started_at is null then now()
                            else started_at
                        end,
                        completed_at = case
                            when %s in ('completed', 'failed', 'cancelled') then now()
                            else completed_at
                        end
                    where id = %s
                    """,
                    (
                        status,
                        progress,
                        output_object_key,
                        error_message,
                        status,
                        status,
                        job_id,
                    ),
                )
                if updated.rowcount != 1:
                    raise JobRepositoryError(f"Render job not found: {job_id}.")
                connection.execute(
                    """
                    insert into public.render_events (
                        render_job_id, event_type, stage, progress, message
                    ) values (%s, %s, %s, %s, %s)
                    """,
                    (job_id, event_type, status, progress, message),
                )
        except JobRepositoryError:
            raise
        except Exception as exc:
            raise JobRepositoryError(
                f"Could not update render job {job_id}: {exc}"
            ) from exc

    def close(self) -> None:
        self.pool.close()


def create_job_repository(
    settings: Settings,
    *,
    pool: Any | None = None,
) -> JobRepository:
    settings.validate_job_repository_configuration()
    if settings.job_repository_backend == "local":
        return LocalJobRepository(settings.outputs_dir)

    assert settings.database_url is not None
    return PostgresJobRepository(
        database_url=settings.database_url.get_secret_value(),
        pool_min_size=settings.database_pool_min_size,
        pool_max_size=settings.database_pool_max_size,
        pool_timeout_seconds=settings.database_pool_timeout_seconds,
        pool=pool,
    )
