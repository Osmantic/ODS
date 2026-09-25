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
| `LLAMA_ARG_SPEC_DRAFT_N_MAX` | unset | Optional speculative draft token cap (`--spec-draft-n-max`; `--draft-max` on native macOS b8210) |
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

On the NVIDIA and CPU images and on native macOS, ODS starts llama-server with `--spec-type ngram-mod` (set through `LLAMA_ARG_SPEC_TYPE` in Docker). llama.cpp drafts tokens by matching n-grams already in the context, and the model verifies every draft before it is emitted. The output is still the model's own, so this is lossless. It speeds up requests that repeat the context: file edits, whole-file rewrites and quoting.

On an RTX 5090 with Qwen3.5-27B Q4_K_M and llama.cpp b9014:

| Workload | Without | With `ngram-mod` |
|---|---|---|
| Copy-heavy edit of a 6.1k-token file | 89.5 s | 13.3 s |
| Whole-file rewrite, prior file in context as raw text | 70.1 s | 15.6 s |
| Same rewrite, prior file JSON-escaped in context | 70.3 s | 60.6 s |
| Novel generation and 24k-token prefill | no change (±0.5%) | no change (±0.5%) |

VRAM did not change.

On a Mac mini M4 (16 GB) with Qwen3.5-9B Q4_K_M and native b9014 (median of 3, every output exact):

| Workload | b8210 (previous pin) | b9014, `LLAMA_SPEC_TYPE=none` | b9014, `ngram-mod` |
|---|---|---|---|
| Copy-heavy edit of a 2.0k-token file | 158.2 s | 135.6 s | 41.1 s |
| Whole-file rewrite, prior file raw | 155.6 s | 133.8 s | 49.0 s |
| Same rewrite, prior file JSON-escaped | 152.8 s | 131.2 s | 116.5 s |

Drafts were accepted at 84%, 72% and 57% respectively. Peak llama-server RSS was 8.5 GB with ngram-mod and 8.2 GB without.

Draft sizes keep llama.cpp's defaults (`--spec-ngram-mod-n-match 24`, `--spec-ngram-mod-n-min 48`, `--spec-ngram-mod-n-max 64`).

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
| Native macOS Metal | b9014 (installs from before this pin keep b8210) | `ngram-mod` when the installed binary supports it; none on b8210 |
| AMD (Lemonade, `docker-compose.amd.yml`) | Lemonade-managed | none; not a llama.cpp launch that ODS controls |
| Intel Docker (`docker-compose.intel.yml`, started by hand; the installer uses `docker-compose.arc.yml`) | b9014 | none (not measured on Intel) |
| Intel Arc local build (`docker-compose.arc.yml`) | source default b9014; the installer does not build this image, and b9014 has not been built on its oneAPI 2025.0.0 base | none |
| Apple Docker (`docker-compose.apple.yml`) | b9014 | none |
| Native Windows llama-server (Vulkan fallback) | b9014 for fresh installs; existing installs keep b8248 until `<install>\llama-server` is deleted and the installer re-run | none (not measured on Windows) |
| Registered native model-store profiles | qualified executable | none; the profile keeps its own argument list |

Builds b8210 and b8248 accept `--spec-type ngram-mod`, but they predate both changes. They draft with the generic 12-token lookup, which upstream logs as too small, and they turn speculation off for hybrid models. Remote and cloud providers never start llama-server.

### Native macOS runtime arguments

Native macOS keeps the `llama-server` binary it was installed with, so an older install can still run b8210 after ODS moves its pin. Before a running model is stopped, every macOS launcher (installer, `ods-macos.sh`, the bootstrap full-model swap and dashboard model switches) passes the settings below through `installers/macos/lib/native-checkpoint-args.py`. That helper reads the binary's own `--help` and:

- spells the draft settings for that binary. `LLAMA_ARG_SPEC_DRAFT_N_MAX` becomes `--spec-draft-n-max` on b9014 and `--draft-max` on b8210, which rejects the newer name. `LLAMA_ARG_SPEC_DRAFT_TYPE_K`/`_V` work the same way.
- adds `--ctx-checkpoints 32` unless `LLAMA_ARG_CTX_CHECKPOINTS` is set. b8210 keeps 8 prompt checkpoints per slot, so changing a tool result more than 8 turns back re-processes the whole prompt. On a Mac mini M4 (16 GB) with Qwen3.5-9B and b8210, editing turn 3's result after 12 tool turns took 84.3 s with 8 checkpoints and 33.4 s with 32. b9014 already defaults to 32. Each checkpoint costs about 50 MiB for this model. Set a lower value, or 0, to save memory.
- adds `--spec-type ngram-mod` when the binary has the b8955+ implementation, `LLAMA_ARG_SPEC_TYPE` is unset and `LLAMA_SPEC_TYPE` is not `none`.
- passes `LLAMA_REASONING` (default `off`) as `--reasoning` when the binary has that switch, and leaves `--reasoning-format` at llama.cpp's default, as Docker does with `LLAMA_ARG_REASONING`. On the Mac mini with b9014, the old native flags (`--reasoning-format none` only) left `--reasoning` at `auto`: the server logged `thinking = 1` and returned its reasoning as the reply. With `--reasoning off` but `--reasoning-format none`, every reply, tool calls included, started with an empty `<think>` block. `--reasoning off` with the default format replied exactly as b8210 did. b8210 has no `--reasoning` switch, so it keeps `--reasoning-format none`.

A setting you add to `.env` that the binary cannot honour stops the restart before the running model is touched. The bootstrap full-model swap instead logs a warning and starts the full model without the tuning. A default the binary does not support is left out. A fresh install, or `get-ods.sh --force`, installs the pinned b9014.

