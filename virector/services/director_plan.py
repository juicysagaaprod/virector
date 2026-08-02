import re

from virector.models.director_plan import (
    DialogueCue,
    DirectorPlan,
    DirectorSegment,
    PlanReference,
)
from virector.models.omni_asset import (
    AssetRole,
    BindingModality,
    OmniMediaType,
    ReferenceBinding,
    ReferenceOperation,
    ReferenceRole,
)
from virector.services.references import normalize_reference_mentions

TIME_RANGE_PATTERN = re.compile(
    r"(?m)^\s*(\d+):(\d{2}(?:\.\d+)?)\s*[\u2013\u2014-]\s*"
    r"(\d+):(\d{2}(?:\.\d+)?)\s*$"
)
REFERENCE_PATTERN = re.compile(
    r"(?mi)^\s*(@(image|video|audio)([1-9]))\s*:\s*(.+?)\s*$"
)
DIALOGUE_PATTERN = re.compile(
    r"(?mi)^\s*(?!(?:sound|end sound|message|transition|title card)\s*:)"
    r"([^:\n]{1,100}?)\s*:\s*[\u201c\"]([^\u201d\"\n]+)[\u201d\"]\s*$"
)
CUE_PATTERN = re.compile(
    r"(?i)(Sound|End sound|Message|Transition|Title card)\s*:\s*(.*?)"
    r"(?=\s+(?:Sound|End sound|Message|Transition|Title card)\s*:|$)",
    re.IGNORECASE | re.MULTILINE,
)
IMAGE_TAG_PATTERN = re.compile(r"(?<![a-z0-9_])@image([1-9])\b", re.IGNORECASE)
OMNI_TAG_PATTERN = re.compile(
    r"(?<![a-z0-9_])@(image|video|audio)([1-9])\b",
    re.IGNORECASE,
)
METADATA_LABELS = ("Model", "Duration", "Method", "Purpose", "Voice")
METADATA_PATTERN = re.compile(
    rf"(?is)\b({'|'.join(METADATA_LABELS)})\s*:\s*(.*?)"
    rf"(?=\s+\b(?:{'|'.join(METADATA_LABELS)})\s*:|$)"
)

ENVIRONMENT_WORDS = {
    "background",
    "building",
    "exterior",
    "house",
    "interior",
    "landscape",
    "location",
    "office",
    "restaurant",
    "room",
    "scene",
    "street",
    "world",
}
PROP_WORDS = {
    "account",
    "bottle",
    "car",
    "camera",
    "document",
    "folder",
    "id",
    "item",
    "logo",
    "phone",
    "product",
    "smartphone",
    "thermos",
    "vehicle",
}
TEXT_WORDS = {
    "document",
    "folder",
    "id",
    "logo",
    "message",
    "phone",
    "sign",
    "smartphone",
    "text",
}
WARDROBE_WORDS = {"clothing", "costume", "dress", "outfit", "uniform", "wardrobe"}
PERSON_WORDS = {
    "boy",
    "cat",
    "character",
    "dad",
    "dog",
    "father",
    "girl",
    "man",
    "mother",
    "person",
    "woman",
}
ANGLE_WORDS = {
    "back",
    "front",
    "left",
    "profile",
    "rear",
    "right",
    "side",
    "three",
    "quarter",
    "view",
}
VISUAL_USAGE_LABELS = (
    "thermos bottle",
    "coffee shop",
    "home scene",
    "storyboard panels",
    "storyboard",
    "restaurant",
    "background",
    "environment",
    "outfit",
    "uniform",
    "wardrobe",
    "logo",
    "camera",
    "product",
    "bottle",
    "character",
    "woman",
    "girl",
    "boy",
    "father",
    "dad",
    "man",
    "cat",
    "dog",
)


def _timestamp(minutes: str, seconds: str) -> float:
    return int(minutes) * 60 + float(seconds)


