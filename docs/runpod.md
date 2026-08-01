# RunPod PerformanceWorker deployment

Virector's RunPod image contains the VACE segment engine, multi-shot
PerformanceWorker, FFmpeg assembly, the official Wan2.2-Animate runtime and a
queue-based RunPod handler. Building the image does not download model weights.
Weights are downloaded once at worker startup and retained on an attached
network volume.

## 1. Publish the image

In GitHub, open **Actions → Publish RunPod worker image → Run workflow**. Keep
the `edge` tag for the first deployment. The workflow publishes:

```text
ghcr.io/juicysagaaprod/virector-runpod:edge
```

The workflow is manual because the CUDA image is large. Normal application
pushes continue to run only the fast test workflow.

If the GitHub package is private, create a read-only GitHub personal access
token for packages and add it to RunPod as container-registry authentication.

## 2. Create persistent model storage

Create a RunPod network volume in the same datacenter as the endpoint. Use at
least 200 GB; 250 GB leaves practical room for VACE, Wan2.2-Animate, preprocessing
checkpoints, Hugging Face cache and temporary files. Serverless mounts this
volume at `/runpod-volume`.

Do not use the network volume as Virector's permanent video store. Inputs and
outputs travel through short-lived Cloudflare R2 signed URLs, and temporary job
files are deleted after every request.

## 3. Create the Serverless endpoint

Use the published GHCR image and configure:

- GPU: 80 GB for the first production test; concurrency `1`.
- Active workers: `0` while testing cost controls.
- Max workers: `1` initially.
- Network volume: the volume created above.
- Container disk: at least 30 GB for the CUDA/Python image.

The image already supplies these non-secret settings:

```text
VIRECTOR_WORKER_MODE=performance
VIRECTOR_PERFORMANCE_SEGMENT_WORKER=vace
VIRECTOR_PERFORMANCE_MOTION_BACKEND=wan-animate
VIRECTOR_PERFORMANCE_SPEECH_BACKEND=disabled
VIRECTOR_PERFORMANCE_AUDIO_BACKEND=disabled
VIRECTOR_VACE_ALLOW_DOWNLOAD=true
VIRECTOR_WAN_ANIMATE_REPO_DIR=/opt/Wan2.2
VIRECTOR_WAN_ANIMATE_PYTHON=/opt/wan-animate-venv/bin/python
VIRECTOR_WAN_ANIMATE_CHECKPOINT_DIR=/runpod-volume/virector/models/Wan2.2-Animate-14B
VIRECTOR_WAN_ANIMATE_ALLOW_DOWNLOAD=true
VIRECTOR_MODELS_DIR=/runpod-volume/virector/models
VIRECTOR_CACHE_DIR=/runpod-volume/virector/cache
```

Optional endpoint variables:

```text
HF_TOKEN=your_hugging_face_token_if_required
VIRECTOR_VACE_INFERENCE_STEPS=8
VIRECTOR_VACE_GUIDANCE_SCALE=5.0
VIRECTOR_WAN_ANIMATE_INFERENCE_STEPS=20
VIRECTOR_WAN_ANIMATE_TIMEOUT_SECONDS=7200
VIRECTOR_RUNPOD_MAX_REFERENCE_BYTES=26214400
VIRECTOR_RUNPOD_MAX_VIDEO_BYTES=104857600
VIRECTOR_RUNPOD_ALLOWED_ASSET_HOSTS=YOUR_ACCOUNT_ID.r2.cloudflarestorage.com
```

Never place R2 credentials, database passwords or Supabase service-role keys in
the image. The API generates per-job signed URLs instead.

## 4. Queue contract

The application backend will submit a compact JSON payload. Images and videos
are not embedded because RunPod request and response bodies have size limits.

```json
{
  "input": {
    "job_id": "0123456789abcdef0123456789abcdef",
    "shot_spec": {
      "title": "Clip 8",
      "prompt": "Full direction prompt",
      "director_plan": {},
      "duration_seconds": 15
    },
    "references": [
      {
        "index": 1,
        "tag": "@image1",
        "media_type": "image",
        "download_url": "https://signed-r2-download-url",
        "strength": 0.9
      }
    ],
    "output_upload_url": "https://signed-r2-upload-url",
    "output_object_key": "renders/JOB_ID/preview.mp4"
  }
}
```

The handler validates the DirectorPlan and downloads up to twelve signed HTTPS
assets: at most nine images, three videos and three audio files. It forwards
shot progress to RunPod, uploads the final MP4 directly to R2, and returns only
job metadata. The current VACE segment backend consumes image references; the
motion bindings with a tagged `@video` are passed through the Wan2.2-Animate
stage after VACE generation. The base shot's first frame is used
as the character/world reference, helping the motion pass retain the staged
scene. Successful motion routes are persisted as `applied`. Camera, voice and
audio remain available for the next specialist stages.

Before enabling an endpoint, run the guarded preflight inside a GPU pod:

```bash
python3 -m virector.wan_animate_preflight
python3 -m virector.wan_animate_preflight --download
```

The first command performs no download. Virector's guarded profile blocks below
40 GB visible VRAM, 48 GB RAM or 140 GB free persistent storage and warns below
80 GB VRAM. The 80 GB endpoint recommendation is intentionally conservative for
the official 14B animation model and preprocessing stack.

## 5. Connect the FastAPI service

The API-side `RunpodWorker` uploads tagged references to R2, creates short-lived
download and upload URLs, submits the queue job through `/run`, polls `/status`,
forwards progress into the existing render record and downloads the completed
MP4 for normal publication.

Configure the private FastAPI environment (never the browser bundle):

```text
VIRECTOR_WORKER_MODE=runpod
VIRECTOR_STORAGE_BACKEND=s3
VIRECTOR_S3_ENDPOINT_URL=https://YOUR_ACCOUNT_ID.r2.cloudflarestorage.com
VIRECTOR_S3_REGION=auto
VIRECTOR_S3_BUCKET=virector-bucket
VIRECTOR_S3_ACCESS_KEY_ID=your_private_r2_access_key
VIRECTOR_S3_SECRET_ACCESS_KEY=your_private_r2_secret_key
VIRECTOR_S3_PRESIGNED_URL_TTL_SECONDS=10800
VIRECTOR_RUNPOD_ENDPOINT_ID=your_endpoint_id
VIRECTOR_RUNPOD_API_KEY=your_endpoint_scoped_api_key
VIRECTOR_RUNPOD_API_BASE_URL=https://api.runpod.ai/v2
VIRECTOR_RUNPOD_REQUEST_TIMEOUT_SECONDS=30
VIRECTOR_RUNPOD_POLL_INTERVAL_SECONDS=3
VIRECTOR_RUNPOD_JOB_TIMEOUT_SECONDS=7200
```

The signed-URL TTL must exceed the RunPod job timeout by at least five minutes,
because the output upload URL is used only after generation finishes. Keep
active workers at zero during testing; the first submitted render will scale a
worker up and begin GPU billing.
