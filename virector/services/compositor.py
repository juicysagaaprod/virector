from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter

from virector.models.shot_spec import ShotSpec


def _cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    target_width, target_height = size
    source_width, source_height = image.size
    scale = max(target_width / source_width, target_height / source_height)
    resized = image.resize(
        (round(source_width * scale), round(source_height * scale)),
        Image.Resampling.LANCZOS,
    )
    left = max(0, (resized.width - target_width) // 2)
    top = max(0, (resized.height - target_height) // 2)
    return resized.crop((left, top, left + target_width, top + target_height))


def _prepare_character(
    image: Image.Image,
    target_height: int,
    scale: float,
) -> Image.Image:
    character = image.convert("RGBA")
    character.thumbnail(
        (round(target_height * scale), round(target_height * scale)),
        Image.Resampling.LANCZOS,
    )
    return character


def _shadow_for(character: Image.Image) -> Image.Image:
    alpha = character.getchannel("A")
    shadow_alpha = alpha.filter(ImageFilter.GaussianBlur(radius=max(3, character.width // 80)))
    shadow_alpha = ImageEnhance.Brightness(shadow_alpha).enhance(0.45)
    shadow = Image.new("RGBA", character.size, (0, 0, 0, 0))
    shadow.putalpha(shadow_alpha)
    return shadow


def compose_start_frame(
    character_path: str | Path,
    world_path: str | Path,
    spec: ShotSpec,
    output_path: str | Path,
) -> Path:
    """Compose a deterministic director-controlled start frame."""

    character_path = Path(character_path)
    world_path = Path(world_path)
    output_path = Path(output_path)

    if not character_path.is_file():
        raise FileNotFoundError(f"Character image not found: {character_path}")
    if not world_path.is_file():
        raise FileNotFoundError(f"World image not found: {world_path}")

    canvas_size = (spec.width, spec.height)
    with Image.open(world_path) as source_world:
        canvas = _cover(source_world.convert("RGB"), canvas_size).convert("RGBA")

    with Image.open(character_path) as source_character:
        character = _prepare_character(
            source_character,
            target_height=spec.height,
            scale=spec.character.scale,
        )

    center_x = round(spec.character.position_x * spec.width)
    baseline_y = round(spec.character.position_y * spec.height)
    left = center_x - character.width // 2
    top = baseline_y - character.height

    shadow = _shadow_for(character)
    shadow_offset = max(2, spec.width // 160)
    canvas.alpha_composite(shadow, (left + shadow_offset, top + shadow_offset))
    canvas.alpha_composite(character, (left, top))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(output_path, format="PNG", optimize=True)
    return output_path


def prepare_reference_start_frame(
    reference_path: str | Path,
    spec: ShotSpec,
    output_path: str | Path,
) -> Path:
    """Prepare the first omni reference as an LTX-compatible start frame."""

    reference_path = Path(reference_path)
    output_path = Path(output_path)
    if not reference_path.is_file():
        raise FileNotFoundError(f"Reference image not found: {reference_path}")

    with Image.open(reference_path) as source:
        frame = _cover(source.convert("RGB"), (spec.width, spec.height))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame.save(output_path, format="PNG", optimize=True)
    return output_path
