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
from virector.services.references import build_reference_directives
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
    def __init__(self, settings: Settings, worker: VideoWorker) -> None:
        self.settings = settings
        self.worker = worker

    def create(
        self,
        character_path: str | Path,
        world_path: str | Path,
        spec: ShotSpec,
    ) -> RenderResult:
        job_id = uuid4().hex
        job_dir = self.settings.outputs_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=False)

        artifacts = JobArtifacts(
            job_id=job_id,
            directory=job_dir,
            start_frame=job_dir / "start_frame.png",
            shot_spec=job_dir / "shot_spec.json",
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

        return self.worker.render(
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

    def create_from_references(
        self,
        reference_paths: list[str | Path],
        spec: ShotSpec,
        reference_directives: list[ReferenceDirective] | None = None,
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

        job_id = uuid4().hex
        job_dir = self.settings.outputs_dir / job_id
        references_dir = job_dir / "references"
        references_dir.mkdir(parents=True, exist_ok=False)

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

        return self.worker.render(
            RenderJob(
                job_id=job_id,
                output_dir=job_dir,
                start_frame=artifacts.start_frame,
                spec=spec,
                reference_images=artifacts.references,
                reference_assets=reference_assets,
            )
        )
