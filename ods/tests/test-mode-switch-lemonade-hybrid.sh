#!/usr/bin/env bash
# Behavioural coverage for `mode hybrid` on Lemonade installs.
#
# hybrid.yaml routes `local`/`default`/`ods/current` to
# http://llama-server:8080/v1 — a path the Lemonade-backed container does
# not serve (it exposes /api/v1, and needs extra.<gguf> model ids). When the
# model switchboard is disabled, `mode hybrid` on a Lemonade install mounts
# that config and every local request 404s. The switch must refuse loudly
# instead of writing a route it cannot honor. With the switchboard enabled,
# hybrid traffic goes through model-router and stays supported.
#
# Run: bash tests/test-mode-switch-lemonade-hybrid.sh

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE_SWITCH="$ROOT_DIR/scripts/mode-switch.sh"

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
success() { :; }
warn() { :; }
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
check_contains() {
    local label="$1" needle="$2" haystack="$3"
    if [[ "$haystack" == *"$needle"* ]]; then
        pass "$label"
    else
        fail "$label" "missing [$needle] in: $haystack"
    fi
}

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

new_root() {
    local root
    root="$(mktemp -d "$WORKDIR/root.XXXXXX")"
    mkdir -p "$root/scripts"
    cp "$MODE_SWITCH" "$root/scripts/"
    : > "$root/docker-compose.base.yml"
    printf '%s' "$root"
}

run_mode() {
    local root="$1"; shift
    ( cd "$root/scripts" && bash ./mode-switch.sh "$@" ) 2>&1
}
run_rc() {
    local root="$1"; shift
    ( cd "$root/scripts" && bash ./mode-switch.sh "$@" ) >/dev/null 2>&1
    echo $?
}
run_cli() {
    local root="$1"; shift
    ( INSTALL_DIR="$root" cmd_mode "$@" ) 2>&1
}
run_cli_rc() {
    local root="$1"; shift
    ( INSTALL_DIR="$root" cmd_mode "$@" ) >/dev/null 2>&1
    echo $?
}

# ── 1. Lemonade + switchboard disabled: hybrid is refused ────────────────

ROOT="$(new_root)"
cat > "$ROOT/.env" <<'EOF'
ODS_MODE=lemonade
LLM_BACKEND=lemonade
ODS_MODEL_SWITCHBOARD=disabled
LITELLM_KEY=sk-test
EOF
BEFORE="$(cat "$ROOT/.env")"
OUT="$(run_mode "$ROOT" hybrid)"
check_eq "script: lemonade+disabled hybrid exits 1" "1" "$(run_rc "$ROOT" hybrid)"
check_contains "script: refusal explains the switchboard requirement" "switchboard" "$OUT"
check_eq "script: refused hybrid leaves .env unchanged" "$BEFORE" "$(cat "$ROOT/.env")"

OUT="$(run_cli "$ROOT" hybrid)"
check_eq "cli: lemonade+disabled hybrid exits 1" "1" "$(run_cli_rc "$ROOT" hybrid)"
check_contains "cli: refusal explains the switchboard requirement" "switchboard" "$OUT"
check_eq "cli: refused hybrid leaves .env unchanged" "$BEFORE" "$(cat "$ROOT/.env")"

# ── 2. Lemonade + switchboard enabled: hybrid stays supported ─────────────

ROOT="$(new_root)"
cat > "$ROOT/.env" <<'EOF'
ODS_MODE=lemonade
LLM_BACKEND=lemonade
ODS_MODEL_SWITCHBOARD=enabled
LITELLM_KEY=sk-test
EOF
check_eq "script: lemonade+enabled hybrid is allowed" "0" "$(run_rc "$ROOT" hybrid)"
check_eq "script: hybrid writes ODS_MODE=hybrid" "ODS_MODE=hybrid" "$(grep -m1 '^ODS_MODE=' "$ROOT/.env")"

ROOT="$(new_root)"
cat > "$ROOT/.env" <<'EOF'
ODS_MODE=lemonade
LLM_BACKEND=lemonade
ODS_MODEL_SWITCHBOARD=enabled
LITELLM_KEY=sk-test
EOF
check_eq "cli: lemonade+enabled hybrid is allowed" "0" "$(run_cli_rc "$ROOT" hybrid)"
check_eq "cli: hybrid writes ODS_MODE=hybrid" "ODS_MODE=hybrid" "$(grep -m1 '^ODS_MODE=' "$ROOT/.env")"

# ── 3. llama.cpp + switchboard disabled: hybrid unaffected ────────────────

ROOT="$(new_root)"
cat > "$ROOT/.env" <<'EOF'
ODS_MODE=local
LLM_BACKEND=llama-server
ODS_MODEL_SWITCHBOARD=disabled
EOF
check_eq "script: llama.cpp+disabled hybrid still works" "0" "$(run_rc "$ROOT" hybrid)"
check_eq "script: llama.cpp hybrid writes ODS_MODE=hybrid" "ODS_MODE=hybrid" "$(grep -m1 '^ODS_MODE=' "$ROOT/.env")"

# ── Summary ───────────────────────────────────────────────────────────────

echo ""
echo "Passed: $PASS  Failed: $FAIL"
[[ "$FAIL" -eq 0 ]] || exit 1
echo "[PASS] mode hybrid refuses the unservable Lemonade route"
