from pathlib import Path

import gradio as gr

from virector.models.shot_spec import (
    RESOLUTION_PRESETS,
    AspectRatio,
    CameraDirection,
    CharacterDirection,
    LightingDirection,
    ShotSpec,
)
from virector.services.jobs import JobService


def create_studio(job_service: JobService) -> gr.Blocks:
    def compose(
        character_image: str | None,
        world_image: str | None,
        title: str,
        prompt: str,
        aspect_ratio: str,
        position_x: float,
        position_y: float,
        character_scale: float,
        character_name: str,
        action: str,
        expression: str,
        camera_movement: str,
        lens_mm: int,
        lighting: str,
        seed: int,
    ) -> tuple[str | None, str | None, str, str]:
        if not character_image or not world_image:
            return None, None, "Upload both images.", "{}"

        ratio = AspectRatio(aspect_ratio)
        width, height = RESOLUTION_PRESETS[ratio]
        spec = ShotSpec(
            title=title or "Untitled shot",
            prompt=prompt,
            aspect_ratio=ratio,
            width=width,
            height=height,
            seed=int(seed),
            character=CharacterDirection(
                name=character_name or "Character",
                action=action,
                expression=expression,
                position_x=position_x,
                position_y=position_y,
                scale=character_scale,
            ),
            camera=CameraDirection(
                movement=camera_movement,
                lens_mm=int(lens_mm),
            ),
            lighting=LightingDirection(style=lighting),
        )
        result = job_service.create(
            character_path=Path(character_image),
            world_path=Path(world_image),
            spec=spec,
        )
        return (
            str(result.start_frame),
            str(result.video) if result.video else None,
            f"{result.message} Job: {result.job_id}",
            spec.model_dump_json(indent=2),
        )

    with gr.Blocks(title="Virector Studio") as studio:
        gr.Markdown(
            "# Virector Studio\n"
            "Compose a director-controlled start frame for local or cloud AI video."
        )
        with gr.Row():
            character_image = gr.Image(
                label="Character PNG",
                type="filepath",
                image_mode="RGBA",
            )
            world_image = gr.Image(
                label="World/background",
                type="filepath",
                image_mode="RGB",
            )
            output_image = gr.Image(label="Composed start frame", interactive=False)
            output_video = gr.Video(label="Generated preview", interactive=False)

        with gr.Row():
            with gr.Column():
                title = gr.Textbox(label="Shot title", value="Episode 1 — Shot 1")
                character_name = gr.Textbox(label="Character name", value="Lead")
                prompt = gr.Textbox(
                    label="Director prompt",
                    value=(
                        "The lead enters the scene with controlled natural movement, "
                        "cinematic realism and consistent identity."
                    ),
                    lines=4,
                )
                action = gr.Textbox(
                    label="Character action",
                    value="walks forward slowly and stops",
                )
                expression = gr.Textbox(
                    label="Expression",
                    value="focused, emotionally restrained",
                )
            with gr.Column():
                aspect_ratio = gr.Dropdown(
                    label="Aspect ratio",
                    choices=[item.value for item in AspectRatio],
                    value=AspectRatio.portrait.value,
                )
                position_x = gr.Slider(
                    label="Horizontal position",
                    minimum=0.0,
                    maximum=1.0,
                    value=0.5,
                    step=0.01,
                )
                position_y = gr.Slider(
                    label="Ground/baseline position",
                    minimum=0.2,
                    maximum=1.0,
                    value=0.95,
                    step=0.01,
                )
                character_scale = gr.Slider(
                    label="Character scale",
                    minimum=0.1,
                    maximum=1.5,
                    value=0.72,
                    step=0.01,
                )
            with gr.Column():
                camera_movement = gr.Dropdown(
                    label="Camera movement",
                    choices=[
                        "static",
                        "slow dolly in",
                        "slow dolly out",
                        "pan left",
                        "pan right",
                        "orbit clockwise",
                        "orbit counter-clockwise",
                        "handheld follow",
                    ],
                    value="slow dolly in",
                )
                lens_mm = gr.Slider(
                    label="Virtual lens (mm)",
                    minimum=14,
                    maximum=135,
                    value=50,
                    step=1,
                )
                lighting = gr.Textbox(
                    label="Lighting",
                    value="cinematic natural light, realistic skin, soft contrast",
                )
                seed = gr.Number(label="Seed", value=42, precision=0)

        compose_button = gr.Button("Compose start frame", variant="primary")
        status = gr.Textbox(label="Status", interactive=False)
        shot_json = gr.Code(label="ShotSpec JSON", language="json")

        compose_button.click(
            fn=compose,
            inputs=[
                character_image,
                world_image,
                title,
                prompt,
                aspect_ratio,
                position_x,
                position_y,
                character_scale,
                character_name,
                action,
                expression,
                camera_movement,
                lens_mm,
                lighting,
                seed,
            ],
            outputs=[output_image, output_video, status, shot_json],
        )

    return studio
