from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from virector.models.omni_asset import OmniMediaType
from virector.models.shot_spec import ShotSpec


@dataclass(frozen=True)
class ReferenceAsset:
    index: int
    tag: str
    path: Path
    media_type: OmniMediaType = OmniMediaType.image
    strength: float = 0.9


@dataclass(frozen=True)
class RenderJob:
    job_id: str
    output_dir: Path
    start_frame: Path
    spec: ShotSpec
    reference_images: tuple[Path, ...] = ()
    reference_videos: tuple[Path, ...] = ()
    reference_audio: tuple[Path, ...] = ()
    reference_assets: tuple[ReferenceAsset, ...] = ()
    progress_callback: Callable[[int, str], None] | None = None


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
