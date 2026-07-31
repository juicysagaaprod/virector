import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from virector.config import Settings
from virector.models.shot_spec import ShotSpec
from virector.services.compositor import compose_start_frame
from virector.workers.base import RenderJob, RenderResult, VideoWorker


@dataclass(frozen=True)
class JobArtifacts:
    job_id: str
    directory: Path
    start_frame: Path
    shot_spec: Path


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
            )
        )

