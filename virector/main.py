from contextlib import asynccontextmanager

import gradio as gr
from fastapi import FastAPI

from virector.api import build_api
from virector.config import get_settings
from virector.services.jobs import JobService
from virector.ui.studio import create_studio
from virector.workers.factory import create_worker


settings = get_settings()
worker = create_worker(settings)
job_service = JobService(settings=settings, worker=worker)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.ensure_directories()
    yield


app = FastAPI(
    title="Virector API",
    version="0.1.0",
    description="Director-control API for local and cloud AI video workers.",
    lifespan=lifespan,
)
app.include_router(build_api(job_service))
app = gr.mount_gradio_app(app, create_studio(job_service), path="/studio")


@app.get("/")
def root() -> dict[str, str]:
    return {
        "name": "Virector",
        "studio": "/studio",
        "docs": "/docs",
        "health": "/api/health",
    }
