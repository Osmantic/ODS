#!/usr/bin/env bash
# Regression (#2351 bug 1): start_native_llama()'s health-wait loop polled
# /health for up to 60s with no check that the process was still alive. A
# crash (e.g. "unknown model architecture" for a GGUF newer than the
# installed llama.cpp build supports) exits almost immediately, not
# gradually — so the old code waited out the full 60s and then reported a
# misleading "llama-server may still be loading model..." even though the
# process had been dead the whole time, with the real crash reason left
# sitting unseen in the log file.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CLI="$ROOT_DIR/installers/macos/ods-macos.sh"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'
PASSED=0
FAILED=0
pass() { printf "  ${GREEN}✓ PASS${NC} %s\n" "$1"; PASSED=$((PASSED + 1)); }
fail() { printf "  ${RED}✗ FAIL${NC} %s\n" "$1"; FAILED=$((FAILED + 1)); }

echo ""
echo "=== native llama-server crash detection ==="
echo ""

if bash -n "$CLI" 2>/dev/null; then
    pass "ods-macos.sh passes bash -n"
else
    fail "ods-macos.sh bash -n failed"
    echo "Result: $PASSED passed, $FAILED failed"
    exit 1
fi

# Extract just start_native_llama so the CLI's dispatch never runs.
START_NATIVE_LLAMA_SRC="$(sed -n '/^start_native_llama()/,/^}/p' "$CLI")"
if [[ -z "$START_NATIVE_LLAMA_SRC" ]]; then
    fail "could not extract start_native_llama from ods-macos.sh"
    echo "Result: $PASSED passed, $FAILED failed"
    exit 1
fi

# start_native_llama() calls several other CLI functions for setup
# (reading .env, configuring the Colima bridge, checking current status).
# Stub them so the function under test reaches its actual launch/health
# logic without needing the rest of the CLI's environment.
read_ods_env() {
    ENV_GGUF_FILE="$GGUF_FILE"
    ENV_ODS_MODE="local"
    ENV_CTX_SIZE=4096
    ENV_ODS_NATIVE_LLAMA_PORT=18099
    ENV_BIND_ADDRESS=127.0.0.1
    ENV_LLAMA_REASONING="off"
}
macos_configure_llm_bridge_from_env() { return 0; }
get_native_llama_status() { NATIVE_LLAMA_RUNNING=false; NATIVE_LLAMA_HEALTHY=false; }
macos_bind_probe_host() { printf '%s' "$1"; }
ai() { printf 'ai: %s\n' "$*"; }
ai_ok() { printf 'ai_ok: %s\n' "$*"; }
ai_err() { printf 'ai_err: %s\n' "$*"; }
ai_warn() { printf 'ai_warn: %s\n' "$*"; }
# curl must never actually hit the network in this test — a dead process
# means nothing is listening, but stubbing keeps the test deterministic
# and fast regardless of port availability.
curl() { return 1; }

eval "$START_NATIVE_LLAMA_SRC"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

INSTALL_DIR="$TMP_DIR"
mkdir -p "$INSTALL_DIR/data/models"
GGUF_FILE="gemma-4-E4B-it-Q4_K_M.gguf"
touch "$INSTALL_DIR/data/models/$GGUF_FILE"
LLAMA_SERVER_PID_FILE="$TMP_DIR/llama-server.pid"
LLAMA_SERVER_LOG="$TMP_DIR/llama-server.log"

# Fake llama-server binary that crashes immediately, the way a real
# architecture-mismatch failure does, and writes the real error to its log.
LLAMA_SERVER_BIN="$TMP_DIR/fake-llama-server"
cat > "$LLAMA_SERVER_BIN" <<'EOF'
#!/usr/bin/env bash
echo "llama_model_load: error loading model architecture: unknown model architecture: 'gemma4'"
exit 1
EOF
chmod +x "$LLAMA_SERVER_BIN"

output="$(start_native_llama 2>&1)"

if echo "$output" | grep -q "exited before becoming healthy"; then
    pass "crash is detected instead of waiting out the full timeout"
else
    fail "crash was not detected (output: $output)"
fi

if echo "$output" | grep -q "may still be loading"; then
    fail "misleading 'may still be loading' message was shown despite the process being dead"
else
    pass "misleading 'may still be loading' message is not shown for a dead process"
fi

if echo "$output" | grep -q "unknown model architecture"; then
    pass "the real crash reason from the log is surfaced to the operator"
else
    fail "the real crash reason was not surfaced (output: $output)"
fi

echo ""
echo "Result: $PASSED passed, $FAILED failed"
echo ""
[[ $FAILED -eq 0 ]]
