#!/usr/bin/env bash
# test-curl-timeout-stragglers.sh — the last unbounded operational curl calls:
#   * installers/phases/12-health.sh    Perplexica /api/config fetch used to push
#                                       the provider config (sibling probe on the
#                                       same endpoint already uses --max-time 5)
#   * installers/macos/ods-macos.sh     cmd_chat chat-completions POST
#
# A stub `curl` sleeps 30s when invoked without --max-time, so unbounded call
# sites hang under an outer timeout (red on base) while bounded sites pass.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODS_DIR="$(dirname "$SCRIPT_DIR")"

PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); echo "  PASS: $1"; }
fail() { FAIL=$((FAIL + 1)); echo "  FAIL: $1"; }

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

STUB_BIN="$TMP_ROOT/bin"
mkdir -p "$STUB_BIN"
cat > "$STUB_BIN/curl" <<'STUB'
#!/usr/bin/env bash
printf '%q ' "$@" >> "${CURL_LOG:?}"
printf '\n' >> "${CURL_LOG:?}"
bounded=0
for a in "$@"; do [[ "$a" == "--max-time" ]] && bounded=1; done
[[ "$bounded" -eq 0 ]] && sleep 30
printf '{"choices":[{"message":{"content":"stub reply"}}]}\n'
exit 0
STUB
chmod +x "$STUB_BIN/curl"

CURL_LOG="$TMP_ROOT/curl.log"
export CURL_LOG

#----------------------------------------------------------------------
# 1) Behavioral: eval the real cmd_chat function from ods-macos.sh under a
#    stub curl. Unbounded POST -> 30s stall -> outer timeout kills it.
#----------------------------------------------------------------------
echo "macOS cmd_chat under stub curl"
: > "$CURL_LOG"

# Extract cmd_chat body (function start to closing brace at col 0)
awk '/^cmd_chat\(\)/{f=1} f{print} f&&/^}/{exit}' \
    "$ODS_DIR/installers/macos/ods-macos.sh" > "$TMP_ROOT/cmd_chat.sh"
grep -q 'curl' "$TMP_ROOT/cmd_chat.sh" || { fail "could not extract cmd_chat"; }

run_chat() {
    timeout 10 env PATH="$STUB_BIN:$PATH" CURL_LOG="$CURL_LOG" bash -c '
        ai() { :; }; ai_err() { :; }
        resolve_cli_llm_route() {
            CLI_LLM_MODE="local"
            CLI_LLM_BASE_URL="http://localhost:11434"
            CLI_LLM_API_KEY=""
        }
        . "$0"
        cmd_chat "hello"
    ' "$TMP_ROOT/cmd_chat.sh" >/dev/null 2>&1
}

run_chat
rc=$?
if [[ $rc -eq 0 ]]; then
    pass "cmd_chat completes under wedged-peer simulation"
else
    fail "cmd_chat exits $rc — chat POST is unbounded (hangs on wedged peer)"
fi
if grep -q -- '--max-time' "$CURL_LOG"; then
    pass "cmd_chat curl argv carries --max-time"
else
    fail "cmd_chat curl argv lacks --max-time"
fi

#----------------------------------------------------------------------
# 2) Behavioral: the two Perplexica /api/config fetches in 12-health.sh.
#    Extract each curl invocation line and run it under the stub.
#----------------------------------------------------------------------
echo "12-health.sh Perplexica /api/config fetch"
idx=0
while IFS= read -r line; do
    idx=$((idx + 1))
    : > "$CURL_LOG"
    # strip the pipeline continuation so the curl command evaluates alone;
    # restore the closing paren when the call sits inside $(...)
    stmt="${line%%|*}"
    [[ "$line" == *'$('* ]] && stmt="$stmt)"
    timeout 10 env PATH="$STUB_BIN:$PATH" CURL_LOG="$CURL_LOG" PERPLEXICA_URL="http://x" \
        bash -c "$stmt" >/dev/null 2>&1
    rc=$?
    if [[ $rc -eq 124 || $rc -eq 10 ]]; then
        fail "12-health.sh /api/config fetch #$idx hangs (no --max-time)"
    elif grep -q -- '--max-time' "$CURL_LOG"; then
        pass "12-health.sh /api/config fetch #$idx is bounded"
    else
        fail "12-health.sh /api/config fetch #$idx lacks --max-time"
    fi
done < <(grep -n 'curl .*api/config' "$ODS_DIR/installers/phases/12-health.sh" | cut -d: -f1 | \
         while read -r n; do sed -n "${n}p" "$ODS_DIR/installers/phases/12-health.sh"; done)
[[ $idx -ge 2 ]] || fail "expected >=2 /api/config fetches in 12-health.sh, found $idx"

#----------------------------------------------------------------------
# 3) Static sweep: no bare curl invocation may remain in either file.
#----------------------------------------------------------------------
echo "static sweep"
for f in installers/phases/12-health.sh installers/macos/ods-macos.sh; do
    bad=$(grep -nE '(^|[^"a-zA-Z_-])curl +-' "$ODS_DIR/$f" \
        | grep -v 'echo \|ai_\|# ' \
        | grep -vc 'max-time\|CURL_.*_FLAGS\|curl_args' || true)
    if [[ "$bad" -eq 0 ]]; then
        pass "$f: every curl invocation is bounded"
    else
        fail "$f: $bad curl invocation(s) lack a time bound"
    fi
done

echo ""
echo "Result: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
