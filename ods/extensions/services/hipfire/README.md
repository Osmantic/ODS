# hipfire — RDNA-native LLM inference engine

An AMD-only, ROCm-direct inference engine ([upstream](https://github.com/Kaden-Schutt/hipfire))
that runs **alongside** `llama-server`/Lemonade rather than replacing it. LiteLLM routes text to
hipfire; Lemonade keeps serving ODS Talk vision and acts as the fallback.

Side-by-side is the blessed ODS pattern for a second engine — see `docs/VLLM-SETUP.md:42`.

## Why

Measured on this host (2× Radeon AI PRO R9700, gfx1201, ROCm 7.2.4), same weights
(Qwen3.6-35B-A3B), same prompt, same harness, 256 tokens:

| engine | GPUs | throughput |
|---|---|---|
| llama.cpp / Lemonade (Q4_K_M) | **2** | 64.7 tok/s |
| **hipfire (MQ4)** | **1** | **104.3 tok/s** |

~1.61× faster on half the hardware, which is what frees GPU1 to keep Lemonade resident.

## GPU split

hipfire owns **GPU0** (`HIPFIRE_GPU_INDEX=0`); Lemonade is pinned to **GPU1**
(`LLAMA_SERVER_GPU_INDICES=1`). This is required, not cosmetic: the 35B MQ4 needs ~30 GB and
must be resident on a *single* card, because hipfire has **no pipeline-parallel on master**
(upstream issue #58 — `--tp N` is expert-parallel for MoE routing, not a model split).

## The model is NOT interchangeable with ODS's GGUFs

hipfire uses its own MQ4 format. Two traps, both verified the hard way:

1. **`hipfire pull qwen3.6:35b-a3b` 404s.** Both 35B-A3B MoE entries in upstream's registry point
   at unpublished HuggingFace repos (one is even tagged "LOCAL ONLY").
2. **Converting the existing GGUF does not work.** hipfire's GGUF→MQ4 path has no branch for 3-D
   MoE expert tensors, so it dumps all 256 experts/layer to F16: 7.1 % quantized, **66 GB out of a
   22 GB input**, unloadable. Only the *safetensors* path splits and quantizes experts.

So the model was built from the original BF16 safetensors (kept at
`/home/tim/models/Qwen3.6-35B-A3B`):

```bash
hipfire-quantize --input /home/tim/models/Qwen3.6-35B-A3B \
                 --output data/hipfire/models/qwen36-35b-a3b.mq4 --format mq4
# -> 22.8 GB, 100.0% of params quantized, mean quant error 0.0
```

`ods model swap` and the dashboard's model endpoints do **not** know about this file — they
manage GGUFs for llama-server. Re-quantize by hand to change hipfire's model.

## Version pin

`HIPFIRE_REF` is pinned to a **master commit, deliberately not the v0.2.1 tag**: v0.2.1's
quantizer *silently* produces the broken 66 GB model above, and the gfx1201 kernel work landed
after the tag. Bump it consciously.

## Enable / disable

```bash
./install-core.sh --hipfire      # or --no-hipfire (default)
```
Enable/disable is a file rename (`compose.yaml` ↔ `compose.yaml.disabled`) driven by
`_sync_extension_compose` in `installers/phases/03-features.sh`. Never hand-edit `.compose-flags`
— it is a cache the resolver regenerates.

## Model control (dashboard)

hipfire models are catalog entries in `config/model-library.json` with `"engine": "hipfire"`
and a `model_file` instead of a GGUF. They appear on the dashboard's Models page whenever
`ENABLE_HIPFIRE=true` (never as a download — hipfire pulls its own weights on container start).

Activating one drives the normal path (`POST /api/models/{id}/load` → host-agent
`/v1/model/activate`), which pins `HIPFIRE_MODEL` in `.env`, recreates the container,
health-gates until the model is resident, and re-renders `config/litellm/lemonade.yaml` so
`default` routes to hipfire. Activating a GGUF flips `default` back to llama-server/Lemonade.
Either way both engines stay reachable via their named LiteLLM routes (`hipfire` / `lemonade`)
— the routing is rendered from the template by `scripts/render-runtime-configs.py`, so a model
swap can no longer wipe it. State keys: `HIPFIRE_MODEL` (what hipfire serves) and
`HIPFIRE_ACTIVE` (whether it owns the `default` route).

## Notes

- No `/v1/embeddings` (ODS runs a separate TEI service — unaffected) and **no auth**. Safe only
  because the host port stays loopback-bound via `BIND_ADDRESS`.
- `HIPFIRE_IDLE_TIMEOUT=0` on purpose: upstream's 300 s default evicts the model and forces a
  30 s–2 min cold reload on the next request, which is wrong behind a router.
- 69 of 528 gfx1201 kernels fail to compile upstream (all `attention_dflash_wmma_*` spec-decode
  variants). This reproduces on the bare host toolchain and does not affect serving; the
  Dockerfile gates on a kernel **count**, not on the compile script's exit status.
