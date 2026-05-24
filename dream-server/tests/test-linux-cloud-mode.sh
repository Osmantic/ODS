#!/usr/bin/env bash
# Regression checks for Linux cloud mode. Cloud/external LLM installs must not
# require a local llama-server container or local-mode dependency overlays.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

pass() { printf '[PASS] %s\n' "$1"; }
fail() { printf '[FAIL] %s\n' "$1" >&2; exit 1; }

contains() {
    local haystack="$1" needle="$2" label="$3"
    [[ "$haystack" == *"$needle"* ]] && pass "$label" || fail "$label"
}

rejects() {
    local haystack="$1" needle="$2" label="$3"
    [[ "$haystack" != *"$needle"* ]] && pass "$label" || fail "$label"
}

PY="${DREAM_PYTHON_CMD:-}"
if [[ -z "$PY" ]]; then
    if command -v python3 >/dev/null 2>&1; then
        PY=python3
    elif command -v python >/dev/null 2>&1; then
        PY=python
    else
        fail "python is required"
    fi
fi

flags="$(DREAM_PYTHON_CMD="$PY" ./scripts/resolve-compose-stack.sh \
    --script-dir "$ROOT_DIR" \
    --tier CLOUD \
    --gpu-backend cpu \
    --gpu-count 0 \
    --dream-mode cloud)"
flags="${flags//\\//}"

contains "$flags" "docker-compose.base.yml" "cloud mode keeps base stack"
contains "$flags" "docker-compose.cloud.yml" "cloud mode layers cloud overlay"
contains "$flags" "extensions/services/litellm/compose.yaml" "cloud mode includes LiteLLM gateway"
rejects "$flags" "docker-compose.cpu.yml" "cloud mode does not include CPU llama-server overlay"
rejects "$flags" "compose.local.yaml" "cloud mode does not include local dependency overlays"

lemonade_flags="$(LEMONADE_EXTERNAL=true AMD_INFERENCE_RUNTIME=lemonade AMD_INFERENCE_MANAGED=false DREAM_PYTHON_CMD="$PY" ./scripts/resolve-compose-stack.sh \
    --script-dir "$ROOT_DIR" \
    --tier CLOUD \
    --gpu-backend cpu \
    --gpu-count 0 \
    --dream-mode lemonade)"
lemonade_flags="${lemonade_flags//\\//}"

contains "$lemonade_flags" "docker-compose.base.yml" "external Lemonade keeps base stack"
contains "$lemonade_flags" "docker-compose.cloud.yml" "external Lemonade profiles managed llama-server out"
contains "$lemonade_flags" "docker-compose.lemonade-external.yml" "external Lemonade layers dedicated overlay"
rejects "$lemonade_flags" "docker-compose.cpu.yml" "external Lemonade does not include CPU llama-server overlay"

if grep -q 'profiles:' docker-compose.cloud.yml && grep -q 'local-inference' docker-compose.cloud.yml; then
    pass "cloud overlay profiles local llama-server out of default startup"
else
    fail "cloud overlay must profile local llama-server out of default startup"
fi

if grep -Fq -- '--dream-mode "${DREAM_MODE:-local}"' installers/lib/compose-select.sh \
    && grep -Fq -- '--dream-mode "${DREAM_MODE:-local}"' installers/phases/03-features.sh \
    && grep -Fq -- '--dream-mode "${DREAM_MODE:-local}"' installers/phases/11-services.sh \
    && grep -Fq -- '--dream-mode "${DREAM_MODE:-local}"' dream-cli; then
    pass "installer and CLI pass dream mode to compose resolver"
else
    fail "all installer/CLI resolver calls must pass --dream-mode"
fi

if grep -q 'DREAM_MODE:-local.*cloud' installers/phases/12-health.sh \
    && grep -Fq 'LiteLLM' installers/phases/12-health.sh \
    && grep -Fq 'skipping local llama-server pre-warm' installers/phases/12-health.sh; then
    pass "cloud health path skips local llama-server"
else
    fail "cloud health path must skip local llama-server"
fi

