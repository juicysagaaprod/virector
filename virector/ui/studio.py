from pathlib import Path

import gradio as gr

from virector.models.shot_spec import (
    OUTPUT_RESOLUTION_PRESETS,
    RESOLUTION_PRESETS,
    AspectRatio,
    OutputResolution,
    ShotSpec,
)
from virector.services.jobs import JobService
from virector.services.references import (
    build_omni_reference_directives,
    validate_prompt_reference_tags,
)


VIDEO_MODEL = "ltx-video-2b-distilled"


def create_studio(job_service: JobService) -> gr.Blocks:
    def render(
        reference_images: list[str] | None,
        reference_videos: list[str] | None,
        reference_audio: list[str] | None,
        title: str,
        direction_prompt: str,
        video_model: str,
        aspect_ratio: str,
        output_resolution: str,
        duration_seconds: float,
        seed: int,
    ) -> tuple[str | None, str, str]:
        images = [Path(path) for path in (reference_images or [])]
        videos = [Path(path) for path in (reference_videos or [])]
        audio = [Path(path) for path in (reference_audio or [])]
        references = images + videos + audio
        if not images:
            return None, "Upload at least one omni reference image.", "{}"
        if not direction_prompt or len(direction_prompt.strip()) < 3:
            return None, "Describe the video in the direction prompt.", "{}"

        try:
            reference_directives = build_omni_reference_directives(
                len(images),
                len(videos),
                len(audio),
            )
            validate_prompt_reference_tags(direction_prompt, reference_directives)
        except ValueError as exc:
            return None, str(exc), "{}"

        ratio = AspectRatio(aspect_ratio)
        resolution = OutputResolution(output_resolution)
        width, height = RESOLUTION_PRESETS[ratio]
        output_width, output_height = OUTPUT_RESOLUTION_PRESETS[resolution][ratio]
        spec = ShotSpec(
            title=title or "Untitled shot",
            prompt=direction_prompt.strip(),
            video_model=video_model,
            reference_mode="omni",
            aspect_ratio=ratio,
            output_resolution=resolution,
            width=width,
            height=height,
            duration_seconds=float(duration_seconds),
            seed=int(seed),
            references=reference_directives,
        )
        result = job_service.create_from_references(
            reference_paths=references,
            spec=spec,
            reference_directives=reference_directives,
        )
        tagged_references = ", ".join(item.tag for item in reference_directives)
        reference_note = (
            f"{len(references)} omni reference asset(s) saved as "
            f"{tagged_references}. "
        )
        return (
            str(result.video) if result.video else None,
            (
                f"{result.message} {reference_note}"
                f"Requested {duration_seconds:g}s at "
                f"{output_width}×{output_height}. Job: {result.job_id}"
            ),
            spec.model_dump_json(indent=2),
        )

    with gr.Blocks(title="Virector Studio") as studio:
        gr.Markdown(
            "# Virector Studio\n"
            "Upload character and world-design images, then describe how they "
            "work together in one direction prompt."
        )
        gr.Markdown(
            "**Omni-reference workflow:** uploads are automatically named "
            "`@image1`, `@image2`, and so on. Mention those names directly in the "
            "direction prompt—for example, “@image1 walks through the world in "
            "@image2.”"
        )

        with gr.Row():
            with gr.Column(scale=2):
                reference_images = gr.File(
                    label="Omni reference images",
                    file_count="multiple",
                    file_types=["image"],
                    type="filepath",
                    allow_reordering=True,
                    height=240,
                )
                gr.Markdown(
                    "Upload order assigns `@image1`, `@image2`, up to `@image9`."
                )
                with gr.Row():
                    reference_videos = gr.File(
                        label="Motion/camera/effect videos",
                        file_count="multiple",
                        file_types=["video"],
                        type="filepath",
                    )
                    reference_audio = gr.File(
                        label="Voice/music/audio references",
                        file_count="multiple",
                        file_types=["audio"],
                        type="filepath",
                    )
                direction_prompt = gr.Textbox(
                    label="Direction prompt",
                    placeholder=(
                        "@image1 is the character and @image2 is the world design. "
                        "Show @image1 walking naturally through @image2 while "
                        "preserving the character's face and clothing. Describe "
                        "Use @video and @audio tags for motion, camera, voice, "
                        "rhythm or sound. Describe action, lighting and timing."
                    ),
                    lines=10,
                )
            with gr.Column(scale=1):
                output_video = gr.Video(
                    label="Generated video",
                    interactive=False,
                )

        with gr.Row():
            title = gr.Textbox(label="Shot title", value="Episode 1 — Shot 1")
            video_model = gr.Dropdown(
                label="Video model",
                choices=[
                    (
                        "LTX-Video 2B Distilled — local omni-reference workflow",
                        VIDEO_MODEL,
                    )
                ],
                value=VIDEO_MODEL,
                interactive=True,
            )
            aspect_ratio = gr.Dropdown(
                label="Aspect ratio",
                choices=[item.value for item in AspectRatio],
                value=AspectRatio.portrait.value,
            )
            output_resolution = gr.Dropdown(
                label="Resolution",
                choices=[item.value for item in OutputResolution],
                value=OutputResolution.preview.value,
                info="720p and 1080p upscale the native local preview.",
            )

        with gr.Row():
            duration_seconds = gr.Slider(
                label="Video length (seconds)",
                minimum=1,
                maximum=15,
                value=4,
                step=1,
            )
            seed = gr.Number(label="Seed", value=42, precision=0)

        render_button = gr.Button("Generate video", variant="primary", size="lg")
        status = gr.Textbox(label="Render status", interactive=False, lines=3)
        with gr.Accordion("Advanced job details", open=False):
            shot_json = gr.Code(label="ShotSpec JSON", language="json")

        render_button.click(
            fn=render,
            inputs=[
                reference_images,
                reference_videos,
                reference_audio,
                title,
                direction_prompt,
                video_model,
                aspect_ratio,
                output_resolution,
                duration_seconds,
                seed,
            ],
            outputs=[output_video, status, shot_json],
        )

    return studio
