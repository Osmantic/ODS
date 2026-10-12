# cafe-llama (library extension)

Optional **advanced** llama.cpp runtime based on [cafe-llama.cpp](https://github.com/quimmedes/cafe-llama.cpp).

Does **not** replace the core `llama-server` service or `LLM_BACKEND=llama-server`.

## Capabilities (OBSERVED)

- MoE expert offload to pinned host RAM (`-hmoe`), system RAM (`-cmoe`) or SSD streaming (`-ssd`)
- Turbo low-bit KV cache (`turbo4` / `turbo3` / `turbo2`) + Flash Attention
- MTP speculative decoding (`--spec-type draft-mtp`, recommended `--spec-draft-n-max 4`)
- PLE / N-gram control for Qwen 3.8 Flash Next
- Native safetensors loading
- OpenAI-compatible API (same clients as core llama-server)

## Enable (explicit opt-in)

The service recipe must first be installed at `extensions/services/cafe-llama`.
The host agent currently supports runtime activation only on host-native Linux
with NVIDIA, or CPU when a compatible `CAFE_LLAMA_RELEASE_URL` is configured.
WSL-managed, agent-in-container, macOS, and AMD paths fail closed. The core
`llama-server` remains the default.

Set `CAFE_LLAMA_ENABLED=true` in `.env` only when you intend subsequent model
activation requests to use cafe-llama. The host agent supplies the selected
GGUF path to the container; do not hard-code a different model in
`CAFE_LLAMA_MODEL`. Before switching routes it verifies `/health`,
`/v1/models`, the requested context, and a real chat completion. On failure,
the prior runtime and route are restored.

`ods enable cafe-llama` is not introduced by this change; install the recipe
through the available ODS extension installation path or copy it into place
before enabling the flag.

### Image build

By default the Dockerfile downloads the **0.75 Linux x64 CUDA 12.4** binary.
For CPU-only builds, select a compatible CPU archive:

```bash
docker build \
  --build-arg CAFE_LLAMA_RELEASE_URL='https://github.com/quimmedes/cafe-llama.cpp/releases/download/0.75/llama-bin-ubuntu-x64-cpu.zip' \
  -t ods-cafe-llama:local \
  extensions/services/cafe-llama
```

Set `CAFE_LLAMA_RELEASE_URL` in `.env` before requesting model activation.

## Evidence rules (LEONES)

| Class     | Meaning                                      |
|-----------|----------------------------------------------|
| REPORTED  | Published by upstream / third parties        |
| OBSERVED  | Verified from docs / releases / code         |
| ESTIMATED | Pre-run viability guess                      |
| MEASURED  | Produced on target hardware under controlled workload |

Only **MEASURED** results justify local performance claims.

## Docs

- Operator: `docs/CAFE-LLAMA.md`
- Design / ICD: LEONES `docs/integrations/cafe-llama.cpp/README.md`