def _normalise(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _asset_roles(
    description: str,
    media_type: OmniMediaType = OmniMediaType.image,
) -> list[AssetRole]:
    normalised = _normalise(description)
    words = set(normalised.split())
    roles: list[AssetRole] = []
    if media_type == OmniMediaType.video:
        if words & {
            "action",
            "actions",
            "choreography",
            "gesture",
            "motion",
            "movement",
            "performance",
            "running",
        }:
            roles.append(AssetRole.motion)
        if words & {"camera", "framing", "lens", "perspective"}:
            roles.append(AssetRole.camera)
        if words & {"effect", "effects", "particles", "trajectory", "wings"}:
            roles.append(AssetRole.effect)
        return roles or [AssetRole.motion]
    if media_type == OmniMediaType.audio:
        if words & {
            "dialogue",
            "delivery",
            "speech",
            "speaker",
            "vocal",
            "voice",
        }:
            roles.append(AssetRole.voice)
        if words & {
            "audio",
            "beat",
            "music",
            "rhythm",
            "song",
            "sound",
            "soundtrack",
        }:
            roles.append(AssetRole.audio)
        return roles or [AssetRole.audio]
    document_or_graphic = bool(
        words & {"document", "folder", "id", "logo", "message", "sign", "text"}
    )
    if normalised.startswith("unlabelled image"):
        return [AssetRole.style]
    if words & {"storyboard", "panel", "panels"}:
        roles.extend([AssetRole.storyboard, AssetRole.composition])
    if words & ENVIRONMENT_WORDS and not document_or_graphic:
        roles.extend([AssetRole.environment, AssetRole.composition])
    camera_control = bool(words & {"framing", "lens", "movement", "perspective"})
    if words & PROP_WORDS and not ("camera" in words and camera_control):
        roles.append(AssetRole.prop)
    if words & TEXT_WORDS:
        roles.append(AssetRole.readable_text)
    if words & WARDROBE_WORDS:
        roles.append(AssetRole.wardrobe)
    if words & {"framing", "lens"} or ("camera" in words and camera_control):
        roles.append(AssetRole.camera)
    if words & {"effect", "effects", "particles", "wings"}:
        roles.append(AssetRole.effect)
    if words & {"style", "aesthetic", "grade", "lighting"}:
        roles.append(AssetRole.style)
    if words & PERSON_WORDS or not roles:
        roles.extend([AssetRole.character_identity, AssetRole.wardrobe])
    return list(dict.fromkeys(roles))


def _identity_group(description: str, roles: list[AssetRole]) -> str | None:
    identity_role = AssetRole.character_identity in roles
    multi_angle_prop = AssetRole.prop in roles and bool(
        set(_normalise(description).split()) & ANGLE_WORDS
    )
    if not identity_role and not multi_angle_prop:
        return None
    normalised = _normalise(description)
    removable = {
        "appearance",
        "image",
        "reference",
        *ANGLE_WORDS,
    }
    words = [word for word in normalised.split() if word not in removable]
    return "-".join(words)[:120] or None


def _omni_asset(
    index: int,
    tag: str,
    description: str,
    media_type: OmniMediaType = OmniMediaType.image,
) -> PlanReference:
    roles = _asset_roles(description, media_type)
    if media_type == OmniMediaType.audio:
        reference_role = ReferenceRole.AUDIO
    elif media_type == OmniMediaType.video:
        reference_role = (
            ReferenceRole.CAMERA
            if AssetRole.camera in roles and AssetRole.motion not in roles
            else ReferenceRole.MOTION
        )
    elif AssetRole.environment in roles:
        reference_role = ReferenceRole.WORLD_ENVIRONMENT
    elif AssetRole.character_identity in roles:
        reference_role = ReferenceRole.CHARACTER_IDENTITY
    else:
        reference_role = ReferenceRole.PROP
    return PlanReference(
        index=index,
        tag=tag.lower(),
        media_type=media_type,
        description=description.strip(),
        roles=roles,
        reference_role=reference_role,
        identity_group=_identity_group(description, roles),
    )


def _enrich_nonvisual_roles(
    assets: list[PlanReference],
    prompt: str,
) -> None:
    """Infer video/audio controls from both labels and prompt usage."""

    lines = prompt.splitlines()
    for asset in assets:
        if asset.media_type == OmniMediaType.image:
            continue
        usage = " ".join(line for line in lines if asset.tag in line.lower())
        asset.roles = _asset_roles(
            f"{asset.description} {usage}",
            asset.media_type,
        )


def _usage_description(tag: str, prompt: str) -> str:
    """Infer the closest named visual element associated with an image tag."""

    candidates: list[tuple[int, str]] = []
    lowered = prompt.lower()
    for tag_match in re.finditer(re.escape(tag), lowered):
        tag_position = tag_match.start()
        window_start = max(0, tag_position - 120)
        window_end = min(len(lowered), tag_match.end() + 120)
        window = lowered[window_start:window_end]
        for label in VISUAL_USAGE_LABELS:
            for label_match in re.finditer(rf"\b{re.escape(label)}\b", window):
                absolute_position = window_start + label_match.start()
                candidates.append((abs(absolute_position - tag_position), label))
    if not candidates:
        return f"Unlabelled {tag.removeprefix('@')}"

    label = min(candidates, key=lambda item: item[0])[1]
    descriptions = {
        "storyboard": "Storyboard composition",
        "storyboard panels": "Storyboard composition",
        "background": "World background",
        "environment": "World environment",
        "home scene": "Home environment",
        "coffee shop": "Coffee shop environment",
        "restaurant": "Restaurant environment",
        "outfit": "Outfit wardrobe",
        "uniform": "Uniform wardrobe",
        "wardrobe": "Outfit wardrobe",
        "logo": "Logo graphic",
        "camera": "Camera product",
        "product": "Product item",
        "bottle": "Bottle product",
        "thermos bottle": "Thermos bottle product",
    }
    return descriptions.get(label, f"{label.title()} character")


def _enrich_unlabelled_visual_roles(
    assets: list[PlanReference],
    prompt: str,
) -> None:
    """Infer visual roles when users describe assets only inside prose."""

    for asset in assets:
        if asset.media_type != OmniMediaType.image:
            continue
        if not asset.description.lower().startswith("unlabelled image"):
            continue
        description = _usage_description(asset.tag, prompt)
        roles = _asset_roles(description, asset.media_type)
        asset.description = description
        asset.roles = roles
        asset.reference_role = (
            ReferenceRole.WORLD_ENVIRONMENT
            if AssetRole.environment in roles
            else ReferenceRole.CHARACTER_IDENTITY
            if AssetRole.character_identity in roles
            else ReferenceRole.PROP
        )
        asset.identity_group = _identity_group(description, roles)


def _apply_two_image_legacy_contract(assets: list[PlanReference]) -> None:
    """Apply the documented @image1 character/@image2 world legacy mapping."""

    images = [asset for asset in assets if asset.media_type == OmniMediaType.image]
    by_tag = {asset.tag: asset for asset in images}
    character = by_tag.get("@image1")
    world = by_tag.get("@image2")
    if character is not None:
        character.description = "Primary character identity and wardrobe"
        character.roles = [AssetRole.character_identity, AssetRole.wardrobe]
        character.reference_role = ReferenceRole.CHARACTER_IDENTITY
        character.identity_group = "primary-character"
    if world is not None:
        world.description = "World environment and location design"
        world.roles = [AssetRole.environment, AssetRole.composition]
        world.reference_role = ReferenceRole.WORLD_ENVIRONMENT
        world.identity_group = None


def _reference_operations(
    text: str,
    *,
    combined: bool,
    maintain: bool,
) -> list[ReferenceOperation]:
    normalised = _normalise(text)
    operations: list[ReferenceOperation] = []
    patterns = (
        (ReferenceOperation.extract, r"\bextract\b"),
        (ReferenceOperation.combine, r"\bcombine\b"),
        (ReferenceOperation.follow, r"\b(?:follow|following)\b"),
        (ReferenceOperation.replace, r"\breplace\b"),
        (ReferenceOperation.generate, r"\bgenerate\b"),
        (
            ReferenceOperation.maintain,
            r"\b(?:maintain|maintaining|preserve|preserving|keep|keeping|consistent)\b",
        ),
    )
    for operation, pattern in patterns:
        if re.search(pattern, normalised):
            operations.append(operation)
    if not operations or ReferenceOperation.reference not in operations:
        operations.insert(0, ReferenceOperation.reference)
    if combined and ReferenceOperation.combine not in operations:
        operations.append(ReferenceOperation.combine)
    if maintain and ReferenceOperation.maintain not in operations:
        operations.append(ReferenceOperation.maintain)
    return operations


def _preservation_constraints(roles: list[AssetRole]) -> list[str]:
    constraints: list[str] = []
    if AssetRole.character_identity in roles:
        constraints.append("preserve face, identity and body proportions")
    if AssetRole.wardrobe in roles:
        constraints.append("preserve clothing, colours and accessories")
    if AssetRole.environment in roles:
        constraints.append("preserve location design, lighting and spatial layout")
    if AssetRole.prop in roles:
        constraints.append("preserve prop shape, material and distinguishing details")
    if AssetRole.readable_text in roles:
        constraints.append("preserve readable text and graphic layout")
    if AssetRole.composition in roles:
        constraints.append("follow composition, framing and spatial relationships")
    if AssetRole.storyboard in roles:
        constraints.append("follow storyboard panel order")
    if AssetRole.camera in roles:
        constraints.append("follow camera framing and movement")
    if AssetRole.effect in roles:
        constraints.append("follow the referenced visual effect trajectory")
    if AssetRole.motion in roles:
        constraints.append("follow action timing, pose sequence and physical motion")
    if AssetRole.voice in roles:
        constraints.append("follow speaker identity, delivery and emotional cadence")
    if AssetRole.audio in roles:
        constraints.append("follow rhythm, sound timing and musical structure")
    if AssetRole.style in roles:
        constraints.append("preserve the referenced visual style")
    return constraints or ["preserve the referenced visual features"]


def _reference_bindings(
    segment: DirectorSegment,
    assets: list[PlanReference],
) -> list[ReferenceBinding]:
    by_tag = {asset.tag: asset for asset in assets}
    visible_assets = [by_tag[tag] for tag in segment.reference_tags if tag in by_tag]
    grouped: dict[str, list[PlanReference]] = {}
    for asset in visible_assets:
        key = asset.identity_group or asset.tag
        grouped.setdefault(key, []).append(asset)

    bindings: list[ReferenceBinding] = []
    combined = len(visible_assets) > 1
    for grouped_assets in grouped.values():
        roles = list(
            dict.fromkeys(
                role for asset in grouped_assets for role in asset.roles
            )
        )
        target = grouped_assets[0].description
        constraints = _preservation_constraints(roles)
        operations = _reference_operations(
            segment.action,
            combined=combined or len(grouped_assets) > 1,
            maintain=True,
        )
        bindings.append(
            ReferenceBinding(
                asset_tags=[asset.tag for asset in grouped_assets],
                modality=BindingModality.visual,
                operations=operations,
                controls=roles,
                target=target,
                instruction=(
                    f"Use {' and '.join(asset.tag for asset in grouped_assets)} "
                    f"for {target}; " + "; ".join(constraints) + "."
                ),
                visible=True,
                strength=(
                    0.95 if AssetRole.character_identity in roles else 0.9
                ),
            )
        )

    for tag in segment.asset_tags:
        asset = by_tag.get(tag)
        if asset is None or asset.media_type == OmniMediaType.image:
            continue
        operations = _reference_operations(
            segment.action,
            combined=len(segment.asset_tags) > 1,
            maintain=True,
        )
        modality_roles: list[tuple[BindingModality, list[AssetRole]]] = []
        if asset.media_type == OmniMediaType.video:
            if AssetRole.motion in asset.roles:
                modality_roles.append((BindingModality.motion, [AssetRole.motion]))
            if AssetRole.camera in asset.roles:
                modality_roles.append((BindingModality.camera, [AssetRole.camera]))
            if AssetRole.effect in asset.roles:
                modality_roles.append((BindingModality.effect, [AssetRole.effect]))
        else:
            if AssetRole.voice in asset.roles:
                modality_roles.append((BindingModality.voice, [AssetRole.voice]))
            if AssetRole.audio in asset.roles:
                modality_roles.append((BindingModality.audio, [AssetRole.audio]))
        for modality, roles in modality_roles:
            bindings.append(
                ReferenceBinding(
                    asset_tags=[asset.tag],
                    modality=modality,
                    operations=operations,
                    controls=roles,
                    target=asset.description,
                    instruction=(
                        f"Use {asset.tag} for {asset.description}; "
                        + "; ".join(_preservation_constraints(roles))
                        + "."
                    ),
                    visible=False,
                    strength=0.9,
                )
            )

    voice_tags: set[str] = set()
    for cue in segment.dialogue:
        tag = cue.speaker_reference_tag
        if not tag or tag in voice_tags or tag not in by_tag:
            continue
        voice_tags.add(tag)
        asset = by_tag[tag]
        bindings.append(
            ReferenceBinding(
                asset_tags=[tag],
                modality=BindingModality.voice,
                operations=[ReferenceOperation.reference],
                controls=[AssetRole.voice],
                target=cue.speaker,
                instruction=(
                    f"Link dialogue and emotional performance to {cue.speaker} "
                    f"({tag}); do not make the speaker visible unless the shot "
                    "also contains a visual binding for that asset."
                ),
                visible=False,
                strength=0.9,
            )
        )
    return bindings


def _reference_for_name(
    name: str,
    references: list[PlanReference],
) -> str | None:
    target = _normalise(name)
    if not target:
        return None
    target_words = set(target.split())
    object_words = {
        "account",
        "exterior",
        "folder",
        "house",
        "id",
        "office",
        "phone",
        "smartphone",
    }
    best: tuple[int, str] | None = None
    for reference in references:
        if reference.media_type != OmniMediaType.image:
            continue
        description = _normalise(reference.description)
        description_words = set(description.split())
        score = len(target_words & description_words)
        if description == target:
            score += 10
        if description_words & object_words:
            score -= 2
        if score > 0 and (best is None or score > best[0]):
            best = (score, reference.tag)
    return best[1] if best else None


def _metadata(header: str) -> dict[str, str]:
    return {
        label.lower(): value.strip()
        for label, value in METADATA_PATTERN.findall(header)
        if value.strip()
    }


def _duration(
    metadata: dict[str, str],
    final_segment_end: float,
    fallback_duration: float = 4.0,
) -> float:
    configured = metadata.get("duration", "")
    match = re.search(r"\d+(?:\.\d+)?", configured)
    duration = float(match.group()) if match else final_segment_end
    if duration <= 0:
        duration = fallback_duration
    return max(duration, final_segment_end)


def _title(header: str) -> str:
    for line in header.splitlines():
        candidate = line.strip().lstrip("#").strip()
        if not candidate or candidate.lower() in {
            "image references",
            "video references",
            "audio references",
        }:
            continue
        if re.match(r"(?i)^(?:model|duration|method|purpose|voice)\s*:", candidate):
            continue
        if len(candidate) > 200:
            continue
        return candidate
    return "Untitled clip"


def _narrative(body: str) -> str:
    dialogue_spans = {match.span() for match in DIALOGUE_PATTERN.finditer(body)}
    lines: list[str] = []
    cursor = 0
    for line in body.splitlines(keepends=True):
        start, end = cursor, cursor + len(line)
        cursor = end
        if any(start >= span_start and end <= span_end + 1 for span_start, span_end in dialogue_spans):
            continue
        if CUE_PATTERN.search(line):
            cleaned = CUE_PATTERN.sub("", line).strip()
            if cleaned:
                lines.append(cleaned)
            continue
        cleaned = line.strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines).strip() or "Continue the directed performance."


