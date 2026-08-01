# Virector

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/juicysagaaprod/virector?quickstart=1)

Virector is a full-stack, director-controlled AI video application. Its
production web client sends ordered image, video and audio omni references with
one natural-language direction prompt to a model-independent Python render
service.

The starter includes:

- Next.js and TypeScript production web client
- FastAPI render API
- Gradio internal model-testing panel
- Pydantic `ShotSpec`
- Pydantic `DirectorPlan` with screenplay-to-timeline compilation
- Hidden `OmniAsset` and per-shot `ReferenceBinding` compilation
- Capability-aware conditioning routes with explicit deferred-stage reporting
- Ordered `@image`, `@video` and `@audio` uploads with persistent worker transport
- Transparent-PNG character compositor
- Job manifests and output folders
- Pluggable workers for local LTX, self-hosted VACE and future cloud GPUs
- Docker configuration
- Tests for shot validation and image composition

The mock worker remains the default so the compositor can run without a GPU.
The optional LTX runtime uses the official LTX-Video 2B distilled checkpoint
through Diffusers. If the runtime or CUDA is unavailable, Virector reports the
reason through `/api/health` and safely falls back to the mock worker.

The dedicated RunPod Serverless image and deployment checklist are documented
in [`docs/runpod.md`](docs/runpod.md). The image uses signed R2 URLs for media,
keeps model/cache data under `/runpod-volume`, and publishes manually through
the **Publish RunPod worker image** GitHub Actions workflow.

## Cloud staging foundation

Virector keeps generated artifacts behind a storage interface. Local development
continues to use `VIRECTOR_STORAGE_BACKEND=local`. Staging and production can use
any private S3-compatible service, with Cloudflare R2 as the recommended media
store. Completed job manifests, references, internal conditioning images and
videos are uploaded under:

```text
<key-prefix>/renders/<job-id>/
```

Video responses keep the stable Virector API route and redirect to a short-lived
presigned download URL. The bucket does not need to be public.

Copy `.env.staging.example` into the deployment platform's secret manager and
replace every placeholder. Never commit a populated `.env.staging` file. When
`VIRECTOR_STORAGE_BACKEND=s3`, startup rejects missing endpoint, bucket or access
credentials instead of silently writing ephemeral container files.

Supabase Auth is optional in local mode and required by the staging template.
The browser uses only the public project URL and publishable key. FastAPI
verifies every bearer token against Supabase signing keys before passing the
trusted owner and selected project to the job repository. `MockWorker` remains
the staging default while the production GPU worker is being provisioned.

### Supabase database schema

The version-controlled schema under `supabase/` defines private project and
render metadata. It includes:

- projects owned by Supabase Auth users;
- render jobs with status, progress, attempts and `ShotSpec` JSON;
- R2 object metadata for references, manifests and generated videos;
- append-only progress and error events; and
- Row-Level Security policies that restrict authenticated reads to the owner.

Browser clients cannot mutate render jobs directly. FastAPI will perform those
writes through its private database connection, while authenticated clients can
read only their own projects and render history.

FastAPI selects its metadata repository with
`VIRECTOR_JOB_REPOSITORY_BACKEND`. The default `local` mode writes an atomic
`job_state.json` beside each render, recording accepted, validating, rendering
and terminal events. The `postgres` adapter uses a small Psycopg connection pool
and requires both a verified Supabase Auth owner ID and a project ID; it refuses
anonymous writes rather than creating fake production users. Authenticated
video downloads are also checked against the job owner.

Render submission returns `202 Accepted` immediately and runs inference outside
the HTTP request. The web client polls the owner-scoped
`GET /api/renders/{job_id}` endpoint for progress and retrieves the protected
video only after the job reaches `completed`. This keeps FastAPI responsive
while local or cloud GPU work is running.

### Automatic DirectorPlan compilation

The production Studio compiles every screenplay-style direction prompt in the
backend when **Generate video** is selected. Use headings such as `Image References`, ordered `@image1:` through
`@image9:` definitions and timed ranges such as `0:00-0:03`. DirectorPlan
extracts:

- shot timing and duration;
- the visual references required by each shot;
- dialogue, delivery notes and speaker-to-character reference links;
- sound cues and on-screen messages; and
- transitions and title cards.

The structured plan is stored privately inside the render job's `ShotSpec`; the
normal Studio does not expose an analysis step or timeline. The local LTX worker
still treats the plan as a single preview request unless Performance mode is
enabled. The authenticated `POST /api/director-plans/preview` endpoint remains
available for developer diagnostics.

