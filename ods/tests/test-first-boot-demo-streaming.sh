#!/usr/bin/env bash
# Regression test: first-boot-demo.sh Demo 3 must verify that streaming
# actually produced tokens before printing "Streaming works!".
#
# The streaming request runs through a stubbed curl keyed by the script's
# LLM_URL/WHISPER_URL/... env overrides, so no real services are needed.
set -euo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMO_SCRIPT="${TEST_DIR}/../scripts/first-boot-demo.sh"

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT
BIN_DIR="$TMP_ROOT/bin"
mkdir -p "$BIN_DIR"

# --- stub curl -------------------------------------------------------------
# Routes by hostname (env URL overrides) and by the JSON payload:
#   * health endpoints on the required services succeed, optional ones fail
#   * non-streaming chat completions return a canned message
#   * streaming requests honour STUB_STREAM_MODE=ok|fail
cat > "$BIN_DIR/curl" <<'STUB'
#!/usr/bin/env bash
url=""
data=""
for arg in "$@"; do
    case "$arg" in
        http://*|https://*) url="$arg" ;;
        \{*) data="$arg" ;;
    esac
done

case "$url" in
    http://llm.stub/health) exit 0 ;;
    http://webui.stub/*|http://webui.stub) exit 0 ;;
    http://whisper.stub|http://whisper.stub/*|\
    http://piper.stub|http://piper.stub/*|\
    http://n8n.stub|http://n8n.stub/*) exit 1 ;;
esac

if [[ "$url" == http://llm.stub/v1/chat/completions* ]]; then
    if [[ "$data" == *'"stream": true'* || "$data" == *'"stream":true'* ]]; then
        if [[ "${STUB_STREAM_MODE:-ok}" == "ok" ]]; then
            printf 'data: {"choices":[{"delta":{"content":"1"}}]}\n'
            printf 'data: {"choices":[{"delta":{"content":"2"}}]}\n'
            printf 'data: [DONE]\n'
            exit 0
        fi
        exit 22   # HTTP error under curl -f: no body is emitted
    fi
    printf '{"choices":[{"message":{"content":"local reply"}}]}\n'
    exit 0
fi

exit 0
STUB
chmod +x "$BIN_DIR/curl"

# --- stub jq ---------------------------------------------------------------
cat > "$BIN_DIR/jq" <<'STUB'
#!/usr/bin/env bash
filter=""
for arg in "$@"; do
    case "$arg" in
        .choices*) filter="$arg" ;;
    esac
done
cat >/dev/null 2>&1 || true
case "$filter" in
    *message.content*) echo "local reply" ;;
    *delta.content*)   echo "tok" ;;
esac
exit 0
STUB
chmod +x "$BIN_DIR/jq"

# --- stub clear (no TTY in test environments) ------------------------------
cat > "$BIN_DIR/clear" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB
chmod +x "$BIN_DIR/clear"

run_demo() {
    local mode="$1"
    STUB_STREAM_MODE="$mode" \
    PATH="$BIN_DIR:$PATH" \
    LLM_URL="http://llm.stub" \
    WHISPER_URL="http://whisper.stub" \
    PIPER_URL="http://piper.stub" \
    N8N_URL="http://n8n.stub" \
    WEBUI_URL="http://webui.stub" \
    LLM_MODEL="stub-model" \
    bash "$DEMO_SCRIPT" --quick 2>&1
}

# Case 1: a healthy stream still reports success.
out_ok="$(run_demo ok)"
if ! grep -q "Streaming works!" <<< "$out_ok"; then
    echo "FAIL: healthy stream did not report success"
    echo "$out_ok"
    exit 1
fi

# Case 2: a failed stream must not claim success — and the demo must not
# crash outright (pipefail turns a failed curl pipeline into a script kill).
set +e
out_fail="$(run_demo fail)"
rc_fail=$?
set -e
if [[ $rc_fail -ne 0 ]]; then
    echo "FAIL: demo exited $rc_fail on a failed stream instead of reporting the failure"
    echo "$out_fail"
    exit 1
fi
if grep -q "Streaming works!" <<< "$out_fail"; then
    echo "FAIL: reported 'Streaming works!' although the stream produced no tokens"
    exit 1
fi
if ! grep -q "No streaming response from LLM" <<< "$out_fail"; then
    echo "FAIL: expected a failure message for the broken stream"
    echo "$out_fail"
    exit 1
fi

echo "PASS: first-boot-demo verifies streaming output before claiming success"
