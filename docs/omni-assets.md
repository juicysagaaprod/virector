# OmniAsset and ReferenceBinding contract

Virector keeps the user workflow simple: upload ordered references, describe the
video in one direction prompt, and generate. Asset roles are inferred privately
when DirectorPlan is compiled; the Studio does not ask users to fill out a
separate role form.

## OmniAsset

An `OmniAsset` describes an ordered source and the controls it can provide:

- media type: image, video or audio;
- prompt tag such as `@image1`, `@video1` or `@audio1`;
- natural-language description;
- inferred roles such as character identity, wardrobe, environment, prop,
  readable text, composition, storyboard, motion, camera, effect, voice, audio
  or style; and
- an optional identity group that combines multiple angles of one character.

The Studio and API ingest up to nine images, three videos and three audio files,
with a maximum of twelve assets in one request. Images are currently required
to establish the first visual frame. Video and audio files are persisted and
transported to local and RunPod workers with their bindings.

## ReferenceBinding

A `ReferenceBinding` says how assets control one DirectorPlan segment. It stores:

- one or more asset tags;
- the modality being controlled;
- reference operations such as reference, extract, combine, follow, replace or
  maintain;
- the exact roles being conditioned;
- a model-facing preservation instruction;
- whether the asset should be visible; and
- conditioning strength.

Multiple views with the same inferred identity group are combined into one
identity binding. Dialogue creates a separate non-visible voice binding. This
allows an off-screen speaker to remain connected to a character without placing
that character in the shot.

## Worker behavior

PerformanceWorker uses bindings to select the relevant image, video and audio
files for each segment. It also embeds the structured binding instructions in
the segment prompt passed to the selected generation backend. Motion, camera,
effect, voice and audio bindings remain available to compatible specialized
backends.

Every compiled plan and binding is serialized inside `ShotSpec`, persisted in
the job manifest and sent unchanged to cloud workers.

## Current boundary

This compiler provides the correct orchestration and conditioning contract. A
generation model must still support the requested conditioning channel to obey
it. LTX remains a lightweight image-to-video preview model; it cannot become a
native multimodal performance model through prompting alone.
