# Running vLLM Alongside ODS

This is a recipe, not a turnkey extension. ODS's default inference backend is `llama-server` (llama.cpp), which is the right choice for the platform's portability story — it runs on CPU, NVIDIA, AMD, and Apple Silicon. **vLLM** is an alternate backend that wins on a narrower slice of hardware (mostly high-end NVIDIA), and only for specific workload shapes. This guide explains when to reach for it, what to install, and which flags actually matter — so you can stand it up without having to learn the same lessons through trial and error.

There is no installer support for vLLM yet. If there's maintainer interest in shipping a first-class `vllm` extension, this doc is the precursor — see *Future work* at the bottom.

---

## When vLLM is worth it

vLLM is throughput-optimized: it serves many concurrent requests by batching prefill and decode across requests with continuous-batching and PagedAttention. That changes the cost-benefit vs llama-server in concrete ways:

**Reach for vLLM when…**
- You serve more than a handful of concurrent users or agents.
- Your bottleneck is decode throughput across requests, not single-request latency.
- You're running a model vLLM has good kernels for — recent Qwen, Llama, Mistral, DeepSeek, Phi families.
- You have a high-end NVIDIA GPU (24 GB+ VRAM) and the headroom to give vLLM a generous KV cache.

**Stay on llama-server when…**
- You're a single user (vLLM's batching wins evaporate at concurrency = 1).
- You're on AMD, Apple Silicon, or CPU. ROCm vLLM exists but has gaps; llama.cpp is the better bet on those backends today.
- You need to swap models frequently. vLLM holds one model resident; reloading takes 60–120 s.
- You're constrained on VRAM. vLLM's KV cache pre-allocation is hungrier than llama-server's.

---

## Hardware fit

| Setup | Recommendation |
|---|---|
| NVIDIA, single GPU, 24 GB+ | Good fit. Use `--tensor-parallel-size 1`. |
| NVIDIA, 2× same-class GPU | Good fit for larger models. **Pipeline-parallel typically beats tensor-parallel** at this scale (see *Multi-GPU layout* below). |
| NVIDIA, mixed GPUs | Possible but constrained — vLLM expects symmetric topology. |
| AMD ROCm | Possible (vLLM has a ROCm path) but kernel support lags NVIDIA; expect rough edges. |
| Apple Silicon | No. vLLM has no Metal backend. |
| CPU only | No. vLLM is CUDA-first. |

---

## Install path

vLLM ships an OpenAI-compatible Docker image. It can join ODS's existing network and live alongside `llama-server`, not replace it. Both can run; route to the one you want per use case.

```bash
docker pull vllm/vllm-openai:latest
```

Pin a digest in production. The `:latest` tag moves; behavior changes between releases.

---

## A working launch command

This is a real config validated 2026-05-04 against a sustained-concurrency load test. It serves Qwen3-Coder (AWQ 4-bit) on a single 24 GB-class NVIDIA GPU. Adapt the model path and `--gpus` selector for your setup.

```bash
docker run -d --name vllm-server --restart=no \
  --gpus '"device=0"' \
  --network ods-network \
  -p 127.0.0.1:8000:8000 \
  --shm-size 8g \
  -v /path/to/models:/models \
  vllm/vllm-openai:latest \
  --model /models/your-model-dir \
  --served-model-name your-model-name \
  --host 0.0.0.0 --port 8000 \
  --tensor-parallel-size 1 \
  --max-model-len 65536 \
  --gpu-memory-utilization 0.92 \
  --enable-chunked-prefill \
  --enable-prefix-caching \
  --max-num-batched-tokens 8192 \
  --max-num-seqs 256
```

**Flags worth understanding before you change them:**

| Flag | Why it's there |
|---|---|
| `--max-model-len 65536` | KV cache is allocated up-front per slot. Halving this from 256k to 64k freed enough memory for ~8× more concurrent slots, which dominated throughput in our load test. Pick the smallest value that covers your real prompt + output budget. |
| `--gpu-memory-utilization 0.92` | Leaves ~8% VRAM headroom for the CUDA runtime + driver. Going higher risks OOM under prefill spikes. |
| `--enable-chunked-prefill` | Splits long prefills into batched chunks so decode-phase requests don't get starved. Smooths TTFT under load. |
| `--enable-prefix-caching` | Caches the KV of repeated system-prompt prefixes. Free win when you serve a chat app or a fixed-persona endpoint. |
| `--max-num-batched-tokens 8192` | The chunk size for chunked prefill. 8192 worked well; smaller hurts throughput, larger raises tail latency. |
| `--max-num-seqs 256` | Concurrent request ceiling. The vLLM default; explicit so reviewers don't have to guess. |
| `--shm-size 8g` (docker arg) | vLLM uses `/dev/shm` heavily for inter-process tensors. The Docker default (64 MB) hangs at startup. |

