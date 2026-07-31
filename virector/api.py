from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import ValidationError

from virector.models.shot_spec import (
    RESOLUTION_PRESETS,
    AspectRatio,
    OutputResolution,
    ShotSpec,
)
from virector.services.jobs import JobService
from virector.services.references import (
    build_reference_directives,
    validate_prompt_reference_tags,
)
from virector.services.storage import ArtifactStorageError


def build_api(job_service: JobService) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/health")
    def health() -> dict[str, str]:
        worker = job_service.worker
        payload = {
            "status": "ok",
            "worker": worker.__class__.__name__,
            "worker_mode": worker.mode,
            "requested_worker_mode": worker.requested_mode,
        }
        if worker.fallback_reason:
            payload["fallback_reason"] = worker.fallback_reason
        return payload

    @router.post("/renders")
    async def create_render(
        reference_images: list[UploadFile] = File(...),
        direction_prompt: str = Form(...),
        title: str = Form("Untitled shot"),
        video_model: str = Form("ltx-video-2b-distilled"),
        aspect_ratio: AspectRatio = Form(AspectRatio.portrait),
        output_resolution: OutputResolution = Form(OutputResolution.preview),
        duration_seconds: float = Form(4.0),
        seed: int = Form(42),
    ) -> dict[str, str | None]:
        try:
            directives = build_reference_directives(len(reference_images))
            validate_prompt_reference_tags(direction_prompt, len(reference_images))
            width, height = RESOLUTION_PRESETS[aspect_ratio]
            spec = ShotSpec(
                title=title or "Untitled shot",
                prompt=direction_prompt.strip(),
                video_model=video_model,
                reference_mode="omni",
                references=directives,
                aspect_ratio=aspect_ratio,
                output_resolution=output_resolution,
                width=width,
                height=height,
                duration_seconds=duration_seconds,
                seed=seed,
            )
        except (ValidationError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        try:
            with TemporaryDirectory() as temporary_directory:
                reference_paths: list[Path] = []
                for index, upload in enumerate(reference_images, start=1):
                    suffix = Path(upload.filename or f"image-{index}.png").suffix
                    temporary_path = Path(temporary_directory) / (
                        f"reference-{index:02d}{suffix or '.png'}"
                    )
                    temporary_path.write_bytes(await upload.read())
                    reference_paths.append(temporary_path)

                result = job_service.create_from_references(
                    reference_paths=reference_paths,
                    spec=spec,
                    reference_directives=directives,
                )
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ArtifactStorageError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        video_url = None
        if result.video:
            video_url = f"/api/renders/{result.job_id}/video"
        return {
            "job_id": result.job_id,
            "status": result.status,
            "video_url": video_url,
            "message": result.message,
        }

    @router.get("/renders/{job_id}/video")
    def get_render_video(job_id: str) -> Response:
        if len(job_id) != 32 or any(
            character not in "0123456789abcdef" for character in job_id
        ):
            raise HTTPException(status_code=404, detail="Render not found.")
        try:
            location = job_service.artifact_store.get_video_location(job_id)
        except ArtifactStorageError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if location is None:
            raise HTTPException(status_code=404, detail="Render video not found.")
        if location.path is not None:
            return FileResponse(
                location.path,
                media_type="video/mp4",
                filename="preview.mp4",
            )
        assert location.url is not None
        return RedirectResponse(location.url, status_code=307)

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
        except ArtifactStorageError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        return {
            "job_id": result.job_id,
            "status": result.status,
            "video": str(result.video) if result.video else None,
            "message": result.message,
        }

    return router
