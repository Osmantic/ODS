#!/usr/bin/env bash
# Regression: test_whisper_functional must let curl generate the multipart
# boundary. An explicit `-H "Content-Type: multipart/form-data"` overrides the
# header curl builds for -F parts — the boundary parameter is then missing, so
# a healthy Whisper server cannot parse the upload and the functional test
# reports a false failure.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT_DIR/scripts/ods-test-functional.sh"

fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

[[ -f "$SCRIPT" ]] || fail "missing $SCRIPT"

# Static guard: no explicit multipart/form-data Content-Type may coexist with
# -F uploads in this script — curl must own the boundary parameter.
if grep -n 'multipart/form-data' "$SCRIPT" | grep -q 'Content-Type'; then
    fail "script pins a boundary-less multipart/form-data Content-Type"
fi
pass "no explicit multipart/form-data Content-Type header in the script"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

# Isolated copy: no lib/service-registry.sh next to it, so the script uses
# default ports and never touches the real registry.
mkdir -p "$tmp_dir/scripts"
cp "$SCRIPT" "$tmp_dir/scripts/ods-test-functional.sh"

curl_log="$tmp_dir/curl-args.log"
bin="$tmp_dir/bin"
mkdir -p "$bin"
cat > "$bin/curl" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$curl_log"
out=""
prev=""
for arg in "\$@"; do
    if [[ "\$prev" == "-o" ]]; then out="\$arg"; fi
    prev="\$arg"
done
case "\$*" in
    *audio/speech*)
        [[ -n "\$out" ]] && head -c 2048 /dev/zero > "\$out"
        printf '200' ;;
    *audio/transcriptions*)
        printf '{"text":"hello world"}' ;;
    *) : ;;
esac
exit 0
EOF
chmod +x "$bin/curl"
# TTS validation uses `file`; provide a stub that reports audio.
cat > "$bin/file" <<'EOF'
#!/usr/bin/env bash
echo "RIFF (little-endian) data, WAVE audio"
EOF
chmod +x "$bin/file"

PATH="$bin:$PATH" bash "$tmp_dir/scripts/ods-test-functional.sh" >"$tmp_dir/out.log" 2>&1 || true

grep -q 'audio/transcriptions' "$curl_log" \
    || fail "whisper transcription request was never attempted; got: $(cat "$tmp_dir/out.log")"

# The recorded argv of the transcription call must not carry a manual
# Content-Type — with -F present, that would strip curl's boundary.
transcription_line="$(grep 'audio/transcriptions' "$curl_log" | head -1)"
if [[ "$transcription_line" == *"multipart/form-data"* ]]; then
    fail "transcription request still pins multipart/form-data: $transcription_line"
fi
[[ "$transcription_line" == *"-F"* ]] \
    || fail "transcription request lost its -F upload parts: $transcription_line"
pass "transcription request uses -F without a boundary-less Content-Type"

grep -q "Whisper" "$tmp_dir/out.log" || fail "whisper test section missing from output"
pass "whisper functional test ran end-to-end against the stub"
