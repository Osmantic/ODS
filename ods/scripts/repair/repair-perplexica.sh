#!/bin/bash
set -euo pipefail
# repair-perplexica.sh — Re-seed Perplexica config via HTTP API
# Called by: ods repair perplexica
# Requires: Perplexica container running, python3 available

PERPLEXICA_URL="${1:-http://127.0.0.1:3004}"
LLM_MODEL="${2:-qwen3-30b-a3b}"
PERPLEXICA_MODEL="${PERPLEXICA_MODEL:-}"
PERPLEXICA_LLM_BASE_URL="${PERPLEXICA_LLM_BASE_URL:-${LLM_API_URL:-http://llama-server:8080}}"
PERPLEXICA_API_KEY="${PERPLEXICA_API_KEY:-${LITELLM_KEY:-${OPENAI_API_KEY:-no-key}}}"
_perplexica_switchboard_mode="$(printf '%s' "${ODS_MODEL_SWITCHBOARD:-observe}" | tr '[:upper:]' '[:lower:]')"
if [[ "$_perplexica_switchboard_mode" == "enabled" ]]; then
    : "${PERPLEXICA_MODEL:=ods/current}"
    PERPLEXICA_LLM_BASE_URL="http://litellm:4000/v1"
    PERPLEXICA_API_KEY="${LITELLM_KEY:-${OPENAI_API_KEY:-no-key}}"
fi
case "$PERPLEXICA_LLM_BASE_URL" in
    */v1|*/api/v1) ;;
    *) PERPLEXICA_LLM_BASE_URL="${PERPLEXICA_LLM_BASE_URL%/}/v1" ;;
esac

if [[ -z "$PERPLEXICA_MODEL" ]]; then
    if [[ -n "${GGUF_FILE:-}" ]]; then
        PERPLEXICA_MODEL="$GGUF_FILE"
        _perplexica_backend="$(printf '%s' "${LLM_BACKEND:-${AMD_INFERENCE_RUNTIME:-}}" | tr '[:upper:]' '[:lower:]')"
        if [[ "$_perplexica_backend" == "lemonade" ]]; then
            PERPLEXICA_MODEL="extra.$GGUF_FILE"
        fi
    else
        PERPLEXICA_MODEL="$LLM_MODEL"
    fi
fi

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_CMD="python3"
if [[ -f "$SCRIPT_DIR/lib/python-cmd.sh" ]]; then
    . "$SCRIPT_DIR/lib/python-cmd.sh"
    PYTHON_CMD="$(ods_detect_python_cmd)"
elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
fi

# Wait for Perplexica to be ready (up to 60s)
for i in $(seq 1 12); do
    if curl -sf --max-time 5 "${PERPLEXICA_URL}/api/config" >/dev/null 2>&1; then
        break
    fi
    [[ $i -lt 12 ]] && sleep 5
done

# Seed config via API — export vars so Python reads from env (no shell interpolation)
export PERPLEXICA_URL PERPLEXICA_MODEL PERPLEXICA_LLM_BASE_URL PERPLEXICA_API_KEY

curl -sf --max-time 10 "${PERPLEXICA_URL}/api/config" | \
"$PYTHON_CMD" -c '
import sys, os, json, urllib.request

config = json.load(sys.stdin)["values"]
providers = config.get("modelProviders", [])
openai_prov = next((p for p in providers if p["type"] == "openai"), None)
transformers_prov = next((p for p in providers if p["type"] == "transformers"), None)

if not openai_prov:
    print("error: no openai provider found")
    sys.exit(1)

url = os.environ["PERPLEXICA_URL"] + "/api/config"
model = os.environ["PERPLEXICA_MODEL"]
base_url = os.environ["PERPLEXICA_LLM_BASE_URL"]
api_key = os.environ["PERPLEXICA_API_KEY"] or "no-key"

def post(key, value):
    data = json.dumps({"key": key, "value": value}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10)

def post_setup_complete():
    setup_url = os.environ["PERPLEXICA_URL"] + "/api/config/setup-complete"
    req = urllib.request.Request(setup_url, data=b"{}", headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        post("setupComplete", True)

# Set connection details
openai_prov["config"] = {
    **(openai_prov.get("config") or {}),
    "apiKey": api_key,
    "baseURL": base_url,
}
openai_prov["chatModels"] = [{"key": model, "name": model}]
post("modelProviders", providers)

# Set default providers and models
post("preferences", {
    "defaultChatProvider": openai_prov["id"],
    "defaultChatModel": model,
    "defaultEmbeddingProvider": transformers_prov["id"] if transformers_prov else openai_prov["id"],
    "defaultEmbeddingModel": "Xenova/all-MiniLM-L6-v2"
})

# Mark setup complete to bypass the wizard
post_setup_complete()
print("ok")
'
