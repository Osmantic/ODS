# MLX Engine (Experimental)

> **Status: experimental.** Opt-in, Apple Silicon only, not wired into the
> installer, dashboard, or tier system yet. The default engine on macOS
> remains the native Metal llama-server. Nothing in a normal install starts
> MLX — you have to turn it on.

[MLX](https://github.com/ml-explore/mlx) is Apple's machine-learning
framework for Apple Silicon unified memory. `mlx-lm` ships an
OpenAI-compatible inference server, and the
[mlx-community](https://huggingface.co/mlx-community) org publishes
ready-to-run quantized conversions of most open models. On M-series
hardware, MLX models can outperform their GGUF equivalents for some
workloads — and some model families ship MLX conversions before GGUF ones.

Dream Server's MLX support is a self-contained native engine manager,
`scripts/mlx-server.sh`, that runs **beside** the existing llama-server on
its own port (default `8081`). It deliberately changes nothing about the
default stack.

## Requirements

- Apple Silicon Mac (the script refuses to run elsewhere)
- `python3` (Xcode CLT or Homebrew)
- Disk space for model weights under `<install>/data/mlx/`

## Quickstart

All mutating verbs are gated behind `DREAM_ENABLE_EXPERIMENTAL_MLX=1`
(the same opt-in pattern as experimental Jetson support). `stop`,
`status`, and `health` work without the gate, so you can always inspect
or bring down a server you started.

```bash
cd ~/dream-server

# One-time: create the dedicated venv and install mlx-lm (PEP 668-safe —
# never touches system or Homebrew Python site-packages)
DREAM_ENABLE_EXPERIMENTAL_MLX=1 scripts/mlx-server.sh install

# Start (first start downloads the model into data/mlx/hf-cache)
DREAM_ENABLE_EXPERIMENTAL_MLX=1 scripts/mlx-server.sh start

# Verify
scripts/mlx-server.sh status
curl -s http://127.0.0.1:8081/v1/models
```

Chat against it with any OpenAI-compatible client:

```bash
curl -s http://127.0.0.1:8081/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mlx-community/Qwen3-4B-4bit",
    "messages": [{"role": "user", "content": "/no_think hello"}],
    "max_tokens": 200
  }'
```

> **Thinking models:** Qwen3-family models emit internal reasoning into the
> `reasoning` field and can spend the whole `max_tokens` budget before
> producing `content` — the same reason the stack defaults
> `LLAMA_REASONING=off`. Prefix prompts with `/no_think`, or raise
> `max_tokens`.

Stop it:

```bash
scripts/mlx-server.sh stop
```

## Configuration

Set in `.env` (see `.env.example`) or as environment variables:

| Key | Default | Meaning |
|---|---|---|
| `DREAM_ENABLE_EXPERIMENTAL_MLX` | `0` | `1` enables install/start/restart |
| `MLX_PORT` | `8081` | API port (native process, no Docker mapping) |
| `MLX_MODEL` | `mlx-community/Qwen3-4B-4bit` | Hugging Face repo to serve |
| `MLX_START_TIMEOUT` | `600` | Seconds to wait for first health (downloads count) |
| `BIND_ADDRESS` | `127.0.0.1` | Same knob the native llama-server honours |

A different model for one run: `... mlx-server.sh start --model mlx-community/<repo>`.

Pick models by RAM the same way you would GGUF quants — a 4-bit MLX model
needs roughly the same memory as its `Q4_K_M` GGUF counterpart. Browse
[mlx-community](https://huggingface.co/mlx-community) for conversions.

Defaults live in `config/backends/apple.json` under `runtime.mlx`,
following the same pattern as the Lemonade runtime block in
`config/backends/amd.json`.

## Using MLX from the rest of the stack

The server speaks the OpenAI API at `http://127.0.0.1:8081/v1`. From
**Open WebUI**: Admin Settings → Connections → add an OpenAI API
connection with that base URL (from inside a container, use
`http://host.docker.internal:8081/v1`). Any other OpenAI-compatible
client works the same way.

## State and uninstall

Everything lives under the install dir:

```
data/mlx/venv/        # dedicated Python venv with mlx-lm
data/mlx/hf-cache/    # model weights (HF_HOME)
data/.mlx-server.pid
data/mlx-server.log
```

Uninstall completely:

```bash
scripts/mlx-server.sh stop
rm -rf data/mlx data/.mlx-server.pid data/mlx-server.log
```

## Current limitations (deliberate, while experimental)

- Not started by the installer, `dream-cli`, or launchd — manual lifecycle only.
- Not registered with the dashboard, LiteLLM, or Hermes; no tier-map model
  selection (set `MLX_MODEL` yourself).
- No SHA-pinned model verification (weights come from the Hugging Face hub
  with its own integrity checks, unlike the curated GGUF catalog).
- Single model per server instance.

If the experiment earns its keep, the follow-up path is: `llm_engine: "mlx"`
as a first-class engine in the apple backend contract, tier-map MLX model
selection, dashboard/host-agent integration, and a launchd plist — each its
own reviewed change.

## Validation contract

`tests/contracts/test-macos-mlx-contracts.sh` (runs in `make test` on every
platform) pins the safety properties: experimental gate on every mutating
verb, Bash 3.2 compatibility, PEP 668 venv-only installs, loopback-default
bind, state containment under the install dir, and TERM→KILL shutdown.