New plans infer character identity, wardrobe, environment, prop, readable-text,
composition and style roles automatically. They also compile visible and
off-screen voice bindings, multi-angle identity groups, preservation constraints
and reference operations such as extract, combine, follow, replace and maintain.
The internal contract is documented in [`docs/omni-assets.md`](docs/omni-assets.md).
The compiler is protected by a guide-pattern compatibility suite; see
[`docs/prompt-guide-compatibility.md`](docs/prompt-guide-compatibility.md) for
supported image/directing patterns and the executable video/audio backlog.

### Multi-shot PerformanceWorker

`PerformanceWorker` executes a hidden DirectorPlan sequentially. For each timed
shot it:

1. selects only the `@image` references assigned to that segment;
2. creates a segment-specific prompt, duration and deterministic continuity seed;
3. delegates generation to the configured LTX or VACE video worker;
4. reports shot-level progress through the normal render-status endpoint; and
5. joins the completed MP4 files into one final `preview.mp4` with FFmpeg.

Enable orchestration explicitly after choosing hardware suitable for every
segment:

```text
VIRECTOR_WORKER_MODE=performance
VIRECTOR_PERFORMANCE_SEGMENT_WORKER=ltx
```

Use `vace` instead of `ltx` on a sufficiently large cloud GPU. Local mode stays
on the existing worker until this setting is changed, preventing a multi-shot
screenplay from unexpectedly starting several long renders. The PerformanceWorker
currently assembles video segments; synthesized speech, native lip-sync and a
mixed soundtrack require the upcoming audio-performance backend.

### Capability-aware conditioning routes

Performance mode now compiles every `ReferenceBinding` into a private
`conditioning_plan.json` beside the render. Each route records its shot,
modality, assets, target backend and one of five honest capability states:

- `native`: the current generator applies that control directly;
- `limited`: it receives only partial or text-based conditioning;
- `external`: a configured specialist stage is waiting to apply it;
- `applied`: the specialist stage completed successfully; or
- `deferred`: no specialist stage is connected, so the control is preserved
  without claiming it was executed.

VACE is treated as native for ordered visual references. LTX is marked limited
because it currently uses the primary reference as a start frame. Motion and
camera references can target Wan2.2-Animate; voice and lip-sync can target
InfiniteTalk or HunyuanVideo-Avatar; soundtrack assembly can target FFmpeg.
These targets default to `disabled` until their cloud workers are actually
deployed:

```dotenv
VIRECTOR_PERFORMANCE_MOTION_BACKEND=disabled
VIRECTOR_PERFORMANCE_SPEECH_BACKEND=disabled
VIRECTOR_PERFORMANCE_AUDIO_BACKEND=disabled
```

The health endpoint exposes the selected targets. A render result also names
any deferred modalities, preventing a static base-model preview from being
mistaken for completed motion transfer or lip-sync.

When the cloud worker has `VIRECTOR_PERFORMANCE_MOTION_BACKEND=wan-animate`,
motion routes execute after base shot generation. Virector extracts
the first frame of the composed/base shot, preprocesses the tagged driving
`@video` into pose and face controls with the official Wan2.2 tools, runs
Wan2.2-Animate in animation mode, and changes those routes to `applied`. The
isolated runtime is deliberately unavailable in the local Docker image.
Camera-video bindings remain deferred until a dedicated camera-trajectory
backend is connected; Wan2.2-Animate is not reported as applying that control.

To enable authentication locally, set the same public values in the root `.env`
for both the API and browser, then rebuild the web image:

```text
VIRECTOR_SUPABASE_URL=https://YOUR_PROJECT.supabase.co
VIRECTOR_SUPABASE_PUBLISHABLE_KEY=sb_publishable_...
VIRECTOR_AUTH_REQUIRED=true
NEXT_PUBLIC_SUPABASE_URL=https://YOUR_PROJECT.supabase.co
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=sb_publishable_...
```

The publishable key is intended for browser use. Never put a Supabase secret or
service-role key in a `NEXT_PUBLIC_` variable.

Validate the migration without touching staging:

```powershell
npx --yes supabase@latest db start
npx --yes supabase@latest db lint --local --level warning
npx --yes supabase@latest stop --no-backup
```

Deploy pending migrations only after reviewing a dry run:

```powershell
npx --yes supabase@latest db push --dry-run
npx --yes supabase@latest db push
```

## GitHub Codespaces preview

Select **Open in GitHub Codespaces** above, create the Codespace, and wait for
the forwarded **Virector Web** tab to open. The repository configuration installs
the lightweight Python and Node dependencies, starts both services, and forwards
the public web client on port 3000. FastAPI remains an internal service on port
8000.

