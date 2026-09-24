#!/usr/bin/env bash
# Regression tests for stale service references in scripts/ods-test.sh:
#   1. LiveKit is optional external infra — an absent LiveKit must SKIP, not FAIL
#      (matches test-integration.sh's test_optional semantics).
#   2. The embeddings port fallback must match the manifest default (8090).
#   3. The TTS "actionable fix" hint must name the real compose service (tts).
#   4. tool-calling must resolve the installed model id instead of hardcoding
#      a checkpoint the install may not have.
set -euo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODS_ROOT="$(cd "${TEST_DIR}/.." && pwd)"
SOURCE_SCRIPT="${ODS_ROOT}/scripts/ods-test.sh"

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

# --- copied layout: script + safe-env only (no service-registry) -----------
# Deterministic env-driven runs; port fallbacks come from the script itself.
FAKE_ODS="$TMP_ROOT/ods"
mkdir -p "$FAKE_ODS/scripts" "$FAKE_ODS/lib" "$TMP_ROOT/odshome"
cp "$SOURCE_SCRIPT" "$FAKE_ODS/scripts/ods-test.sh"
cp "$ODS_ROOT/lib/safe-env.sh" "$FAKE_ODS/lib/safe-env.sh"
SCRIPT="$FAKE_ODS/scripts/ods-test.sh"

BIN_DIR="$TMP_ROOT/bin"
mkdir -p "$BIN_DIR"
STUB_URL_LOG="$TMP_ROOT/urls.log"
STUB_PAYLOAD_LOG="$TMP_ROOT/payloads.log"
: > "$STUB_URL_LOG"
: > "$STUB_PAYLOAD_LOG"

# --- stub timeout: every /dev/tcp probe reports "closed" --------------------
cat > "$BIN_DIR/timeout" <<'STUB'
#!/usr/bin/env bash
exit 1
STUB
chmod +x "$BIN_DIR/timeout"

# --- stub curl: logs URLs + payloads, serves canned LLM endpoints -----------
cat > "$BIN_DIR/curl" <<'STUB'
#!/usr/bin/env bash
url=""
payload=""
want_code=0
for arg in "$@"; do
    case "$arg" in
        http://*|https://*) url="$arg" ;;
        *http_code*) want_code=1 ;;
        \{*) payload="$arg" ;;
    esac
done
[[ -n "$url" ]] && echo "$url" >> "${STUB_URL_LOG:?}"
[[ -n "$payload" ]] && echo "$payload" >> "${STUB_PAYLOAD_LOG:?}"

case "$url" in
    *"/v1/models")
        printf '{"data":[{"id":"real-model-9b"}]}\n'; exit 0 ;;
    *"/v1/chat/completions")
        printf '{"choices":[{"message":{"tool_calls":[{"id":"c1"}],"content":"hi"}}]}\n'; exit 0 ;;
esac
[[ $want_code -eq 1 ]] && printf '000'
exit 0
STUB
chmod +x "$BIN_DIR/curl"

run_suite() {
    env -i PATH="$BIN_DIR:/usr/bin:/bin" HOME="$TMP_ROOT" \
        ODS_DIR="$TMP_ROOT/odshome" ENV_FILE="$TMP_ROOT/odshome/.env" \
        STUB_URL_LOG="$STUB_URL_LOG" STUB_PAYLOAD_LOG="$STUB_PAYLOAD_LOG" \
        LLM_HOST=llm.stub WHISPER_HOST=whisper.stub TTS_HOST=tts.stub \
        EMBEDDING_HOST=emb.stub LIVEKIT_HOST=livekit.stub \
        "$@" bash "$SCRIPT" "${EXTRA_ARGS[@]:-}"
}

EXTRA_ARGS=(--service livekit)

# 1. Absent LiveKit (closed port, no credentials) → skip, exit 0.
set +e
out="$(run_suite)"; rc=$?
set -e
[[ $rc -eq 0 ]] || { echo "FAIL: suite exited $rc with LiveKit absent"; echo "$out"; exit 1; }
grep -q "not deployed" <<< "$out" || { echo "FAIL: LiveKit absence not reported as skip"; echo "$out"; exit 1; }

# 2. Configured-but-down LiveKit must still fail.
set +e
out="$(run_suite LIVEKIT_API_KEY=lk_test_key)"; rc=$?
set -e
[[ $rc -ne 0 ]] || { echo "FAIL: configured LiveKit outage did not fail the suite"; exit 1; }
grep -q "LiveKit Port.*FAIL" <<< "$out" || { echo "FAIL: expected LiveKit Port failure"; echo "$out"; exit 1; }

# 3. Embeddings port fallback matches the manifest default (8090).
EXTRA_ARGS=(--service embeddings)
: > "$STUB_URL_LOG"
set +e
run_suite >/dev/null 2>&1
set -e
grep -q "emb.stub:8090" "$STUB_URL_LOG" || { echo "FAIL: embeddings fallback used wrong port"; cat "$STUB_URL_LOG"; exit 1; }

# 4. Dead TTS → actionable hint names the real compose service.
EXTRA_ARGS=(--service tts)
set +e
out="$(run_suite)"; rc=$?
set -e
[[ $rc -ne 0 ]] || { echo "FAIL: dead TTS did not fail the suite"; exit 1; }
grep -q "docker compose up tts" <<< "$out" || { echo "FAIL: TTS hint missing/wrong"; echo "$out"; exit 1; }
! grep -q "kokoro-tts" <<< "$out" || { echo "FAIL: hint names nonexistent kokoro-tts service"; exit 1; }

# 5. tool-calling resolves the installed model from /v1/models.
EXTRA_ARGS=(--service tool-calling)
: > "$STUB_PAYLOAD_LOG"
set +e
out="$(run_suite)"; rc=$?
set -e
[[ $rc -eq 0 ]] || { echo "FAIL: tool-calling suite run exited $rc"; echo "$out"; exit 1; }
grep -q "real-model-9b" "$STUB_PAYLOAD_LOG" || { echo "FAIL: tool-calling did not use installed model id"; cat "$STUB_PAYLOAD_LOG"; exit 1; }

# 6. Static guard: no stale literals remain in the script.
! grep -qE "Qwen2\.5-32B|kokoro-tts" "$SCRIPT" || { echo "FAIL: stale model/service literals remain"; exit 1; }

echo "PASS: ods-test.sh has no stale service references"
