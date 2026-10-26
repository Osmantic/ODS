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
| NVIDIA (CUDA) | `compose.nvidia.yaml` | Requires NVIDIA Container Toolkit. The image is built on the machine (x86-64, CUDA 12.8). |
| AMD (ROCm) | `compose.amd.yaml` | Runs the detected GPU's native architecture; the bundled ROCm 7.2 torch wheel includes gfx1151 and gfx1201; uses flash attention |

The image generation feature asks for 8 GB of GPU memory: SDXL Lightning at 1024×1024. Not validated yet:
- AMD GPUs other than gfx1151 (Strix Halo) and gfx1201 (RDNA 4);
- Arm64 NVIDIA systems (DGX Spark, RTX Spark).

> **Apple Silicon:** ComfyUI is not currently configured for Apple Silicon (macOS ARM). Use the native ComfyUI application instead.

The NVIDIA image ships a fixed set of custom nodes: ComfyUI-GGUF, ComfyUI-KJNodes, ComfyUI-VideoHelperSuite and
ComfyUI-LTXVideo. ComfyUI-Manager is not included. Its custom-node checkout never loaded under ComfyUI 0.16.4, and
nodes installed at run time would not survive a container recreate.

## Configuration

Environment variables (set in `.env`):

| Variable | Default | Description |
|----------|---------|-------------|
| `COMFYUI_PORT` | 8188 | External port for the ComfyUI web UI and API |

Open WebUI's image generation reads these from `.env` each time it starts:

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_IMAGE_GENERATION` | `true` when image generation was chosen at install | Shows the image button in Open WebUI chats |
| `COMFYUI_BASE_URL` | `http://comfyui:8188` | The ComfyUI that Open WebUI sends prompts to |
| `IMAGE_GENERATION_MODEL` | `sdxl_lightning_4step.safetensors` | Checkpoint for Open WebUI's built-in SDXL workflow |
| `IMAGE_SIZE` | `1024x1024` | Image size |
| `IMAGE_STEPS` | `4` | Sampling steps (SDXL Lightning 4-step is tuned for 4) |

## Image model

ComfyUI's default image pack is SDXL Lightning 4-step (`sdxl_lightning_4step.safetensors`, 6.94 GB). It is pinned
by URL (a fixed Hugging Face revision), size and SHA-256 in `model-packs.json` and goes into `checkpoints/`. It is
downloaded:
- when you add ComfyUI from the Extensions page, after its image, with progress on the card;
- by the Linux installer, in the background, when image generation is chosen there. It keeps going through up to
  five dropped connections.

The Windows installer and `ods enable comfyui` do not download it yet; fetch it by hand (below), or disable and add
ComfyUI again on the Extensions page.

Both downloads use `bin/extension_assets.py`. It checks free disk first, resumes a partial download (`*.part`),
deletes a file that fails its checksum, and puts the model in place only once it is verified. If a download stops,
try again: click **Add** on the Extensions page again, or run the command below. Either way it resumes.

To fetch it by hand, run this from your ODS install directory (on Windows, `python` in PowerShell), then click
**Refresh** in ComfyUI:

```bash
python3 bin/extension_assets.py extensions/services/comfyui . nvidia   # or amd
```

On AMD the folder is `data/comfyui/ComfyUI/models/checkpoints`. The AMD container runs as root and owns what it
creates there, so a download may report that the folder belongs to another account. Run the `sudo chown` command
the error prints, then try again.

Or with curl (`sudo` on AMD if `mkdir` or `curl` reports "Permission denied"):

```bash
dir=data/comfyui/models/checkpoints
mkdir -p "$dir"
curl -fL -C - -o "$dir/sdxl_lightning_4step.safetensors.part" \
  https://huggingface.co/ByteDance/SDXL-Lightning/resolve/c9a24f48e1c025556787b0c58dd67a091ece2e44/sdxl_lightning_4step.safetensors
echo "e0d996ee0013e79d9d3561f50fcafb9a17e3ff07b780358e3b66d67932c4d490  $dir/sdxl_lightning_4step.safetensors.part" | sha256sum -c - \
  && mv "$dir/sdxl_lightning_4step.safetensors.part" "$dir/sdxl_lightning_4step.safetensors"
```

