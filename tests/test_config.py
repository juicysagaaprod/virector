from pathlib import Path

import pytest
from pytest import MonkeyPatch

from virector.config import Settings
from virector.services.job_repository import create_job_repository

STORAGE_ENV_VARS = (
    "VIRECTOR_MODELS_DIR",
    "VIRECTOR_CACHE_DIR",
    "VIRECTOR_OUTPUTS_DIR",
)


def test_storage_defaults_are_derived_from_data_dir(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    for name in STORAGE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None, data_dir=tmp_path)

    assert settings.models_dir == tmp_path / "models"
    assert settings.cache_dir == tmp_path / "cache"
    assert settings.outputs_dir == tmp_path / "outputs"
    assert settings.uploads_dir == tmp_path / "uploads"


def test_storage_overrides_are_created(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    models = tmp_path / "persistent-models"
    cache = tmp_path / "persistent-cache"
    outputs = tmp_path / "persistent-outputs"
    monkeypatch.setenv("VIRECTOR_MODELS_DIR", str(models))
    monkeypatch.setenv("VIRECTOR_CACHE_DIR", str(cache))
    monkeypatch.setenv("VIRECTOR_OUTPUTS_DIR", str(outputs))

    settings = Settings(_env_file=None, data_dir=tmp_path / "data")
    settings.ensure_directories()

    assert settings.models_dir == models
    assert settings.cache_dir == cache
    assert settings.outputs_dir == outputs
    assert models.is_dir()
    assert cache.is_dir()
    assert outputs.is_dir()


def test_cors_origins_are_split_and_trimmed(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        cors_origins="http://localhost:3000, https://virector.example.com ",
    )

    assert settings.allowed_cors_origins == [
        "http://localhost:3000",
        "https://virector.example.com",
    ]


def test_vace_checkpoint_defaults_to_persistent_models_dir(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.delenv("VIRECTOR_MODELS_DIR", raising=False)
    monkeypatch.delenv("VIRECTOR_VACE_CHECKPOINT_DIR", raising=False)
    settings = Settings(_env_file=None, data_dir=tmp_path)

    assert settings.vace_checkpoint_path == (
        tmp_path / "models" / "Wan2.1-VACE-1.3B-diffusers"
    )


def test_postgres_repository_requires_database_url(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        job_repository_backend="postgres",
    )

    with pytest.raises(ValueError, match="VIRECTOR_DATABASE_URL"):
        create_job_repository(settings)


def test_database_pool_minimum_cannot_exceed_maximum(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        job_repository_backend="postgres",
        database_url="postgresql://example.invalid/virector",
        database_pool_min_size=6,
        database_pool_max_size=5,
    )

    with pytest.raises(ValueError, match="POOL_MIN_SIZE"):
        create_job_repository(settings)


def test_required_auth_requires_supabase_url() -> None:
    settings = Settings(_env_file=None, auth_required=True, supabase_url=None)

    with pytest.raises(ValueError, match="VIRECTOR_SUPABASE_URL"):
        settings.validate_auth_configuration()
