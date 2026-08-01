"""Compatibility checks derived from reusable multimodal prompt patterns.

The prompts are compact Virector fixtures, not copies of third-party examples.
Passing tests describe the current compiler and transport contract. Model-native
conditioning remains the responsibility of each generation backend.
"""

from virector.models.omni_asset import (
    AssetRole,
    BindingModality,
    ReferenceOperation,
)
from virector.services.director_plan import compile_director_plan


def _asset(plan, tag):
    return next(asset for asset in plan.omni_assets if asset.tag == tag)


def _binding(segment, tag, modality=BindingModality.visual):
    return next(
        binding
        for binding in segment.reference_bindings
        if tag in binding.asset_tags and binding.modality == modality
    )


def test_basic_subject_motion_environment_camera_and_audio_cues() -> None:
    plan = compile_director_plan(
        """CITY WALK
Duration: 4 seconds

0:00-0:04
A woman walks through a rain-lit city while the camera tracks backward.
Sound: Footsteps, soft traffic and distant thunder.
"""
    )

    assert plan.duration_seconds == 4
    assert "camera tracks backward" in plan.segments[0].action
    assert plan.segments[0].sound_cues == [
        "Footsteps, soft traffic and distant thunder."
    ]


def test_multi_angle_images_compile_to_one_identity_binding() -> None:
    plan = compile_director_plan(
        """IDENTITY TEST
Duration: 4 seconds

Image References
@image1: Amara Jones front view
@image2: Amara Jones side view
@image3: Coffee shop interior

0:00-0:04
Extract and combine @image1 and @image2. @image1 drinks coffee inside
@image3 while maintaining her face, proportions and clothing.
"""
    )

    identity = _binding(plan.segments[0], "@image1")

    assert identity.asset_tags == ["@image1", "@image2"]
    assert identity.controls == [
        AssetRole.character_identity,
        AssetRole.wardrobe,
    ]
    assert identity.operations == [
        ReferenceOperation.reference,
        ReferenceOperation.extract,
        ReferenceOperation.combine,
        ReferenceOperation.maintain,
    ]


def test_multi_element_prompt_assigns_each_asset_control() -> None:
    plan = compile_director_plan(
        """RESTAURANT SCENE
Duration: 5 seconds

Image References
@image1: Lead girl
@image2: Red uniform outfit
@image3: Lead boy
@image4: Restaurant interior
@image5: Restaurant logo

0:00-0:05
Inside @image4, @image1 wears @image2 and tidies the counter. @image3 walks
up to her. Keep @image5 visible in the lower-right corner.
"""
    )

    assert _asset(plan, "@image1").roles == [
        AssetRole.character_identity,
        AssetRole.wardrobe,
    ]
    assert _asset(plan, "@image2").roles == [AssetRole.wardrobe]
    assert _asset(plan, "@image4").roles == [
        AssetRole.environment,
        AssetRole.composition,
    ]
    assert _asset(plan, "@image5").roles == [
        AssetRole.prop,
        AssetRole.readable_text,
    ]
    assert len(plan.segments[0].reference_bindings) == 5
    assert all(
        ReferenceOperation.combine in binding.operations
        for binding in plan.segments[0].reference_bindings
    )


def test_storyboard_and_character_references_compile_together() -> None:
    plan = compile_director_plan(
        """DINNER STORYBOARD
Duration: 6 seconds

Image References
@image1: Lead girl
@image2: Father character
@image3: Storyboard panels

0:00-0:03
Follow the composition from @image3. @image1 waits while @image2 cooks.
Girl: "Is dinner ready?"

0:03-0:06
Continue in the same location and cut closer to @image2.
Father: "Almost done."
"""
    )

    storyboard = _asset(plan, "@image3")
    storyboard_binding = _binding(plan.segments[0], "@image3")

    assert storyboard.roles == [
        AssetRole.storyboard,
        AssetRole.composition,
    ]
    assert ReferenceOperation.follow in storyboard_binding.operations
    assert storyboard_binding.controls == [
        AssetRole.storyboard,
        AssetRole.composition,
    ]
    assert plan.segments[0].dialogue[0].speaker_reference_tag == "@image1"
    assert plan.segments[1].dialogue[0].speaker_reference_tag == "@image2"
    assert "@image3" in plan.segments[1].reference_tags


def test_camera_and_effect_images_do_not_become_character_identities() -> None:
    plan = compile_director_plan(
        """REFERENCE CONTROLS
Duration: 4 seconds

Image References
@image1: Lead woman
@image2: Camera framing reference
@image3: Golden particle effect

0:00-0:04
@image1 walks forward. Follow @image2 and reproduce the trajectory from
@image3 while maintaining her appearance.
"""
    )

    assert _asset(plan, "@image2").roles == [AssetRole.camera]
    assert _asset(plan, "@image3").roles == [AssetRole.effect]
    assert _asset(plan, "@image2").identity_group is None
    assert _asset(plan, "@image3").identity_group is None


def test_timed_dialogue_text_and_transition_are_worker_ready() -> None:
    plan = compile_director_plan(
        """REVEAL
Duration: 4 seconds

Image References
@image1: Nia character

0:00-0:02
@image1 turns toward camera.
Nia: "I know the truth."
Message: THE SECRET IS OUT

0:02-0:04
Smash close-up on @image1.
Transition: Hard cut to black.
Title card: TO BE CONTINUED
"""
    )

    assert plan.segments[0].on_screen_text == ["THE SECRET IS OUT"]
    assert plan.segments[1].transition == "Hard cut to black."
    assert plan.segments[1].title_card == "TO BE CONTINUED"


def test_video_action_camera_and_effect_reference_contract() -> None:
    plan = compile_director_plan(
        """ACTION REFERENCE
Duration: 4 seconds

Video References
@video1: Fight action and camera choreography

Image References
@image1: Hero character
@image2: Rival character

0:00-0:04
Follow the action and camera movement from @video1 while @image1 fights
@image2, maintaining both identities.
"""
    )

    assert _asset(plan, "@video1").roles == [
        AssetRole.motion,
        AssetRole.camera,
    ]
    assert [
        binding.modality
        for binding in plan.segments[0].reference_bindings
        if binding.asset_tags == ["@video1"]
    ] == [BindingModality.motion, BindingModality.camera]


def test_audio_voice_and_rhythm_reference_contract() -> None:
    plan = compile_director_plan(
        """VOICE REFERENCE
Duration: 4 seconds

Audio References
@audio1: Lead actor voice and delivery

Image References
@image1: Lead actor

0:00-0:04
@image1 speaks with the voice, emotion and rhythm from @audio1.
"""
    )

    assert _asset(plan, "@audio1").roles == [AssetRole.voice, AssetRole.audio]
    binding = _binding(plan.segments[0], "@audio1", BindingModality.voice)
    assert binding.visible is False
    assert _binding(
        plan.segments[0],
        "@audio1",
        BindingModality.audio,
    ).controls == [AssetRole.audio]
