from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings shared by local and cloud deployments."""

    data_dir: Path = Path("./data")
    environment: Literal["local", "staging", "production"] = "local"
    storage_backend: Literal["local", "s3"] = "local"
    job_repository_backend: Literal["local", "postgres"] = "local"
    s3_endpoint_url: str | None = None
    s3_region: str = "auto"
    s3_bucket: str | None = None
    s3_key_prefix: str = "virector"
    s3_access_key_id: SecretStr | None = Field(default=None, repr=False)
    s3_secret_access_key: SecretStr | None = Field(default=None, repr=False)
    s3_presigned_url_ttl_seconds: int = Field(default=900, ge=60, le=86400)
    database_url: SecretStr | None = Field(default=None, repr=False)
    database_pool_min_size: int = Field(default=1, ge=1, le=20)
    database_pool_max_size: int = Field(default=5, ge=1, le=50)
    database_pool_timeout_seconds: float = Field(default=10.0, ge=1.0, le=60.0)
    supabase_url: str | None = None
    supabase_publishable_key: str | None = Field(default=None, repr=False)
    supabase_jwt_audience: str = "authenticated"
    auth_required: bool = False
    enable_studio: bool = True
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
    worker_mode: Literal["mock", "ltx", "vace", "performance", "runpod"] = "mock"
    runpod_endpoint_id: str | None = None
    runpod_api_key: SecretStr | None = Field(default=None, repr=False)
    runpod_api_base_url: str = "https://api.runpod.ai/v2"
    runpod_request_timeout_seconds: float = Field(default=30.0, ge=5.0, le=120.0)
    runpod_poll_interval_seconds: float = Field(default=3.0, ge=0.1, le=30.0)
    runpod_job_timeout_seconds: int = Field(default=7200, ge=60, le=21600)
    performance_segment_worker: Literal["ltx", "vace"] = "vace"
    performance_motion_backend: Literal["disabled", "wan-animate"] = "disabled"
    performance_speech_backend: Literal[
        "disabled", "infinitetalk", "hunyuan-avatar"
    ] = "disabled"
    performance_audio_backend: Literal["disabled", "ffmpeg"] = "disabled"
    wan_animate_repo_dir: Path = Path("/opt/Wan2.2")
    wan_animate_checkpoint_dir: Path | None = None
    wan_animate_python: str = "/opt/wan-animate-venv/bin/python"
    wan_animate_model_repo: str = "Wan-AI/Wan2.2-Animate-14B"
    wan_animate_inference_steps: int = Field(default=20, ge=1, le=50)
    wan_animate_timeout_seconds: int = Field(default=7200, ge=300, le=21600)
    wan_animate_allow_download: bool = False
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
    def wan_animate_checkpoint_path(self) -> Path:
        return self.wan_animate_checkpoint_dir or (
            self.models_dir / "Wan2.2-Animate-14B"
        )

    @property
    def allowed_cors_origins(self) -> list[str]:
        return [
            origin.strip() for origin in self.cors_origins.split(",") if origin.strip()
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

    def validate_cloud_configuration(self) -> None:
        """Reject partial cloud configuration before accepting render jobs."""

        if self.storage_backend != "s3":
            return

        required = {
            "VIRECTOR_S3_ENDPOINT_URL": self.s3_endpoint_url,
            "VIRECTOR_S3_BUCKET": self.s3_bucket,
            "VIRECTOR_S3_ACCESS_KEY_ID": self.s3_access_key_id,
            "VIRECTOR_S3_SECRET_ACCESS_KEY": self.s3_secret_access_key,
        }
        missing = []
        for name, value in required.items():
            if isinstance(value, SecretStr):
                configured = bool(value.get_secret_value().strip())
            elif isinstance(value, str):
                configured = bool(value.strip())
            else:
                configured = value is not None
            if not configured:
                missing.append(name)
        if missing:
            raise ValueError(
                "S3-compatible storage is enabled but required settings are "
                f"missing: {', '.join(missing)}."
            )

    def validate_job_repository_configuration(self) -> None:
        if self.job_repository_backend != "postgres":
            return
        if (
            self.database_url is None
            or not self.database_url.get_secret_value().strip()
        ):
            raise ValueError(
                "Postgres job persistence is enabled but "
                "VIRECTOR_DATABASE_URL is missing."
            )
        if self.database_pool_min_size > self.database_pool_max_size:
            raise ValueError(
                "VIRECTOR_DATABASE_POOL_MIN_SIZE cannot exceed "
                "VIRECTOR_DATABASE_POOL_MAX_SIZE."
            )

    def validate_auth_configuration(self) -> None:
        if not self.auth_required:
            return
        if not self.supabase_url or not self.supabase_url.strip():
            raise ValueError(
                "Authentication is required but VIRECTOR_SUPABASE_URL is missing."
            )

    def validate_runpod_configuration(self) -> None:
        if self.worker_mode != "runpod":
            return
        self.validate_cloud_configuration()
        if self.storage_backend != "s3":
            raise ValueError(
                "RunPod rendering requires VIRECTOR_STORAGE_BACKEND=s3."
            )
        required = {
            "VIRECTOR_RUNPOD_ENDPOINT_ID": self.runpod_endpoint_id,
            "VIRECTOR_RUNPOD_API_KEY": self.runpod_api_key,
        }
        missing = []
        for name, value in required.items():
            if isinstance(value, SecretStr):
                configured = bool(value.get_secret_value().strip())
            elif isinstance(value, str):
                configured = bool(value.strip())
            else:
                configured = value is not None
            if not configured:
                missing.append(name)
        if missing:
            raise ValueError(
                "RunPod rendering is enabled but required settings are missing: "
                f"{', '.join(missing)}."
            )
        minimum_ttl = self.runpod_job_timeout_seconds + 300
        if self.s3_presigned_url_ttl_seconds < minimum_ttl:
            raise ValueError(
                "VIRECTOR_S3_PRESIGNED_URL_TTL_SECONDS must be at least "
                f"{minimum_ttl} for the configured RunPod job timeout."
            )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