Add these as needed:

| Flag | When |
|---|---|
| `--enable-auto-tool-choice --tool-call-parser <name>` | Serving a tool-calling model (e.g. `qwen3_coder`, `llama3_json`). |
| `--quantization awq` / `gptq` / `fp8` | Quantization isn't always autodetected from the model dir; specify it if startup logs complain. |
| `--enforce-eager` | Disables CUDA graphs. Significantly slower; only set if you hit a graph-capture bug on a new model. |

Container takes 90–120 s to load weights and warm up CUDA graphs. `/v1/models` returns 503 during that window — bake that into any healthcheck.

---

## Multi-GPU layout

vLLM offers two ways to spread a model across GPUs:

- **Tensor-parallel (`--tensor-parallel-size N`)** — splits each layer's weights across N GPUs. All GPUs work on every token. Simple, but pays NCCL all-reduce on every layer.
- **Pipeline-parallel (`--pipeline-parallel-size N`)** — splits *layers* across N GPUs in a sequence. Each GPU processes a chunk of the model.

Counterintuitive finding from a 2-GPU same-class NVIDIA rig: **pipeline-parallel beat tensor-parallel-row by ~45% on decode throughput** for a large MoE model. The all-reduce overhead of TP across layers outweighed the pipeline-bubble cost of PP for that workload.

Don't generalize this past the rig it was measured on. Test both for your specific model + GPU pair before committing. Default to `--tensor-parallel-size 2` for safety; switch to `--pipeline-parallel-size 2` if you measure a meaningful gain.

---

## Operational gotchas

### Qwen3 think-mode

Qwen3 chat templates emit thinking blocks (`<think>…</think>`) by default. If you're routing vLLM into a UI or agent that doesn't strip them, output will look broken or chatty. The fix is to set `chat_template_kwargs.enable_thinking = false` on each request.

Perplexica is the closest in-tree integration point today: its [`compose.yaml`](../extensions/services/perplexica/compose.yaml) reads `LLM_API_URL` and passes it through as an OpenAI-compatible base URL. If you route Perplexica or another ODS consumer to Qwen3 on vLLM, add a small proxy or client-side request hook that forces this flag for every chat completion.

### Power cap behavior (NVIDIA)

If you set a per-GPU power cap via `nvidia-smi -pl`, vLLM's sustained-load throughput is much less sensitive to the cap than diffusion workloads are. Measured on a Blackwell-class card serving a quantized Qwen3 at sustained concurrency: **500 W cap was within ~3.3% of the optimal cap across the entire 350–600 W sweep range**. Sustained native draw under vLLM load topped out around 575 W on that card, so caps above ~575 W don't change anything. Lowering to 500 W is essentially free.

Don't extrapolate this to image/video generation — those workloads are V/f-bound and *do* care about the cap.

### Model load time

90–120 s for weights + CUDA graph warm-up is normal. Anything that pings `/v1/models` before that window expires will see 503. If you wire vLLM behind a healthcheck, give it a `start_period` of at least 180 s.

### KV cache vs context length

The cost of doubling `--max-model-len` is doubling the per-slot KV allocation, which proportionally cuts how many concurrent slots fit in VRAM. This is the biggest tuning lever; pick the shortest context that covers your real workload, not the model's max.

---

## Existing ODS integration

vLLM is not a first-class extension yet, but Perplexica can already be pointed at an OpenAI-compatible vLLM container on the `ods-network` in place of the default `llama-server` by changing `LLM_API_URL`. Treat that as the current integration seam: ODS has a consumer that can talk to vLLM, while a production-ready vLLM service wrapper still belongs in future work.

---

## Future work

If maintainers and contributors are interested, the natural follow-up is a first-class `extensions/services/vllm/` extension: `manifest.yaml` + `compose.yaml` + a Dockerfile that pins a vLLM digest, with `gpu_backends: [nvidia]` and `category: optional`. Open a discussion on the issue tracker before sending that PR — it touches inference-backend territory and the maintainers should weigh whether it fits the platform's portability-first philosophy or stays a recipe in this doc.

---

## Provenance

The launch flags, multi-GPU finding, and power-cap tolerance numbers in this doc come from a 2026-04 to 2026-05 sweep on an NVIDIA Blackwell-class workstation serving Qwen3 family models under sustained concurrent load. Numbers will differ on other hardware and workload shapes — treat this guide as a starting point, not a benchmark.
