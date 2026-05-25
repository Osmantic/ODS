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

echo "[PASS] linux cloud mode contracts"
