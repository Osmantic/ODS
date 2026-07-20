# ComfyUI

Node-based image generation UI and backend for ODS

## Overview

ComfyUI provides a powerful, node-based interface for running Stable Diffusion and SDXL image generation models locally. It exposes both a visual workflow editor in the browser and a REST API, enabling programmatic image generation from other services. ComfyUI requires a GPU (NVIDIA or AMD) and is not available on CPU-only systems.

## Features

- **Node-based workflow editor**: Build and share custom generation pipelines visually
- **SDXL Lightning**: Configured for SDXL Lightning 4-step image generation out of the box
- **Multiple model types**: Supports checkpoints, LoRAs, VAEs, text encoders, and diffusion models
- **Persistent model storage**: Models stored in `./data/comfyui/models` and survive container rebuilds
- **Workflow templates**: Pre-loaded workflow JSON files from `./data/comfyui/workflows`
- **REST API**: Programmatic image generation via HTTP
- **NVIDIA and AMD GPU support**: Separate optimized images for each GPU vendor

## GPU Requirements

ComfyUI is GPU-only. The service definition is split by GPU vendor:

| Backend | Compose file | Notes |
|---------|-------------|-------|
| NVIDIA (CUDA) | `compose.nvidia.yaml` | Requires NVIDIA Container Toolkit |
| AMD (ROCm) | `compose.amd.yaml` | Runs on the GPU's native arch — the bundled image ships a ROCm 7.2 PyTorch whose arch list includes gfx1151 (Strix Halo) and gfx1201 (RDNA4), among others; uses flash attention |

> **Apple Silicon:** ComfyUI is not currently configured for Apple Silicon (macOS ARM). Use the native ComfyUI application instead.

## Configuration

Environment variables (set in `.env`):

| Variable | Default | Description |
|----------|---------|-------------|
| `COMFYUI_PORT` | 8188 | External port for the ComfyUI web UI and API |

## Volume Mounts

### NVIDIA

| Host Path | Container Path | Purpose |
|-----------|---------------|---------|
| `./data/comfyui/models` | `/models` | AI model files (checkpoints, LoRAs, VAEs, etc.) |
| `./data/comfyui/output` | `/output` | Generated images output directory |
| `./data/comfyui/input` | `/input` | Input images for img2img and inpainting |
| `./data/comfyui/workflows` | `/workflows` | Workflow JSON templates (read-only) |

### AMD

| Host Path | Container Path | Purpose |
|-----------|---------------|---------|
| `./data/comfyui/ComfyUI` | `/opt/ComfyUI` | Full ComfyUI installation (models, outputs, custom nodes) |
| `./data/comfyui/miopen` | `/root/.config/miopen` | Persists MIOpen find-db so first-generation tuning survives container recreates |

> **Note:** The AMD image keeps the entire ComfyUI directory in a single mount. Models go inside `./data/comfyui/ComfyUI/models/` rather than a separate `./data/comfyui/models/` mount.

### Model Subdirectories (NVIDIA)

Place model files in the appropriate subdirectory under `./data/comfyui/models/`:

| Subdirectory | Model type |
|-------------|------------|
| `checkpoints/` | Full Stable Diffusion / SDXL checkpoints |
| `diffusion_models/` | Standalone diffusion model weights |
| `text_encoders/` | CLIP and T5 text encoders |
| `vae/` | Variational Autoencoders |
| `loras/` | LoRA fine-tuned weights |
| `latent_upscale_models/` | Latent upscale models |

## Architecture

```
┌──────────┐  HTTP :8188    ┌──────────────┐
│ Browser  │───────────────▶│   ComfyUI    │
│ (Node UI)│◀───────────────│  (PyTorch)   │
└──────────┘                └──────┬───────┘
                                   │
                    ┌──────────────┼──────────────┐
                    ▼              ▼              ▼
              /models/       /output/        /input/
           (checkpoints,   (generated      (source
            LoRAs, VAEs)    images)         images)
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /` | GET | Web UI / health check |
| `POST /prompt` | POST | Queue a generation workflow |
| `GET /queue` | GET | View current generation queue |
| `GET /history` | GET | View completed generation history |
| `GET /view` | GET | Retrieve a generated image by filename |
| `GET /system_stats` | GET | GPU memory and system resource stats |

