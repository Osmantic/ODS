# llama-server

Core LLM inference engine for ODS

## Overview

llama-server is the local LLM inference backend, powered by [llama.cpp](https://github.com/ggml-org/llama.cpp). It loads GGUF-format models and exposes an OpenAI-compatible HTTP API on port 8080. GPU acceleration is provided via CUDA (NVIDIA) or ROCm (AMD); CPU fallback is available for systems without a supported GPU.

All other services that perform AI inference — Open WebUI, LiteLLM, Privacy Shield, and the dashboard chat endpoint — connect to llama-server internally.

## Features

- **OpenAI-compatible API**: Drop-in replacement for the OpenAI Chat Completions and Completions endpoints
- **GGUF model support**: Load any GGUF-quantized model from `data/models/`
- **GPU acceleration**: CUDA (NVIDIA) and ROCm/HIP (AMD) backends
- **Configurable context window**: Token limit tunable via `CTX_SIZE`
- **Prometheus metrics**: `/metrics` endpoint for throughput and token stats
- **Memory-aware GPU offload**: llama.cpp selects the safe layer count by default; operators can override it with `N_GPU_LAYERS`
- **Hardware-tier model selection**: Installer auto-selects model size based on detected VRAM

## Configuration

Environment variables (set in `.env`):

| Variable | Default | Description |
|----------|---------|-------------|
| `GGUF_FILE` | `Qwen3.5-9B-Q4_K_M.gguf` | Model filename inside `data/models/` |
| `CTX_SIZE` | `16384` | Context window size in tokens |
| `OLLAMA_PORT` | `11434` | External host port (maps to internal 8080) |
| `GPU_BACKEND` | `nvidia` | GPU backend: `nvidia` or `amd` |
| `N_GPU_LAYERS` | `auto` | GPU offload policy: `auto`, `all`, or a non-negative layer count |
| `LLAMA_ARG_FLASH_ATTN` | `auto` | llama.cpp Flash Attention mode: `auto`, `on`, or `off` |
| `LLAMA_ARG_CACHE_TYPE_K` | `f16` | KV cache key precision. Use `q8_0` to reduce long-context memory pressure |
| `LLAMA_ARG_CACHE_TYPE_V` | `f16` | KV cache value precision. Use `q8_0` to reduce long-context memory pressure |
| `LLAMA_ARG_N_CPU_MOE` | unset | Optional MoE-only CPU expert offload (`--n-cpu-moe`). Leave unset for dense models |
| `LLAMA_SPEC_TYPE` | `ngram-mod` on the NVIDIA and CPU images | Default speculative decoding when the model sets no `LLAMA_ARG_SPEC_TYPE`. Set `none` to turn it off. See [N-gram speculative decoding](#n-gram-speculative-decoding) |
| `LLAMA_ARG_SPEC_TYPE` | unset | Optional per-model speculative decoding mode (`--spec-type`), normally written by a runtime profile. Overrides `LLAMA_SPEC_TYPE`. Use only with supported GGUF/runtime combinations |
| `LLAMA_ARG_SPEC_DRAFT_N_MAX` | unset | Optional speculative draft token cap (`--spec-draft-n-max`) |
| `LLAMA_SERVER_MEMORY_LIMIT` | `64G` | Docker memory limit for the container |

### Long-context profile

For larger context windows on memory-constrained GPUs, keep the model unchanged and tune the attention/KV cache first:

```env
CTX_SIZE=32768
LLAMA_ARG_FLASH_ATTN=on
LLAMA_ARG_CACHE_TYPE_K=q8_0
LLAMA_ARG_CACHE_TYPE_V=q8_0
```

This is opt-in. The defaults remain `auto` Flash Attention and `f16` KV cache to preserve existing behavior.

### MoE expert offload

For Mixture-of-Experts GGUF models, llama.cpp can keep the first N MoE expert layers on CPU/RAM:

```env
LLAMA_ARG_N_CPU_MOE=25
```

Tune this value per machine. Lower values keep more work on GPU and can be faster if enough VRAM is available; higher values reduce VRAM pressure. Leave this unset for dense models.

### N-gram speculative decoding

On the NVIDIA and CPU images, ODS starts llama-server with `--spec-type ngram-mod` (set through `LLAMA_ARG_SPEC_TYPE`). llama.cpp drafts tokens by matching n-grams already in the context, and the model verifies every draft before it is emitted. The output is still the model's own, so this is lossless. It speeds up requests that repeat the context: file edits, whole-file rewrites and quoting.

On an RTX 5090 with Qwen3.5-27B Q4_K_M and llama.cpp b9014:

| Workload | Without | With `ngram-mod` |
|---|---|---|
| Copy-heavy edit of a 6.1k-token file | 89.5 s | 13.3 s |
| Whole-file rewrite, prior file in context as raw text | 70.1 s | 15.6 s |
| Same rewrite, prior file JSON-escaped in context | 70.3 s | 60.6 s |
| Novel generation and 24k-token prefill | no change (±0.5%) | no change (±0.5%) |

VRAM did not change. Draft sizes keep llama.cpp's defaults (`--spec-ngram-mod-n-match 24`, `--spec-ngram-mod-n-min 48`, `--spec-ngram-mod-n-max 64`).

To turn it off, add this to `.env` and restart llama-server:

```env
LLAMA_SPEC_TYPE=none
```

A model runtime profile that sets `LLAMA_ARG_SPEC_TYPE` (for example `draft-mtp`) takes precedence over `LLAMA_SPEC_TYPE`.

The default applies only where the pinned llama.cpp build has the benchmarked implementation: the dedicated ngram-mod parameters (b8955 and later) and speculative checkpoints for hybrid models such as Qwen3.5 (b8842 and later).

| Runtime | llama.cpp | Default |
|---|---|---|
| NVIDIA Docker (`docker-compose.nvidia.yml`) | b9014 | `ngram-mod` |
| CPU Docker (`docker-compose.cpu.yml`) | b9014 | `ngram-mod` |
| AMD (Lemonade, `docker-compose.amd.yml`) | Lemonade-managed | none; not a llama.cpp launch that ODS controls |
| Intel Arc / SYCL (`docker-compose.intel.yml`, `docker-compose.arc.yml`) | b8248 | none |
| Apple Docker (`docker-compose.apple.yml`) and native macOS Metal | b8248 / b8210 | none |
| Native Windows llama-server (Vulkan fallback) | b8248 | none |
| Registered native model-store profiles | qualified executable | none; the profile keeps its own argument list |

Builds b8210 and b8248 accept `--spec-type ngram-mod`, but they predate both changes. They draft with the generic 12-token lookup, which upstream logs as too small, and they turn speculation off for hybrid models. Remote and cloud providers never start llama-server.

### MTP speculative decoding

Newer llama.cpp builds support MTP speculative decoding for GGUFs that include compatible MTP data. ODS exposes the flags but does not enable them automatically, because normal GGUFs and older llama.cpp builds will reject or ignore these settings.

```env
LLAMA_ARG_SPEC_TYPE=draft-mtp
LLAMA_ARG_SPEC_DRAFT_N_MAX=3
```

Use this only with a llama.cpp image or native binary built after MTP support landed, and with a model family that explicitly publishes MTP-capable GGUFs. Normal GGUFs without MTP layers should leave these variables unset.

In router or multi-model setups, do not put MTP settings in a shared default section when any routed model lacks MTP layers. Apply `spec-type = draft-mtp` and `spec-draft-n-max = 3` only to the MTP-capable model section so non-MTP models keep loading normally.

### AMD-specific variables

| Variable | Default | Description |
|----------|---------|-------------|
| `VIDEO_GID` | `44` | GID of the `video` group (`getent group video \| cut -d: -f3`) |
| `RENDER_GID` | `992` | GID of the `render` group (`getent group render \| cut -d: -f3`) |

## API Endpoints

llama-server exposes an OpenAI-compatible REST API:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check |
| `GET` | `/metrics` | Prometheus inference metrics |
| `POST` | `/v1/chat/completions` | Chat completions (OpenAI format) |
| `POST` | `/v1/completions` | Text completions |
| `GET` | `/v1/models` | List loaded models |

### Example

```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "default",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  docker-compose.base.yml  (GPU-agnostic command + ports) │
│        +                                                  │
│  docker-compose.nvidia.yml  OR  docker-compose.amd.yml   │
│        (image + GPU device passthrough)                   │
└──────────────────────────┬──────────────────────────────┘
                           │
                    ┌──────▼──────────┐
                    │  llama-server   │
                    │  (llama.cpp)    │
                    │  :8080 (int)    │
                    │  :8080 (ext)    │
                    └──────┬──────────┘
                           │  OpenAI-compatible API
          ┌────────────────┼──────────────────┐
          │                │                  │
    ┌─────▼─────┐   ┌──────▼───────┐  ┌──────▼──────┐
    │ Open WebUI│   │   LiteLLM    │  │Privacy Shield│
    └───────────┘   └──────────────┘  └─────────────┘
```

## Files

- `manifest.yaml` — Service metadata and feature definitions

## Troubleshooting

**Container not starting:**
```bash
docker compose ps llama-server
docker compose logs llama-server
```

**Model not found:**
- Confirm the GGUF file exists: `ls ods/data/models/`
- Check `GGUF_FILE` in `.env` matches the filename exactly

**Out of VRAM:**
- Reduce `CTX_SIZE` in `.env` (try `8192` or `4096`)
- Use a smaller quantized model (Q4 instead of Q8)

**AMD GPU not detected:**
- Verify group IDs: `getent group video | cut -d: -f3` and `getent group render | cut -d: -f3`
- Update `VIDEO_GID` and `RENDER_GID` in `.env`
- Confirm `/dev/kfd` and `/dev/dri` exist on the host

**Check inference metrics:**
```bash
curl http://localhost:8080/metrics
```

## License

Part of ODS — Local AI Infrastructure
