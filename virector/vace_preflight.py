import argparse

from virector.config import get_settings
from virector.workers.vace_diffusers import probe_vace_hardware


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect VACE hardware readiness.")
    parser.add_argument(
        "--download",
        action="store_true",
        help="Download the official checkpoint only when preflight passes.",
    )
    args = parser.parse_args()
    settings = get_settings()
    report = probe_vace_hardware(settings)
    print(report.to_json())

    if not report.supported:
        return 2
    if args.download:
        from huggingface_hub import snapshot_download

        snapshot_download(
            repo_id=settings.vace_model_repo,
            local_dir=settings.vace_checkpoint_path,
            cache_dir=settings.cache_dir / "huggingface",
        )
        print(f"VACE checkpoint downloaded to {settings.vace_checkpoint_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
