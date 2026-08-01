import logging
import shutil
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    UploadFile,
)
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import ValidationError

from virector.models.director_plan import DirectorPlan, DirectorPlanRequest
from virector.models.shot_spec import (
    RESOLUTION_PRESETS,
    AspectRatio,
    OutputResolution,
    ShotSpec,
)
from virector.services.auth import (
    AuthenticatedUser,
    AuthenticationError,
    TokenVerifier,
    create_token_verifier,
)
from virector.services.director_plan import compile_director_plan
from virector.services.job_repository import JobIdentity, JobRepositoryError
from virector.services.jobs import JobService
from virector.services.references import (
    build_reference_directives,
    validate_prompt_reference_tags,
)
from virector.services.storage import ArtifactStorageError

logger = logging.getLogger(__name__)


def build_api(
    job_service: JobService,
    token_verifier: TokenVerifier | None = None,
) -> APIRouter:
    job_service.settings.validate_auth_configuration()
    verifier = token_verifier or create_token_verifier(job_service.settings)
    router = APIRouter(prefix="/api")

    def authenticate(
        authorization: str | None = Header(default=None),
    ) -> AuthenticatedUser | None:
        authentication_required = (
            job_service.settings.auth_required
            or job_service.job_repository.requires_identity
        )
        if authorization is None:
            if authentication_required:
                raise HTTPException(
                    status_code=401,
                    detail="Authentication is required.",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            return None
        scheme, separator, token = authorization.partition(" ")
        if separator != " " or scheme.lower() != "bearer" or not token.strip():
            raise HTTPException(
                status_code=401,
                detail="Use a valid Bearer authorization header.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if verifier is None:
            raise HTTPException(
                status_code=503,
                detail="Authentication is not configured on this server.",
            )
        try:
            return verifier.verify(token.strip())
        except AuthenticationError as exc:
            raise HTTPException(
                status_code=401,
                detail=str(exc),
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc

    def job_identity(
        user: AuthenticatedUser | None,
        project_id: str | None,
    ) -> JobIdentity | None:
        if user is None:
            return None
        if not project_id:
            raise HTTPException(
                status_code=422,
                detail="Select a project before creating a render.",
            )
        try:
            normalized_project_id = str(UUID(project_id))
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail="project_id must be a valid UUID.",
            ) from exc
        return JobIdentity(
            owner_id=user.user_id,
            project_id=normalized_project_id,
        )

    def valid_job_id(job_id: str) -> bool:
        return len(job_id) == 32 and all(
            character in "0123456789abcdef" for character in job_id
        )

    @router.get("/health")
    def health() -> dict[str, str | bool]:
        worker = job_service.worker
        payload = {
            "status": "ok",
            "worker": worker.__class__.__name__,
            "worker_mode": worker.mode,
            "requested_worker_mode": worker.requested_mode,
            "job_repository": job_service.job_repository.backend,
            "auth_required": job_service.settings.auth_required,
        }
        if worker.fallback_reason:
            payload["fallback_reason"] = worker.fallback_reason
        return payload

    @router.post("/director-plans/preview", response_model=DirectorPlan)
    def preview_director_plan(
        request: DirectorPlanRequest,
        _user: AuthenticatedUser | None = Depends(authenticate),
    ) -> DirectorPlan:
        try:
            return compile_director_plan(request.direction_prompt)
        except (ValidationError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/renders", status_code=202)
    async def create_render(
        background_tasks: BackgroundTasks,
        reference_images: list[UploadFile] = File(...),
        direction_prompt: str = Form(...),
        title: str = Form("Untitled shot"),
        video_model: str = Form("ltx-video-2b-distilled"),
        aspect_ratio: AspectRatio = Form(AspectRatio.portrait),
        output_resolution: OutputResolution = Form(OutputResolution.preview),
        duration_seconds: float = Form(4.0),
        seed: int = Form(42),
        project_id: str | None = Form(None),
        user: AuthenticatedUser | None = Depends(authenticate),
    ) -> dict[str, str | None]:
        identity = job_identity(user, project_id)
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

        job_id = uuid4().hex
        queued_uploads = job_service.settings.uploads_dir / "queued" / job_id
        try:
            queued_uploads.mkdir(parents=True, exist_ok=False)
            reference_paths: list[Path] = []
            for index, upload in enumerate(reference_images, start=1):
                suffix = Path(upload.filename or f"image-{index}.png").suffix
                queued_path = queued_uploads / (
                    f"reference-{index:02d}{suffix or '.png'}"
                )
                queued_path.write_bytes(await upload.read())
                reference_paths.append(queued_path)
        except OSError as exc:
            shutil.rmtree(queued_uploads, ignore_errors=True)
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        def run_render() -> None:
            try:
                job_service.create_from_references(
                    reference_paths=reference_paths,
                    spec=spec,
                    reference_directives=directives,
                    identity=identity,
                    job_id=job_id,
                )
            except Exception:
                logger.exception("Background render %s failed", job_id)
            finally:
                shutil.rmtree(queued_uploads, ignore_errors=True)

        background_tasks.add_task(run_render)
        return {
            "job_id": job_id,
            "status": "queued",
            "video_url": None,
            "message": "Render queued.",
        }

    @router.get("/renders/{job_id}")
    def get_render_status(
        job_id: str,
        user: AuthenticatedUser | None = Depends(authenticate),
    ) -> dict[str, str | int | None]:
        if not valid_job_id(job_id):
            raise HTTPException(status_code=404, detail="Render not found.")
        try:
            record = job_service.job_repository.get_status(
                job_id,
                user.user_id if user else None,
            )
        except JobRepositoryError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if record is None:
            raise HTTPException(status_code=404, detail="Render not found.")
        return {
            "job_id": record.job_id,
            "status": record.status,
            "progress": record.progress,
            "message": record.message,
            "error": record.error_message,
            "video_url": (
                f"/api/renders/{record.job_id}/video"
                if record.status == "completed" and record.output_object_key
                else None
            ),
        }

    @router.get("/renders/{job_id}/video")
    def get_render_video(
        job_id: str,
        user: AuthenticatedUser | None = Depends(authenticate),
    ) -> Response:
        if not valid_job_id(job_id):
            raise HTTPException(status_code=404, detail="Render not found.")
        if user is not None:
            try:
                is_owner = job_service.job_repository.is_owned_by(
                    job_id, user.user_id
                )
            except JobRepositoryError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            if not is_owner:
                raise HTTPException(status_code=404, detail="Render not found.")
        try:
            location = job_service.artifact_store.get_video_location(job_id)
        except ArtifactStorageError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except JobRepositoryError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
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
        project_id: str | None = Form(None),
        user: AuthenticatedUser | None = Depends(authenticate),
    ) -> dict[str, str | None]:
        identity = job_identity(user, project_id)
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
                        identity=identity,
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
