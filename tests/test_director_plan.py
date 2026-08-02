import pytest
from pydantic import ValidationError

from virector.models.director_plan import DirectorPlan
from virector.models.omni_asset import AssetRole, BindingModality, ReferenceOperation
from virector.services.director_plan import compile_director_plan


CLIP_8 = """CLIP 8 — DON’T CALL MICAH

Model: Seedance 2.0 Duration: Approximately 15 seconds Method: Image-to-video, forward-only Purpose: Major reveal and cliffhanger Voice: Natural Kingston Jamaican accents, fluid patois, emotionally grounded

Image References

@image1: Marcia Campbell
@image2: Uncle Teddy
@image3: Micah Campbell
@image4: Campbell House
@image5: Micah’s corrected law-firm ID
@image6: Micah’s legal case folder
@image7: Dario’s smartphone
@image8: Dario Campbell
@image9: Redwater Legal Group exterior or generic law-office entrance

0:00–0:03

Start at @image4 from the previous call’s aftermath. Camera pushes through the dim house toward @image1 holding the phone.

Dario, through phone: “And whatever yuh do… nuh call Micah.”

Marcia ends the call and immediately looks at Uncle Teddy.

Sound: Low heartbeat, distant vehicle hum, tense strings.

0:03–0:06

Forward motion continues as Marcia walks quickly across the room and selects Micah’s number.

@image2 follows behind her.

Uncle Teddy: “Him just tell yuh nuh call Micah.”

Marcia: “Dario also tell me everything alright.”

Uncle Teddy: “Call him.”

0:06–0:09

Hard forward cut to @image3 leaving @image9 with @image6 under his arm. His phone rings as the camera dollies backward in front of him.

Micah: “Mummy, wah happen?”

A generic dark vehicle waits across the road.

0:09–0:12

Micah stops walking. Close insert of a message or payment reference on his phone, then rack focus to @image5 hanging from his neck.

His expression changes.

Micah: “Hold on… this account belong to my firm.”

Sound: Music drops out, single low bass pulse.

0:12–0:15

Forward cut to @image8 still driving. @image7 vibrates with a new image of Micah outside the law office.

Message: BRING BACK WI MONEY OR YUH BROTHER PAY.

Smash close-up on Dario.

Dario: “Kemo set up mi whole family.”

End sound: Rising string, deep bass impact, rapid shutters. Transition: Hard cut to black.

Title card: THE SON WHO OWED BOTH SIDES TO BE CONTINUED
"""


def test_compiles_screenplay_into_timed_director_plan() -> None:
    plan = compile_director_plan(CLIP_8)

    assert plan.title == "CLIP 8 — DON’T CALL MICAH"
    assert plan.duration_seconds == 15
    assert plan.requested_model == "Seedance 2.0"
    assert plan.method == "Image-to-video, forward-only"
    assert plan.voice_direction == (
        "Natural Kingston Jamaican accents, fluid patois, emotionally grounded"
    )
    assert len(plan.references) == 9
    assert len(plan.segments) == 5
    assert plan.warnings == []


def test_routes_visual_and_voice_references_independently() -> None:
    plan = compile_director_plan(CLIP_8)
    first = plan.segments[0]

    assert first.reference_tags == ["@image1", "@image2", "@image4"]
    assert first.dialogue[0].speaker == "Dario"
    assert first.dialogue[0].delivery == "through phone"
    assert first.dialogue[0].speaker_reference_tag == "@image8"
    assert "@image8" not in first.reference_tags
    assert plan.segments[1].reference_tags == ["@image1", "@image2", "@image4"]
    assert "@image3" not in plan.segments[1].reference_tags


def test_extracts_dialogue_sound_message_transition_and_title_card() -> None:
    plan = compile_director_plan(CLIP_8)

    assert [cue.speaker for cue in plan.segments[1].dialogue] == [
        "Uncle Teddy",
        "Marcia",
        "Uncle Teddy",
    ]
    assert plan.segments[3].sound_cues == [
        "Music drops out, single low bass pulse."
    ]
    final = plan.segments[4]
    assert final.on_screen_text == [
        "BRING BACK WI MONEY OR YUH BROTHER PAY."
    ]
    assert final.transition == "Hard cut to black."
    assert final.title_card == "THE SON WHO OWED BOTH SIDES TO BE CONTINUED"


def test_infers_clip_8_omni_asset_roles() -> None:
    plan = compile_director_plan(CLIP_8)
    assets = {asset.tag: asset for asset in plan.omni_assets}

    assert assets["@image1"].roles == [
        AssetRole.character_identity,
        AssetRole.wardrobe,
    ]
    assert assets["@image1"].identity_group == "marcia-campbell"
    assert assets["@image4"].roles == [
        AssetRole.environment,
        AssetRole.composition,
    ]
    assert assets["@image5"].roles == [
        AssetRole.prop,
        AssetRole.readable_text,
    ]
    assert assets["@image7"].roles == [
        AssetRole.prop,
        AssetRole.readable_text,
    ]


