from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import ValidationError

from virector.models.shot_spec import ShotSpec
from virector.services.jobs import JobService


def build_api(job_service: JobService) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "worker": job_service.worker.__class__.__name__}

    @router.post("/compose")
    async def compose(
        character: UploadFile = File(...),
        world: UploadFile = File(...),
        shot_spec_json: str = Form(...),
    ) -> dict[str, str | None]:
        try:
            spec = ShotSpec.model_validate_json(shot_spec_json)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc

        character_suffix = Path(character.filename or "character.png").suffix or ".png"
        world_suffix = Path(world.filename or "world.png").suffix or ".png"

        try:
            with NamedTemporaryFile(suffix=character_suffix) as character_temp:
                character_temp.write(await character.read())
                character_temp.flush()

                with NamedTemporaryFile(suffix=world_suffix) as world_temp:
                    world_temp.write(await world.read())
                    world_temp.flush()

                    result = job_service.create(
                        character_path=character_temp.name,
                        world_path=world_temp.name,
                        spec=spec,
                    )
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return {
            "job_id": result.job_id,
            "status": result.status,
            "start_frame": str(result.start_frame),
            "video": str(result.video) if result.video else None,
            "message": result.message,
        }

    return router