The Codespaces preview intentionally uses `MockWorker`: it exercises the hosted
web client, uploads, `@imageN` tagging, `ShotSpec` validation and job manifests without
downloading the LTX model. GitHub Codespaces does not generally provide a GPU,
so LTX/VACE rendering remains on the local RTX machine or a future cloud GPU
worker.

Forwarded ports are private by default. To share the preview temporarily, open
the Codespace **Ports** panel, right-click port 3000, select **Port Visibility →
Public**, and copy the forwarded `app.github.dev` URL. The Codespace must remain
running for that URL to work.

## Recommended Windows folder

Extract this project to:

```text
E:\Virector\virector-starter
```

Persistent runtime data is kept outside the repository:

```text
E:\Virector\models\     Model weights
E:\Virector\cache\      Hugging Face, PyTorch and shared download caches
E:\Virector\outputs\    Job manifests, internal inputs and generated videos
E:\Virector\uploads\    Persistent uploaded assets
```

Docker Compose mounts each directory independently. Override the host locations
with `VIRECTOR_HOST_MODELS_DIR`, `VIRECTOR_HOST_CACHE_DIR`,
`VIRECTOR_HOST_OUTPUTS_DIR` and `VIRECTOR_HOST_UPLOADS_DIR` in `.env`.

## Option A: Run in WSL2

Open Ubuntu in WSL2:

```bash
cd /mnt/e/Virector/virector-starter
cp .env.example .env
python3.10 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
pytest
uvicorn virector.main:app --host 0.0.0.0 --port 8000
```

Open:

```text
http://localhost:8000/studio
```

API documentation:

```text
http://localhost:8000/docs
```

## Option B: Run with Docker Desktop

From PowerShell:

```powershell
Set-Location E:\Virector\virector-starter
Copy-Item .env.example .env
docker compose up --build
```

Then open `http://localhost:3000` for the production web client.

The internal Gradio model-testing studio remains available at
`http://localhost:8000/studio/`, and the FastAPI documentation is available at
`http://localhost:8000/docs`.

## Run the web client without Docker

Keep the FastAPI service running on port 8000, then open a second PowerShell
window:

```powershell
Set-Location E:\Virector\virector-starter\web
Copy-Item .env.example .env.local
npm ci
npm run dev
```

Open `http://localhost:3000`. Next.js proxies `/api` to FastAPI, so the public
browser only needs the web origin.

## Enable local LTX previews

The local runtime is intentionally opt-in because its CUDA dependencies add
several gigabytes to the Docker image. In `.env`, set:

```dotenv
VIRECTOR_INSTALL_LTX=1
VIRECTOR_WORKER_MODE=ltx
```

Rebuild and start Virector:

```powershell
docker compose up --build -d
Invoke-RestMethod http://localhost:8000/api/health
```

The health response should identify `LtxWorker`. Model, Hugging Face and PyTorch
caches are retained under `E:\Virector\models` and `E:\Virector\cache`, so later
container rebuilds do not download the weights again.

The selected low-memory path:

- uses `ltxv-2b-0.9.8-distilled.safetensors`;
- loads the T5 text encoder in 4-bit, encodes the prompt, then unloads it;
- runs the video transformer with sequential CPU offload;
- chains LTX-compatible segments to generate 1–15 seconds at 24 fps;
- accepts ordered image, video and audio omni references;
- offers native preview, 720p and 1080p output; and
- enables VAE tiling before exporting H.264 MP4.

An RTX 5060 8GB validation render completed at 480x832. The initial model setup
is slow and needs about 27GB of persistent model/cache storage. Once initialized,
the eight denoising steps took about two minutes; total time also includes model
loading and final video decoding. Longer clips chain additional segments. The
720p and 1080p choices upscale after native generation to keep local VRAM use
within the 8GB target.

Studio automatically assigns each uploaded reference a name based on its media
type and upload order. Describe what every asset represents and how it controls
the result directly in the single direction prompt:

```text
@image1 is the lead character and @image2 is the world design. Follow the
movement and camera from @video1 while @image1 walks through @image2. Use the
voice and rhythm from @audio1.
```

The tags `@image1`–`@image9`, `@video1`–`@video3` and `@audio1`–`@audio3` are
stored in both `ShotSpec.references` and the job manifest, with a maximum of 12
assets per render. Virector validates that every upload is mentioned and that
the prompt does not refer to an asset that was not uploaded.

The current local LTX adapter technically requires a single conditioning image,
so it prepares one internally from `@image1`; this internal artifact is no longer
shown in Studio. All uploaded references remain available to the VACE/Omni worker.

## VACE worker scaffold