### MTP speculative decoding

Newer llama.cpp builds support MTP speculative decoding for GGUFs that include compatible MTP data. ODS exposes the flags but does not enable them automatically, because normal GGUFs and older llama.cpp builds will reject or ignore these settings.

```env
LLAMA_ARG_SPEC_TYPE=draft-mtp
LLAMA_ARG_SPEC_DRAFT_N_MAX=3
```

Use this only with a llama.cpp image or native binary built after MTP support landed, and with a model family that explicitly publishes MTP-capable GGUFs. Normal GGUFs without MTP layers should leave these variables unset.

In router or multi-model setups, do not put MTP settings in a shared default section when any routed model lacks MTP layers. Apply `spec-type = draft-mtp` and `spec-draft-n-max = 3` only to the MTP-capable model section so non-MTP models keep loading normally.

In Docker, set the draft model's KV cache types with `LLAMA_ARG_SPEC_DRAFT_CACHE_TYPE_K` and `LLAMA_ARG_SPEC_DRAFT_CACHE_TYPE_V`, the names llama.cpp reads. Native macOS and Windows launchers take `LLAMA_ARG_SPEC_DRAFT_TYPE_K`/`_V` and pass them as `--spec-draft-type-k`/`-v`. llama.cpp has no env variable by that name, so Docker ignores it.

### Image pins

Every llama.cpp image ODS ships is pinned by tag and sha256 digest, for example `ghcr.io/ggml-org/llama.cpp:server-cuda-b9014@sha256:fcf28582…55c4f`. llama.cpp publishes a ghcr tag for only some of its builds, and ODS once pinned a tag that was never published. The digest also means a re-pushed tag cannot change what an install runs. `scripts/check-dependency-pins.py` rejects a llama.cpp image without a digest, and `tests/contracts/test-llama-cpp-compat.py` checks that every copy of a pin (Compose, installer pulls, tier maps, host agent, model catalog) agrees.

| Artifact | Pin |
|---|---|
| `server-cuda-b9014` (NVIDIA, multi-arch) | `sha256:fcf285820892e7ce3218379634e3590826fc697e8b6745b9392072462e355c4f` |
| `server-b9014` (CPU, multi-arch) | `sha256:2e7953dfef88f302bf0683bffa7dc1f8d86ef75910380bc41126ec5b8bedaf53` |
| `server-intel-b9014` (Intel) | `sha256:9c7bbaad3663523a3deb8927d3cfbf58d33f00a7634c69843e9eeeda01568c1b` |
| `server-b9014` (Apple Docker; same image as CPU) | `sha256:2e7953dfef88f302bf0683bffa7dc1f8d86ef75910380bc41126ec5b8bedaf53` |
| `llama-b9014-bin-win-vulkan-x64.zip` (native Windows) | SHA-256 `6cd4bc7a44256e674458b0c5ea2ae3461dca29ee87876c8d410ecc78652a3b0f` |
| Intel Arc local build (`images/llama-sycl`, source default; not built or tested) | tag `b9014`, commit `d4b0c22f9e67f0295e91dc1ab4f17c0fb2557fa4` |

To use another build, set `LLAMA_SERVER_IMAGE` to a `tag@sha256:digest` reference. `docker buildx imagetools inspect <image>` prints the digest of a tag.

### llama.cpp env names

llama-server reads only the `LLAMA_ARG_*` names defined in its `common/arg.cpp`, and ignores any other name without a warning. `tests/contracts/test-llama-spec-default.py` keeps the names that the NVIDIA and CPU containers receive in step with the pinned build.

- `LLAMA_ARG_NO_CACHE_PROMPT` works on b9014 and later. `--cache-prompt` is a negatable flag, so llama.cpp also reads the `LLAMA_ARG_NO_` form, and any value, even `0`, disables prompt caching.
- `LLAMA_ARG_CHECKPOINT_EVERY_N_TOKENS` never existed in llama.cpp. The flag `--checkpoint-every-n-tokens` reads `LLAMA_ARG_CHECKPOINT_EVERY_NT`.
- llama.cpp b9310 removed `--checkpoint-every-n-tokens` (`LLAMA_ARG_CHECKPOINT_EVERY_NT`) and added `--checkpoint-min-step` (`LLAMA_ARG_CHECKPOINT_MIN_SPACING_NT`), a minimum spacing rather than a fixed interval. Docker passes both names through, and each build reads only its own. Native Windows passes the interval only when the installed `llama-server --help` lists the flag, because llama-server exits on a flag it does not know.
- Native Windows passes `LLAMA_REASONING` as `--reasoning` when the installed `llama-server --help` lists it (b9014), and as `--reasoning-format` otherwise (b8248), adding `--reasoning-budget 0` for `off`. b9014 defaults `--reasoning` to `auto`, and `--reasoning-format none` alone returns the reasoning inside the reply. On b8248 only `--reasoning-budget 0` turns thinking off: with Qwen3.5-2B on the b8248 image, `--reasoning-format none` alone answered with 105 tokens of reasoning in the reply, and adding `--reasoning-budget 0` answered `42` (the server logs `thinking = 0`).
- Intel images: ODS does not set `SYCL_CACHE_PERSISTENT`, which crashes llama-server with the oneAPI 2025.3 runtime in the b9014 image (ggml-org/llama.cpp#21474, #22095). On hosts with more than one Intel GPU, set `ONEAPI_DEVICE_SELECTOR=level_zero:0` (ggml-org/llama.cpp#21747).
- On NVIDIA, ODS uses `--split-mode layer` for every multi-GPU assignment. llama.cpp b9890 removed CUDA row split: `--split-mode row` still parses, but the model fails to load.

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
