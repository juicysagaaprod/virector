from pathlib import Path

from PIL import Image

from virector.models.shot_spec import CharacterDirection, ShotSpec
from virector.services.compositor import compose_start_frame


def test_compositor_creates_requested_frame(tmp_path: Path) -> None:
    world = tmp_path / "world.png"
    character = tmp_path / "character.png"
    output = tmp_path / "output.png"

    Image.new("RGB", (1920, 1080), (20, 50, 80)).save(world)
    Image.new("RGBA", (300, 900), (220, 80, 80, 255)).save(character)

    spec = ShotSpec(
        prompt="A character stands in a cinematic world.",
        width=480,
        height=832,
        character=CharacterDirection(scale=0.5),
    )
    result = compose_start_frame(character, world, spec, output)

    assert result == output
    assert output.is_file()
    with Image.open(output) as composed:
        assert composed.size == (480, 832)
        assert composed.mode == "RGB"