def _reference_tags(
    body: str,
    narrative: str,
    references: list[PlanReference],
) -> list[str]:
    tags = {f"@image{value}" for value in IMAGE_TAG_PATTERN.findall(body)}
    normalised_narrative = f" {_normalise(narrative)} "
    object_words = {
        "account",
        "exterior",
        "folder",
        "house",
        "id",
        "office",
        "phone",
        "smartphone",
    }
    visible_action = (
        r"walks|stops|follows|drives|driving|looks|leaves|holds|holding|"
        r"stands|sits|runs|moves|turns|speaks|talks"
    )
    for reference in references:
        if reference.media_type != OmniMediaType.image:
            continue
        description = _normalise(reference.description)
        words = description.split()
        full_name_visible = (
            len(words) > 1
            and not set(words) & object_words
            and f" {description} " in normalised_narrative
        )
        action_visible = False
        if words and not set(words) & object_words:
            name = re.escape(words[0])
            action_visible = bool(
                re.search(
                    rf"(?i)\b{name}\s+(?:{visible_action})\b|"
                    rf"\b(?:toward|towards|on|at)\s+{name}\b",
                    narrative,
                )
            )
        if full_name_visible or action_visible:
            tags.add(reference.tag)
    return sorted(tags, key=lambda tag: int(tag.removeprefix("@image")))


