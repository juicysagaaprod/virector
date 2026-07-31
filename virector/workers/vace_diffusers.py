import importlib.util
import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from virector.config import Settings
from virector.models.shot_spec import (
    OUTPUT_RESOLUTION_PRESETS,
    OutputResolution,
)
from virector.workers.base import RenderJob
from virector.workers.ltx_diffusers import _upscale_video
from virector.workers.vace import VaceBackend, VaceWorkerUnavailableError


VACE_DOWNLOAD_GB = 19.04
MINIMUM_GPU_GB = 7.5
MINIMUM_RUNTIME_RAM_GB = 10.0
RECOMMENDED_RUNTIME_RAM_GB = 24.0


@dataclass(frozen=True)
class VaceHardwareReport:
    cuda_available: bool
    gpu_name: str | None
    gpu_total_gb: float
    gpu_free_gb: float
    system_total_gb: float
    system_available_gb: float
    disk_free_gb: float
    checkpoint_present: bool
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def supported(self) -> bool:
        return not self.blockers

    def to_json(self) -> str:
        return json.dumps({**asdict(self), "supported": self.supported}, indent=2)


def evaluate_vace_hardware(
    *,
    cuda_available: bool,
    gpu_name: str | None,
    gpu_total_gb: float,
    gpu_free_gb: float,
    system_total_gb: float,
    system_available_gb: float,
    disk_free_gb: float,
    checkpoint_present: bool,
) -> VaceHardwareReport:
    blockers: list[str] = []
    warnings: list[str] = []

    if not cuda_available:
        blockers.append("A CUDA GPU is required for VACE inference.")
    elif gpu_total_gb < MINIMUM_GPU_GB:
        blockers.append(
            f"VACE requires at least {MINIMUM_GPU_GB:g}GB total VRAM; "
            f"only {gpu_total_gb:.2f}GB is visible."
        )
    elif gpu_free_gb < 6.0:
        warnings.append(
            f"Only {gpu_free_gb:.2f}GB VRAM is currently free. Close GPU-heavy "
            "applications before rendering."
        )

    if system_total_gb < MINIMUM_RUNTIME_RAM_GB:
        blockers.append(
            f"The VACE worker can see only {system_total_gb:.2f}GB system RAM; "
            f"at least {MINIMUM_RUNTIME_RAM_GB:g}GB is required for the guarded "
            "4-bit loading attempt."
        )
    elif system_total_gb < RECOMMENDED_RUNTIME_RAM_GB:
        warnings.append(
            f"Only {system_total_gb:.2f}GB system RAM is visible; "
            f"{RECOMMENDED_RUNTIME_RAM_GB:g}GB or more is recommended."
        )

    if system_available_gb < 6.0:
        warnings.append(
            f"Only {system_available_gb:.2f}GB system RAM is currently available."
        )

    if not checkpoint_present and disk_free_gb < VACE_DOWNLOAD_GB + 5.0:
        blockers.append(
            f"At least {VACE_DOWNLOAD_GB + 5.0:.0f}GB free model storage is "
            f"required; only {disk_free_gb:.2f}GB is available."
        )

    return VaceHardwareReport(
        cuda_available=cuda_available,
        gpu_name=gpu_name,
        gpu_total_gb=round(gpu_total_gb, 2),
        gpu_free_gb=round(gpu_free_gb, 2),
        system_total_gb=round(system_total_gb, 2),
        system_available_gb=round(system_available_gb, 2),
        disk_free_gb=round(disk_free_gb, 2),
        checkpoint_present=checkpoint_present,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
    )


def probe_vace_hardware(settings: Settings) -> VaceHardwareReport:
    storage_path = settings.models_dir
    while not storage_path.exists() and storage_path != storage_path.parent:
        storage_path = storage_path.parent
    try:
        import psutil
        import torch
    except ImportError:
        return evaluate_vace_hardware(
            cuda_available=False,
            gpu_name=None,
            gpu_total_gb=0,
            gpu_free_gb=0,
            system_total_gb=0,
            system_available_gb=0,
            disk_free_gb=shutil.disk_usage(storage_path).free / 1e9,
            checkpoint_present=(
                settings.vace_checkpoint_path / "model_index.json"
            ).is_file(),
        )

    cuda_available = torch.cuda.is_available()
    gpu_name = torch.cuda.get_device_name(0) if cuda_available else None
    if cuda_available:
        gpu_free, gpu_total = torch.cuda.mem_get_info(0)
    else:
        gpu_free = gpu_total = 0
    system_memory = psutil.virtual_memory()
    system_total = system_memory.total / 1e9
    system_available = system_memory.available / 1e9
    checkpoint_present = (settings.vace_checkpoint_path / "model_index.json").is_file()
    return evaluate_vace_hardware(
        cuda_available=cuda_available,
        gpu_name=gpu_name,
        gpu_total_gb=gpu_total / 1e9,
        gpu_free_gb=gpu_free / 1e9,
        system_total_gb=system_total,
        system_available_gb=system_available,
        disk_free_gb=shutil.disk_usage(storage_path).free / 1e9,
        checkpoint_present=checkpoint_present,
    )


