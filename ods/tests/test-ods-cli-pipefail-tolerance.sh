#!/usr/bin/env bash
# Regression coverage for ods-cli paths made stricter by shell strict mode.

set -euo pipefail

# ods-cli requires Bash 4+; macOS ships Bash 3.2, so running this suite
# there makes every case fail with a misleading exit 1 from the CLI's own
# version guard. Re-exec under Homebrew bash when available (mirrors
# scripts/health-check.sh); otherwise skip — without Bash 4+ there is no
# meaningful ods-cli behavior to test on this host.
if [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
    for _modern_bash in /opt/homebrew/bin/bash /usr/local/bin/bash; do
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
BIN_DIR="$TMP_DIR/bin"

PASS=0
FAIL=0
SKIP=0

cleanup() {
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT

pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }
skip() { echo "[SKIP] $1"; SKIP=$((SKIP + 1)); }

run_ods() {
    # Invoke through "$BASH" (the interpreter running this test) instead of
    # the #!/usr/bin/env bash shebang: on macOS, env can resolve to the
    # system Bash 3.2 even when this suite runs under a modern bash. Same
    # pattern as test-validate-env.sh and test-ods-config-secret-mask.sh.
    local output rc
    set +e
    output=$(ODS_HOME="$INSTALL_DIR" NO_COLOR=1 "$BASH" "$ODS_CLI" "$@" 2>&1)
    rc=$?
    set -e
    printf '%s\n' "$rc"
    printf '%s\n' "$output"
}

reset_install() {
    rm -rf "$INSTALL_DIR"
    mkdir -p "$INSTALL_DIR"
    cp "$ROOT_DIR/docker-compose.base.yml" "$INSTALL_DIR/docker-compose.base.yml"
}

mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/docker" <<'SH'
#!/usr/bin/env bash
if [[ "$1" == "compose" && "$2" == "ps" ]]; then
    exit 0
fi
if [[ "$1" == "ps" ]]; then
    exit 0
fi
exit 1
SH
cat > "$BIN_DIR/docker-compose" <<'SH'
#!/usr/bin/env bash
exit 0
SH
cat > "$BIN_DIR/curl" <<'SH'
#!/usr/bin/env bash
echo "curl: (7) Failed to connect" >&2
exit 7
SH
chmod +x "$BIN_DIR/docker" "$BIN_DIR/docker-compose" "$BIN_DIR/curl"
export PATH="$BIN_DIR:$PATH"

if grep -Eq '^set -euo pipefail|^set -eo pipefail' "$ODS_CLI"; then
    pass "ods-cli enables pipefail"
else
    fail "ods-cli does not enable pipefail"
fi

if grep -q '^set -euo pipefail' "$ODS_CLI"; then
    pass "ods-cli enables nounset"
else
    fail "ods-cli does not enable nounset"
fi

reset_install
: > "$INSTALL_DIR/.env"
result="$(run_ods config show)"
rc="$(printf '%s\n' "$result" | sed -n '1p')"
if [[ "$rc" == "0" ]]; then
    pass "config show tolerates empty .env"
else
    fail "config show exited $rc for empty .env"
fi

cat > "$INSTALL_DIR/.env" <<'EOF'
ODS_MODE=local
EOF
result="$(run_ods mode)"
rc="$(printf '%s\n' "$result" | sed -n '1p')"
if [[ "$rc" == "0" ]]; then
    pass "mode display tolerates missing optional .env keys"
else
    fail "mode display exited $rc with missing optional .env keys"
fi

result="$(run_ods model current)"
rc="$(printf '%s\n' "$result" | sed -n '1p')"
if [[ "$rc" == "0" ]]; then
    pass "model current tolerates missing model/tier keys"
else
    fail "model current exited $rc with missing model/tier keys"
fi

mkdir -p "$INSTALL_DIR/presets/left" "$INSTALL_DIR/presets/right"
cat > "$INSTALL_DIR/presets/left/env" <<'EOF'
SHARED=value
ONLY_LEFT=one
EOF
cat > "$INSTALL_DIR/presets/right/env" <<'EOF'
SHARED=value
ONLY_RIGHT=two
EOF
cat > "$INSTALL_DIR/presets/left/extensions.list" <<'EOF'
enabled:left-only
EOF
cat > "$INSTALL_DIR/presets/right/extensions.list" <<'EOF'
enabled:right-only
EOF
result="$(run_ods preset diff left right)"
rc="$(printf '%s\n' "$result" | sed -n '1p')"
if [[ "$rc" == "0" ]] && grep -q 'ONLY_LEFT' <<<"$result" && grep -q 'ONLY_RIGHT' <<<"$result" && grep -q 'right-only' <<<"$result"; then
    pass "preset diff tolerates one-sided env and service keys"
else
    fail "preset diff failed for one-sided env/service keys"
fi

result="$(run_ods preset diff)"
rc="$(printf '%s\n' "$result" | sed -n '1p')"
if [[ "$rc" != "0" ]] && grep -q 'Usage:' <<<"$result"; then
    pass "preset diff validates missing positional arguments"
else
    fail "preset diff did not validate missing positional arguments"
fi

if command -v python3 >/dev/null 2>&1 && python3 -c 'import yaml' >/dev/null 2>&1; then
    reset_install
    cat > "$INSTALL_DIR/.env" <<'EOF'
ODS_MODE=local
TIER=1
GPU_BACKEND=cpu
LLM_MODEL=test
OLLAMA_PORT=65535
EOF
    mkdir -p "$INSTALL_DIR/data"
    printf '{}' > "$INSTALL_DIR/data/bootstrap-status.json"
    result="$(run_ods status)"
    rc="$(printf '%s\n' "$result" | sed -n '1p')"
    if [[ "$rc" == "0" ]]; then
        pass "status tolerates malformed bootstrap-status.json"
    else
        fail "status exited $rc for malformed bootstrap-status.json"
    fi

    mkdir -p "$INSTALL_DIR/presets/bad"
    printf 'name=bad\n' > "$INSTALL_DIR/presets/bad/meta.txt"
    result="$(run_ods preset list)"
    rc="$(printf '%s\n' "$result" | sed -n '1p')"
    if [[ "$rc" == "0" ]] && grep -q 'unknown' <<<"$result"; then
        pass "preset list tolerates missing meta fields"
    else
        fail "preset list failed to tolerate missing meta fields"
    fi
else
    skip "PyYAML unavailable; skipped status/preset registry-backed cases"
fi

reset_install
cat > "$INSTALL_DIR/.env" <<'EOF'
ODS_MODE=local
TIER=1
GPU_BACKEND=cpu
LLM_MODEL=test
OLLAMA_PORT=65535
EOF
result="$(run_ods chat hello)"
rc="$(printf '%s\n' "$result" | sed -n '1p')"
if [[ "$rc" != "0" ]] && grep -q 'llama-server not reachable' <<<"$result"; then
    pass "chat surfaces dead backend as failure"
else
    fail "chat did not fail clearly for dead backend"
fi

result="$(run_ods benchmark)"
rc="$(printf '%s\n' "$result" | sed -n '1p')"
if [[ "$rc" != "0" ]] && grep -q 'Benchmark failed' <<<"$result"; then
    pass "benchmark propagates chat failure"
else
    fail "benchmark did not propagate chat failure"
fi

reset_install
cat > "$INSTALL_DIR/.env" <<'EOF'
ODS_MODE=local
TIER=1
GPU_BACKEND=cpu
ODS_COMPOSE_STARTUP_RETRY_ATTEMPTS=1
EOF

cat > "$BIN_DIR/docker" <<'SH'
#!/usr/bin/env bash
if [[ "$1" == "compose" && "$*" == *" up -d"* ]]; then
    echo "container ods-dashboard-api is unhealthy" >&2
    exit 1
fi
if [[ "$1" == "compose" && "$*" == *" ps -a"* ]]; then
    echo "ods-dashboard-api"
    exit 0
fi
if [[ "$1" == "inspect" ]]; then
    echo "running unhealthy"
    exit 0
fi
if [[ "$1" == "logs" && "$2" == "--tail" && "$3" == "20" ]]; then
    echo "STARTUP_EVIDENCE_SENTINEL"
    exit 0
fi
if [[ "$1" == "ps" ]]; then
    exit 0
fi
exit 0
SH
chmod +x "$BIN_DIR/docker"

result="$(run_ods start)"
rc="$(printf '%s\n' "$result" | sed -n '1p')"
if [[ "$rc" != "0" ]] \
   && grep -q 'STARTUP_EVIDENCE_SENTINEL' <<<"$result" \
   && grep -q 'ods doctor' <<<"$result" \
   && grep -q 'Full compose output:' <<<"$result"; then
    pass "start failure surfaces container evidence and recovery step"
else
    fail "start failure diagnostics did not preserve failure evidence contract"
fi

cat > "$BIN_DIR/docker" <<'SH'
#!/usr/bin/env bash
if [[ "$1" == "compose" && "$*" == *" up -d"* ]]; then
    echo "container ods-dashboard-api is unhealthy" >&2
    exit 23
fi
if [[ "$1" == "compose" && "$*" == *" ps -a"* ]]; then
    [[ "${DIAGNOSTIC_FAIL_STEP:-}" == "ps" ]] && exit 1
    echo "ods-dashboard-api"
    exit 0
fi
if [[ "$1" == "inspect" ]]; then
    [[ "${DIAGNOSTIC_FAIL_STEP:-}" == "inspect" ]] && exit 1
    echo "running unhealthy"
    exit 0
fi
if [[ "$1" == "logs" ]]; then
    [[ "${DIAGNOSTIC_FAIL_STEP:-}" == "logs" ]] && exit 1
    exit 0
fi
if [[ "$1" == "ps" ]]; then
    exit 0
fi
exit 0
SH
chmod +x "$BIN_DIR/docker"

for fail_step in ps inspect logs; do
    DIAGNOSTIC_FAIL_STEP="$fail_step"
    export DIAGNOSTIC_FAIL_STEP
    result="$(run_ods start)"
    rc="$(printf '%s\n' "$result" | sed -n '1p')"
    if [[ "$rc" == "23" ]] && grep -q 'Full compose output:' <<<"$result"; then
        pass "diagnostic $fail_step failure preserves compose failure"
    else
        fail "diagnostic $fail_step failure masked compose failure"
    fi
done
unset DIAGNOSTIC_FAIL_STEP

echo "Result: $PASS passed, $FAIL failed, $SKIP skipped"
[[ "$FAIL" -eq 0 ]]