if grep -Fq 'LLM_SERVICE_ID="litellm"' scripts/dream-preflight.sh \
    && grep -Fq 'LLM_CONTAINER="${SERVICE_CONTAINERS[litellm]:-dream-litellm}"' scripts/dream-preflight.sh \
    && grep -Fq 'skipped (cloud mode)' scripts/dream-preflight.sh \
    && ! grep -Fq 'echo -n "llama-server API' scripts/dream-preflight.sh; then
    pass "companion preflight checks LiteLLM instead of llama-server in cloud"
else
    fail "scripts/dream-preflight.sh must be cloud-aware and avoid local llama-server checks"
fi

"$PY" - "$ROOT_DIR" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
text = (root / "installers/phases/11-services.sh").read_text(encoding="utf-8")
model_config = text.index('mkdir -p "$INSTALL_DIR/config/llama-server"')
hermes_block = text.index('if [[ "${ENABLE_HERMES:-false}" == "true" ]]; then')
soul_block = text.index('_soul_output="$INSTALL_DIR/data/persona/SOUL.md"')
if model_config < hermes_block < soul_block:
    # Make sure the local-model block was closed before Hermes/SOUL rendering begins.
    between = text[model_config:hermes_block]
    if '\n    fi\n' in between:
        print("[PASS] SOUL.md render is outside local-model-only block")
        sys.exit(0)
print("[FAIL] SOUL.md render must run for cloud installs too", file=sys.stderr)
sys.exit(1)
PY

"$PY" - "$ROOT_DIR" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
phase11 = (root / "installers/phases/11-services.sh").read_text(encoding="utf-8")
macos = (root / "installers/macos/install-macos.sh").read_text(encoding="utf-8")
for label, text in {"linux": phase11, "macos": macos}.items():
    if '_hermes_model="${LLM_MODEL:-default}"' in text:
        print(f"[FAIL] {label} cloud Hermes config must not send raw LLM_MODEL to LiteLLM", file=sys.stderr)
        sys.exit(1)
    if '_hermes_model="private-cloud"' not in text or '_hermes_model="default"' not in text:
        print(f"[FAIL] {label} cloud Hermes config must choose explicit LiteLLM route names", file=sys.stderr)
        sys.exit(1)
print("[PASS] cloud Hermes model routes use LiteLLM route names")
PY

"$PY" - "$ROOT_DIR" <<'PY'
from pathlib import Path
import sys
import yaml

root = Path(sys.argv[1])
cfg = yaml.safe_load((root / "config/litellm/cloud.yaml").read_text(encoding="utf-8"))
routes = {item["model_name"]: item["litellm_params"] for item in cfg["model_list"]}
default_model = routes["default"]["model"]
private_cloud = routes["private-cloud"]
local_lan = routes["local-lan"]
if "${CLOUD_LLM_MODEL}" in default_model or routes["default"].get("api_base") == "os.environ/CLOUD_LLM_BASE_URL":
    print("[FAIL] hosted cloud default must not point at private-cloud CLOUD_LLM_*", file=sys.stderr)
    sys.exit(1)
if not default_model.startswith(("anthropic/", "openai/")):
    print("[FAIL] hosted cloud default must stay on a hosted provider route", file=sys.stderr)
    sys.exit(1)
if private_cloud.get("model") != "openai/${CLOUD_LLM_MODEL}" or private_cloud.get("api_base") != "os.environ/CLOUD_LLM_BASE_URL":
    print("[FAIL] private-cloud LM Studio route must use CLOUD_LLM_*", file=sys.stderr)
    sys.exit(1)
if local_lan != private_cloud:
    print("[FAIL] local-lan must remain a back-compat alias for private-cloud", file=sys.stderr)
    sys.exit(1)
print("[PASS] hosted-cloud default and private-cloud route stay separate")
PY

if grep -Fq 'API key [${CLOUD_LLM_API_KEY}]' install-core.sh; then
    fail "interactive cloud API key prompt must not echo the secret default"
fi
if grep -Fq 'read -r -s -p "  Private-cloud API key' install-core.sh; then
    pass "interactive private-cloud API key prompt is silent"
else
    fail "interactive private-cloud API key prompt must be silent"
fi

echo "[PASS] linux cloud mode contracts"