def vace_frame_count(duration_seconds: float, fps: int, max_frames: int) -> int:
    """Return a Wan-compatible frame count in the form ``4n + 1``."""

    target_intervals = max(4, round(duration_seconds * fps / 4) * 4)
    frames = target_intervals + 1
    max_compatible_frames = ((max_frames - 1) // 4) * 4 + 1
    return min(frames, max_compatible_frames)


class DiffusersVaceBackend(VaceBackend):
    """Quantized, low-memory Wan VACE backend with ordered references."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._pipeline: Any | None = None

    def ensure_available(self) -> None:
        missing = [
            package
            for package in (
                "accelerate",
                "bitsandbytes",
                "diffusers",
                "huggingface_hub",
                "imageio",
                "torch",
                "transformers",
            )
            if importlib.util.find_spec(package) is None
        ]
        if missing:
            raise VaceWorkerUnavailableError(
                "The VACE runtime is missing packages: " + ", ".join(missing) + "."
            )

        report = probe_vace_hardware(self.settings)
        if report.blockers and not self.settings.vace_ignore_preflight:
            raise VaceWorkerUnavailableError(" ".join(report.blockers))
        if not report.checkpoint_present and not self.settings.vace_allow_download:
            raise VaceWorkerUnavailableError(
                "The VACE checkpoint is not downloaded. Run `python -m "
                "virector.vace_preflight --download` after preflight passes."
            )

    def _resolve_checkpoint(self) -> Path:
        checkpoint = self.settings.vace_checkpoint_path
        if (checkpoint / "model_index.json").is_file():
            return checkpoint
        if not self.settings.vace_allow_download:
            raise RuntimeError(
                "The 19GB VACE checkpoint is not downloaded. Run "
                "`python -m virector.vace_preflight --download` after the "
                "hardware preflight passes."
            )

        from huggingface_hub import snapshot_download

        snapshot_download(
            repo_id=self.settings.vace_model_repo,
            local_dir=checkpoint,
            cache_dir=self.settings.cache_dir / "huggingface",
        )
        return checkpoint

    def _load_pipeline(self):
        if self._pipeline is not None:
            return self._pipeline

        import torch
        from diffusers import (
            AutoencoderKLWan,
            BitsAndBytesConfig as DiffusersBitsAndBytesConfig,
            WanVACEPipeline,
        )
        from diffusers.quantizers import PipelineQuantizationConfig
        from diffusers.schedulers import UniPCMultistepScheduler
        from transformers import BitsAndBytesConfig as TransformersBitsAndBytesConfig

        checkpoint = self._resolve_checkpoint()
        quantization_config = None
        if self.settings.vace_quantize_4bit:
            quantization_config = PipelineQuantizationConfig(
                quant_mapping={
                    "transformer": DiffusersBitsAndBytesConfig(
                        load_in_4bit=True,
                        bnb_4bit_compute_dtype=torch.bfloat16,
                        bnb_4bit_quant_type="nf4",
                        bnb_4bit_use_double_quant=True,
                    ),
                    "text_encoder": TransformersBitsAndBytesConfig(
                        load_in_4bit=True,
                        bnb_4bit_compute_dtype=torch.bfloat16,
                        bnb_4bit_quant_type="nf4",
                        bnb_4bit_use_double_quant=True,
                    ),
                }
            )

        vae = AutoencoderKLWan.from_pretrained(
            checkpoint,
            subfolder="vae",
            torch_dtype=torch.float32,
            low_cpu_mem_usage=True,
        )
        pipeline = WanVACEPipeline.from_pretrained(
            checkpoint,
            vae=vae,
            quantization_config=quantization_config,
            torch_dtype=torch.bfloat16,
            low_cpu_mem_usage=True,
        )
        pipeline.scheduler = UniPCMultistepScheduler.from_config(
            pipeline.scheduler.config,
            flow_shift=3.0,
        )
        pipeline.vae.enable_tiling()
        if self.settings.vace_cpu_offload:
            pipeline.enable_sequential_cpu_offload()
        else:
            pipeline.to("cuda")
        self._pipeline = pipeline
        return pipeline

    def render(self, job: RenderJob) -> Path:
        self.ensure_available()
        if job.spec.duration_seconds > 5:
            raise RuntimeError(
                "The first local VACE milestone supports up to five seconds per "
                "segment. Continuation chaining is not connected yet."
            )

        import torch
        from diffusers.utils import export_to_video
        from PIL import Image

        pipeline = self._load_pipeline()
        references = []
        for asset in sorted(job.reference_assets, key=lambda item: item.index):
            with Image.open(asset.path) as source:
                references.append(source.convert("RGB"))

        frame_count = vace_frame_count(
            job.spec.duration_seconds,
            self.settings.vace_fps,
            self.settings.vace_max_frames,
        )
        generator = torch.Generator(device="cuda").manual_seed(job.spec.seed)
        result = pipeline(
            prompt=job.spec.prompt,
            negative_prompt=job.spec.negative_prompt,
            reference_images=references,
            height=job.spec.height,
            width=job.spec.width,
            num_frames=frame_count,
            num_inference_steps=self.settings.vace_inference_steps,
            guidance_scale=self.settings.vace_guidance_scale,
            generator=generator,
        )

        output = job.output_dir / "preview.mp4"
        if job.spec.output_resolution == OutputResolution.preview:
            export_to_video(result.frames[0], str(output), fps=self.settings.vace_fps)
            return output

        native_output = job.output_dir / "preview-native.mp4"
        export_to_video(
            result.frames[0],
            str(native_output),
            fps=self.settings.vace_fps,
        )
        output_width, output_height = OUTPUT_RESOLUTION_PRESETS[
            job.spec.output_resolution
        ][job.spec.aspect_ratio]
        _upscale_video(native_output, output, output_width, output_height)
        return output
