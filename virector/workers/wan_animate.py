import os
import shutil
import subprocess
from pathlib import Path
from typing import Protocol

from virector.config import Settings
from virector.models.conditioning import ConditioningRoute
from virector.models.omni_asset import OmniMediaType
from virector.workers.base import RenderJob


class WanAnimateUnavailableError(RuntimeError):
    """Raised when the isolated Wan2.2-Animate runtime is not ready."""


class WanAnimateBackend(Protocol):
    def ensure_available(self) -> None:
        """Validate the runtime and checkpoint without starting inference."""

    def render(
        self,
        job: RenderJob,
        source_video: Path,
        driving_video: Path,
        output_path: Path,
    ) -> Path:
        """Apply driving motion to the composed/base shot."""


class WanAnimateWorker:
    """Select motion assets and delegate the cloud-only animation pass."""

    mode = "wan-animate"

    def __init__(self, backend: WanAnimateBackend) -> None:
        self.backend = backend
        self.backend.ensure_available()

    def apply(
        self,
        job: RenderJob,
        source_video: Path,
        routes: list[ConditioningRoute],
    ) -> Path:
        route_tags = {
            tag
            for route in routes
            for tag in route.asset_tags
        }
        driving_asset = next(
            (
                asset
                for asset in job.reference_assets
                if asset.media_type == OmniMediaType.video
                and asset.tag in route_tags
            ),
            None,
        )
        if driving_asset is None:
            raise WanAnimateUnavailableError(
                "Wan2.2-Animate requires a tagged driving video for this shot."
            )
        output = job.output_dir / "wan-animate-preview.mp4"
        try:
            rendered = Path(
                self.backend.render(
                    job,
                    source_video,
                    driving_asset.path,
                    output,
                )
            )
        except WanAnimateUnavailableError:
            raise
        except Exception as exc:
            raise WanAnimateUnavailableError(
                f"Wan2.2-Animate inference failed: {exc}"
            ) from exc
        if not rendered.is_file():
            raise WanAnimateUnavailableError(
                "Wan2.2-Animate did not create its declared output: "
                f"{rendered}"
            )
        return rendered


class SubprocessWanAnimateBackend:
    """Run the official preprocessing and generation scripts in an isolated venv."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def preprocess_script(self) -> Path:
        return (
            self.settings.wan_animate_repo_dir
            / "wan/modules/animate/preprocess/preprocess_data.py"
        )

    @property
    def generate_script(self) -> Path:
        return self.settings.wan_animate_repo_dir / "generate.py"

    def _download_checkpoint(self) -> None:
        try:
            from huggingface_hub import snapshot_download
        except ImportError as exc:
            raise WanAnimateUnavailableError(
                "huggingface_hub is required to download Wan2.2-Animate."
            ) from exc
        checkpoint = self.settings.wan_animate_checkpoint_path
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        snapshot_download(
            repo_id=self.settings.wan_animate_model_repo,
            local_dir=checkpoint,
        )

    def _checkpoint_ready(self) -> bool:
        checkpoint = self.settings.wan_animate_checkpoint_path
        return all(
            path.exists()
            for path in (
                checkpoint / "config.json",
                checkpoint / "diffusion_pytorch_model.safetensors.index.json",
                checkpoint / "process_checkpoint",
            )
        )

    def ensure_available(self) -> None:
        python = Path(self.settings.wan_animate_python)
        missing = [
            path
            for path in (python, self.preprocess_script, self.generate_script)
            if not path.is_file()
        ]
        if missing:
            raise WanAnimateUnavailableError(
                "Wan2.2-Animate runtime files are missing: "
                + ", ".join(str(path) for path in missing)
            )
        checkpoint = self.settings.wan_animate_checkpoint_path
        if not self._checkpoint_ready() and self.settings.wan_animate_allow_download:
            self._download_checkpoint()
        if not self._checkpoint_ready():
            raise WanAnimateUnavailableError(
                "Wan2.2-Animate checkpoint is missing or incomplete at "
                f"{checkpoint}. Enable VIRECTOR_WAN_ANIMATE_ALLOW_DOWNLOAD only "
                "on a cloud worker with persistent storage."
            )

    @staticmethod
    def _ffmpeg() -> str:
        executable = shutil.which("ffmpeg")
        if executable:
            return executable
        try:
            import imageio_ffmpeg

            return imageio_ffmpeg.get_ffmpeg_exe()
        except (ImportError, RuntimeError) as exc:
            raise WanAnimateUnavailableError(
                "FFmpeg is required to extract the Wan animation reference frame."
            ) from exc

    def _run(self, command: list[str]) -> None:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(self.settings.wan_animate_repo_dir)
        try:
            completed = subprocess.run(
                command,
                cwd=self.settings.wan_animate_repo_dir,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=self.settings.wan_animate_timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise WanAnimateUnavailableError(
                f"Wan2.2-Animate command could not complete: {exc}"
            ) from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip().splitlines()
            message = detail[-1] if detail else "command returned no diagnostics"
            raise WanAnimateUnavailableError(
                f"Wan2.2-Animate command failed: {message}"
            )

    def render(
        self,
        job: RenderJob,
        source_video: Path,
        driving_video: Path,
        output_path: Path,
    ) -> Path:
        self.ensure_available()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        reference_frame = job.output_dir / "wan-animate-reference.png"
        self._run(
            [
                self._ffmpeg(),
                "-y",
                "-i",
                str(source_video),
                "-frames:v",
                "1",
                str(reference_frame),
            ]
        )
        if not reference_frame.is_file():
            raise WanAnimateUnavailableError(
                "Could not extract the base shot reference frame."
            )

        portrait = job.spec.height > job.spec.width
        width, height = (720, 1280) if portrait else (1280, 720)
        processed = job.output_dir / "wan-animate-processed"
        self._run(
            [
                self.settings.wan_animate_python,
                str(self.preprocess_script),
                "--ckpt_path",
                str(self.settings.wan_animate_checkpoint_path / "process_checkpoint"),
                "--video_path",
                str(driving_video),
                "--refer_path",
                str(reference_frame),
                "--save_path",
                str(processed),
                "--resolution_area",
                str(width),
                str(height),
                "--retarget_flag",
                "--use_flux",
            ]
        )
        self._run(
            [
                self.settings.wan_animate_python,
                str(self.generate_script),
                "--task",
                "animate-14B",
                "--size",
                f"{width}*{height}",
                "--ckpt_dir",
                str(self.settings.wan_animate_checkpoint_path),
                "--src_root_path",
                str(processed),
                "--refert_num",
                "1",
                "--offload_model",
                "True",
                "--convert_model_dtype",
                "--sample_steps",
                str(self.settings.wan_animate_inference_steps),
                "--base_seed",
                str(job.spec.seed),
                "--prompt",
                job.spec.prompt,
                "--save_file",
                str(output_path),
            ]
        )
        return output_path
