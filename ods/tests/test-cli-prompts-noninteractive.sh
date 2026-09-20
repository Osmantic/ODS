#!/usr/bin/env bash
# ods-cli confirmation prompts must take their documented default when stdin
# is not a terminal (CI, cron, the dashboard, `ods ... </dev/null`).
#
# Every prompt is `read -p ... -n 1 -r` under `set -euo pipefail`. On EOF,
# read returns 1 and the command died with exit 1 and no output -- before the
# "Cancelled." branch right below it could run. `ods preset load x` printed
# the preset details and then vanished; `ods rollback` and the update
# compatibility prompts behaved the same way.
#
# Run from repo root:  bash ods/tests/test-cli-prompts-noninteractive.sh
# Or from ods:         bash tests/test-cli-prompts-noninteractive.sh

set -euo pipefail

if [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
    for _modern_bash in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        # The probe must expand in the candidate shell, not this one.
        # shellcheck disable=SC2016
        if [ -x "$_modern_bash" ] && [ "$("$_modern_bash" -c 'echo "${BASH_VERSINFO[0]}"')" -ge 4 ]; then
            exec "$_modern_bash" "$0" "$@"
        fi
    done
    echo "[SKIP] ods-cli requires Bash 4+; this host only has Bash ${BASH_VERSION} (brew install bash)"
    echo "Result: 0 passed, 0 failed, 1 skipped"
    exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ODS_CLI="$ROOT_DIR/ods-cli"
TMP_DIR="$(mktemp -d)"
INSTALL_DIR="$TMP_DIR/install"
trap 'rm -rf "$TMP_DIR"' EXIT

PASS=0
FAIL=0
pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }

run_ods() {
    # Through "$BASH", stdin closed: this is the non-interactive case.
    local output rc
    set +e
    output=$(ODS_HOME="$INSTALL_DIR" NO_COLOR=1 "$BASH" "$ODS_CLI" "$@" </dev/null 2>&1)
    rc=$?
    set -e
    printf '%s\n' "$rc"
    printf '%s\n' "$output"
}

# Minimal install: compose base file for check_install, the real extension
# manifests for sr_load, one saved preset.
mkdir -p "$INSTALL_DIR/presets/keepme" "$INSTALL_DIR/extensions"
cp "$ROOT_DIR/docker-compose.base.yml" "$INSTALL_DIR/docker-compose.base.yml"
ln -s "$ROOT_DIR/extensions/services" "$INSTALL_DIR/extensions/services"
printf 'ODS_MODE=local\nLLM_MODEL=current-model\n' > "$INSTALL_DIR/.env"
printf 'ODS_MODE=local\nLLM_MODEL=preset-model\n' > "$INSTALL_DIR/presets/keepme/env"
printf 'name=keepme\ncreated=2026-09-08T00:00:00Z\n' > "$INSTALL_DIR/presets/keepme/meta.txt"
: > "$INSTALL_DIR/presets/keepme/extensions.list"

echo "Test 1: 'ods preset delete' with stdin closed declines with a message and keeps the preset"
result="$(run_ods preset delete keepme)"
rc="${result%%$'\n'*}"
output="${result#*$'\n'}"
if [[ "$rc" -eq 0 && "$output" == *"Cancelled."* && -d "$INSTALL_DIR/presets/keepme" ]]; then
    pass "preset delete: exit 0, 'Cancelled.', preset kept"
else
    fail "preset delete: rc=$rc, preset exists=$([[ -d "$INSTALL_DIR/presets/keepme" ]] && echo yes || echo no), output: $(printf '%s' "$output" | tail -n 2 | tr '\n' '|')"
fi

echo "Test 2: 'ods preset load' with stdin closed declines with a message and leaves .env alone"
result="$(run_ods preset load keepme)"
rc="${result%%$'\n'*}"
output="${result#*$'\n'}"
if [[ "$rc" -eq 0 && "$output" == *"Cancelled."* ]] && grep -q '^LLM_MODEL=current-model$' "$INSTALL_DIR/.env"; then
    pass "preset load: exit 0, 'Cancelled.', .env untouched"
else
    fail "preset load: rc=$rc, LLM_MODEL=$(grep '^LLM_MODEL=' "$INSTALL_DIR/.env"), output: $(printf '%s' "$output" | tail -n 2 | tr '\n' '|')"
fi

echo "Test 3: every confirmation prompt in ods-cli tolerates EOF"
# A `read` that returns 1 on EOF aborts the command under set -e before the
# decline branch runs; each prompt must fall back to an empty reply.
bare="$(grep -nE 'read -p ' "$ODS_CLI" | grep -vE '\|\| (REPLY|_confirm)=""' || true)"
if [[ -z "$bare" ]]; then
    pass "all $(grep -cE 'read -p ' "$ODS_CLI") prompts fall back to an empty reply on EOF"
else
    fail "prompts without an EOF fallback:"$'\n'"$bare"
fi

echo ""
echo "Result: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
