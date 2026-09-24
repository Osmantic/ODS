#!/usr/bin/env bash
# test-demo-curl-bounds.sh — every operational curl call in the demo scripts
# must carry a total-time bound so a wedged listener cannot freeze the demo.
#
# A stub `curl` records its argv and *hangs* (sleep 30) whenever --max-time is
# absent, simulating a peer that accepts TCP but never answers. The scripts are
# driven end-to-end under an outer timeout: on the unfixed code the first
# unbounded call stalls the run; fixed code completes and every recorded
# invocation is asserted to carry a bound.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODS_DIR="$(dirname "$SCRIPT_DIR")"

PASS=0
FAIL=0

pass() { PASS=$((PASS + 1)); echo "  PASS: $1"; }
fail() { FAIL=$((FAIL + 1)); echo "  FAIL: $1"; }

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

#----------------------------------------------------------------------
# Stub bin: curl logs argv; hangs when no --max-time; answers JSON to POSTs.
#----------------------------------------------------------------------
STUB_BIN="$TMP_ROOT/bin"
mkdir -p "$STUB_BIN"

cat > "$STUB_BIN/curl" <<'STUB'
#!/usr/bin/env bash
# %q escapes embedded newlines in the -d JSON payload -> one log line per call
printf '%q ' "$@" >> "${CURL_LOG:?}"
printf '\n' >> "${CURL_LOG:?}"
bounded=0
is_post=0
for a in "$@"; do
    case "$a" in
        --max-time) bounded=1 ;;
        -d|--data|--data-raw|--data-binary) is_post=1 ;;
    esac
done
if [[ "$bounded" -eq 0 ]]; then
    sleep 30   # wedged peer: connection accepted, response never arrives
fi
if [[ "$is_post" -eq 1 ]]; then
    printf '{"choices":[{"message":{"content":"stub reply"},"delta":{"content":"stub reply"}}]}\n'
fi
exit 0
STUB
chmod +x "$STUB_BIN/curl"

#----------------------------------------------------------------------
# Isolated script copies: no lib/ beside them -> registry sourcing skipped,
# so the run pre-declares the SERVICE_* maps the scripts index into.
# showcase reads $ODS_DIR/examples for the RAG/code demos.
#----------------------------------------------------------------------
RUN_DIR="$TMP_ROOT/run"
mkdir -p "$RUN_DIR/scripts" "$RUN_DIR/examples"
cp "$ODS_DIR/scripts/first-boot-demo.sh" "$RUN_DIR/scripts/"
cp "$ODS_DIR/scripts/showcase.sh" "$RUN_DIR/scripts/"
printf 'ODS demo document.\n' > "$RUN_DIR/examples/sample-doc.txt"
printf 'print("hello")\n' > "$RUN_DIR/examples/sample-code.py"

CURL_LOG="$TMP_ROOT/curl.log"
export CURL_LOG

PRELUDE='declare -A SERVICE_PORTS=(
    [llama-server]=11434 [whisper]=9000 [tts]=8880
    [n8n]=5678 [open-webui]=3000 [qdrant]=6333
)
declare -A SERVICE_NAMES=()
declare -A SERVICE_HEALTH=()
declare -a SERVICE_IDS=()'

#----------------------------------------------------------------------
# 1) first-boot-demo.sh --quick runs non-interactively through every demo
#    (probe x4, chat POST, code POST, streaming POST).
#----------------------------------------------------------------------
echo "first-boot-demo.sh --quick"
: > "$CURL_LOG"
timeout 20 env PATH="$STUB_BIN:$PATH" \
    bash -c "$PRELUDE; . \"\$0\" --quick" "$RUN_DIR/scripts/first-boot-demo.sh" \
    </dev/null >"$TMP_ROOT/first-boot.out" 2>&1
rc=$?
if [[ $rc -eq 0 ]]; then
    pass "first-boot-demo completes (no unbounded curl hang)"
else
    fail "first-boot-demo exits $rc (hang or error — see curl log)"
fi

if [[ -s "$CURL_LOG" ]]; then
    pass "first-boot-demo issued curl calls ($(wc -l < "$CURL_LOG") recorded)"
else
    fail "first-boot-demo issued no curl calls — test did not exercise the sites"
fi

unbounded=$(grep -cv -- '--max-time' "$CURL_LOG" || true)
if [[ "$unbounded" -eq 0 ]]; then
    pass "every first-boot curl call carries --max-time"
else
    fail "$unbounded first-boot curl call(s) missing --max-time"
fi

#----------------------------------------------------------------------
# 2) showcase.sh: menu 1 (chat POST), 3 (RAG POST), 4 (code POST), q.
#----------------------------------------------------------------------
echo "showcase.sh scripted menu"
: > "$CURL_LOG"
printf '1\nhi\nback\n3\nhi\nback\n4\n1\n\nq\n' | \
    timeout 20 env PATH="$STUB_BIN:$PATH" \
    bash -c "$PRELUDE; . \"\$0\"" "$RUN_DIR/scripts/showcase.sh" \
    >"$TMP_ROOT/showcase.out" 2>&1
rc=$?
if [[ $rc -eq 0 ]]; then
    pass "showcase completes (no unbounded curl hang)"
else
    fail "showcase exits $rc (hang or error — see curl log)"
fi

posts=$(grep -c -- 'chat/completions' "$CURL_LOG" || true)
if [[ "$posts" -ge 3 ]]; then
    pass "showcase exercised all $posts completion calls"
else
    fail "showcase exercised only $posts completion calls (expected >=3)"
fi

unbounded=$(grep -cv -- '--max-time' "$CURL_LOG" || true)
if [[ "$unbounded" -eq 0 ]]; then
    pass "every showcase curl call carries --max-time"
else
    fail "$unbounded showcase curl call(s) missing --max-time"
fi

#----------------------------------------------------------------------
# 3) Static sweep: every curl invocation in both scripts references a bounds
#    array — guards against future call sites regressing.
#----------------------------------------------------------------------
echo "static sweep of curl call sites"
for script in first-boot-demo.sh showcase.sh; do
    bad=$(grep -nE '(^|[^"a-zA-Z])curl -' "$ODS_DIR/scripts/$script" \
        | grep -v 'echo ' \
        | grep -vc 'CURL_\(PROBE\|GEN\)_FLAGS' || true)
    if [[ "$bad" -eq 0 ]]; then
        pass "$script: all curl call sites reference a bounds array"
    else
        fail "$script: $bad curl call site(s) lack a bounds array"
    fi
done

echo ""
echo "Result: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
