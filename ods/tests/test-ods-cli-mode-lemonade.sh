#!/usr/bin/env bash
# Behavioural coverage for `ods mode` on Lemonade installs.
#
# An AMD/Linux install persists ODS_MODE=lemonade and routes every consumer
# through LiteLLM because the Lemonade container serves the OpenAI API at
# /api/v1, not /v1. `ods mode local` on such an install must restore the
# installer's Lemonade route — writing ODS_MODE=local plus
# http://llama-server:8080/v1 endpoints makes LiteLLM mount local.yaml
# (api_base .../v1) and 404s every local request.
#
# Run: bash tests/test-ods-cli-mode-lemonade.sh

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

extract_function() {
    local name="$1"
    awk -v signature="${name}()" '
        $0 == signature " {" { in_fn=1 }
        in_fn { print }
        in_fn && $0 == "}" { exit }
    ' "$ROOT_DIR/ods-cli"
}

eval "$(extract_function _env_set)"
eval "$(extract_function cmd_mode)"

# ── Harness ───────────────────────────────────────────────────────────────
# shellcheck disable=SC2034 # consumed by the eval'd cmd_mode
RED='' GREEN='' YELLOW='' BLUE='' CYAN='' NC=''
success() { echo "OK: $1"; }
warn() { echo "WARN: $1" >&2; }
error() { echo "ERROR: $1" >&2; exit 1; }
check_install() { :; }
load_env() { :; }
_regenerate_compose_flags() { :; }

PASS=0
FAIL=0
pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; echo "       $2"; FAIL=$((FAIL + 1)); }
check_eq() {
    local label="$1" expected="$2" actual="$3"
    if [[ "$expected" == "$actual" ]]; then
        pass "$label"
    else
        fail "$label" "expected [$expected] got [$actual]"
    fi
}

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

new_install() {
    local dir
    dir="$(mktemp -d "$WORKDIR/install.XXXXXX")"
    : > "$dir/docker-compose.base.yml"
    printf '%s' "$dir"
}

env_get() { grep -m1 "^$1=" "$INSTALL_DIR/.env" | cut -d= -f2-; }

# ── 1. Lemonade install + switchboard: `mode local` keeps the gateway ─────

INSTALL_DIR="$(new_install)"
cat > "$INSTALL_DIR/.env" <<'EOF'
ODS_MODE=cloud
LLM_BACKEND=lemonade
ODS_MODEL_SWITCHBOARD=enabled
LITELLM_KEY=sk-test-litellm
EOF
cmd_mode local > /dev/null
check_eq "lemonade+switchboard: ODS_MODE becomes lemonade" "lemonade" "$(env_get ODS_MODE)"
check_eq "lemonade+switchboard: LLM_API_URL keeps LiteLLM" "http://litellm:4000" "$(env_get LLM_API_URL)"
check_eq "lemonade+switchboard: Hermes goes through model-router" "http://model-router:9099/v1" "$(env_get HERMES_LLM_BASE_URL)"
check_eq "lemonade+switchboard: Hermes key is the router key" "no-key" "$(env_get HERMES_LLM_API_KEY)"

# ── 2. Lemonade install without switchboard ───────────────────────────────

INSTALL_DIR="$(new_install)"
cat > "$INSTALL_DIR/.env" <<'EOF'
ODS_MODE=cloud
LLM_BACKEND=lemonade
LITELLM_KEY=sk-test-litellm
EOF
LITELLM_KEY=sk-test-litellm cmd_mode local > /dev/null
check_eq "lemonade: ODS_MODE becomes lemonade" "lemonade" "$(env_get ODS_MODE)"
check_eq "lemonade: LLM_API_URL keeps LiteLLM" "http://litellm:4000" "$(env_get LLM_API_URL)"
check_eq "lemonade: Hermes base is the LiteLLM /v1 gateway" "http://litellm:4000/v1" "$(env_get HERMES_LLM_BASE_URL)"
check_eq "lemonade: Hermes key is LITELLM_KEY" "sk-test-litellm" "$(env_get HERMES_LLM_API_KEY)"

# ── 3. llama.cpp install keeps the direct route ───────────────────────────

INSTALL_DIR="$(new_install)"
cat > "$INSTALL_DIR/.env" <<'EOF'
ODS_MODE=cloud
LLM_BACKEND=llama-server
EOF
cmd_mode local > /dev/null
check_eq "llama.cpp: ODS_MODE becomes local" "local" "$(env_get ODS_MODE)"
check_eq "llama.cpp: LLM_API_URL is the runtime" "http://llama-server:8080" "$(env_get LLM_API_URL)"
check_eq "llama.cpp: Hermes base is the runtime /v1" "http://llama-server:8080/v1" "$(env_get HERMES_LLM_BASE_URL)"
check_eq "llama.cpp: Hermes key is the local key" "sk-ods-hermes-local" "$(env_get HERMES_LLM_API_KEY)"

# ── 4. Status on a lemonade install stays readable ────────────────────────

INSTALL_DIR="$(new_install)"
cat > "$INSTALL_DIR/.env" <<'EOF'
ODS_MODE=lemonade
LLM_BACKEND=lemonade
LLM_API_URL=http://litellm:4000
EOF
OUT="$(cmd_mode)"
case "$OUT" in
    *"Current mode: lemonade"*) pass "status reports the lemonade mode" ;;
    *) fail "status reports the lemonade mode" "output was: $OUT" ;;
esac

# ── Summary ───────────────────────────────────────────────────────────────

echo ""
echo "Passed: $PASS  Failed: $FAIL"
[[ "$FAIL" -eq 0 ]] || exit 1
echo "[PASS] ods mode honors the Lemonade runtime"
