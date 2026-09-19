#!/usr/bin/env bash
# Regression tests for scripts/showcase.sh drift + crash-on-error:
#   1. A failed /v1/chat/completions request must render "Error getting
#      response", not kill the demo (pipefail turned `curl | jq` into a
#      script-level abort before the fallback could print).
#   2. EOF on the menu prompt must exit cleanly instead of aborting via set -e.
#   3. The script must run without lib/service-registry.sh (its port fallbacks
#      are unreachable dead code while SERVICE_* maps stay undeclared).
#   4. Printed voice commands must use the real API surface:
#      /v1/audio/transcriptions + /v1/audio/speech (not /asr, /synthesize).
#   5. Requests must use the installed model id from /v1/models.
set -euo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODS_ROOT="$(cd "${TEST_DIR}/.." && pwd)"
SCRIPT="${ODS_ROOT}/scripts/showcase.sh"

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT
BIN_DIR="$TMP_ROOT/bin"
mkdir -p "$BIN_DIR"
STUB_PAYLOAD_LOG="$TMP_ROOT/payloads.log"
: > "$STUB_PAYLOAD_LOG"

# --- stub curl -------------------------------------------------------------
cat > "$BIN_DIR/curl" <<'STUB'
#!/usr/bin/env bash
url=""
payload=""
for arg in "$@"; do
    case "$arg" in
        http://*|https://*) url="$arg" ;;
        \{*) payload="$arg" ;;
    esac
done
[[ -n "$payload" ]] && echo "$payload" >> "${STUB_PAYLOAD_LOG:?}"

case "$url" in
    *"/v1/models")
        printf '{"data":[{"id":"installed-model-x"}]}\n'; exit 0 ;;
    *"/v1/chat/completions")
        if [[ "${STUB_CHAT_MODE:-ok}" == "fail" ]]; then
            exit 22   # HTTP error under curl -f
        fi
        printf '{"choices":[{"message":{"content":"canned reply"}}]}\n'; exit 0 ;;
    */health|*/healthz|http://*)
        exit 0 ;;
esac
exit 0
STUB
chmod +x "$BIN_DIR/curl"

run_showcase() {
    env -i PATH="$BIN_DIR:/usr/bin:/bin" HOME="$TMP_ROOT" \
        STUB_CHAT_MODE="${STUB_CHAT_MODE:-ok}" \
        STUB_PAYLOAD_LOG="$STUB_PAYLOAD_LOG" \
        LLM_URL="http://llm.stub" WHISPER_URL="http://whisper.stub" \
        TTS_URL="http://tts.stub" QDRANT_URL="http://qdrant.stub" \
        bash "$1" 2>&1
}

# 1. Failed chat request → fallback text, clean exit (base: dies via pipefail).
set +e
out="$(STUB_CHAT_MODE=fail run_showcase "$SCRIPT" <<< $'1\nhello\nback\nq\n')"
rc=$?
set -e
[[ $rc -eq 0 ]] || { echo "FAIL: demo died on a failed chat request (exit $rc)"; echo "$out"; exit 1; }
grep -q "Error getting response" <<< "$out" \
    || { echo "FAIL: chat failure did not render the error fallback"; echo "$out"; exit 1; }

# 2. EOF at the menu → clean exit, not a set -e abort.
set +e
out="$(run_showcase "$SCRIPT" < /dev/null)"
rc=$?
set -e
[[ $rc -eq 0 ]] || { echo "FAIL: EOF at the menu aborted the script (exit $rc)"; echo "$out"; exit 1; }

# 3. Runs without lib/service-registry.sh (fallback defaults must be reachable).
LONE_DIR="$TMP_ROOT/lone/scripts"
mkdir -p "$LONE_DIR"
cp "$SCRIPT" "$LONE_DIR/showcase.sh"
set +e
out="$(run_showcase "$LONE_DIR/showcase.sh" <<< 'q')"
rc=$?
set -e
[[ $rc -eq 0 ]] || { echo "FAIL: standalone run aborted (exit $rc)"; echo "$out"; exit 1; }

# 4. Voice commands match the real API surface.
for needle in "/v1/audio/transcriptions" "/v1/audio/speech" "file=@" "ps tts"; do
    grep -qF "$needle" "$SCRIPT" || { echo "FAIL: showcase missing '$needle'"; exit 1; }
done
for stale in "/asr" "/synthesize" "audio_file="; do
    ! grep -qF "$stale" "$SCRIPT" || { echo "FAIL: stale endpoint '$stale' still printed"; exit 1; }
done
! grep -q "ps whisper.*Voice output" "$SCRIPT" \
    || { echo "FAIL: TTS hint still points at the whisper service"; exit 1; }

# 5. Chat payload uses the installed model id from /v1/models.
: > "$STUB_PAYLOAD_LOG"
set +e
run_showcase "$SCRIPT" <<< $'1\nhi\nback\nq\n' >/dev/null
set -e
grep -q "installed-model-x" "$STUB_PAYLOAD_LOG" \
    || { echo "FAIL: request did not use the resolved model id"; cat "$STUB_PAYLOAD_LOG"; exit 1; }
! grep -q '"model": *"local"' "$STUB_PAYLOAD_LOG" \
    || { echo "FAIL: request still hardcodes model 'local'"; exit 1; }

echo "PASS: showcase.sh has no stale endpoints and survives request/prompt failures"
