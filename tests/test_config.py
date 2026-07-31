from pathlib import Path

from pytest import MonkeyPatch

from virector.config import Settings


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
