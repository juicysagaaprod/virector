import gc
import importlib.util
from dataclasses import dataclass
from pathlib import Path

from virector.config import Settings
from virector.models.shot_spec import ShotSpec
from virector.workers.base import RenderJob
from virector.workers.ltx import LtxBackend, LtxWorkerUnavailableError


def ltx_frame_count(duration_seconds: float, fps: int, max_frames: int) -> int:
    """Return an LTX-compatible frame count in the form ``8n + 1``."""

    target_intervals = max(8, round(duration_seconds * fps))
    frames = (target_intervals // 8) * 8 + 1
    max_compatible_frames = ((max_frames - 1) // 8) * 8 + 1
    return min(frames, max_compatible_frames)


def build_ltx_prompt(spec: ShotSpec) -> str:
    """Translate structured direction into one chronological LTX prompt."""

    return " ".join(
        part.strip()
        for part in (
            spec.prompt,
            f"{spec.character.name} {spec.character.action}.",
            f"The expression is {spec.character.expression}.",
            f"The character faces {spec.character.facing}.",
            (
                f"Camera: {spec.camera.shot_size} shot, "
                f"{spec.camera.movement}, {spec.camera.lens_mm}mm lens, "
                f"focus on {spec.camera.focus_target}."
            ),
            (
                f"Lighting: {spec.lighting.style}, "
                f"{spec.lighting.time_of_day}, "
                f"{spec.lighting.colour_grade}."
            ),
        )
        if part.strip()
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

        num_frames = ltx_frame_count(
            job.spec.duration_seconds,
            job.spec.fps,
            self.settings.ltx_max_frames,
        )
        generator = torch.Generator(device="cuda").manual_seed(job.spec.seed)
        with Image.open(job.start_frame) as source:
            image = source.convert("RGB")
            result = pipeline(
                image=image,
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

        output = job.output_dir / "preview.mp4"
        export_to_video(result.frames[0], str(output), fps=job.spec.fps)
        return output
