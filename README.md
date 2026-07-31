# Virector Starter

Virector Milestone 1A is a cloud-ready Python scaffold for composing an
AI-generated character image over a world/background image and saving a
structured director specification.

The starter includes:

- FastAPI control API
- Gradio director panel
- Pydantic `ShotSpec`
- Transparent-PNG character compositor
- Job manifests and output folders
- Pluggable worker interface for local LTX and future cloud workers
- Docker configuration
- Tests for shot validation and image composition

The mock worker remains the default so the compositor can run without a GPU.
The optional LTX runtime uses the official LTX-Video 2B distilled checkpoint
through Diffusers. If the runtime or CUDA is unavailable, Virector reports the
reason through `/api/health` and safely falls back to the mock worker.

## Recommended Windows folder

Extract this project to:

```text
E:\Virector\virector-starter
```

Persistent runtime data is kept outside the repository:

```text
E:\Virector\models\     Model weights
E:\Virector\cache\      Hugging Face, PyTorch and shared download caches
E:\Virector\outputs\    Job manifests, start frames and generated videos
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

Then open `http://localhost:8000/studio`.

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
- generates 97 frames for a four-second preview at 24 fps; and
- enables VAE tiling before exporting H.264 MP4.

An RTX 5060 8GB validation render completed at 480x832. The initial model setup
is slow and needs about 27GB of persistent model/cache storage. Once initialized,
the eight denoising steps took about two minutes; total time also includes model
loading and final video decoding.

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

1. Upload a transparent PNG containing one character.
2. Upload a world/background image.
3. Select `9:16`.
4. Adjust character scale and horizontal/vertical placement.
5. Enter action, expression and camera instructions.
6. Select **Compose start frame**.

The generated frame and its `shot_spec.json` are written to:

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
  workers/base.py         Model-independent worker contract
  workers/mock.py         Milestone 1A start-frame worker
  workers/ltx.py          Backend-neutral LTX adapter
  workers/ltx_diffusers.py Low-memory Diffusers LTX backend
  workers/factory.py      Configured worker selection and fallback
  ltx_smoke.py            Direct four-second LTX smoke-render command
  ui/studio.py            Gradio director interface
tests/
  test_compositor.py
  test_shot_spec.py
  test_workers.py
```

## Next milestone

Use a real character/world composition for the next quality render, add
per-stage progress reporting, and keep the loaded runtime warm between jobs.
After that, add continuation jobs for 8, 12 and 15-second clips. A cloud worker
will consume the same `ShotSpec` and return the same `RenderResult`.
