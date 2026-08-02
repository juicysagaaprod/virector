from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from virector.api import build_api
from virector.config import get_settings
from virector.services.jobs import JobService
from virector.workers.factory import create_worker

settings = get_settings()
worker = create_worker(settings)
job_service = JobService(settings=settings, worker=worker)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.ensure_directories()
    job_service.healthcheck()
    try:
        yield
    finally:
        job_service.close()


app = FastAPI(
    title="Virector API",
    version="0.1.0",
    description="Director-control API for local and cloud AI video workers.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(build_api(job_service))
if settings.enable_studio:
    import gradio as gr

    from virector.ui.studio import create_studio

    app = gr.mount_gradio_app(app, create_studio(job_service), path="/studio")


@app.get("/")
def root() -> dict[str, str]:
    links = {
        "name": "Virector",
        "docs": "/docs",
        "health": "/api/health",
    }
    if settings.enable_studio:
        links["studio"] = "/studio"
    return links
