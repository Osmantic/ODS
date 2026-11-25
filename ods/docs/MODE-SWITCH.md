# ODS Mode Switch

One-command switching between local, cloud, and hybrid LLM modes.

---

## Quick Start

```bash
# Check current mode
ods mode

# Switch to local mode (llama-server, requires GPU)
ods mode local

# Switch to cloud mode (LiteLLM + API keys, no GPU needed)
ods mode cloud

# Switch to hybrid mode (local primary, cloud fallback)
ods mode hybrid

# Restart to apply
ods restart
```

---

## How It Works

One env var (`LLM_API_URL`) controls where all services send LLM requests.
Three modes are user-selectable via `ods mode`. AMD GPUs use local mode like
every other GPU. The maintainer contract for provider modes lives in
[Engine Provider Modes](ENGINE-PROVIDER-MODES.md).

| Mode | `LLM_API_URL` | `ODS_MODE` | LiteLLM config |
|------|---------------|--------------|-----------------|
| **local** | `http://llama-server:8080` | `local` | `config/litellm/local.yaml` |
| **cloud** | `http://litellm:4000` | `cloud` | `config/litellm/cloud.yaml` |
| **hybrid** | `http://litellm:4000` | `hybrid` | `config/litellm/hybrid.yaml` |

All compose files reference `${LLM_API_URL:-http://llama-server:8080}`, so existing installs work without changes.

In local mode, an install whose model runs outside the stack sends requests
through LiteLLM (`LLM_API_URL=http://litellm:4000`): the Windows Portal's
`llama-server.exe` on an AMD GPU, or an external OpenAI-compatible server set
with `--external-llm-url`.

---

## Modes

### Local Mode (default)
All inference runs on your hardware via llama-server.

| Aspect | Details |
|--------|---------|
| **LLM** | llama-server (GGUF models) |
| **Cost** | $0 (electricity only) |
| **Requires** | GPU or CPU with sufficient RAM |
| **Web Search** | via SearXNG |

```bash
ods mode local
```

### Cloud Mode
LLM requests routed through LiteLLM to cloud APIs.

| Aspect | Details |
|--------|---------|
| **LLM** | Claude, GPT-4o, MiniMax via LiteLLM |
| **Cost** | ~$0.003-0.06/1K tokens |
| **Requires** | Internet, API keys |
| **GPU** | Not needed |

```bash
ods mode cloud
```

**Required .env variables:**
```bash
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
```

### Hybrid Mode
Local llama-server as primary, cloud APIs as fallback via LiteLLM.

| Aspect | Details |
|--------|---------|
| **LLM** | Local first, cloud on failure |
| **Cost** | $0 normally, cloud rates on fallback |
| **Requires** | GPU + API keys (recommended) |

```bash
ods mode hybrid
```

### AMD GPUs

AMD GPUs run in local mode on llama.cpp's `llama-server`, like every other GPU:
the Vulkan container image on Linux (ROCm is optional) and `llama-server.exe`
on Windows. The former `lemonade` mode is retired. An installer rerun moves an
install that used it to local mode; until then ODS reads it as `local`. See
[AMD GPUs now run on llama.cpp](MIGRATION-LEMONADE-TO-LLAMACPP.md).

For AMD Strix Halo performance tuning (GRUB, kernel module, sysctl settings), see [`config/system-tuning/README.md`](../config/system-tuning/README.md).

