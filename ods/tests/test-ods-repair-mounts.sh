#!/usr/bin/env bash
# `ods repair mounts`: after a WSL restart Docker Desktop can bring ODS's
# containers back with an empty folder where ODS's data belongs (RTX Spark,
# 2026-10-25). The command hands the recovery helper the stack's own Compose
# files, resolved the way every other ods command resolves them
# (get_compose_flags: the saved .compose-flags, else the resolver with .env
# loaded, never a container's recorded file list), and turns its exit codes
# into one clear next step. The helper is a stub here; nothing runs Docker.

set -euo pipefail

if [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
    for _modern_bash in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        if [ -x "$_modern_bash" ] && [ "$("$_modern_bash" -c 'echo "${BASH_VERSINFO[0]}"')" -ge 4 ]; then
            exec "$_modern_bash" "$0" "$@"
        fi
    done
    echo "[SKIP] ods-cli requires Bash 4+; this host only has Bash ${BASH_VERSION}"
    exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ODS_CLI="$ROOT_DIR/ods-cli"
TMP_DIR="$(mktemp -d)"
INSTALL_DIR="$TMP_DIR/install"
BIN_DIR="$TMP_DIR/bin"
RECORD="$TMP_DIR/recovery-args"
trap 'rm -rf "$TMP_DIR"' EXIT

PASS=0
FAIL=0
pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }

mkdir -p "$BIN_DIR" "$INSTALL_DIR/scripts"
cp "$ROOT_DIR/docker-compose.base.yml" "$INSTALL_DIR/docker-compose.base.yml"
: > "$INSTALL_DIR/docker-compose.gateway-only.yml"
cp "$ROOT_DIR/scripts/wsl-bind-recovery.py" "$INSTALL_DIR/scripts/"
# The resolver adds the no-WebUI overlay only when .env turns Open WebUI off.
cat > "$INSTALL_DIR/scripts/resolve-compose-stack.sh" <<'SH'
#!/usr/bin/env bash
if [[ "${ENABLE_OPEN_WEBUI:-true}" == "false" ]]; then
    echo "-f docker-compose.base.yml -f docker-compose.gateway-only.yml"
else
    echo "-f docker-compose.base.yml"
fi
SH
chmod +x "$INSTALL_DIR/scripts/resolve-compose-stack.sh"

# ods-cli runs every Python through ODS_PYTHON_CMD, so this one stands in for
# the recovery helper only: it records the helper's arguments and exits with
# FAKE_RECOVERY_RC. Everything else (the service registry) gets real Python.
REAL_PYTHON="$(. "$ROOT_DIR/lib/python-cmd.sh"; ods_detect_python_cmd)"
cat > "$BIN_DIR/fake-recovery-python" <<SH
#!/usr/bin/env bash
if [[ "\${1:-}" == */wsl-bind-recovery.py ]]; then
    printf '%s\n' "\$@" > "$RECORD"
    exit "\${FAKE_RECOVERY_RC:-0}"
fi
exec "$REAL_PYTHON" "\$@"
SH
cat > "$BIN_DIR/uname" <<'SH'
#!/usr/bin/env bash
if [[ "${1:-}" == "-r" ]]; then
    printf '%s\n' "${FAKE_KERNEL_RELEASE:-6.6.87.2-microsoft-standard-WSL2}"
    exit 0
fi
command -p uname "$@"
SH
cat > "$BIN_DIR/docker" <<'SH'
#!/usr/bin/env bash
exit 0
SH
chmod +x "$BIN_DIR/fake-recovery-python" "$BIN_DIR/uname" "$BIN_DIR/docker"
export PATH="$BIN_DIR:$PATH"

run_repair() {
    local output rc
    rm -f "$RECORD"
    set +e
    output=$(INSTALL_DIR="$INSTALL_DIR" ODS_HOME="$INSTALL_DIR" NO_COLOR=1 \
        ODS_PYTHON_CMD="$BIN_DIR/fake-recovery-python" "$BASH" "$ODS_CLI" repair mounts "$@" 2>&1)
    rc=$?
    set -e
    REPAIR_RC="$rc"
    REPAIR_OUTPUT="$output"
}

recorded_flags() {
    # Everything after "--": the Compose flags the helper received.
    sed -n '/^--$/,$p' "$RECORD" | sed '1d' | tr '\n' ' ' | sed 's/ $//'
}

printf 'ENABLE_OPEN_WEBUI=true\n' > "$INSTALL_DIR/.env"
printf '%s\n' "-f docker-compose.base.yml -f docker-compose.gateway-only.yml" > "$INSTALL_DIR/.compose-flags"
FAKE_RECOVERY_RC=0 run_repair
if [[ "$REPAIR_RC" == 0 && "$(recorded_flags)" == "-f docker-compose.base.yml -f docker-compose.gateway-only.yml" ]] \
    && grep -qx -- "--install-dir" "$RECORD" && grep -qx -- "$INSTALL_DIR" "$RECORD" \
    && ! grep -qx -- "--check" "$RECORD" && grep -q "see your ODS data" <<<"$REPAIR_OUTPUT"; then
    pass "a saved .compose-flags is what every ods command uses, so the repair uses it"
