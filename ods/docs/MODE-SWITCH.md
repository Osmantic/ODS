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
Three modes are user-selectable via `ods mode`; a fourth (`lemonade`) is
auto-configured by the installer on AMD hardware today. The maintainer contract
for provider modes lives in [Engine Provider Modes](ENGINE-PROVIDER-MODES.md).

| Mode | `LLM_API_URL` | `ODS_MODE` | LiteLLM config |
|------|---------------|--------------|-----------------|
| **local** | `http://llama-server:8080` | `local` | `config/litellm/local.yaml` |
| **cloud** | `http://litellm:4000` | `cloud` | `config/litellm/cloud.yaml` |
| **hybrid** | `http://litellm:4000` | `hybrid` | `config/litellm/hybrid.yaml` |

All compose files reference `${LLM_API_URL:-http://llama-server:8080}`, so existing installs work without changes.

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

### Lemonade Mode (AMD — auto-configured)

**Not user-switchable.** This mode is automatically set by the installer on AMD hardware. `ods mode` does not accept `lemonade` as an argument — only the installer sets it.

All LLM traffic routes through the LiteLLM proxy, which delegates to the Lemonade SDK (`lemonade-server`). The dashboard API uses a distinct `/api/v1` URL prefix in this mode (instead of `/v1`).

| Aspect | Details |
|--------|---------|
| **LLM** | Lemonade SDK via LiteLLM proxy |
| **Cost** | $0 (local inference) |
| **Requires** | AMD GPU (auto-detected at install time) |
| **Set by** | Installer (Phase 06), not `ods mode` |

For AMD Strix Halo performance tuning (GRUB, kernel module, sysctl settings), see [`config/system-tuning/README.md`](../config/system-tuning/README.md).

Existing Lemonade SDK installs on Linux AMD hosts can be wrapped without letting
ODS manage the Lemonade runtime. See [Lemonade SDK Compatibility](LEMONADE-SDK-COMPAT.md).
Future Lemonade work should follow the provider-mode contract rather than
adding one-off installer or dashboard paths.

---

## .env Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ODS_MODE` | `local` | Active mode: `local`, `cloud`, or `hybrid`; `lemonade` is auto-set on AMD (not user-switchable) |
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

In lemonade mode, `ods model swap` also regenerates `config/litellm/lemonade.yaml`
(which pins the model by name) and restarts `ods-litellm`, so clients pick up the
new model without manual config edits.

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

| Feature | Local | Cloud | Hybrid | Lemonade (AMD) |
|---------|-------|-------|--------|----------------|
| Internet required | No | Yes | Yes (for fallback) | No |
| API keys required | No | Yes | Recommended | No |
| GPU required | Yes | No | Yes | Yes (AMD) |
| Response quality | Good | Best | Best of both | Good |
| Cost | $0 | $$$ | $0 or $$$ | $0 |
| Privacy | 100% local | Data to cloud | Local unless fallback | 100% local |

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
