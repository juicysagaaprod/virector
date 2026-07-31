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

The current worker is intentionally a mock. It produces and saves the controlled
start frame. The next milestone connects the worker interface to LTX-Video for a
four-second image-to-video preview.

## Recommended Windows folder

Extract this project to:

```text
E:\Virector\virector-starter
```

Keep models, cache, projects and outputs under `E:\Virector`.

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

when using Docker, or to the configured `VIRECTOR_DATA_DIR` when running
directly.

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
  ui/studio.py            Gradio director interface
tests/
  test_compositor.py
  test_shot_spec.py
```

## Next milestone

Implement `LTXWorker` behind the existing `VideoWorker` interface:

```python
class LTXWorker(VideoWorker):
    def render(self, job: RenderJob) -> RenderResult:
        ...
```

No API or studio rewrite is required. A cloud worker will consume the same
`ShotSpec` and return the same `RenderResult`.

