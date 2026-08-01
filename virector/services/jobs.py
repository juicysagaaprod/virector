import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from virector.config import Settings
from virector.models.shot_spec import ReferenceDirective, ShotSpec
from virector.services.compositor import (
    compose_start_frame,
    prepare_reference_start_frame,
)
from virector.services.job_repository import (
    JobAssetRecord,
    JobIdentity,
    JobRecord,
    JobRepository,
    JobRepositoryError,
    create_job_repository,
)
from virector.services.references import build_reference_directives
from virector.services.storage import ArtifactStore, create_artifact_store
from virector.workers.base import (
    ReferenceAsset,
    RenderJob,
    RenderResult,
    VideoWorker,
)


@dataclass(frozen=True)
class JobArtifacts:
    job_id: str
    directory: Path
    start_frame: Path
    shot_spec: Path
    references: tuple[Path, ...] = ()


class JobService:
    def __init__(
        self,
        settings: Settings,
        worker: VideoWorker,
        artifact_store: ArtifactStore | None = None,
        job_repository: JobRepository | None = None,
    ) -> None:
        self.settings = settings
        self.worker = worker
        self.artifact_store = artifact_store or create_artifact_store(settings)
        self.job_repository = job_repository or create_job_repository(settings)

    def healthcheck(self) -> None:
        self.job_repository.healthcheck()

    def close(self) -> None:
        self.job_repository.close()

    def _accept_job(
        self,
        job_id: str,
        spec: ShotSpec,
        identity: JobIdentity | None,
    ) -> None:
        self.job_repository.create_job(
            JobRecord(
                job_id=job_id,
                title=spec.title,
                direction_prompt=spec.prompt,
                shot_spec=spec.model_dump(mode="json"),
                worker_mode=self.worker.mode,
                identity=identity,
            )
        )

    def _record_failure(self, job_id: str, error: Exception) -> None:
        try:
            self.job_repository.transition(
                job_id,
                status="failed",
                progress=100,
                message="Render job failed.",
                error_message=str(error),
            )
        except JobRepositoryError:
            # Preserve the original rendering/storage exception for the API.
            pass

    @staticmethod
    def _content_type(path: Path) -> str | None:
        return {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".json": "application/json",
            ".mp4": "video/mp4",
        }.get(path.suffix.lower())

    @staticmethod
    def _result_status(result: RenderResult) -> str:
        if result.status == "complete":
            return "completed"
        if result.status in {"completed", "composed", "failed", "cancelled"}:
            return result.status
        return "failed"

    def create(
        self,
        character_path: str | Path,
        world_path: str | Path,
        spec: ShotSpec,
        identity: JobIdentity | None = None,
    ) -> RenderResult:
        job_id = uuid4().hex
        job_dir = self.settings.outputs_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=False)
        self._accept_job(job_id, spec, identity)

        artifacts = JobArtifacts(
            job_id=job_id,
            directory=job_dir,
            start_frame=job_dir / "start_frame.png",
            shot_spec=job_dir / "shot_spec.json",
        )

        try:
            self.job_repository.transition(
                job_id,
                status="validating",
                progress=10,
                message="Preparing the directed shot.",
            )
            compose_start_frame(
                character_path=character_path,
                world_path=world_path,
                spec=spec,
                output_path=artifacts.start_frame,
            )

            payload = {
                "job_id": job_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "shot": spec.model_dump(mode="json"),
                "assets": {
                    "character_image": str(Path(character_path).resolve()),
                    "world_image": str(Path(world_path).resolve()),
                    "start_frame": str(artifacts.start_frame.resolve()),
                },
            }
            artifacts.shot_spec.write_text(
                json.dumps(payload, indent=2),
                encoding="utf-8",
            )
            self.job_repository.add_assets(
                job_id,
                [
                    JobAssetRecord(
                        kind="manifest",
                        object_key=self.artifact_store.object_key(
                            job_id, "shot_spec.json"
                        ),
                        content_type="application/json",
                        size_bytes=artifacts.shot_spec.stat().st_size,
                    ),
                    JobAssetRecord(
                        kind="conditioning",
                        object_key=self.artifact_store.object_key(
                            job_id, "start_frame.png"
                        ),
                        content_type="image/png",
                        size_bytes=artifacts.start_frame.stat().st_size,
                    ),
                ],
            )
            self.job_repository.transition(
                job_id,
                status="rendering",
                progress=25,
                message="Render worker started.",
            )
            result = self.worker.render(
                RenderJob(
                    job_id=job_id,
                    output_dir=job_dir,
                    start_frame=artifacts.start_frame,
                    spec=spec,
                    reference_images=(Path(character_path), Path(world_path)),
                    reference_assets=(
                        ReferenceAsset(
                            index=1,
                            tag="@image1",
                            path=Path(character_path),
                        ),
                        ReferenceAsset(
                            index=2,
                            tag="@image2",
                            path=Path(world_path),
                        ),
                    ),
                )
            )
            status = self._result_status(result)
            output_key = None
            if result.video:
                output_key = self.artifact_store.object_key(job_id, "preview.mp4")
                self.job_repository.add_assets(
                    job_id,
                    [
                        JobAssetRecord(
                            kind="preview",
                            object_key=output_key,
                            content_type="video/mp4",
                            size_bytes=result.video.stat().st_size,
                        )
                    ],
                )
            self.job_repository.transition(
                job_id,
                status=status,
                progress=100,
                message=result.message,
                output_object_key=output_key,
                error_message=result.message if status == "failed" else None,
            )
            self.artifact_store.publish_job(job_id, job_dir)
            return result
        except Exception as exc:
            self._record_failure(job_id, exc)
            raise

    def create_from_references(
        self,
        reference_paths: list[str | Path],
        spec: ShotSpec,
        reference_directives: list[ReferenceDirective] | None = None,
        identity: JobIdentity | None = None,
        job_id: str | None = None,
    ) -> RenderResult:
        """Create a job from one ordered, @imageN-tagged reference image set."""

        if not reference_paths:
            raise ValueError("Upload at least one omni reference image.")
        directives = reference_directives or spec.references
        if not directives:
            directives = build_reference_directives(len(reference_paths))
        if len(directives) != len(reference_paths):
            raise ValueError(
                "Each uploaded reference image must have one reference directive."
            )
        spec = ShotSpec.model_validate(
            {
                **spec.model_dump(),
                "references": directives,
            }
        )

        job_id = job_id or uuid4().hex
        job_dir = self.settings.outputs_dir / job_id
        references_dir = job_dir / "references"
        references_dir.mkdir(parents=True, exist_ok=False)
        self._accept_job(job_id, spec, identity)

        try:
            self.job_repository.transition(
                job_id,
                status="validating",
                progress=10,
                message="Validating omni references.",
            )
            saved_references: list[Path] = []
            for index, source_value in enumerate(reference_paths, start=1):
                source = Path(source_value)
                if not source.is_file():
                    raise FileNotFoundError(f"Reference image not found: {source}")
                suffix = source.suffix.lower() or ".png"
                destination = references_dir / f"reference-{index:02d}{suffix}"
                shutil.copy2(source, destination)
                saved_references.append(destination)

            reference_assets = tuple(
                ReferenceAsset(
                    index=directive.index,
                    tag=directive.tag,
                    path=path,
                    strength=directive.strength,
                )
                for directive, path in zip(directives, saved_references, strict=True)
            )

            artifacts = JobArtifacts(
                job_id=job_id,
                directory=job_dir,
                start_frame=job_dir / "start_frame.png",
                shot_spec=job_dir / "shot_spec.json",
                references=tuple(saved_references),
            )
            prepare_reference_start_frame(
                reference_path=artifacts.references[0],
                spec=spec,
                output_path=artifacts.start_frame,
            )

            payload = {
                "job_id": job_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "shot": spec.model_dump(mode="json"),
                "assets": {
                    "reference_images": [
                        str(path.resolve()) for path in artifacts.references
                    ],
                    "references": [
                        {
                            "index": asset.index,
                            "tag": asset.tag,
                            "strength": asset.strength,
                            "path": str(asset.path.resolve()),
                        }
                        for asset in reference_assets
                    ],
                    "primary_reference": str(artifacts.references[0].resolve()),
                    "start_frame": str(artifacts.start_frame.resolve()),
                },
            }
            artifacts.shot_spec.write_text(
                json.dumps(payload, indent=2),
                encoding="utf-8",
            )
            repository_assets = [
                JobAssetRecord(
                    kind="reference",
                    image_tag=asset.tag,
                    ordinal=asset.index,
                    object_key=self.artifact_store.object_key(
                        job_id, asset.path.relative_to(job_dir).as_posix()
                    ),
                    content_type=self._content_type(asset.path),
                    size_bytes=asset.path.stat().st_size,
                    metadata={"strength": asset.strength},
                )
                for asset in reference_assets
            ]
            repository_assets.extend(
                [
                    JobAssetRecord(
                        kind="manifest",
                        object_key=self.artifact_store.object_key(
                            job_id, "shot_spec.json"
                        ),
                        content_type="application/json",
                        size_bytes=artifacts.shot_spec.stat().st_size,
                    ),
                    JobAssetRecord(
                        kind="conditioning",
                        object_key=self.artifact_store.object_key(
                            job_id, "start_frame.png"
                        ),
                        content_type="image/png",
                        size_bytes=artifacts.start_frame.stat().st_size,
                    ),
                ]
            )
            self.job_repository.add_assets(job_id, repository_assets)
            self.job_repository.transition(
                job_id,
                status="rendering",
                progress=25,
                message="Render worker started.",
            )

            result = self.worker.render(
                RenderJob(
                    job_id=job_id,
                    output_dir=job_dir,
                    start_frame=artifacts.start_frame,
                    spec=spec,
                    reference_images=artifacts.references,
                    reference_assets=reference_assets,
                )
            )
            status = self._result_status(result)
            output_key = None
            if result.video:
                output_key = self.artifact_store.object_key(job_id, "preview.mp4")
                self.job_repository.add_assets(
                    job_id,
                    [
                        JobAssetRecord(
                            kind="preview",
                            object_key=output_key,
                            content_type="video/mp4",
                            size_bytes=result.video.stat().st_size,
                        )
                    ],
                )
            self.job_repository.transition(
                job_id,
                status=status,
                progress=100,
                message=result.message,
                output_object_key=output_key,
                error_message=result.message if status == "failed" else None,
            )
            self.artifact_store.publish_job(job_id, job_dir)
            return result
        except Exception as exc:
            self._record_failure(job_id, exc)
            raise