`VaceWorker` is the self-hosted multi-reference worker boundary. It receives the
complete indexed reference set and the prompt containing `@imageN` instructions.
`DiffusersVaceBackend` connects that boundary to the official
`Wan-AI/Wan2.1-VACE-1.3B-diffusers` checkpoint, passes every ordered reference
image to `WanVACEPipeline`, and uses 4-bit quantization plus sequential CPU
offload for the guarded local path.

Run the hardware preflight before downloading the 19GB checkpoint:

```powershell
docker compose exec virector python -m virector.vace_preflight
```

The command reports visible CUDA memory, worker RAM, persistent model storage,
warnings and hard blockers. It will not download anything. Once preflight
passes, explicitly download the checkpoint with:

```powershell
docker compose exec virector python -m virector.vace_preflight --download
```

Automatic downloads from web render requests remain disabled by default. This
prevents an accidental 19GB transfer or an unsafe model load. After a successful
download, set `VIRECTOR_WORKER_MODE=vace` and rebuild the service.

On the original 16GB Windows host, Docker exposes approximately 8.12GB decimal
RAM to the worker. The guarded VACE path requires at least 10GB visible worker
RAM and therefore stops before downloading or loading the checkpoint. LTX
remains the active fallback until the Docker memory allocation or physical RAM
is increased.

The persistent locations for the upcoming runtime are configurable in `.env`:

```dotenv
VIRECTOR_VACE_MODEL_NAME=Wan2.1-VACE-1.3B
VIRECTOR_VACE_MODEL_REPO=Wan-AI/Wan2.1-VACE-1.3B-diffusers
VIRECTOR_VACE_REPO_DIR=/data/models/VACE
VIRECTOR_VACE_CHECKPOINT_DIR=/data/models/Wan2.1-VACE-1.3B-diffusers
VIRECTOR_VACE_QUANTIZE_4BIT=true
VIRECTOR_VACE_CPU_OFFLOAD=true
VIRECTOR_VACE_ALLOW_DOWNLOAD=false
```

To run a direct smoke test with a start frame already created by the Studio:

```powershell
docker compose exec virector python -m virector.ltx_smoke `
  --input /data/outputs/<job-id>/start_frame.png `
  --output-dir /data/outputs/ltx-smoke `
  --width 480 --height 832 --duration 4 --fps 24 --seed 42
```

The result is written to:

```text
E:\Virector\outputs\ltx-smoke\preview.mp4
```

## First test

1. Upload one or more images and optionally add video or audio references.
2. Use the generated `@image`, `@video` and `@audio` tags in upload order.
3. Describe what each asset controls and the complete action in the single
   **Direction prompt**.
4. Select the aspect ratio, resolution and a length from 1–15 seconds.
5. Select **Generate video**.

The references, `shot_spec.json`, internal conditioning artifacts and generated
MP4 are written to:

```text
E:\Virector\outputs\<job-id>\
```

when using Docker, or to `VIRECTOR_OUTPUTS_DIR` (falling back to
`VIRECTOR_DATA_DIR/outputs`) when running directly.

## Project structure

```text
virector/
  main.py                 FastAPI application and mounted studio
  config.py               Environment-based paths and settings
  models/shot_spec.py     Stable director-control contract
  services/compositor.py  Character/world start-frame compositor
  services/jobs.py        Job orchestration and manifests
  services/references.py  Ordered @imageN tags and prompt validation
  workers/base.py         Model-independent worker contract
  workers/mock.py         Milestone 1A start-frame worker
  workers/ltx.py          Backend-neutral LTX adapter
  workers/ltx_diffusers.py Low-memory Diffusers LTX backend
  workers/vace.py         Indexed multi-reference worker boundary
  workers/vace_diffusers.py Guarded Diffusers VACE backend and preflight
  workers/factory.py      Configured worker selection and fallback
  ltx_smoke.py            Direct four-second LTX smoke-render command
  vace_preflight.py       Hardware probe and explicit model download command
  ui/studio.py            Gradio director interface
web/
  app/page.tsx            Production director interface
  app/globals.css         Responsive visual system
  next.config.ts          FastAPI reverse proxy
  Dockerfile              Standalone production web image
tests/
  test_api.py
  test_compositor.py
  test_jobs.py
  test_references.py
  test_shot_spec.py
  test_workers.py
```

## Next milestone

Increase worker-visible RAM to at least 10GB, rerun the guarded preflight, then
download VACE 1.3B and attempt a one-second 480p multi-reference render. For
smooth local work, upgrade the host to 64GB before keeping the quantized runtime
warm. Then add per-stage progress reporting and continuation chaining. A cloud
worker will consume the same `ShotSpec`, tagged reference set and `RenderResult`.
