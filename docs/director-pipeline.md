# Multi-reference director pipeline

## Audit evidence (2 August 2026)

The last paid validation job was `5a45112884d74ec99d16ce9ff144ef62`.
Its stored `shot_spec.json` proved that both `@image1` and `@image2` were
compiled as `character_identity`/`wardrobe` with the description `Man
character`. The world image therefore had no environment role. The omni job
also built `start_frame.png` from only the first image. The VACE call received
both images as an anonymous `reference_images` list and supplied neither a
first-frame control video nor a mask. There was no speech, TTS, audio-mixing or
lip-sync execution stage. The resulting 832x480 file contained 65 frames at 16
FPS even though the delivery spec requested 24 FPS. Visual inspection of its
contact sheet showed a dark, almost static subject with weak world grounding.

These are code/data findings, not guesses:

- `services/director_plan.py` used nearby words to infer unlabelled image roles;
  the same word could be selected for both images.
- `services/jobs.py` used the first image as the start frame in the omni path.
- `workers/vace_diffusers.py` previously passed `reference_images` only.
- `workers/conditioning.py` explicitly deferred voice and audio when specialist
  workers were absent.
- the deployed output did not apply the configured 24 FPS post-process, so the
  deployed container/release did not match the current source behavior or did
  not complete that post-process. A future `render_metrics.json` records this.

## Implemented architecture

```text
UI prompt + ordered uploads
        |
        v
DirectorPlan + ShotSpec.timeline
        |
        v
ResolvedReferenceMap
  @image1 -> CHARACTER_IDENTITY
  @image2 -> WORLD_ENVIRONMENT
        |
        +--> debug refs + resolved_reference_map.json
        |
        v
SceneAnchorProvider
  current: explicit fallback compositor (matte, colour match, floor shadow)
  future: approved neural scene-anchor adapter
        |
        v
per-shot structured prompt compiler (no unresolved @imageN tokens)
        |
        v
VACE R2V: first-frame video + black/white mask + independent refs
        |
        v
final frame extraction -> next-shot continuity anchor
        |
        v
FFmpeg normalized assembly/audio mixing
```

The prompt compiler includes a deterministic action-direction layer inspired by
public director-style guidance. It converts ordinary verbs such as walking,
running, turning, reaching, gesturing and sitting into timed anticipation,
primary-motion and follow-through phases. It adds action-specific biomechanics,
secondary clothing/hair motion, camera coordination and failure constraints.
This is model-agnostic prompt engineering and does not import Seedance code or
turn VACE into Seedance's unified architecture.
The public behavior reference is ByteDance's published Seedance 2.0 overview,
which demonstrates explicit action phases, physical details, camera language and
multimodal role assignment:
https://seed.bytedance.com/en/blog/official-launch-of-seedance-2-0

The fallback compositor is intentionally reported as a fallback and is not
described as neural scene generation. VACE receives the scene anchor as an
official first-frame R2V control: the first frame is retained by a black mask,
while later neutral frames have white generation masks. Character and world
images remain separate reference inputs.

The black mask is a model-conditioning contract, not a promise that the VAE
decoder will reproduce the conditioned pixels exactly. Virector therefore also
replaces VACE's decoded frame zero with the scene anchor before frame-rate
interpolation and overlays that anchor on delivery frame zero in the final
FFmpeg encode. This keeps the exact scene composition at the clip boundary
while allowing subsequent frames to be generated normally.

Every eligible job now saves:

- `character_reference.png`
- `world_reference.png` (when supplied)
- `resolved_reference_map.json`
- `scene_anchor.png`
- `compiled_shot_spec.json`
- `compiled_model_prompt.txt`
- per-shot compiled prompts and continuity final frames
- `conditioning_plan.json`
- `capability_plan.json`
- `render_metrics.json` after real VACE inference

## Capability truth table