A Lemonade Server you run yourself connects like any other OpenAI-compatible
server; see
[Can ODS reuse a model already running in Ollama or LM Studio?](FAQ.md#can-ods-reuse-a-model-already-running-in-ollama-or-lm-studio).

---

## .env Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ODS_MODE` | `local` | Active mode: `local`, `cloud`, or `hybrid`. The retired `lemonade` value is read as `local` |
| `LLM_API_URL` | `http://llama-server:8080` | Where services send LLM requests |
| `ANTHROPIC_API_KEY` | *(empty)* | Anthropic API key (cloud/hybrid) |
| `OPENAI_API_KEY` | *(empty)* | OpenAI API key (cloud/hybrid) |
| `TOGETHER_API_KEY` | *(empty)* | Together AI API key (optional) |
| `MINIMAX_API_KEY` | *(empty)* | MiniMax API key (optional, cloud/hybrid) |

---

## Installer: `--cloud` Flag

Install in cloud mode (skips GPU detection and model download):

```bash
./install-core.sh --cloud
```

This sets `ODS_MODE=cloud`, `LLM_API_URL=http://litellm:4000`, and auto-enables the LiteLLM extension.

An ordinary installer rerun preserves the valid `ODS_MODE` already stored in
the owner-controlled `.env`. This prevents an upgrade or repair rerun from
silently moving a configured cloud or hybrid installation back to local
inference. An explicit operator selection still wins: use `--cloud` or set
`ODS_MODE` in the installer environment when you intentionally want to change
the mode.

---

## Model Management

```bash
# Show current model
ods model current

# List available tiers
ods model list

# Swap to a different tier
ods model swap T3
```

For Dashboard downloads, loading catalog models, and manual GGUF swaps, see
[MODEL-MANAGEMENT.md](MODEL-MANAGEMENT.md).

---

## Architecture

### Local Mode
```
User -> Open WebUI -> llama-server (local) -> Response
```

### Cloud Mode
```
User -> Open WebUI -> LiteLLM -> Cloud APIs (Claude/GPT-4o)
```

### Hybrid Mode
```
User -> Open WebUI -> LiteLLM -> llama-server (local) -> Response
                                      |
                                 [On timeout/error]
                                      |
                                 Cloud APIs (fallback)
```

---

## Files

| File | Purpose |
|------|---------|
| `config/litellm/local.yaml` | LiteLLM config for local mode |
| `config/litellm/cloud.yaml` | LiteLLM config for cloud mode |
| `config/litellm/hybrid.yaml` | LiteLLM config for hybrid mode |
| `scripts/mode-switch.sh` | Backend script for mode switching |
| `.env` | Stores `ODS_MODE`, `LLM_API_URL`, API keys |

---

## Data Safety

**All modes share the same data volumes:**
- `./data/open-webui/` -- Conversations, users
- `./data/qdrant/` -- Vector database
- `./data/models/` -- Downloaded GGUF models

**Switching modes preserves all data.** Only the LLM routing changes.

---

## Mode Comparison

| Feature | Local | Cloud | Hybrid |
|---------|-------|-------|--------|
| Internet required | No | Yes | Yes (for fallback) |
| API keys required | No | Yes | Recommended |
| GPU required | Yes | No | Yes |
| Response quality | Good | Best | Best of both |
| Cost | $0 | $$$ | $0 or $$$ |
| Privacy | 100% local | Data to cloud | Local unless fallback |

---

## CLI Reference

```bash
# Mode commands
ods mode              # Show current mode
ods mode local        # Switch to local mode
ods mode cloud        # Switch to cloud mode
ods mode hybrid       # Switch to hybrid mode

# Model commands
ods model current     # Show current model
ods model list        # List available tiers
ods model swap T2     # Switch model tier

# Shorthand
ods m local           # Shorthand for mode local
```

---

## Troubleshooting

### Cloud mode: "No API keys found"
```bash
# Add your API keys to .env
ods config edit
# Add: ANTHROPIC_API_KEY=sk-ant-...
ods restart
```

### Local mode: llama-server won't start
```bash
# Check GPU status
nvidia-smi
# Check model is downloaded
ls -la data/models/*.gguf
# Check logs
ods logs llama-server
```

### Mode switch not taking effect
```bash
# Verify .env
grep ODS_MODE .env
grep LLM_API_URL .env
# Restart all services
ods restart
```

---

## Rollback

If anything breaks, restore default behavior:
```bash
ods mode local
ods restart
```

Or manually edit `.env`:
```bash
ODS_MODE=local
LLM_API_URL=http://llama-server:8080
```
