# Model Management

Dream Server runs local language models as GGUF files from `data/models/`.
The recommended path is the Dashboard Models page; manual model swaps are
possible, but they are an operator workflow today.

## Recommended: Dashboard Models Page

Open the Dashboard and go to **Models**.

From there you can:

- See the curated Dream Server model catalog.
- Check approximate model size, VRAM requirement, context length, and specialty.
- Download a catalog model into `data/models/`.
- Load a downloaded model.
- Delete a downloaded catalog model.

When a catalog model is loaded, Dream Server updates the active GGUF settings
and restarts the local inference service so OpenAI-compatible clients use the
new model. After the switch settles, verify it from the host:

```bash
dream model current
curl http://localhost:11434/v1/models
```

On macOS native Metal and Windows native/Lemonade installs, use
`http://localhost:8080/v1/models` unless you changed the port.

Downstream apps that talk to `llama-server` or LiteLLM pick up the active model
through those services. Examples include Open WebUI, Perplexica, Token Spy, and
OpenAI-compatible SDK clients configured against Dream Server.

Hermes Agent keeps its own model name in `data/hermes/config.yaml`. If Hermes is
enabled after a model switch, verify the `model.default` line:

```bash
grep -n "default:" data/hermes/config.yaml
docker restart dream-hermes
```

For Lemonade/AMD backends, Hermes and LiteLLM may need the model name in the
form `extra.<GGUF_FILE>`.

## Where Models Live

Default model directory:

```bash
~/dream-server/data/models/
```

On Windows installs:

```powershell
%LOCALAPPDATA%\DreamServer\data\models\
```

Each model is normally a single `.gguf` file:

```bash
ls -lh ~/dream-server/data/models/*.gguf
```

The active model is recorded in `.env`:

```bash
grep -E "^(LLM_MODEL|GGUF_FILE|CTX_SIZE|MAX_CONTEXT)=" ~/dream-server/.env
```

`GGUF_FILE` is the filename Dream Server should load from `data/models/`.
`LLM_MODEL` is the friendly logical model name used by scripts and config.
`CTX_SIZE` and `MAX_CONTEXT` control context length.

Hermes requires at least a 64K context window. Installer bootstrap mode uses
`65536` for the fast-start model, then switches `.env`, llama-server, and
Hermes config to the full model context, usually `131072`, when the background
download completes.

## Manual: Download a Catalog Model

For most users, use the Dashboard. If you are debugging a failed download or
preloading a machine, download the exact catalog GGUF URL from
`config/model-library.json` into `data/models/`.

Example:

```bash
cd ~/dream-server
mkdir -p data/models

curl -L \
  -o data/models/Qwen3.5-9B-Q4_K_M.gguf \
  https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/main/Qwen3.5-9B-Q4_K_M.gguf
```

Then open Dashboard -> Models. If the filename matches a catalog entry, the
model should appear as downloaded and you can load it from the Dashboard.

## Manual: Bring Your Own GGUF

Custom GGUFs are supported by the underlying stack, but not yet as a polished
Dashboard import flow. Treat this as an operator procedure.

1. Download the GGUF into `data/models/`.

```bash
cd ~/dream-server
mkdir -p data/models
cp /path/to/MyModel-Q4_K_M.gguf data/models/
```

2. Update `.env`.

```bash
dream config edit
```

Set:

```dotenv
LLM_MODEL=my-model
GGUF_FILE=MyModel-Q4_K_M.gguf
CTX_SIZE=8192
MAX_CONTEXT=8192
```

3. Update `config/llama-server/models.ini`.

```ini
[my-model]
filename = MyModel-Q4_K_M.gguf
load-on-startup = true
n-ctx = 8192
```

4. If Hermes is enabled, update `data/hermes/config.yaml`.

```yaml
model:
  default: "MyModel-Q4_K_M.gguf"
  context_length: 65536
```

For Lemonade/AMD backends, use:

```yaml
model:
  default: "extra.MyModel-Q4_K_M.gguf"
  context_length: 65536
```

Also keep `auxiliary.compression.context_length` at the same value and use
`compression.threshold: 0.50`; older absolute-token thresholds can leave Hermes
waiting too long to compact.

5. Restart the affected services.

```bash
dream restart llama-server
dream restart litellm
docker restart dream-hermes 2>/dev/null || true
```

If your install uses direct Docker Compose commands instead of the `dream` CLI,
recreate `llama-server` so it rereads `.env`.

## Verify a Switch

Use these checks after Dashboard or manual model changes:

```bash
dream model current
curl http://localhost:11434/v1/models
```

For LiteLLM installs that require an API key, use the key from `.env`:

```bash
LITELLM_KEY=$(grep '^LITELLM_KEY=' .env | cut -d= -f2-)
curl -H "Authorization: Bearer $LITELLM_KEY" http://localhost:4000/v1/models
```

From inside a Docker container, the inference endpoint is:

```text
http://llama-server:8080/v1
```

## Troubleshooting

### The download finished, but the model is not visible

Check the file is present and non-empty:

```bash
ls -lh data/models/*.gguf
```

If it is a catalog model, confirm the filename exactly matches
`config/model-library.json`. The Dashboard only marks catalog models as
downloaded when the on-disk filename matches the catalog entry.

### The model file exists, but loading fails

Check service logs:

```bash
dream logs llm
```

Common causes:

- The model needs more VRAM or unified memory than the machine has.
- Context length is too high; lower `CTX_SIZE` / `MAX_CONTEXT`.
- The GGUF is not compatible with the active backend.
- On AMD/Lemonade, a service is still asking for the raw filename instead of
  `extra.<GGUF_FILE>`.

### Open WebUI or another app still shows the old model

Verify the server first:

```bash
curl http://localhost:11434/v1/models
```

If the server is correct, refresh the app. If the server is wrong, restart
`llama-server` and verify `.env` / `models.ini`.

### Hermes still asks for the old model

Hermes has its own config:

```bash
grep -n "default:\|context_length:" data/hermes/config.yaml
docker restart dream-hermes
```

For AMD/Lemonade, use `extra.<GGUF_FILE>`.

## Current Limitations

- Dashboard model download and load are catalog-based.
- Custom GGUF import from a local file or arbitrary URL is not yet a first-class
  Dashboard workflow.
- `dream model swap` switches Dream Server tiers, not arbitrary GGUF files.
- `scripts/upgrade-model.sh` is a legacy helper for model-directory layouts and
  should not be used as the primary GGUF switch path on current installs.
