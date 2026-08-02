from virector.services.action_prompt import compile_action_prompt


def test_walking_action_has_timed_physics_and_real_displacement() -> None:
    compiled = compile_action_prompt(
        "The character walks six steps down the lane",
        duration_seconds=4,
        framing="wide full-body",
        camera_motion="tracks alongside",
    )

    assert "0.00-0.80s ANTICIPATION" in compiled
    assert "0.80-3.28s PRIMARY MOTION" in compiled
    assert "heel-to-toe foot plants" in compiled
    assert "visible forward body displacement" in compiled
    assert "foot sliding" in compiled
    assert "wide full-body" in compiled


def test_turning_action_preserves_identity_through_angle_change() -> None:
    compiled = compile_action_prompt(
        "She turns toward camera",
        duration_seconds=2.5,
        framing="medium waist-up",
        camera_motion="slow push-in",
    )

    assert "eyes first" in compiled
    assert "preserve facial structure" in compiled
    assert "identity drift" in compiled


def test_unknown_action_uses_generic_physical_motion_contract() -> None:
    compiled = compile_action_prompt(
        "The character carefully examines the room",
        duration_seconds=3,
        framing="medium",
        camera_motion="subtle handheld",
    )

    assert "REQUESTED ACTION" in compiled
    assert "gravity, contact, momentum" in compiled
    assert "static pose" in compiled
