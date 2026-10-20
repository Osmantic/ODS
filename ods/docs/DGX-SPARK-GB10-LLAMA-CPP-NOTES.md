# DGX Spark GB10 llama.cpp Notes

Date: 2026-05-07

This note tracks a field investigation on an NVIDIA DGX Spark / GB10 machine where
Qwen3-Coder-Next returned only question marks through Open WebUI.

## Machine

- Host: DGX Spark field machine
- OS: Ubuntu 24.04.4 LTS, aarch64
- Kernel: `6.17.0-1014-nvidia`
- GPU: NVIDIA GB10, compute capability 12.1
- CUDA toolkit: 13.0.88
- Driver: 580.142

## Symptom

Open WebUI displayed responses like:

```text
????????????????????????????????
```

The issue reproduced outside Open WebUI by calling llama.cpp directly:

```bash
curl -sS http://127.0.0.1:11434/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3-coder-next","messages":[{"role":"user","content":"What is 2+2? Answer in one short sentence."}],"max_tokens":32,"temperature":0}'
```

With `ghcr.io/ggml-org/llama.cpp:server-cuda-b9014`, the response content was
question marks. Raw `/completion` and `/v1/completions` requests showed the same
behavior, so this was not an Open WebUI rendering issue.

## Isolation

- Same GGUF on CPU-only llama.cpp generated normal text.
- GPU offload with the generic llama.cpp CUDA image generated question marks.
- Disabling flash attention did not fix the output.
- `--no-op-offload` improved a trivial echo prompt but still failed a basic
  `2+2` prompt.
- The model file itself was viable:
  - Path: `$HOME/ods/data/models/qwen3-coder-next-Q4_K_M.gguf`
  - SHA256: `9e6032d2f3b50a60f17ce8bf5a1d85c71af9b53b89c7978020ae7c660f29b090`

## Bad Runtime Evidence

The failing container reported GB10 as compute 12.1, but the compiled CUDA archs
did not include the Spark target:

```text
Device 0: NVIDIA GB10, compute capability 12.1
system_info: ... CUDA : ARCHS = 500,610,700,750,800,860,890,1200 ...
```

NVIDIA's DGX Spark llama.cpp playbook instructs Spark users to build llama.cpp
with:

```bash
-DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES="121"
```

The DGX Spark porting guide similarly recommends compiling for `121-real`.

Relevant upstream references:

- https://build.nvidia.com/spark/llama-cpp/instructions
- https://build.nvidia.com/spark/llama-cpp/troubleshooting
- https://docs.nvidia.com/dgx/dgx-spark-porting-guide/porting/compilation.html
- https://github.com/ggml-org/llama.cpp/issues/19305

## Working Runtime

Built llama.cpp natively on the Spark:

```bash
git clone --depth=1 https://github.com/ggml-org/llama.cpp $HOME/code/llama.cpp-gb10
cd $HOME/code/llama.cpp-gb10
cmake -S . -B build-gb10 \
  -DGGML_CUDA=ON \
  -DCMAKE_CUDA_ARCHITECTURES=121 \
  -DCMAKE_BUILD_TYPE=Release \
  -DLLAMA_CURL=OFF \
  -DCMAKE_CUDA_COMPILER=/usr/local/cuda/bin/nvcc
cmake --build build-gb10 --target llama-server -j 12
```

CMake rewrote the Spark target as expected:

```text
Replacing 121 in CMAKE_CUDA_ARCHITECTURES with 121a
Using CMAKE_CUDA_ARCHITECTURES=121a
```

The working binary reports:

```text
build_info: b1-2496f9c
system_info: ... CUDA : ARCHS = 1210 ... BLACKWELL_NATIVE_FP4 = 1 ...
```

Native and containerized tests both produced normal text:

```text
2 + 2 = 4.
Hello from Qwen.
```

## Local ODS Fix Applied

Created a local image:

```text
ods-llama-cpp-gb10:sm121-2496f9c
```

The image uses:

- Base: `nvidia/cuda:13.0.0-runtime-ubuntu24.04`
- Runtime deps: `ca-certificates`, `curl`, `libgomp1`, `libssl3`
- Payload: `build-gb10/bin/` copied to `/app`
- Entry point: `/app/llama-server`

Updated the live install at `$HOME/ods/.env`:

```env
LLAMA_SERVER_IMAGE=ods-llama-cpp-gb10:sm121-2496f9c
```

Validation after `./ods-cli start llm`:

- `ods-llama-server` is healthy.
- Host API `http://127.0.0.1:11434/v1/chat/completions` returns normal text.
- Open WebUI container path `http://llama-server:8080/v1/chat/completions`
  returns normal text.

## Upstream Changes And Follow-ups

- Added a `ods doctor` runtime check that detects DGX Spark / GB10
  (`compute_cap=12.1` or `nvidia-smi` name contains `GB10`) and compares
  llama-server's reported CUDA archs against the required `sm_121` target.
- Warn when GB10 is served by a llama.cpp CUDA binary that reports archs without
  `1210` / `121` / `121a`.
- Surface a remediation hint to build llama.cpp with
  `-DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=121` or use a GB10-specific
  llama-server image.

## CUDA 13 image for compute capability 12.1 (2026-10)

llama.cpp publishes a CUDA 13 build of each release beside the CUDA 12 one
(`server-cuda13-<build>` beside `server-cuda-<build>`). The CUDA 12 builds,
including the ODS default `server-cuda-b11429` (CUDA 12.8), have no kernels
for compute capability 12.1. The CUDA 13 build does: on an RTX Spark (N1X,
compute capability 12.1) under WSL it answered `2+2` with `4` and wrote
coherent text.

ODS now uses it on every GPU of compute capability 12.1, the DGX Spark GB10
and the RTX Spark N1X:

- Detection records the GPU's compute capability as `GPU_COMPUTE_CAP` in
  `.env`.
- `config/backends/nvidia.json` maps `12.1` to `server-cuda13-b11429`, pinned
  by its multi-arch index digest and recorded in `config/dependency-lock.json`.
- Phase 06 replaces a CUDA 12 llama.cpp image, or the empty Compose default,
  with that image right before it writes `.env`; phase 08 pulls it.
- The host agent applies the same map when the Models page switches models
  and when it rolls a failed switch back.
- An owner's own `LLAMA_SERVER_IMAGE` (for example a local `sm_121` build) and
  any other CUDA 13 build are kept.

Every other NVIDIA GPU keeps the CUDA 12 default. An install made before this
change fixes itself on the next installer run.

Follow-up ideas:

- Add a GB10-specific local build path or documented override for
  `LLAMA_SERVER_IMAGE`.
- Add troubleshooting text for the specific question-mark output pattern:
  direct API repro, CPU-only isolation, and `sm_121` rebuild guidance.
