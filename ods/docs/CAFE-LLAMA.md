# cafe-llama.cpp optional runtime

**Status:** experimental enable path  
**Upstream:** https://github.com/quimmedes/cafe-llama.cpp  


## Role in ODS

cafe-llama.cpp is an **optional advanced** inference runtime. It does **not** replace the default `llama-server` or `LLM_BACKEND=llama-server`.

Use it when you need:

- MoE expert offload to pinned host RAM, system RAM, or SSD streaming
- Turbo low-bit KV cache (with flash-attention)
- Strong MTP speculative decoding and PLE/N-gram controls
- Native safetensors loading (no intermediate GGUF required for some checkpoints)

It remains OpenAI-compatible and GGUF-compatible, so Portal, Open WebUI, Hermes, LiteLLM, and external clients can keep the same API base URL once routing points at this runtime.

## Architecture alignment (ICD)

```
Model selection
      ↓
Runtime profile
      ↓
Inference Configuration Discovery
      ↓
┌──────────────────────────────┐
│ Dashboard editor             │
│ GPU layers / Context /       │
│ KV cache K/V / KV offload /  │
│ Flash Attention / MoE offload│
│ Batch / Speculation-MTP /    │
│ Draft tokens                 │
└──────────────────────────────┘
      ↓
runtime validation
      ↓
ODS env update
      ↓
llama-server recreate (cafe-llama)
      ↓
benchmark
      ↓
MEASURED configuration_id
```

## Enable (explicit opt-in)

Install the curated service recipe at `extensions/services/cafe-llama` first
(using the ODS extension installation path, or copying it from
`extensions/library/services/cafe-llama/`). This change does not add an
`ods enable` or `ods start` CLI command.

Set `CAFE_LLAMA_ENABLED=true` only when you want model-activation requests to
select cafe-llama. The host agent currently supports host-native Linux with
NVIDIA, or CPU when a compatible `CAFE_LLAMA_RELEASE_URL` is configured.
WSL-managed, agent-in-container, macOS, and AMD configurations fail closed.
The regular `llama-server` remains the default when the flag is false.

During model activation the agent maps the selected GGUF into `/models`, stops
the competing `llama-server` process to avoid double VRAM use, starts cafe,
and requires all of the following before publishing its route:

1. `/health` returns a healthy response.
2. `/v1/models` reports the requested model identity.
3. `/props` proves a valid context for the selected model.
4. A real chat completion returns visible content from that model.

The route is published as `cafe-llama-default` only after those checks pass.
If staging or verification fails, ODS restores the previously active backend,
model configuration, and route. A host-side end-to-end activation/rollback
run on target hardware is still required; CI cannot prove NVIDIA runtime behavior.

Library recipe: `extensions/library/services/cafe-llama/`

The image defaults to the 0.75 Linux x64 CUDA 12.4 release. CPU-only use requires
a compatible release archive configured through `CAFE_LLAMA_RELEASE_URL`.

## Dashboard / `.env` keys

| Key | Purpose |
|-----|---------|
| `CAFE_LLAMA_ENABLED` | Opt-in marker (does not change `LLM_BACKEND`) |
| `EXT_CAFE_LLAMA_PORT` | Host port for the loopback-published API |
| `CAFE_LLAMA_MODEL` | Activation-managed path under `/models`; leave blank in `.env` |
| `CAFE_LLAMA_RELEASE_URL` | Optional CPU-compatible release archive URL; required for CPU builds |
| `CAFE_LLAMA_SERVER` | Optional in-container binary override |
| `CAFE_LLAMA_EXTRA_ARGS` | Raw extra CLI flags |
| `LLAMA_ARG_HOST_MOE` / `LLAMA_ARG_N_HOST_MOE` | Host MoE offload |
| `LLAMA_ARG_CPU_MOE` / `LLAMA_ARG_N_CPU_MOE` | CPU MoE offload |
| `LLAMA_ARG_SSD_STREAMING` / `LLAMA_ARG_N_SSD` | SSD expert streaming |
| `LLAMA_ARG_CACHE_TYPE_K` / `V` | KV types (`turbo4`/`turbo3`/`turbo2` on cafe builds) |
| `LLAMA_ARG_FLASH_ATTN` | Required for turbo KV |
| `LLAMA_ARG_SPEC_TYPE` / `LLAMA_ARG_SPEC_DRAFT_N_MAX` | MTP / speculative |
| `LLAMA_ARG_PIPELINE_PARALLEL` | Host→device overlap |
| `LLAMA_ARG_NO_NGRAM` / `LLAMA_ARG_NGRAM_SSD` | Qwen Flash Next PLE |

Recommended starting point (MoE + long context on 8–24 GB):

```bash
LLAMA_ARG_HOST_MOE=on
LLAMA_ARG_CACHE_TYPE_K=turbo4
LLAMA_ARG_CACHE_TYPE_V=turbo4
LLAMA_ARG_FLASH_ATTN=on
LLAMA_ARG_SPEC_TYPE=draft-mtp
LLAMA_ARG_SPEC_DRAFT_N_MAX=4
```

## Evidence rules

| Class | Meaning |
|-------|---------|
| **REPORTED** | Cifras publicadas por el proyecto o terceros |
| **OBSERVED** | Capacidad verificada en README / releases / código |
| **ESTIMATED** | Inferencia de viabilidad antes de ejecutar |
| **MEASURED** | Result produced on target hardware under a controlled workload |

Only **MEASURED** results justify local performance claims.

## Out of scope (follow-ups)

- Installer auto-promotion of cafe-llama as a first-class `LLM_BACKEND` value
- Automatic ICD sweeps / recommended profiles
- Replacement of default `llama-server` paths

## References

- https://github.com/quimmedes/cafe-llama.cpp
- https://github.com/quimmedes/cafe-llama.cpp/releases
- https://huggingface.co/quimmedes/Qwen3.8-Flash-Next-MTP-GGUF
