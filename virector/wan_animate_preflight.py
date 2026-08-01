import argparse
import json
import shutil
from pathlib import Path

import psutil

from virector.config import get_settings


def hardware_report(
    *,
    vram_gb: float,
    ram_gb: float,
    storage_gb: float,
    runtime_ready: bool,
) -> dict[str, object]:
    blockers = []
    warnings = []
    if vram_gb < 40:
        blockers.append(
            "Wan2.2-Animate is cloud-only in Virector and requires at least "
            "40 GB visible GPU memory for the guarded worker profile."
        )
    elif vram_gb < 80:
        warnings.append(
            "Less than 80 GB GPU memory is visible; CPU offload may be slow or "
            "still run out of memory."
        )
    if ram_gb < 48:
        blockers.append("At least 48 GB worker RAM is required for model offload.")
    if storage_gb < 140:
        blockers.append(
            "At least 140 GB free persistent storage is required before download."
        )
    if not runtime_ready:
        blockers.append("The isolated official Wan2.2 runtime is not installed.")
    return {
        "supported": not blockers,
        "vram_gb": round(vram_gb, 2),
        "ram_gb": round(ram_gb, 2),
        "storage_gb": round(storage_gb, 2),
        "runtime_ready": runtime_ready,
        "blockers": blockers,
        "warnings": warnings,
    }


def probe() -> dict[str, object]:
    settings = get_settings()
    try:
        import torch

        vram_gb = (
            torch.cuda.get_device_properties(0).total_memory / 1024**3
            if torch.cuda.is_available()
            else 0.0
        )
    except (ImportError, RuntimeError):
        vram_gb = 0.0
    ram_gb = psutil.virtual_memory().total / 1024**3
    storage_target = settings.models_dir
    existing = storage_target
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    storage_gb = shutil.disk_usage(existing).free / 1024**3
    runtime_ready = all(
        path.is_file()
        for path in (
            Path(settings.wan_animate_python),
            settings.wan_animate_repo_dir / "generate.py",
            settings.wan_animate_repo_dir
            / "wan/modules/animate/preprocess/preprocess_data.py",
        )
    )
    return hardware_report(
        vram_gb=vram_gb,
        ram_gb=ram_gb,
        storage_gb=storage_gb,
        runtime_ready=runtime_ready,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect Wan2.2-Animate cloud worker readiness."
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="Download weights only after the guarded hardware checks pass.",
    )
    args = parser.parse_args()
    report = probe()
    print(json.dumps(report, indent=2))
    if not report["supported"]:
        return 2
    if args.download:
        from huggingface_hub import snapshot_download

        settings = get_settings()
        snapshot_download(
            repo_id=settings.wan_animate_model_repo,
            local_dir=settings.wan_animate_checkpoint_path,
            cache_dir=settings.cache_dir / "huggingface",
        )
        print(
            "Wan2.2-Animate checkpoint downloaded to "
            f"{settings.wan_animate_checkpoint_path}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