def _asset_tags(body: str) -> list[str]:
    media_order = {"image": 0, "video": 1, "audio": 2}
    tags = {
        f"@{media_type.lower()}{int(index)}"
        for media_type, index in OMNI_TAG_PATTERN.findall(body)
    }
    return sorted(
        tags,
        key=lambda tag: (
            media_order[re.match(r"@(image|video|audio)", tag).group(1)],
            int(re.search(r"\d+$", tag).group()),
        ),
    )


def _segment(
    index: int,
    start_seconds: float,
    end_seconds: float,
    body: str,
    references: list[PlanReference],
) -> DirectorSegment:
    dialogue: list[DialogueCue] = []
    for match in DIALOGUE_PATTERN.finditer(body):
        speaker_label = match.group(1).strip()
        speaker, separator, delivery = speaker_label.partition(",")
        dialogue.append(
            DialogueCue(
                speaker=speaker.strip(),
                text=match.group(2).strip(),
                delivery=delivery.strip() if separator else None,
                speaker_reference_tag=_reference_for_name(speaker, references),
            )
        )

    sound_cues: list[str] = []
    on_screen_text: list[str] = []
    transition: str | None = None
    title_card: str | None = None
    for label, value in CUE_PATTERN.findall(body):
        cleaned = value.strip()
        lowered = label.lower()
        if lowered in {"sound", "end sound"}:
            sound_cues.append(cleaned)
        elif lowered == "message":
            on_screen_text.append(cleaned)
        elif lowered == "transition":
            transition = cleaned
        elif lowered == "title card":
            title_card = cleaned

    narrative = _narrative(body)
    return DirectorSegment(
        index=index,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        duration_seconds=end_seconds - start_seconds,
        action=narrative,
        reference_tags=_reference_tags(body, narrative, references),
        asset_tags=_asset_tags(body),
        dialogue=dialogue,
        sound_cues=sound_cues,
        on_screen_text=on_screen_text,
        transition=transition,
        title_card=title_card,
    )


