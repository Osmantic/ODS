#!/usr/bin/env bash
# Disposable installed gateway used to test optional Whisper/Kokoro add-back.
set -euo pipefail

product="${ODS_ACCEPTANCE_PRODUCT_ROOT:?product checkout is required}"
harness="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
audit_root="$RUNNER_TEMP/ods-voice-addback"
export INSTALL_DIR="$audit_root/install"
export LOG_FILE="$audit_root/install.log"
key_file="$audit_root/mock.key"
mock_port=18080
mock_pid=""

cleanup() {
    if [[ -n "$mock_pid" ]]; then
        kill "$mock_pid" 2>/dev/null || true
        wait "$mock_pid" 2>/dev/null || true
    fi
}
trap cleanup EXIT

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    docker ps --format '{{.Names}} {{.Status}}' >&2 || true
    exit 1
}

diagnose_whisper() {
    local model_url='http://127.0.0.1:9000/v1/models/Systran%2Ffaster-whisper-base'
    local response="$audit_root/whisper-diagnostic-response.txt" code
    code="$(curl -sS --max-time 10 -o "$response" -w '%{http_code}' "$model_url" || true)"
    printf 'DIAG: selected Whisper model cache GET HTTP %s\n' "$code" >&2
    code="$(curl -sS --max-time 20 -X POST -o "$response" -w '%{http_code}' "$model_url" || true)"
    printf 'DIAG: selected Whisper model download POST HTTP %s\n' "$code" >&2
    python3 - "$response" <<'PY' >&2
import pathlib, re, sys
path = pathlib.Path(sys.argv[1])
body = path.read_text(errors="replace") if path.exists() else ""
body = re.sub(r"https?://\S+", "[url]", body)
body = re.sub(r"(?i)(token|authorization|key)[=:][^\s,}]+", r"\1=[redacted]", body)
print("DIAG: model POST response:", body[:300])
PY
    curl -sS --max-time 5 http://127.0.0.1:3001/api/extensions/whisper/progress \
        -o "$audit_root/whisper-progress.json" || true
    python3 - "$audit_root/whisper-progress.json" <<'PY' >&2
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
try:
    row = json.loads(path.read_text())
except (OSError, ValueError):
    row = {}
print("DIAG: extension progress:", {key: str(row.get(key, ""))[:200]
                                   for key in ("status", "phase", "error", "message")})
PY
    docker logs --tail 100 ods-whisper 2>&1 | python3 -c '
import re, sys
lines = [line.strip() for line in sys.stdin if re.search(r"error|fail|download|model|traceback", line, re.I)]
for line in lines[-15:]:
    line = re.sub(r"https?://\S+", "[url]", line)
    line = re.sub(r"(?i)(token|authorization|key)[=:][^\s,}]+", r"\1=[redacted]", line)
    print("DIAG: Whisper log:", line[:300])
' >&2 || true
}

wait_health() {
    local service="$1" url="$2" attempts="${3:-60}"
    for ((i=0; i<attempts; i++)); do
        if curl -fsS --max-time 5 "$url" >/dev/null 2>&1; then
            printf 'PASS: %s healthy\n' "$service"
            return 0
        fi
        sleep 5
    done
    fail "$service never became healthy at $url"
}

enable_library_service() {
    local service="$1" output="$audit_root/$1-enable.json" code
    code="$(curl -sS --max-time 900 -o "$output" -w '%{http_code}' \
        -X POST "http://127.0.0.1:3001/api/extensions/$service/enable" || true)"
    if [[ "$code" != 200 ]]; then
        python3 - "$output" <<'PY' >&2
import json, sys
try:
    detail = json.load(open(sys.argv[1], encoding="utf-8")).get("detail", "")
except (OSError, ValueError):
    detail = "unreadable response"
print("Library enable detail:", str(detail)[:400])
PY
        [[ "$service" != whisper ]] || diagnose_whisper
        fail "$service Library enable returned HTTP $code"
    fi
    if ! python3 - "$output" "$service" <<'PY'; then
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
assert sys.argv[2] in value.get("enabled_services", []), value
assert not value.get("failed_services"), value
PY
        [[ "$service" != whisper ]] || diagnose_whisper
        fail "$service Library enable did not succeed"
    fi
    printf 'PASS: %s enabled from installed Library\n' "$service"
}

