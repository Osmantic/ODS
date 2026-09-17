#!/usr/bin/env bash
# Process-level regression for per-run ods-test-functional audio workspaces.

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FUNCTIONAL_TEST="$ROOT_DIR/scripts/ods-test-functional.sh"

PASS=0
FAIL=0

pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; echo "       $2"; FAIL=$((FAIL + 1)); }

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT
FAKE_BIN="$WORKDIR/bin"
SCRATCH="$WORKDIR/scratch"
CURL_LOG="$WORKDIR/curl-output-paths.log"
mkdir -p "$FAKE_BIN" "$SCRATCH"

cat > "$FAKE_BIN/curl" <<'FAKE_CURL'
#!/usr/bin/env bash
set -euo pipefail

url=""
output_file=""
wants_status=0
while (($#)); do
    case "$1" in
        http://*) url="$1" ;;
        -o)
            shift
            output_file="$1"
            ;;
        -w) wants_status=1; shift ;;
    esac
    shift
done

case "$url" in
    */v1/models)
        printf '%s' '{"data":[{"id":"fixture"}]}'
        ;;
    */v1/chat/completions)
        printf '%s' '{"choices":[{"message":{"content":"4"}}]}'
        ;;
    */embed|*/)
        printf '%s' '[[0.1,0.2]]'
        ;;
    */v1/audio/speech)
        printf '%s\n' "$output_file" >> "$CURL_LOG"
        head -c 2048 /dev/zero > "$output_file"
        if ((wants_status)); then
            printf '%s' '200'
        fi
        ;;
    */v1/audio/transcriptions)
        printf '%s' '{"text":"hello world"}'
        ;;
    *)
        printf 'unexpected URL: %s\n' "$url" >&2
        exit 2
        ;;
esac
FAKE_CURL

cat > "$FAKE_BIN/file" <<'FAKE_FILE'
#!/usr/bin/env bash
printf '%s: RIFF (little-endian) data, WAVE audio\n' "$1"
FAKE_FILE
chmod +x "$FAKE_BIN/curl" "$FAKE_BIN/file"

run_fixture() {
    local output="$1"
    TMPDIR="$SCRATCH" \
        PATH="$FAKE_BIN:$PATH" \
        CURL_LOG="$CURL_LOG" \
        LLM_URL="http://llm.test" \
        TTS_URL="http://tts.test" \
        EMBEDDING_URL="http://embeddings.test" \
        WHISPER_URL="http://whisper.test" \
        bash "$FUNCTIONAL_TEST" > "$output" 2>&1
}

run_fixture "$WORKDIR/run-one.log" &
pid_one=$!
run_fixture "$WORKDIR/run-two.log" &
pid_two=$!

wait "$pid_one"
rc_one=$?
wait "$pid_two"
rc_two=$?

if [[ "$rc_one" -eq 0 && "$rc_two" -eq 0 ]]; then
    pass "concurrent functional runs complete successfully"
else
    fail "concurrent functional runs complete successfully" \
        "exit codes: $rc_one, $rc_two; logs: $WORKDIR/run-one.log $WORKDIR/run-two.log"
fi

mapfile -t output_paths < "$CURL_LOG"
unique_count="$(printf '%s\n' "${output_paths[@]}" | sort -u | wc -l | tr -d ' ')"
if [[ "${#output_paths[@]}" -eq 4 && "$unique_count" -eq 4 ]]; then
    pass "each run uses distinct TTS and Whisper artifacts"
else
    fail "each run uses distinct TTS and Whisper artifacts" \
        "expected 4 unique paths, got ${#output_paths[@]} paths / $unique_count unique"
fi

paths_are_scoped=1
for output_path in "${output_paths[@]}"; do
    if [[ "$output_path" != "$SCRATCH"/ods-functional.*/tts-output.wav &&
          "$output_path" != "$SCRATCH"/ods-functional.*/whisper-input.wav ]]; then
        paths_are_scoped=0
    fi
done
if ((paths_are_scoped)); then
    pass "generated audio stays inside per-run workspaces"
else
    fail "generated audio stays inside per-run workspaces" "paths: ${output_paths[*]}"
fi

leftovers="$(find "$SCRATCH" -mindepth 1 -print -quit)"
if [[ -z "$leftovers" ]]; then
    pass "exit traps remove functional-test artifacts"
else
    fail "exit traps remove functional-test artifacts" "left behind: $leftovers"
fi

echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
