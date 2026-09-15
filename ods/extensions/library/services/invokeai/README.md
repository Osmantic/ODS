# InvokeAI

Leading creative engine for Stable Diffusion models. Professional-grade image generation with a node-based canvas, layer support, ControlNet, and FLUX model compatibility.

## Requirements

- **GPU:** NVIDIA or AMD (min 8 GB VRAM, 12 GB+ recommended for FLUX models)
- **Dependencies:** None

## Enable / Disable

```bash
ods enable invokeai
ods disable invokeai
```

Your data is preserved when disabling. To re-enable later: `ods enable invokeai`

## Access

- **URL:** `http://localhost:9090`

The container and Dashboard check `/api/v1/app/version`, the version endpoint
provided by InvokeAI `6.11.1`. That release has no `/health` route. A successful
probe confirms the HTTP API is available; it does not verify that a model is
installed or that image generation succeeds. Recreate the service after updating
the recipe to apply a changed container healthcheck.

## First-Time Setup

1. Enable the service: `ods enable invokeai`
2. Open `http://localhost:9090`
3. Install models through the Model Manager
4. Start generating images