command -v docker >/dev/null || fail 'Docker CLI missing'
docker info >/dev/null || fail 'Docker Engine unavailable'
[[ "$(git -C "$product" rev-parse HEAD)" == 911775a6d335f42e1438a4822eb0bb8483e39c98 ]] \
    || fail 'wrong product checkout'
[[ ! -e "$INSTALL_DIR" ]] || fail 'install directory is not fresh'
mkdir -p "$audit_root"
python3 - "$key_file" <<'PY'
import secrets, sys
with open(sys.argv[1], "w", encoding="ascii") as stream:
    stream.write("mock-" + secrets.token_hex(24))
PY
chmod 600 "$key_file"
python3 "$harness/mock-openai-upstream.py" --key-file "$key_file" \
    --port "$mock_port" >"$audit_root/mock.log" 2>&1 &
mock_pid=$!
wait_health 'mock upstream' "http://127.0.0.1:$mock_port/healthz" 12

if ! (cd "$product" && timeout 1500s bash install-core.sh \
    --non-interactive --skip-docker --no-pixel --gateway-only \
    --external-llm-url "http://127.0.0.1:$mock_port" \
    --external-llm-provider openai-compatible \
    --external-llm-model ods-acceptance-mock \
    --external-llm-key-file "$key_file") >>"$LOG_FILE" 2>&1; then
    fail 'fresh external gateway install failed; inspect private runner log'
fi
for optional in ods-whisper ods-tts ods-webui ods-llama-server; do
    if docker ps --format '{{.Names}}' | grep -qx "$optional"; then
        fail "fresh gateway unexpectedly started $optional"
    fi
done
printf 'PASS: fresh gateway omitted voice, WebUI, and managed llama\n'

enable_library_service whisper
wait_health Whisper http://127.0.0.1:9000/health 60
enable_library_service tts
wait_health Kokoro http://127.0.0.1:8880/health 60

# A health endpoint alone does not prove either model can be used.
speech_file="$audit_root/speech.mp3"
speech_code="$(curl -sS --max-time 480 -o "$speech_file" -w '%{http_code}' \
    -H 'Content-Type: application/json' \
    -d '{"model":"kokoro","input":"The quick brown fox jumps over the lazy dog.","voice":"af_heart","response_format":"mp3"}' \
    http://127.0.0.1:8880/v1/audio/speech || true)"
[[ "$speech_code" == 200 ]] || fail "Kokoro speech endpoint returned HTTP $speech_code"
python3 - "$speech_file" <<'PY' || fail 'Kokoro output was not MP3 audio'
import pathlib, sys
payload = pathlib.Path(sys.argv[1]).read_bytes()
assert len(payload) > 5000, len(payload)
assert payload.startswith(b"ID3") or (payload[0] == 0xFF and payload[1] & 0xE0 == 0xE0)
PY
printf 'PASS: Kokoro generated nontrivial MP3 speech\n'

transcript_file="$audit_root/transcription.json"
transcript_code="$(curl -sS --max-time 900 -o "$transcript_file" -w '%{http_code}' \
    -F "file=@$speech_file;type=audio/mpeg" \
    -F 'model=Systran/faster-whisper-base' \
    http://127.0.0.1:9000/v1/audio/transcriptions || true)"
if [[ "$transcript_code" != 200 ]]; then
    python3 - "$transcript_file" <<'PY' >&2
import pathlib, sys
path = pathlib.Path(sys.argv[1])
print("Whisper response:", path.read_text(errors="replace")[:300] if path.exists() else "missing")
PY
    fail "Whisper transcription endpoint returned HTTP $transcript_code"
fi
python3 - "$transcript_file" <<'PY' || fail 'Whisper transcript did not contain the spoken phrase'
import json, sys
text = json.load(open(sys.argv[1], encoding="utf-8")).get("text", "").lower()
assert all(token in text for token in ("quick", "brown", "fox")), text
print("PASS: Whisper transcribed Kokoro speech")
PY

sentinel="$INSTALL_DIR/data/whisper/ods-voice-retention.txt"
printf 'retain owner voice data\n' >"$sentinel"
curl -fsS --max-time 120 -X POST http://127.0.0.1:3001/api/extensions/whisper/disable \
    >"$audit_root/whisper-disable.json" || fail 'Whisper disable failed'
enable_library_service whisper
[[ "$(cat "$sentinel")" == 'retain owner voice data' ]] || fail 'Whisper data was lost'
printf 'PASS: Whisper data retained across Disable/re-enable\n'
