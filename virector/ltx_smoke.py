import argparse
import json
from pathlib import Path

from virector.config import Settings
from virector.models.shot_spec import ShotSpec
from virector.workers.base import RenderJob
from virector.workers.ltx import LtxWorker
from virector.workers.ltx_diffusers import DiffusersLtxBackend


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one local LTX smoke render.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--width", type=int, default=480)
    parser.add_argument("--height", type=int, default=832)
    parser.add_argument("--duration", type=float, default=4.0)
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--prompt",
        default="The scene moves subtly with a slow cinematic dolly in.",
    )
    args = parser.parse_args()

    settings = Settings()
    output_dir = args.output_dir or settings.outputs_dir / "ltx-smoke"
    output_dir.mkdir(parents=True, exist_ok=True)
    spec = ShotSpec(
        title="LTX runtime smoke test",
        prompt=args.prompt,
        width=args.width,
        height=args.height,
        duration_seconds=args.duration,
        fps=args.fps,
        seed=args.seed,
    )
    result = LtxWorker(DiffusersLtxBackend(settings)).render(
        RenderJob(
            job_id="ltx-smoke",
            output_dir=output_dir,
            start_frame=args.input,
            spec=spec,
        )
    )
    print(
        json.dumps(
            {
                "job_id": result.job_id,
                "status": result.status,
                "start_frame": str(result.start_frame),
                "video": str(result.video) if result.video else None,
                "message": result.message,
            }
        )
    )
    return 0 if result.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
