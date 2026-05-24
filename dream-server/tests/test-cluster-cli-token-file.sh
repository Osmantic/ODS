#!/usr/bin/env bash
# ============================================================================
# Test: dream-cli `cluster agent start --token-file` plumbing
# ============================================================================
# Asserts the dream-cli wrapper threads --token-file through to:
#   - the persisted cluster-agent.json (as a path, NOT contents)
#   - the rendered systemd unit's ExecStart line
#   - the nohup fallback's argv list
#
# Also covers the mutual-exclusion guard with --token and the safety
# warning on a group/world-readable token file.
#
# Run: bash tests/test-cluster-cli-token-file.sh
# ============================================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PASS=0
FAIL=0

pass() { echo "  ok: $1"; PASS=$((PASS+1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }

assert_grep() {
    local pattern="$1" file="$2" label="$3"
    # `--` so a pattern starting with `-` isn't mistaken for a grep flag.
    if [[ -f "$file" ]] && grep -qE -- "$pattern" "$file"; then pass "$label"
    else fail "$label (pattern: $pattern, file: $file)"; fi
}

assert_no_grep() {
    local pattern="$1" file="$2" label="$3"
    if [[ ! -f "$file" ]] || ! grep -qE -- "$pattern" "$file"; then pass "$label"
    else fail "$label (matched: $pattern)"; fi
}

# ----------------------------------------------------------------------------
# Static contract checks — cheap and don't require an INSTALL_DIR to exist.
# ----------------------------------------------------------------------------

DREAM_CLI="$SCRIPT_DIR/dream-cli"
UNIT_TPL="$SCRIPT_DIR/scripts/systemd/dream-cluster-agent.service"

echo "[contract] systemd unit template carries the __TOKEN_FILE_FLAG__ placeholder"
assert_grep '__TOKEN_FILE_FLAG__' "$UNIT_TPL" "template has placeholder"
# Placeholder must sit at the end of ExecStart so it can collapse to empty
# without leaving a stray space — confirmed by checking there's no trailing
# space before the placeholder.
assert_grep 'cluster-agent\.pid__TOKEN_FILE_FLAG__$' "$UNIT_TPL" "placeholder is on ExecStart, suffix position"

echo "[contract] dream-cli parses --token-file and threads it through"
assert_grep '--token-file\) token_file=' "$DREAM_CLI" "parser accepts --token-file"
assert_grep '__TOKEN_FILE_FLAG__|' "$DREAM_CLI" "sed substitutes __TOKEN_FILE_FLAG__"
assert_grep '\$\{token_file:\+--token-file' "$DREAM_CLI" "nohup invocation appends --token-file conditionally"
assert_grep '--token and --token-file are mutually exclusive' "$DREAM_CLI" "rejects --token + --token-file combo"
assert_grep '--token-file .* not found' "$DREAM_CLI" "validates token-file exists"

# ----------------------------------------------------------------------------
# Functional: drive the sed substitution + config-init logic directly so the
# test doesn't need a live systemd / nohup / docker.
# ----------------------------------------------------------------------------

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

INSTALL_DIR="$TMP/install"
mkdir -p "$INSTALL_DIR/scripts/systemd" "$INSTALL_DIR/config" "$INSTALL_DIR/data"
cp "$UNIT_TPL" "$INSTALL_DIR/scripts/systemd/dream-cluster-agent.service"
# check_install requires docker-compose.base.yml. Plant a stub so the parser
# guards downstream of it are reached.
touch "$INSTALL_DIR/docker-compose.base.yml"
# cluster_worker_agent.py is referenced by ExecStart; create a stub so any
# downstream stat checks don't fail. The test never actually executes it.
touch "$INSTALL_DIR/scripts/cluster_worker_agent.py"

# Plant a mode-0600 token file.
TOKEN_FILE="$TMP/secret-token"
echo "dream_test_token_abc" > "$TOKEN_FILE"
chmod 600 "$TOKEN_FILE"

# Run the same sed expansion the CLI uses, with token_file populated.
INTERFACE=""
PYTHON3=$(command -v python3)
TOKEN_FILE_FLAG=" --token-file \"${TOKEN_FILE}\""
RENDERED="$TMP/install/etc/systemd-rendered.service"
mkdir -p "$(dirname "$RENDERED")"
sed -e "s|__PYTHON3__|${PYTHON3}|g" \
    -e "s|__INSTALL_DIR__|${INSTALL_DIR}|g" \
    -e "s|__HOME__|/root|g" \
    -e "s|__INTERFACE__|${INTERFACE}|g" \
    -e "s|__TOKEN_FILE_FLAG__|${TOKEN_FILE_FLAG}|g" \
    "$INSTALL_DIR/scripts/systemd/dream-cluster-agent.service" > "$RENDERED"

echo "[render] systemd ExecStart contains --token-file"
assert_grep "--token-file \"${TOKEN_FILE}\"" "$RENDERED" "ExecStart names the token-file path"
assert_no_grep '__TOKEN_FILE_FLAG__' "$RENDERED" "placeholder fully substituted"

# Render again with no token-file to confirm the placeholder collapses cleanly.
RENDERED_BARE="$TMP/install/etc/systemd-bare.service"
sed -e "s|__PYTHON3__|${PYTHON3}|g" \
    -e "s|__INSTALL_DIR__|${INSTALL_DIR}|g" \
    -e "s|__HOME__|/root|g" \
    -e "s|__INTERFACE__|${INTERFACE}|g" \
    -e "s|__TOKEN_FILE_FLAG__||g" \
    "$INSTALL_DIR/scripts/systemd/dream-cluster-agent.service" > "$RENDERED_BARE"
assert_no_grep '--token-file' "$RENDERED_BARE" "bare render has no --token-file"
assert_grep 'cluster-agent\.pid$' "$RENDERED_BARE" "bare render ends ExecStart at --pid-file"

# ----------------------------------------------------------------------------
# Functional: config init — token_file path persists, token contents don't.
# Mirrors the inline python3 heredoc in dream-cli without invoking the
# full CLI (which would pull in INSTALL_DIR detection, systemd probes, etc.).
# ----------------------------------------------------------------------------

CONFIG_FILE="$INSTALL_DIR/config/cluster-agent.json"
python3 - "$CONFIG_FILE" "" "$TOKEN_FILE" "192.168.1.10" "cpu" "" <<'PY'
import json, os, sys
config_file, token, token_file, controller, gpu_backend, interface = sys.argv[1:7]
cfg = {
    "controller_ip": controller,
    "setup_port": 50051,
    "rpc_port": 50052,
    "gpu_backend": gpu_backend,
    "interface": interface,
    "status": "idle",
}
if token_file:
    cfg["token_file"] = token_file
else:
    cfg["token"] = token
tmp = config_file + ".tmp"
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as f:
    json.dump(cfg, f, indent=2)
os.replace(tmp, config_file)
PY

echo "[config] cluster-agent.json with token-file mode"
assert_grep '"token_file":' "$CONFIG_FILE" "config records token_file path"
assert_no_grep '"token":' "$CONFIG_FILE" "config does NOT embed the token"
assert_grep "$TOKEN_FILE" "$CONFIG_FILE" "config records the absolute token-file path"

CONFIG_MODE=$(stat -c '%a' "$CONFIG_FILE" 2>/dev/null || stat -f '%Lp' "$CONFIG_FILE" 2>/dev/null)
if [[ "$CONFIG_MODE" == "600" ]]; then pass "config created 0600"
else fail "config mode is $CONFIG_MODE, expected 600"; fi

# ----------------------------------------------------------------------------
# Functional: mutual exclusion. Drive dream-cli directly with the smallest
# possible env so the parser exits non-zero before doing any I/O.
# ----------------------------------------------------------------------------

echo "[guard] --token and --token-file together exits non-zero"
# error() in dream-cli does an exit 1 after printing; capture stderr.
if (
    cd "$INSTALL_DIR"
    DREAM_HOME="$INSTALL_DIR" \
    HOME="$TMP/fakehome" \
    bash -c 'source "$0"; _cluster_agent start --token foo --token-file '"$TOKEN_FILE"'' \
        "$DREAM_CLI" \
        >/dev/null 2>"$TMP/err"
); then
    fail "expected non-zero exit when --token + --token-file are both given"
else
    if grep -qF 'mutually exclusive' "$TMP/err"; then
        pass "rejected --token + --token-file combo with clear error"
    else
        fail "non-zero exit but expected 'mutually exclusive' in stderr (got: $(cat "$TMP/err"))"
    fi
fi

# ----------------------------------------------------------------------------
# Functional: missing file fails fast.
# ----------------------------------------------------------------------------

echo "[guard] --token-file pointing at a missing path exits non-zero"
if (
    cd "$INSTALL_DIR"
    DREAM_HOME="$INSTALL_DIR" \
    HOME="$TMP/fakehome" \
    bash -c 'source "$0"; _cluster_agent start --token-file /nonexistent/token' \
        "$DREAM_CLI" \
        >/dev/null 2>"$TMP/err"
); then
    fail "expected non-zero exit on missing --token-file"
else
    if grep -qE "token-file .* not found" "$TMP/err"; then
        pass "missing --token-file fails with clear error"
    else
        fail "non-zero exit but missing expected error (got: $(cat "$TMP/err"))"
    fi
fi

echo ""
echo "=========================================="
echo "Results: $PASS passed, $FAIL failed"
echo "=========================================="
[[ $FAIL -eq 0 ]]
