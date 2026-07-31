# Virector

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/juicysagaaprod/virector?quickstart=1)

Virector is a full-stack, director-controlled AI video application. Its
production web client sends ordered omni-reference images and one natural
language direction prompt to a model-independent Python render service.

The starter includes:

- Next.js and TypeScript production web client
- FastAPI render API
- Gradio internal model-testing panel
- Pydantic `ShotSpec`
- Transparent-PNG character compositor
- Job manifests and output folders
- Pluggable workers for local LTX, self-hosted VACE and future cloud GPUs
- Docker configuration
- Tests for shot validation and image composition

The mock worker remains the default so the compositor can run without a GPU.
The optional LTX runtime uses the official LTX-Video 2B distilled checkpoint
through Diffusers. If the runtime or CUDA is unavailable, Virector reports the
reason through `/api/health` and safely falls back to the mock worker.

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

The Supabase connection settings are reserved in the staging template for the
next milestone: persistent users, projects and render-job state. Until that job
repository is connected, `MockWorker` should remain the staging default.

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
- accepts one ordered set of omni reference images;
- offers native preview, 720p and 1080p output; and
- enables VAE tiling before exporting H.264 MP4.

An RTX 5060 8GB validation render completed at 480x832. The initial model setup
is slow and needs about 27GB of persistent model/cache storage. Once initialized,
the eight denoising steps took about two minutes; total time also includes model
loading and final video decoding. Longer clips chain additional segments. The
720p and 1080p choices upscale after native generation to keep local VRAM use
within the 8GB target.

Studio automatically assigns each uploaded reference a name based on upload
order. Describe what every image represents and how it behaves directly in the
single direction prompt:

```text
@image1 is the lead character and @image2 is the world design. Show @image1
walking naturally through @image2 while preserving the face and clothing.
```

The tags `@image1` through `@image9` are stored in both `ShotSpec.references` and
the job manifest. Virector validates that every uploaded image is mentioned and
that the prompt does not refer to an image that was not uploaded.

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

1. Upload one or more images in **Omni reference images**.
2. Treat them as `@image1`, `@image2`, and so on in upload order.
3. Describe what each image represents and the complete action in the single
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