def compile_director_plan(
    direction_prompt: str,
    fallback_duration: float = 4.0,
) -> DirectorPlan:
    """Compile Virector's screenplay-style direction prompt into timed shots."""

    prompt = normalize_reference_mentions(
        direction_prompt.replace("\r\n", "\n").strip()
    )
    if not prompt:
        raise ValueError("Direction prompt is empty.")
    invalid_media_tags = sorted(
        {
            f"@{media_type.lower()}{int(index)}"
            for media_type, index in OMNI_TAG_PATTERN.findall(prompt)
            if media_type.lower() in {"video", "audio"} and int(index) > 3
        }
    )
    if invalid_media_tags:
        raise ValueError(
            "Video and audio reference indexes are limited to 1-3: "
            + ", ".join(invalid_media_tags)
            + "."
        )

    time_matches = list(TIME_RANGE_PATTERN.finditer(prompt))
    first_time_offset = time_matches[0].start() if time_matches else len(prompt)
    planning_header = prompt[:first_time_offset]
    references = [
        _omni_asset(
            int(index),
            tag,
            description,
            OmniMediaType(media_type.lower()),
        )
        for tag, media_type, index, description in REFERENCE_PATTERN.findall(
            planning_header
        )
        if not (
            media_type.lower() in {"video", "audio"} and int(index) > 3
        )
    ]
    media_order = {
        OmniMediaType.image: 0,
        OmniMediaType.video: 1,
        OmniMediaType.audio: 2,
    }
    references.sort(key=lambda reference: (media_order[reference.media_type], reference.index))
    has_reference_definitions = bool(references)
    defined_tags = {reference.tag for reference in references}
    mentioned_tags = {
        (media_type.lower(), int(index))
        for media_type, index in OMNI_TAG_PATTERN.findall(prompt)
        if not (media_type.lower() in {"video", "audio"} and int(index) > 3)
    }
    for media_name, value in sorted(
        mentioned_tags,
        key=lambda item: (media_order[OmniMediaType(item[0])], item[1]),
    ):
        tag = f"@{media_name}{value}"
        if tag not in defined_tags:
            references.append(
                _omni_asset(
                    value,
                    tag,
                    f"Unlabelled {media_name} {value}",
                    OmniMediaType(media_name),
                )
            )
    references.sort(key=lambda reference: (media_order[reference.media_type], reference.index))
    _enrich_unlabelled_visual_roles(references, prompt)
    _enrich_nonvisual_roles(references, prompt)
    if not has_reference_definitions:
        _apply_two_image_legacy_contract(references)

    reference_header = re.search(
        r"(?mi)^\s*(?:Image|Video|Audio) References\s*$",
        planning_header,
    )
    metadata_header = (
        planning_header[: reference_header.start()]
        if reference_header
        else planning_header
    )
    metadata = _metadata(metadata_header)

    segments: list[DirectorSegment] = []
    if time_matches:
        for index, match in enumerate(time_matches, start=1):
            body_start = match.end()
            body_end = (
                time_matches[index].start()
                if index < len(time_matches)
                else len(prompt)
            )
            start_seconds = _timestamp(match.group(1), match.group(2))
            end_seconds = _timestamp(match.group(3), match.group(4))
            segments.append(
                _segment(
                    index,
                    start_seconds,
                    end_seconds,
                    prompt[body_start:body_end].strip(),
                    references,
                )
            )
        for index, segment in enumerate(segments[1:], start=1):
            if re.search(r"(?i)\b(?:continues|same scene|same location)\b", segment.action):
                segment.reference_tags = sorted(
                    set(segment.reference_tags) | set(segments[index - 1].reference_tags),
                    key=lambda tag: int(tag.removeprefix("@image")),
                )
                segment.asset_tags = sorted(
                    set(segment.asset_tags) | set(segments[index - 1].asset_tags),
                    key=lambda tag: (
                        media_order[OmniMediaType(re.match(r"@(image|video|audio)", tag).group(1))],
                        int(re.search(r"\d+$", tag).group()),
                    ),
                )
    else:
        fallback_duration = _duration(metadata, 0, fallback_duration)
        segments.append(
            _segment(1, 0, fallback_duration, prompt, references)
        )

    for segment in segments:
        segment.reference_bindings = _reference_bindings(segment, references)

    duration_seconds = _duration(
        metadata,
        segments[-1].end_seconds,
        fallback_duration,
    )
    warnings: list[str] = []
    if not has_reference_definitions:
        warnings.append("No omni reference definitions were found.")
    for segment in segments:
        if not segment.reference_tags:
            warnings.append(
                f"Shot {segment.index} has no visual reference assigned."
            )
        for cue in segment.dialogue:
            if cue.speaker_reference_tag is None:
                warnings.append(
                    f"Shot {segment.index} speaker {cue.speaker!r} is not linked "
                    "to an image reference."
                )

    bound_tags = {
        tag
        for segment in segments
        for binding in segment.reference_bindings
        for tag in binding.asset_tags
    }
    for asset in references:
        if asset.tag not in bound_tags:
            warnings.append(
                f"Asset {asset.tag} is defined but not used by any shot."
            )

    return DirectorPlan(
        title=_title(metadata_header),
        requested_model=metadata.get("model"),
        method=metadata.get("method"),
        purpose=metadata.get("purpose"),
        voice_direction=metadata.get("voice"),
        duration_seconds=duration_seconds,
        omni_assets=references,
        segments=segments,
        warnings=list(dict.fromkeys(warnings)),
    )
