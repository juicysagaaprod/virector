from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ActionProfile:
    preparation: str
    execution: str
    follow_through: str
    physics: str
    secondary_motion: str
    avoid: tuple[str, ...]


ACTION_PROFILES: tuple[tuple[re.Pattern[str], ActionProfile], ...] = (
    (
        re.compile(r"\b(?:walk|walks|walking|step|steps)\b", re.IGNORECASE),
        ActionProfile(
            preparation=(
                "shift body weight onto the supporting leg and initiate the first "
                "step from the hips"
            ),
            execution=(
                "advance through real space with alternating heel-to-toe foot plants, "
                "opposed arm swing and visible forward body displacement"
            ),
            follow_through=(
                "complete the final planted step, let momentum settle naturally and "
                "maintain balanced posture"
            ),
            physics=(
                "keep the centre of mass above the support foot; each planted foot stays "
                "fixed against the floor until toe-off"
            ),
            secondary_motion=(
                "clothing, hair and accessories respond with restrained delayed motion"
            ),
            avoid=("foot sliding", "floating feet", "marching in place", "frozen arms"),
        ),
    ),
    (
        re.compile(r"\b(?:run|runs|running|sprint|sprints)\b", re.IGNORECASE),
        ActionProfile(
            preparation="lean slightly into the travel direction and load the rear leg",
            execution=(
                "drive forward with alternating push-off, brief airborne phases, active "
                "arm counter-swing and measurable displacement through the scene"
            ),
            follow_through="decelerate through shorter planted steps without snapping still",
            physics=(
                "preserve momentum, ground reaction and consistent travel direction; land "
                "under the body rather than skating across the surface"
            ),
            secondary_motion="wardrobe and hair trail acceleration and settle after deceleration",
            avoid=("running in place", "ground skating", "teleporting", "rigid torso"),
        ),
    ),
    (
        re.compile(r"\b(?:turn|turns|turning|pivot|pivots)\b", re.IGNORECASE),
        ActionProfile(
            preparation="move the eyes first, then begin the head turn",
            execution=(
                "rotate head, shoulders, torso and hips in a natural staggered sequence "
                "while the planted foot supports the pivot"
            ),
            follow_through="settle into the new facing direction with a small posture correction",
            physics="maintain balance and preserve facial structure through the changing angle",
            secondary_motion="hair and loose clothing lag slightly behind the torso rotation",
            avoid=("instantaneous rotation", "twisted anatomy", "identity drift", "head snapping"),
        ),
    ),
    (
        re.compile(r"\b(?:reach|reaches|grab|grabs|pick up|picks up)\b", re.IGNORECASE),
        ActionProfile(
            preparation="look toward the target and shift the torso within comfortable reach",
            execution=(
                "extend shoulder, elbow and wrist in sequence; articulate the fingers around "
                "the target and establish visible contact before applying force"
            ),
            follow_through="bring the object under control and let the arm settle under its weight",
            physics="preserve object scale, grip contact, occlusion and believable carried weight",
            secondary_motion="the hand, sleeve and held object move as one connected system after contact",
            avoid=("extra fingers", "object teleporting", "missed contact", "object fusion"),
        ),
    ),
    (
        re.compile(r"\b(?:gesture|gestures|point|points|wave|waves)\b", re.IGNORECASE),
        ActionProfile(
            preparation="lead the gesture with gaze and a subtle shoulder shift",
            execution="move the arm through a readable arc with stable elbow, wrist and fingers",
            follow_through="hold the communicative pose briefly, then relax without a sudden reset",
            physics="keep the shoulder attached and preserve hand scale and joint limits",
            secondary_motion="add natural blinking, breathing and a small responsive head motion",
            avoid=("rubber arms", "hand flicker", "extra fingers", "frozen expression"),
        ),
    ),
    (
        re.compile(r"\b(?:sit|sits|sitting|stand up|stands up)\b", re.IGNORECASE),
        ActionProfile(
            preparation="place both feet for support and shift the torso over the base of support",
            execution="bend or extend hips and knees together while controlling the torso vertically",
            follow_through="transfer weight fully to the seat or feet and stabilize posture",
            physics="maintain contact with the floor and seat; avoid passing through furniture",
            secondary_motion="clothing compresses and relaxes around the hips and knees",
            avoid=("body intersection", "floating", "knee inversion", "instant pose change"),
        ),
    ),
)

GENERIC_PROFILE = ActionProfile(
    preparation="begin with a readable anticipatory shift of gaze, posture and weight",
    execution="perform the requested movement continuously with articulated full-body motion",
    follow_through="complete the action, absorb momentum and settle into a stable end pose",
    physics="preserve gravity, contact, momentum, joint limits and consistent spatial displacement",
    secondary_motion="include natural breathing, blinking and restrained cloth or hair inertia",
    avoid=("static pose", "motion morphing", "teleporting", "frozen background"),
)


def _profile_for(action: str) -> ActionProfile:
    for pattern, profile in ACTION_PROFILES:
        if pattern.search(action):
            return profile
    return GENERIC_PROFILE


def compile_action_prompt(
    action: str,
    *,
    duration_seconds: float,
    framing: str,
    camera_motion: str,
) -> str:
    """Expand an action into timed, physically explicit model directions."""

    cleaned = " ".join(action.strip().split())
    if not cleaned:
        raise ValueError("Action direction cannot be empty")
    profile = _profile_for(cleaned)
    preparation_end = duration_seconds * 0.2
    execution_end = duration_seconds * 0.82
    camera_instruction = (
        f"Coordinate the {camera_motion} camera with the subject while preserving "
        f"the {framing} composition; camera motion must reveal real subject displacement"
    )
    return "\n".join(
        (
            f"REQUESTED ACTION: {cleaned}",
            f"0.00-{preparation_end:.2f}s ANTICIPATION: {profile.preparation}.",
            f"{preparation_end:.2f}-{execution_end:.2f}s PRIMARY MOTION: "
            f"{profile.execution}.",
            f"{execution_end:.2f}-{duration_seconds:.2f}s FOLLOW-THROUGH: "
            f"{profile.follow_through}.",
            f"BODY MECHANICS: {profile.physics}.",
            f"SECONDARY MOTION: {profile.secondary_motion}.",
            f"CAMERA COORDINATION: {camera_instruction}.",
            "ACTION-SPECIFIC AVOID: " + ", ".join(profile.avoid) + ".",
        )
    )