| Need | Route | Current status |
| --- | --- | --- |
| Character/world R2V and action | Wan2.1 VACE 1.3B Diffusers | Connected in the RunPod image |
| Scene anchor | `SceneAnchorProvider` | Fallback compositor connected; neural provider not installed |
| Multi-shot continuity | final-frame VACE R2V + FFmpeg | Connected in code |
| Driving-video human motion | Wan2.2-Animate | Adapter present; checkpoint/runtime readiness must be verified on the worker |
| Speech-to-video | Wan2.2-S2V | Interface/routing only; checkpoint not installed or approved |
| Existing-video mouth correction | LatentSync | Interface/routing only; checkpoint not installed or approved |
| TTS | `SpeechProvider` | No verified self-hosted provider is connected |
| Audio stems | FFmpeg | Mixer connected; source stems/provider still required |

Virector never treats prompt text such as "accurate lip-sync" as proof of actual
lip synchronization. Speaking beats are marked `requires_install` until a real
S2V or lip-sync provider is configured.

## Verified upstream behavior

- Diffusers documents `WanVACEPipeline` inputs for `video`, `mask` and one or
  more `reference_images`: https://huggingface.co/docs/diffusers/api/pipelines/wan
- The official VACE guide defines first-frame R2V as a real first frame followed
  by gray missing frames, with black retained and white generated mask regions:
  https://github.com/ali-vilab/VACE/blob/main/UserGuide.md
- The official VACE project recommends short clips and first-clip extension for
  longer continuity. The Wan2.1 VACE 1.3B weights are Apache-2.0:
  https://github.com/ali-vilab/VACE
- Wan2.2 officially provides separate S2V and Animate checkpoints; their names in
  a prompt are not a substitute for installing/running those checkpoints:
  https://github.com/Wan-Video/Wan2.2
- LatentSync is a post-process for existing video plus audio; upstream reports
  8 GB minimum VRAM for v1.5 and 18 GB for v1.6:
  https://github.com/bytedance/LatentSync

## Installed/configured components

- Local model disk: `ltxv-2b-0.9.8-distilled.safetensors`, 6.34 GB.
- RunPod image target: `Wan-AI/Wan2.1-VACE-1.3B-diffusers`, approximately
  19 GB, loaded through Diffusers 0.39.0 and PyTorch 2.11.0/CUDA 12.8.
- Wan2.2 source in the RunPod image is pinned to commit
  `42bf4cfaa384bc21833865abc2f9e6c0e67233dc`.
- The RunPod image is configured to use
  `Wan-AI/Wan2.2-Animate-14B`, but exact persistent-volume checkpoint hashes are
  not recorded locally and must be verified before claiming worker readiness.
- No Wan2.2-S2V or LatentSync checkpoint was downloaded in this change.

The VACE model card lists a 19 GB checkpoint. The guarded quantized backend is
configured for at least 7.5 GB VRAM and 10 GB system RAM, with 24 GB system RAM
recommended. Those guards are loading thresholds, not quality guarantees. The
14B S2V/Animate routes should be treated as cloud GPU workloads; do not promise
24 GB compatibility until a controlled preflight succeeds.

## Controlled validation sequence

No paid job is submitted automatically. Validate in this order, requesting
approval before each submission:

1. 4 seconds, 480p, full-body walking, no speech/music.
2. 4 seconds, 480p, dialogue close-up with timing-authority audio.
3. 8 seconds, 480p, three independently rendered/assembled shots.
4. One 720p production test only after all prior checks pass.

For each run, complete the generated `render_metrics.json` manual fields and
score character identity, environment identity and lip sync. Inspect the actual
video, not only worker status.

At the current published RTX 4090 Serverless rate of $1.10/hour, 90-180 GPU
seconds costs roughly $0.03-$0.06. A cold model load can raise a single request
to roughly $0.10-$0.30. RunPod pricing and the endpoint's selected tier must be
checked immediately before approval.