The model is published under the CreativeML OpenRAIL++-M licence (Hugging Face `ByteDance/SDXL-Lightning`).

## Volume Mounts

### NVIDIA

| Host Path | Container Path | Purpose |
|-----------|---------------|---------|
| `./data/comfyui/models` | `/models` | AI model files (checkpoints, LoRAs, VAEs, etc.) |
| `./data/comfyui/output` | `/output` | Generated images output directory |
| `./data/comfyui/input` | `/input` | Input images for img2img and inpainting |
| `./data/comfyui/workflows` | `/workflows` | Workflow JSON templates (read-only) |
| `./data/comfyui/user` | `/user` | Saved workflows and UI settings (persisted across recreates) |

Workflow templates seed missing files in the NVIDIA user directory on startup.
An existing saved workflow keeps its contents even when its template is newer.
To use an updated template, import it under a new name in ComfyUI.

When upgrading an older NVIDIA container, export its saved workflows and settings
before recreating it: files in the old container layer cannot be recovered by the
new entrypoint. Startup preserves any existing image-side `user/` as
`user.migration-backup` after copying missing files to the host mount; a copy failure
stops startup and leaves the original directory intact.

### AMD

| Host Path | Container Path | Purpose |
|-----------|---------------|---------|
| `./data/comfyui/ComfyUI` | `/opt/ComfyUI` | Full ComfyUI installation (models, outputs, custom nodes) |
| `./data/comfyui/miopen` | `/root/.config/miopen` | Persists the MIOpen find database across container recreations |

> **Note:** The AMD image keeps the entire ComfyUI directory in a single mount. Models go inside `./data/comfyui/ComfyUI/models/` rather than a separate `./data/comfyui/models/` mount. ODS leaves `HSA_OVERRIDE_GFX_VERSION` absent in this container so gfx1151 and gfx1201 both run their native kernels.

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
- `compose.amd.yaml` — AMD ROCm service definition (native GPU architecture)
- `startup.sh` — Entrypoint script: sets up model symlinks and launches ComfyUI server
- `Dockerfile` — Container build definition (used by NVIDIA overlay)

## LTX-2.3 Video Generation

ComfyUI ships an official workflow template for Lightricks LTX-2.3 text-to-video. The template is correctly tuned out of the box — many third-party tutorials substitute defaults that produce visibly worse output. Use the official template.

### Required model files

Place under `./data/comfyui/models/` (NVIDIA layout; for AMD use `./data/comfyui/ComfyUI/models/`). ComfyUI 0.16.4 ships
workflow templates 0.9.11, whose `video_ltx2_3_t2v.json` Model Links panel references:

| File | Subdirectory | Size |
|---|---|---|
| `ltx-2.3-22b-dev-fp8.safetensors` ([Lightricks/LTX-2.3-fp8](https://huggingface.co/Lightricks/LTX-2.3-fp8)) | `checkpoints/` | 29.15 GB |
| `gemma_3_12B_it_fp4_mixed.safetensors` ([Comfy-Org/ltx-2](https://huggingface.co/Comfy-Org/ltx-2), `split_files/text_encoders/`) | `text_encoders/` | 9.45 GB |
| `ltx-2.3-22b-distilled-lora-384.safetensors` ([Lightricks/LTX-2.3](https://huggingface.co/Lightricks/LTX-2.3)) | `loras/` | 7.61 GB |
| `ltx-2.3-spatial-upscaler-x2-1.1.safetensors` ([Lightricks/LTX-2.3](https://huggingface.co/Lightricks/LTX-2.3)) | `latent_upscale_models/` | 1.00 GB |

The four files take about 47 GB on disk.

The 0.9.11 template links `ltx-2.3-spatial-upscaler-x2-1.0.safetensors`, which Lightricks no longer publishes. Download
`x2-1.1` instead and select it in the upscaler loader node.

The LTX-2.3 weights are published under the LTX-2 Community License Agreement, and the Gemma text encoder under
Google's Gemma terms. Read both before use.

### Loading the workflow

1. Open ComfyUI at `http://localhost:8188`
2. **Workflow → Browse Templates → Video → LTX-2.3 T2V** (`video_ltx2_3_t2v.json`)
3. Verify all four model loader nodes resolve (no red boxes); pick the `x2-1.1` upscaler if its loader is red

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
