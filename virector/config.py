from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings shared by local and cloud deployments."""

    data_dir: Path = Path("./data")
    models_dir_override: Path | None = Field(
        default=None,
        validation_alias="VIRECTOR_MODELS_DIR",
        repr=False,
    )
    cache_dir_override: Path | None = Field(
        default=None,
        validation_alias="VIRECTOR_CACHE_DIR",
        repr=False,
    )
    outputs_dir_override: Path | None = Field(
        default=None,
        validation_alias="VIRECTOR_OUTPUTS_DIR",
        repr=False,
    )
    worker_mode: Literal["mock", "ltx", "vace"] = "mock"
    ltx_model_repo: str = "Lightricks/LTX-Video"
    ltx_checkpoint_filename: str = "ltxv-2b-0.9.8-distilled.safetensors"
    ltx_inference_steps: int = Field(default=8, ge=1, le=50)
    ltx_max_frames: int = Field(default=97, ge=9, le=257)
    ltx_text_encoder_4bit: bool = True
    ltx_cpu_offload: bool = True
    vace_model_name: str = "Wan2.1-VACE-1.3B"
    vace_model_repo: str = "Wan-AI/Wan2.1-VACE-1.3B-diffusers"
    vace_repo_dir: Path | None = None
    vace_checkpoint_dir: Path | None = None
    vace_inference_steps: int = Field(default=8, ge=1, le=50)
    vace_guidance_scale: float = Field(default=5.0, ge=1.0, le=20.0)
    vace_fps: int = Field(default=16, ge=8, le=24)
    vace_max_frames: int = Field(default=81, ge=5, le=81)
    vace_quantize_4bit: bool = True
    vace_cpu_offload: bool = True
    vace_allow_download: bool = False
    vace_ignore_preflight: bool = False
    cors_origins: str = "http://localhost:3000"
    host: str = "0.0.0.0"
    port: int = 8000

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="VIRECTOR_",
        extra="ignore",
    )

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def outputs_dir(self) -> Path:
        return self.outputs_dir_override or self.data_dir / "outputs"

    @property
    def cache_dir(self) -> Path:
        return self.cache_dir_override or self.data_dir / "cache"

    @property
    def models_dir(self) -> Path:
        return self.models_dir_override or self.data_dir / "models"

    @property
    def vace_checkpoint_path(self) -> Path:
        return self.vace_checkpoint_dir or (
            self.models_dir / "Wan2.1-VACE-1.3B-diffusers"
        )

    @property
    def allowed_cors_origins(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.cors_origins.split(",")
            if origin.strip()
        ]

    def ensure_directories(self) -> None:
        for path in (
            self.data_dir,
            self.uploads_dir,
            self.outputs_dir,
            self.cache_dir,
            self.models_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