## Files

- `manifest.yaml` — Service metadata (port, health endpoint, GPU backends, features)
- `compose.yaml` — Base stub (actual definition is in GPU overlays)
- `compose.nvidia.yaml` — NVIDIA CUDA service definition
- `compose.amd.yaml` — AMD ROCm service definition (runs the GPU's native arch)
- `startup.sh` — Entrypoint script: sets up model symlinks and launches ComfyUI server
- `Dockerfile` — Container build definition (used by NVIDIA overlay)

## LTX-2.3 Video Generation

ComfyUI ships an official workflow template for Lightricks LTX-2.3 text-to-video. The template is correctly tuned out of the box — many third-party tutorials substitute defaults that produce visibly worse output. Use the official template.

### Required model files

Place under `./data/comfyui/models/` (NVIDIA layout; for AMD use `./data/comfyui/ComfyUI/models/`). As of 2026-05-10, the official `video_ltx2_3_t2v.json` template's Model Links panel references:

| File | Subdirectory |
|---|---|
| `ltx-2.3-22b-dev-fp8.safetensors` | `checkpoints/` |
| `ltx-2.3-22b-distilled-lora-384.safetensors` | `loras/` |
| `gemma-3-12b-it-abliterated_lora_rank64_bf16.safetensors` | `loras/` |
| `ltx-2.3-spatial-upscaler-x2-1.1.safetensors` | `latent_upscale_models/` |

The official template links to these files on Hugging Face. If ComfyUI updates the template, trust the template's Model Links panel over older mirrored model-storage notes. Combined disk footprint is roughly 30–35 GB.

### Loading the workflow

1. Open ComfyUI at `http://localhost:8188`
2. **Workflow → Browse Templates → Video → LTX-2.3 T2V** (`video_ltx2_3_t2v.json`)
3. Verify all four model loader nodes resolve (no red boxes)

### Tuning that matters

Validated against side-by-side A/B comparisons; the official template defaults are correct, common substitutions look noticeably worse:

| Setting | Use | Avoid |
|---|---|---|
| Sampler | `euler_cfg_pp` | vanilla `euler` |
| CFG | `1.0` | typical `3.0` |
| Sigmas | `ManualSigmas` (template values) | `BasicScheduler` |
| LoRA strength | `0.5` | `1.0` |

### Operating envelope

- **VRAM**: ~22–24 GB peak (fp8 22B checkpoint + Gemma encoder).
- **Throughput**: ~45–55 s for a 5 s, 1280×704 clip on a single Blackwell-class NVIDIA GPU using the default two-stage workflow.
- **Power-cap tolerance**: throughput holds at a 500 W per-GPU cap; below ~360 W the V/f curve begins to bind. Going from a 500 W to 600 W cap buys roughly +11% throughput.

## Troubleshooting

**ComfyUI not starting (long start period):**

The container has a 120-second start period to allow model loading. Wait for it to elapse, then check:
```bash
docker compose ps ods-comfyui
docker compose logs ods-comfyui --follow
```

**GPU not detected:**

For NVIDIA:
```bash
# Verify NVIDIA Container Toolkit is installed
docker run --rm --gpus all nvidia/cuda:12.0.0-base-ubuntu22.04 nvidia-smi
```

For AMD:
```bash
# Verify GPU device access
ls /dev/dri /dev/kfd
```

**Models not appearing in the UI:**
- Ensure model files are placed in the correct subdirectory under `./data/comfyui/models/`
- Restart ComfyUI or click **Refresh** in the model loader node

**Out of VRAM errors:**
- Use smaller or quantized model variants
- Close other GPU-intensive services before running ComfyUI
- Check VRAM usage: `nvidia-smi` (NVIDIA) or `rocm-smi` (AMD)

**Generated images not saving:**
- Verify `./data/comfyui/output` exists and is writable
- Check container logs for permission errors
