#!/usr/bin/env bash
# Behavioural coverage for scripts/mode-switch.sh.
#
# scripts/README.md and docs/MODE-SWITCH.md both point operators at this
# script, but nothing exercised it. `--status` in particular is the read-only
# entry point people reach for first, and it has to survive a .env that is
# missing or does not pin ODS_MODE.
#
# Run: bash tests/test-mode-switch-status.sh

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE_SWITCH="$ROOT_DIR/scripts/mode-switch.sh"

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

# mode-switch.sh resolves .env as <script dir>/../.env, so give it a fixture
# install root to operate on.
new_root() {
    local root
    root="$(mktemp -d "$WORKDIR/root.XXXXXX")"
    mkdir -p "$root/scripts"
    cp "$MODE_SWITCH" "$root/scripts/"
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

current_mode_line() {
    grep -m1 "^Current mode:" <<< "$1" || true
}

# ── 1. No .env at all ─────────────────────────────────────────────────────
#
# The regression: `current=$(grep ... | cut ...)` under `set -euo pipefail`
# aborted the script when grep matched nothing, so --status printed nothing
# and exited non-zero. The `${current:-local}` default was unreachable.

ROOT="$(new_root)"
OUT="$(run_mode "$ROOT" --status)"
check_eq "no .env: exits 0" "0" "$(run_rc "$ROOT" --status)"
check_eq "no .env: falls back to the default mode" "Current mode: local" "$(current_mode_line "$OUT")"
check_contains "no .env: still lists the available modes" "Available modes:" "$OUT"
check_contains "no .env: says why the value is a default" ".env not found" "$OUT"

# ── 2. .env present but ODS_MODE not pinned ───────────────────────────────

ROOT="$(new_root)"
printf 'LLM_API_URL=http://llama-server:8080\n' > "$ROOT/.env"
OUT="$(run_mode "$ROOT" --status)"
check_eq "no ODS_MODE key: exits 0" "0" "$(run_rc "$ROOT" --status)"
check_eq "no ODS_MODE key: falls back to the default mode" "Current mode: local" "$(current_mode_line "$OUT")"

# ── 3. ODS_MODE pinned ────────────────────────────────────────────────────

ROOT="$(new_root)"
printf 'ODS_MODE=cloud\n' > "$ROOT/.env"
check_eq "pinned mode is reported" "Current mode: cloud" "$(current_mode_line "$(run_mode "$ROOT" --status)")"

ROOT="$(new_root)"
printf 'ODS_MODE="hybrid"\n' > "$ROOT/.env"
check_eq "quoted mode is reported unquoted" "Current mode: hybrid" "$(current_mode_line "$(run_mode "$ROOT" --status)")"

ROOT="$(new_root)"
printf 'ODS_MODE=cloud\r\n' > "$ROOT/.env"
check_eq "CRLF .env does not leak a carriage return" "Current mode: cloud" "$(current_mode_line "$(run_mode "$ROOT" --status)")"

# ── 4. Bare invocation defaults to status ─────────────────────────────────

ROOT="$(new_root)"
printf 'ODS_MODE=cloud\n' > "$ROOT/.env"
check_eq "no argument defaults to status" "Current mode: cloud" "$(current_mode_line "$(run_mode "$ROOT")")"

# ── 5. Switching still works ──────────────────────────────────────────────

ROOT="$(new_root)"
printf 'ODS_MODE=local\nLLM_API_URL=http://llama-server:8080\n' > "$ROOT/.env"
run_mode "$ROOT" cloud > /dev/null
check_eq "switch updates ODS_MODE" "ODS_MODE=cloud" "$(grep -m1 '^ODS_MODE=' "$ROOT/.env")"
check_eq "switch repoints LLM_API_URL" "LLM_API_URL=http://litellm:4000" "$(grep -m1 '^LLM_API_URL=' "$ROOT/.env")"
check_eq "switched mode is reported back" "Current mode: cloud" "$(current_mode_line "$(run_mode "$ROOT" --status)")"

ROOT="$(new_root)"
printf 'ODS_MODE=cloud\n' > "$ROOT/.env"
run_mode "$ROOT" local > /dev/null
check_eq "switching back to local repoints LLM_API_URL" "LLM_API_URL=http://llama-server:8080" "$(grep -m1 '^LLM_API_URL=' "$ROOT/.env")"

# ── 6. External topology cannot be partially overwritten ─────────────────

ROOT="$(new_root)"
cat > "$ROOT/.env" <<'EOF'
ODS_MODE=local
LLM_BACKEND=external
LLM_API_URL=http://host.docker.internal:11434
EXTERNAL_LLM_URL=http://127.0.0.1:11434
EOF
BEFORE="$(cat "$ROOT/.env")"
OUT="$(run_mode "$ROOT" cloud)"
check_eq "external backend rejects direct mode switch" "1" "$(run_rc "$ROOT" cloud)"
check_contains "external backend points to the transactional reset" "--no-external-llm" "$OUT"
check_eq "rejected external mode switch leaves .env unchanged" "$BEFORE" "$(cat "$ROOT/.env")"

# ── 7. Bad input is rejected ──────────────────────────────────────────────

ROOT="$(new_root)"
printf 'ODS_MODE=local\n' > "$ROOT/.env"
check_eq "unknown mode exits 1" "1" "$(run_rc "$ROOT" banana)"
check_contains "unknown mode explains itself" "Unknown mode" "$(run_mode "$ROOT" banana)"

ROOT="$(new_root)"
check_eq "switching without .env exits 1" "1" "$(run_rc "$ROOT" cloud)"

# 8. Dry run previews every mutation without changing files.
ROOT="$(new_root)"
mkdir -p "$ROOT/extensions/services/litellm"
printf 'disabled compose\n' > "$ROOT/extensions/services/litellm/compose.yaml.disabled"
printf 'ODS_MODE=local\nLLM_API_URL=http://llama-server:8080\n' > "$ROOT/.env"
BEFORE="$(cat "$ROOT/.env")"
OUT="$(run_mode "$ROOT" cloud --dry-run)"
check_eq "dry run exits 0" "0" "$(run_rc "$ROOT" cloud --dry-run)"
check_contains "dry run previews mode" "Would set ODS_MODE=cloud" "$OUT"
check_contains "dry run previews routing URL" "Would set LLM_API_URL=http://litellm:4000" "$OUT"
check_contains "dry run previews extension enable" "Would enable litellm:" "$OUT"
check_eq "dry run leaves .env unchanged" "$BEFORE" "$(cat "$ROOT/.env")"
if [[ -f "$ROOT/extensions/services/litellm/compose.yaml.disabled" && ! -e "$ROOT/extensions/services/litellm/compose.yaml" ]]; then
    pass "dry run leaves litellm compose disabled"
else
    fail "dry run leaves litellm compose disabled" "compose file was moved"
fi
check_eq "unknown second argument exits 1" "1" "$(run_rc "$ROOT" cloud --preview)"

# ── Summary ───────────────────────────────────────────────────────────────

echo ""
echo "Passed: $PASS  Failed: $FAIL"
[[ "$FAIL" -eq 0 ]] || exit 1
echo "[PASS] mode-switch status and switching"
