from __future__ import annotations

from virector.models.director_plan import DirectorPlan
from virector.models.shot_spec import ShotBeat


def timeline_from_director_plan(plan: DirectorPlan) -> list[ShotBeat]:
    """Compile DirectorPlan segments into contiguous independently rendered beats."""

    beats: list[ShotBeat] = []
    for segment in plan.segments:
        dialogue = "\n".join(cue.text for cue in segment.dialogue) or None
        action = segment.action.strip()
        lowered = action.lower()
        framing = (
            "close-up"
            if "close-up" in lowered or "close up" in lowered
            else "wide full-body"
            if "wide" in lowered or "full-body" in lowered or "full body" in lowered
            else "medium waist-up"
            if "medium" in lowered or "waist" in lowered
            else "medium"
        )
        camera_motion = (
            "slow push-in"
            if "push" in lowered
            else "tracking"
            if "track" in lowered or "doll" in lowered
            else "subtle handheld"
            if "handheld" in lowered
            else "static"
        )
        beats.append(
            ShotBeat(
                shot_id=f"shot-{segment.index:02d}",
                start_seconds=segment.start_seconds,
                duration_seconds=segment.duration_seconds,
                framing=framing,
                camera_motion=camera_motion,
                subject_action=action,
                expression="emotionally grounded" if dialogue else "natural",
                dialogue=dialogue,
                transition=segment.transition or "cut",
            )
        )
    return beats


def default_eight_second_timeline(dialogue: str) -> list[ShotBeat]:
    """Reference validation timeline requested by the production specification."""

    return [
        ShotBeat(
            shot_id="walk",
            start_seconds=0.0,
            duration_seconds=2.5,
            framing="wide full-body",
            camera_motion="track alongside at walking speed",
            subject_action="walk naturally through the referenced environment",
            expression="focused and natural",
        ),
        ShotBeat(
            shot_id="turn-and-speak",
            start_seconds=2.5,
            duration_seconds=2.5,
            framing="medium waist-up",
            camera_motion="subtle handheld",
            subject_action="slow, stop, and turn naturally toward camera",
            expression="emotionally grounded",
            dialogue=dialogue,
        ),
        ShotBeat(
            shot_id="dialogue-closeup",
            start_seconds=5.0,
            duration_seconds=3.0,
            framing="emotional close-up",
            camera_motion="slow push-in",
            subject_action="finish the line with small head gestures and natural blinking",
            expression="emotional and controlled",
            dialogue=dialogue,
        ),
    ]
