import pytest
from pydantic import ValidationError

from virector.models.director_plan import DirectorPlan
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
    assert plan.warnings == ["No @image reference definitions were found."]


def test_director_plan_serializes_as_worker_ready_json() -> None:
    plan = compile_director_plan(CLIP_8)
    restored = DirectorPlan.model_validate_json(plan.model_dump_json())

    assert restored == plan
