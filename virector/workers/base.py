from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from virector.models.shot_spec import ShotSpec


@dataclass(frozen=True)
class RenderJob:
    job_id: str
    output_dir: Path
    start_frame: Path
    spec: ShotSpec
    reference_images: tuple[Path, ...] = ()


@dataclass(frozen=True)
class RenderResult:
    job_id: str
    status: str
    start_frame: Path
    video: Path | None = None
    message: str = ""


class VideoWorker(ABC):
    mode: str
    requested_mode: str
    fallback_reason: str | None = None

    @abstractmethod
    def render(self, job: RenderJob) -> RenderResult:
        """Render one job and return stable output metadata."""
