#!/usr/bin/env bash
# Boundary coverage for the interactive showcase's live model routing.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SHOWCASE="$ROOT_DIR/scripts/showcase.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

fake_bin="$TMP_DIR/bin"
payload_log="$TMP_DIR/payload.json"
mkdir -p "$fake_bin"
cat > "$fake_bin/curl" <<'SH'
#!/usr/bin/env bash
url=""
payload=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        -d)
            payload="${2:-}"
            shift 2
            ;;
        http://*)
            url="$1"
            shift
            ;;
        *) shift ;;
    esac
done
case "$url" in
    */health)
        printf '{"status":"ok"}\n'
        ;;
    */v1/models)
        [[ "${SHOWCASE_TEST_FAIL_MODELS:-false}" != "true" ]] || exit 22
        printf '{"data":[{"id":"live/model.gguf"}]}\n'
        ;;
    */v1/chat/completions)
        printf '%s\n' "$payload" > "$SHOWCASE_TEST_PAYLOAD_LOG"
        printf '{"choices":[{"message":{"content":"fixture answer"}}]}\n'
        ;;
    *) exit 22 ;;
esac
SH
chmod +x "$fake_bin/curl"

output="$({
    printf '1\nhello\nback\nq\n' \
        | PATH="$fake_bin:$PATH" \
          LLM_URL="http://showcase.test" \
          SHOWCASE_TEST_PAYLOAD_LOG="$payload_log" \
          bash "$SHOWCASE"
} 2>&1)"
[[ "$output" == *"fixture answer"* ]] || fail "chat result did not reach the interactive boundary"
jq -e '.model == "live/model.gguf"' "$payload_log" >/dev/null \
    || fail "showcase did not post the live catalog model id"

set +e
failure_output="$({
    printf '1\nhello\nback\nq\n' \
        | PATH="$fake_bin:$PATH" \
          LLM_URL="http://showcase.test" \
          SHOWCASE_TEST_FAIL_MODELS="true" \
          SHOWCASE_TEST_PAYLOAD_LOG="$payload_log" \
          bash "$SHOWCASE"
} 2>&1)"
failure_rc=$?
set -e
[[ "$failure_rc" == "0" ]] || fail "catalog failure aborted the interactive showcase"
[[ "$failure_output" == *"Error getting response"* ]] \
    || fail "catalog failure was not explained at the menu boundary"
[[ "$failure_output" == *"Thanks for trying ODS"* ]] \
    || fail "showcase did not remain interactive after request failure"

echo "[PASS] showcase discovers the live model and contains request failures"
