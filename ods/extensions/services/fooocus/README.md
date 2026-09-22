# Fooocus

Local SDXL image generation using the official `ghcr.io/lllyasviel/fooocus` image pinned by digest. Replaces the former RunPod reference recipe. Ships disabled as a first-party GPU service; uploaded recipes do not gain GPU privileges.

## Requirements

- **GPU:** NVIDIA (ODS feature requests 8 GB VRAM), compatible CUDA 12.4 driver and Docker NVIDIA GPU runtime.
- **OS:** Linux x86_64 or Windows with Docker Desktop WSL2 NVIDIA support. This Docker recipe does not support AMD, CPU inference or macOS Metal.
- **Storage/network:** Several GB for the image and first model download. Uses a named volume to avoid Windows bind-mount performance issues.

## Enable / Disable

```bash
ods enable fooocus
ods disable fooocus
```

Your data is preserved when disabling. To re-enable later: `ods enable fooocus`

## Access

- **URL:** `http://localhost:7865`

## First-Time Setup

1. Enable the service: `ods enable fooocus`
2. Open `http://localhost:7865`
3. Start generating images with natural language prompts

First startup downloads model files. The initial health window is 30 minutes; inspect download progress before retrying on slower connections. Later starts still require model loading, so readiness is determined by the HTTP health check.

`FOOOCUS_PORT` changes the default host port. The default binding is loopback; public access and authentication are not configured by this recipe.

The `fooocus-data` named volume preserves configuration, models and outputs. Outputs retain upstream's `/content/app/outputs` path, linked to the volume by its entrypoint so history works. Disable/re-enable preserves the volume.

## Provenance and validation

- [Official Docker instructions](https://github.com/lllyasviel/Fooocus/blob/ae05379cc97bc4361ec8b4ec90193dab21be763f/docker.md)
- [Official Compose definition](https://github.com/lllyasviel/Fooocus/blob/ae05379cc97bc4361ec8b4ec90193dab21be763f/docker-compose.yml)
- `upstream.json` records the independently verified image digest and architecture. The published image predates the documentation revision; they are not claimed to be the same build.
- Code license: GPL-3.0. Downloaded checkpoints have separate licenses.
- Static checks do not prove generation. GPU startup and inference remain unverified; no image or model was downloaded for this change.