else
    fail "saved flags: rc=$REPAIR_RC flags='$(recorded_flags 2>/dev/null)' output=$REPAIR_OUTPUT"
fi

rm -f "$INSTALL_DIR/.compose-flags"
FAKE_RECOVERY_RC=0 run_repair
if [[ "$REPAIR_RC" == 0 && "$(recorded_flags)" == "-f docker-compose.base.yml" ]]; then
    pass "without .compose-flags the stack is resolved again with .env loaded (Open WebUI on: no gateway-only overlay)"
else
    fail "re-resolved flags: rc=$REPAIR_RC flags='$(recorded_flags 2>/dev/null)' output=$REPAIR_OUTPUT"
fi

printf 'ENABLE_OPEN_WEBUI=false\n' > "$INSTALL_DIR/.env"
FAKE_RECOVERY_RC=0 run_repair
if [[ "$REPAIR_RC" == 0 && "$(recorded_flags)" == "-f docker-compose.base.yml -f docker-compose.gateway-only.yml" ]]; then
    pass "the .env decides the gateway-only overlay at repair time"
else
    fail "gateway-only flags: rc=$REPAIR_RC flags='$(recorded_flags 2>/dev/null)'"
fi
printf 'ENABLE_OPEN_WEBUI=true\n' > "$INSTALL_DIR/.env"

FAKE_RECOVERY_RC=2 run_repair --check
if [[ "$REPAIR_RC" == 2 ]] && grep -qx -- "--check" "$RECORD" && grep -q "ods repair mounts" <<<"$REPAIR_OUTPUT"; then
    pass "--check only asks, exits 2 for stale views and names the fix"
else
    fail "--check: rc=$REPAIR_RC output=$REPAIR_OUTPUT"
fi

FAKE_RECOVERY_RC=3 run_repair
if [[ "$REPAIR_RC" == 3 ]] && grep -q "Restart Docker Desktop" <<<"$REPAIR_OUTPUT" \
    && grep -q "ods repair mounts" <<<"$REPAIR_OUTPUT" && grep -q "recreated nothing" <<<"$REPAIR_OUTPUT" \
    && grep -q "discord.gg/4ntNp9MAwC" <<<"$REPAIR_OUTPUT"; then
    pass "when a new container cannot see the data either, the one next step is restarting Docker Desktop"
else
    fail "not proven: rc=$REPAIR_RC output=$REPAIR_OUTPUT"
fi

FAKE_RECOVERY_RC=4 run_repair
if [[ "$REPAIR_RC" == 4 ]] && grep -q "Another ODS mount repair is running" <<<"$REPAIR_OUTPUT"; then
    pass "a repair that finds another one running says so"
else
    fail "busy: rc=$REPAIR_RC output=$REPAIR_OUTPUT"
fi

FAKE_RECOVERY_RC=1 run_repair
if [[ "$REPAIR_RC" == 1 ]] && grep -q "discord.gg/4ntNp9MAwC" <<<"$REPAIR_OUTPUT"; then
    pass "a failed repair points to help"
else
    fail "failure: rc=$REPAIR_RC output=$REPAIR_OUTPUT"
fi

FAKE_KERNEL_RELEASE=6.8.0-45-generic FAKE_RECOVERY_RC=1 run_repair
if [[ "$REPAIR_RC" == 0 && ! -e "$RECORD" ]] && grep -q "nothing to do" <<<"$REPAIR_OUTPUT"; then
    pass "outside WSL the command does nothing"
else
    fail "non-WSL: rc=$REPAIR_RC output=$REPAIR_OUTPUT"
fi

run_repair --force
if [[ "$REPAIR_RC" != 0 && ! -e "$RECORD" ]] && grep -q "Unknown option" <<<"$REPAIR_OUTPUT"; then
    pass "unknown options are refused before anything runs"
else
    fail "unknown option: rc=$REPAIR_RC output=$REPAIR_OUTPUT"
fi

# ods start runs the same helper; a refused proof cancels the start with the same next step.
start_body="$(sed -n '/^cmd_start() {/,/^}/p' "$ODS_CLI")"
if grep -q '_ods_cli_mounts_need_docker_restart' <<<"$start_body" && grep -q 'wsl-bind-recovery.py' <<<"$start_body"; then
    pass "ods start gives the same Docker Desktop step when the proof fails"
else
    fail "ods start does not map the helper's exit 3"
fi

echo "Result: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
