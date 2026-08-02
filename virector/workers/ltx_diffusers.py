import gc
import importlib.util
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from virector.config import Settings
from virector.models.shot_spec import (
    OUTPUT_RESOLUTION_PRESETS,
    OutputResolution,
    ShotSpec,
)
from virector.workers.base import RenderJob
from virector.workers.ltx import LtxBackend, LtxWorkerUnavailableError


def ltx_frame_count(duration_seconds: float, fps: int, max_frames: int) -> int:
    """Return an LTX-compatible frame count in the form ``8n + 1``."""

    target_intervals = max(8, round(duration_seconds * fps))
    frames = (target_intervals // 8) * 8 + 1
    max_compatible_frames = ((max_frames - 1) // 8) * 8 + 1
    return min(frames, max_compatible_frames)


def build_ltx_prompt(spec: ShotSpec) -> str:
    """Return the director's single authoritative direction prompt."""

    return spec.prompt.strip()


def ltx_segment_frame_counts(
    duration_seconds: float,
    fps: int,
    max_frames: int,
) -> list[int]:
    """Split a requested duration into LTX-compatible ``8n + 1`` segments."""

    total_intervals = max(8, round(duration_seconds * fps))
    total_intervals = max(8, round(total_intervals / 8) * 8)
    max_segment_intervals = ((max_frames - 1) // 8) * 8
    if max_segment_intervals < 8:
        raise ValueError("LTX max_frames must allow at least nine frames.")

    segments: list[int] = []
    remaining = total_intervals
    while remaining:
        intervals = min(remaining, max_segment_intervals)
        segments.append(intervals + 1)
        remaining -= intervals
    return segments


def _ffmpeg_executable() -> str:
    executable = shutil.which("ffmpeg")
    if executable:
        return executable

    try:
        import imageio_ffmpeg
    except ImportError as exc:
        raise RuntimeError(
            "Video post-processing requires FFmpeg or imageio-ffmpeg."
        ) from exc
    return imageio_ffmpeg.get_ffmpeg_exe()


def _postprocess_video(
    source: Path,
    output: Path,
    *,
    width: int | None = None,
    height: int | None = None,
    fps: int | None = None,
    interpolate: bool = False,
) -> None:
    filters: list[str] = []
    if fps is not None:
        if interpolate:
            filters.append(
                f"minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:"
                "me_mode=bidir:vsbmc=1"
            )
        else:
            filters.append(f"fps={fps}")
    if width is not None or height is not None:
        if width is None or height is None:
            raise ValueError("Video scaling requires both width and height.")
        filters.append(f"scale={width}:{height}:flags=lanczos")

    command = [
        _ffmpeg_executable(),
        "-y",
        "-i",
        str(source),
    ]
    if filters:
        command.extend(["-vf", ",".join(filters)])
    command.extend(
        [
            "-c:v",
            "libx264",
            "-crf",
            "18",
            "-preset",
            "medium",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output),
        ]
    )
    subprocess.run(command, check=True, capture_output=True)


def _upscale_video(
    source: Path,
    output: Path,
    width: int,
    height: int,
) -> None:
    _postprocess_video(
        source,
        output,
        width=width,
        height=height,
    )


@dataclass(frozen=True)
class DiffusersLtxBackend(LtxBackend):
    """Low-memory LTX 2B image-to-video backend using Diffusers."""

    settings: Settings

    def ensure_available(self) -> None:
        missing = [
            package
            for package in (
                "accelerate",
                "bitsandbytes",
                "diffusers",
                "huggingface_hub",
                "imageio",
                "sentencepiece",
                "torch",
                "transformers",
            )
            if importlib.util.find_spec(package) is None
        ]
        if missing:
            raise LtxWorkerUnavailableError(
                "The LTX runtime is not installed; missing packages: "
                + ", ".join(missing)
                + ". Rebuild with VIRECTOR_INSTALL_LTX=1."
            )

    def render(self, job: RenderJob) -> Path:
        self.ensure_available()

        import torch
        from diffusers import (
            LTXImageToVideoPipeline,
            LTXVideoTransformer3DModel,
        )
        from diffusers.utils import export_to_video
        from huggingface_hub import hf_hub_download
        from PIL import Image
        from transformers import (
            BitsAndBytesConfig,
            T5EncoderModel,
            T5TokenizerFast,
        )

        if not torch.cuda.is_available():
            raise RuntimeError(
                "LTX rendering requires a CUDA GPU, but PyTorch cannot access one."
            )

        checkpoint = Path(
            hf_hub_download(
                repo_id=self.settings.ltx_model_repo,
                filename=self.settings.ltx_checkpoint_filename,
                local_dir=self.settings.models_dir,
                cache_dir=self.settings.cache_dir / "huggingface",
            )
        )

        tokenizer = T5TokenizerFast.from_pretrained(
            self.settings.ltx_model_repo,
            subfolder="tokenizer",
            cache_dir=self.settings.cache_dir / "huggingface",
        )
        quantization_config = None
        if self.settings.ltx_text_encoder_4bit:
            quantization_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_compute_dtype=torch.bfloat16,
                bnb_4bit_quant_type="nf4",
            )
        text_encoder = T5EncoderModel.from_pretrained(
            self.settings.ltx_model_repo,
            subfolder="text_encoder",
            cache_dir=self.settings.cache_dir / "huggingface",
            torch_dtype=torch.bfloat16,
            quantization_config=quantization_config,
            device_map={"": 0},
            low_cpu_mem_usage=True,
        )

        prompt = build_ltx_prompt(job.spec)
        tokens = tokenizer(
            prompt,
            padding="max_length",
            max_length=128,
            truncation=True,
            return_tensors="pt",
        )
        input_ids = tokens.input_ids.to("cuda")
        attention_mask = tokens.attention_mask.to("cuda")
        with torch.inference_mode():
            prompt_embeds = text_encoder(
                input_ids=input_ids,
                attention_mask=attention_mask,
            )[0].to(dtype=torch.bfloat16)
        prompt_attention_mask = attention_mask

        del text_encoder, tokenizer, tokens, input_ids, attention_mask
        gc.collect()
        torch.cuda.empty_cache()

        transformer = LTXVideoTransformer3DModel.from_single_file(
            checkpoint,
            torch_dtype=torch.bfloat16,
            low_cpu_mem_usage=True,
        )
        pipeline = LTXImageToVideoPipeline.from_pretrained(
            self.settings.ltx_model_repo,
            transformer=transformer,
            text_encoder=None,
            torch_dtype=torch.bfloat16,
            cache_dir=self.settings.cache_dir / "huggingface",
            low_cpu_mem_usage=True,
        )
        pipeline.vae.enable_tiling()
        if self.settings.ltx_cpu_offload:
            pipeline.enable_sequential_cpu_offload()
        else:
            pipeline.to("cuda")

        segment_frame_counts = ltx_segment_frame_counts(
            job.spec.duration_seconds,
            job.spec.fps,
            self.settings.ltx_max_frames,
        )
        generator = torch.Generator(device="cuda").manual_seed(job.spec.seed)
        with Image.open(job.start_frame) as source:
            conditioning_image = source.convert("RGB")

        frames = []
        for segment_index, num_frames in enumerate(segment_frame_counts):
            result = pipeline(
                image=conditioning_image,
                prompt=None,
                prompt_embeds=prompt_embeds,
                prompt_attention_mask=prompt_attention_mask,
                width=job.spec.width,
                height=job.spec.height,
                num_frames=num_frames,
                frame_rate=job.spec.fps,
                num_inference_steps=self.settings.ltx_inference_steps,
                guidance_scale=1.0,
                decode_timestep=0.05,
                decode_noise_scale=0.025,
                generator=generator,
            )
            segment_frames = result.frames[0]
            frames.extend(
                segment_frames if segment_index == 0 else segment_frames[1:]
            )
            conditioning_image = segment_frames[-1].convert("RGB")

        output = job.output_dir / "preview.mp4"
        if job.spec.output_resolution == OutputResolution.preview:
            export_to_video(frames, str(output), fps=job.spec.fps)
            return output

        native_output = job.output_dir / "preview-native.mp4"
        export_to_video(frames, str(native_output), fps=job.spec.fps)
        output_width, output_height = OUTPUT_RESOLUTION_PRESETS[
            job.spec.output_resolution
        ][job.spec.aspect_ratio]
        _upscale_video(
            source=native_output,
            output=output,
            width=output_width,
            height=output_height,
        )
        return output
