# InvokeAI

Leading creative engine for Stable Diffusion models. Professional-grade image generation with a node-based canvas, layer support, ControlNet, and FLUX model compatibility.

## Requirements

- **GPU:** NVIDIA or AMD (min 8 GB VRAM, 12 GB+ recommended for FLUX models)
- **Dependencies:** None

The recipe uses upstream InvokeAI v6.11.1 images pinned by digest: the base
fragment selects CPU, the NVIDIA overlay selects CUDA, and the AMD overlay
selects ROCm. These image indexes publish Linux x86-64 only; they do not provide
native Apple Silicon/Metal inference. A host must support the selected upstream
GPU runtime. An image pin is not a guarantee that every GPU is supported.

The AMD overlay passes `RENDER_GID` as upstream's `RENDER_GROUP_ID` so the
entrypoint adds its unprivileged application user to the host render group.
Readiness uses `/api/v1/app/version`, the API route shipped by this release.

Sources: [upstream Dockerfile](https://github.com/invoke-ai/InvokeAI/blob/v6.11.1/docker/Dockerfile),
[entrypoint](https://github.com/invoke-ai/InvokeAI/blob/v6.11.1/docker/docker-entrypoint.sh),
and [version endpoint](https://github.com/invoke-ai/InvokeAI/blob/v6.11.1/invokeai/app/api/routers/app_info.py).

## Enable / Disable

```bash
ods enable invokeai
ods disable invokeai
```

Your data is preserved when disabling. To re-enable later: `ods enable invokeai`

## Access

- **URL:** `http://localhost:9090`

## First-Time Setup

1. Enable the service: `ods enable invokeai`
2. Open `http://localhost:9090`
3. Install models through the Model Manager
4. Start generating images
