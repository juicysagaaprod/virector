# RunPod PerformanceWorker deployment

Virector's RunPod image contains the VACE segment engine, multi-shot
PerformanceWorker, FFmpeg assembly and a queue-based RunPod handler. Building
the image does not download model weights. Weights are downloaded once at worker
startup and retained on an attached network volume.

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
least 80 GB; 100 GB leaves practical room for VACE weights, Hugging Face cache
and temporary files. Serverless mounts this volume at `/runpod-volume`.

Do not use the network volume as Virector's permanent video store. Inputs and
outputs travel through short-lived Cloudflare R2 signed URLs, and temporary job
files are deleted after every request.

## 3. Create the Serverless endpoint

Use the published GHCR image and configure:

- GPU: 48 GB for the first production test; concurrency `1`.
- Active workers: `0` while testing cost controls.
- Max workers: `1` initially.
- Network volume: the volume created above.
- Container disk: at least 30 GB for the CUDA/Python image.

The image already supplies these non-secret settings:

```text
VIRECTOR_WORKER_MODE=performance
VIRECTOR_PERFORMANCE_SEGMENT_WORKER=vace
VIRECTOR_PERFORMANCE_MOTION_BACKEND=disabled
VIRECTOR_PERFORMANCE_SPEECH_BACKEND=disabled
VIRECTOR_PERFORMANCE_AUDIO_BACKEND=disabled
VIRECTOR_VACE_ALLOW_DOWNLOAD=true
VIRECTOR_MODELS_DIR=/runpod-volume/virector/models
VIRECTOR_CACHE_DIR=/runpod-volume/virector/cache
```

Optional endpoint variables:

```text
HF_TOKEN=your_hugging_face_token_if_required
VIRECTOR_VACE_INFERENCE_STEPS=8
VIRECTOR_VACE_GUIDANCE_SCALE=5.0
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
motion, camera, effect, voice and audio assets remain available for specialized
workers added to the performance graph.

## 5. Remaining application connection

Packaging is complete when the image builds successfully. The next milestone is
the FastAPI `RunpodWorker` client: it will create R2 signed URLs, call the RunPod
`/run` endpoint, poll job status, and update the existing Supabase render events.
