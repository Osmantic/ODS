# Forge / A1111

The original Stable Diffusion web UI (based on Automatic1111). Features extensive model support, extensions, inpainting, outpainting, and professional-grade image generation capabilities.

## Hardware compatibility

This extension ships a pinned image digest. The bundled Torch + CUDA build inside that image is not portable across every NVIDIA generation, so compatibility is constrained by the image rather than DreamServer itself.

- **NVIDIA-only.** The manifest declares `gpu_backends: [nvidia]`, so this extension is filtered out at install time on AMD, Intel, Apple Silicon, and CPU-only systems. Apple Silicon and CPU-only hosts cannot install it.
- **Confirmed broken: NVIDIA RTX 3070 Mobile (compute capability sm_86) under CUDA driver capability 12.4.** The container starts and the host shows it as running, but the Forge process inside crashes with `Your device does not support the current version of Torch/CUDA`. The pinned Torch wheel in the bundled `ghcr.io/ai-dock/stable-diffusion-webui-forge` image is incompatible with this hardware/driver combination.
- **Untested: all other NVIDIA hardware.** RTX 30-series desktop parts, other sm_86 mobile parts, RTX 40-series (sm_89), Hopper (sm_90), and earlier generations have not been live-tested with this image. We make no prediction about whether they work or fail - treat them as unknown until tested on real hardware.
- **Linux:** the live-confirmed failure above was reproduced on Linux with the NVIDIA Container Toolkit. Non-sm_86 Linux hosts are untested.
- **Windows / WSL2:** the same image runs under WSL2 with `nvidia-container-toolkit`. Because the Torch/CUDA mismatch is internal to the image, a WSL2 host on the same RTX 3070 Mobile (sm_86) silicon is **inferred to hit the same crash** - but this has **not** been live-tested. Treat as inferred, not confirmed.
- **macOS:** Apple Silicon and Intel Macs cannot run this extension. The manifest's `gpu_backends: [nvidia]` filter removes it from the install set on macOS.

If you hit the incompatibility on supported-but-unlisted hardware, the upstream project is [lllyasviel/stable-diffusion-webui-forge](https://github.com/lllyasviel/stable-diffusion-webui-forge); building a compatible image yourself or substituting an alternative Forge image is currently the only path forward, and is outside the scope of this packaged extension.

## Privacy & Defense-in-depth

The upstream `ai-dock` Forge image bundles `syncthing`, `quicktunnel`, `serviceportal`, and `sshd` for cloud workflows, plus a `jupyter` notebook server. These are autostarted by the image's bundled supervisord and would otherwise expose the host to remote relays, tunnel endpoints, inbound SSH, and an unauthenticated notebook on default install.

DreamServer disables them via `SUPERVISOR_NO_AUTOSTART=syncthing,quicktunnel,serviceportal,sshd,jupyter` in `compose.yaml`. To re-enable any of them, override that env var in your `.env` or a compose override.

`cloudflared` is also bundled but only autostarts when `CF_TUNNEL_TOKEN` is set, so it stays a no-op by default.

## Requirements

- **GPU:** NVIDIA (min 8 GB VRAM)
- **Dependencies:** None

## Enable / Disable

```bash
dream enable forge
dream disable forge
```

Your data is preserved when disabling. To re-enable later: `dream enable forge`

## Access

- **URL:** `http://localhost:7861`

## First-Time Setup

1. Enable the service: `dream enable forge`
2. Open `http://localhost:7861`
3. Start generating images with the txt2img tab

First startup may download several GB of model files. Subsequent starts are instant.

### API Usage

```bash
# Generate via txt2img API
curl -X POST http://localhost:7861/sdapi/v1/txt2img \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "a photo of a cat in a garden",
    "steps": 20,
    "width": 512,
    "height": 512
  }'

# Check progress
curl http://localhost:7861/sdapi/v1/progress
```