def test_compiles_visual_and_offscreen_voice_bindings() -> None:
    plan = compile_director_plan(CLIP_8)
    first = plan.segments[0]
    visual = [
        binding
        for binding in first.reference_bindings
        if binding.modality == BindingModality.visual
    ]
    voice = [
        binding
        for binding in first.reference_bindings
        if binding.modality == BindingModality.voice
    ]

    assert [binding.asset_tags for binding in visual] == [
        ["@image1"],
        ["@image2"],
        ["@image4"],
    ]
    assert AssetRole.character_identity in visual[0].controls
    assert AssetRole.environment in visual[2].controls
    assert ReferenceOperation.combine in visual[0].operations
    assert ReferenceOperation.maintain in visual[0].operations
    assert voice[0].asset_tags == ["@image8"]
    assert voice[0].visible is False
    assert "do not make the speaker visible" in voice[0].instruction


def test_groups_multi_angle_images_into_one_identity_binding() -> None:
    prompt = """CHARACTER TEST
Duration: 4 seconds

Image References
@image1: Marcia Campbell front view
@image2: Marcia Campbell side view
@image3: Campbell House

0:00-0:04
Extract and combine @image1 and @image2 to preserve Marcia's appearance as she
walks through @image3.
"""

    plan = compile_director_plan(prompt)
    binding = plan.segments[0].reference_bindings[0]

    assert binding.asset_tags == ["@image1", "@image2"]
    assert binding.target == "Marcia Campbell front view"
    assert binding.operations == [
        ReferenceOperation.reference,
        ReferenceOperation.extract,
        ReferenceOperation.combine,
        ReferenceOperation.maintain,
    ]
    assert binding.controls == [
        AssetRole.character_identity,
        AssetRole.wardrobe,
    ]


def test_camera_reference_is_not_classified_as_a_character() -> None:
    prompt = """CAMERA TEST
Duration: 4 seconds

Image References
@image1: Marcia Campbell
@image2: Camera framing reference

0:00-0:04
@image1 walks forward. Follow the framing from @image2.
"""

    plan = compile_director_plan(prompt)
    camera_asset = plan.omni_assets[1]

    assert camera_asset.roles == [AssetRole.camera]
    assert camera_asset.identity_group is None


def test_warns_when_defined_asset_is_unused() -> None:
    plan = compile_director_plan(
        """CLIP
Duration: 4 seconds
Image References
@image1: Lead character
@image2: Unused logo

0:00-0:04
@image1 walks into frame.
"""
    )

    assert "Asset @image2 is defined but not used by any shot." in plan.warnings


def test_plan_rejects_segment_beyond_fifteen_seconds() -> None:
    with pytest.raises((ValidationError, ValueError)):
        compile_director_plan(
            """CLIP 1\nDuration: 16 seconds\n\n0:00-0:16\nA long shot."""
        )


def test_plain_prompt_compiles_to_one_fallback_segment() -> None:
    plan = compile_director_plan(
        "Model: Local preview Duration: 4 seconds\n"
        "@image1 walks naturally through the room."
    )

    assert len(plan.segments) == 1
    assert plan.segments[0].start_seconds == 0
    assert plan.segments[0].end_seconds == 4
    assert plan.warnings == ["No omni reference definitions were found."]


def test_plain_prompt_uses_requested_fallback_duration() -> None:
    plan = compile_director_plan(
        "@image1 walks through the room while the camera tracks.",
        fallback_duration=7,
    )

    assert plan.duration_seconds == 7
    assert plan.segments[0].duration_seconds == 7


def test_long_plain_prompt_uses_safe_fallback_title() -> None:
    plan = compile_director_plan(
        "@image1 walks naturally through @image2 while maintaining identity, "
        "body proportions, wardrobe, realistic contact shadows, environmental "
        "depth, perspective, natural lighting, grounded feet, stable framing, "
        "and physically convincing movement throughout the complete scene.",
        fallback_duration=1,
    )

    assert plan.title == "Untitled clip"
    assert plan.duration_seconds == 1


def test_director_plan_serializes_as_worker_ready_json() -> None:
    plan = compile_director_plan(CLIP_8)
    restored = DirectorPlan.model_validate_json(plan.model_dump_json())

    assert restored == plan


def test_director_plan_accepts_legacy_references_field() -> None:
    plan = compile_director_plan(CLIP_8)
    payload = plan.model_dump(mode="json")
    payload["references"] = payload.pop("omni_assets")

    restored = DirectorPlan.model_validate(payload)

    assert restored.omni_assets == plan.omni_assets


def test_rejects_video_or_audio_indexes_above_three() -> None:
    with pytest.raises(ValueError, match="limited to 1-3"):
        compile_director_plan("@image1 follows @video4.")
