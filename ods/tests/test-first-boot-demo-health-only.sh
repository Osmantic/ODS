#!/usr/bin/env bash
# Public CLI coverage for the non-interactive first-boot health snapshot.

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT_DIR/scripts/first-boot-demo.sh"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT
mkdir -p "$WORKDIR/bin" "$WORKDIR/root/scripts"
cp "$SCRIPT" "$WORKDIR/root/scripts/first-boot-demo.sh"
SCRIPT="$WORKDIR/root/scripts/first-boot-demo.sh"

cat > "$WORKDIR/bin/clear" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
cat > "$WORKDIR/bin/curl" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$CURL_LOG"
case "$*" in
  *http://llm.test/health*|*http://webui.test/*) exit 0 ;;
  *) exit 22 ;;
esac
EOF
chmod +x "$WORKDIR/bin/clear" "$WORKDIR/bin/curl"
cat > "$WORKDIR/bin/jq" <<'EOF'
#!/usr/bin/env bash
echo called >> "$JQ_LOG"
exit 99
EOF
chmod +x "$WORKDIR/bin/jq"

export CURL_LOG="$WORKDIR/curl.log"
export JQ_LOG="$WORKDIR/jq.log"
OUT="$(
    PATH="$WORKDIR/bin:/usr/bin:/bin" \
    LLM_URL="http://llm.test" WEBUI_URL="http://webui.test" \
    WHISPER_URL="http://whisper.test" PIPER_URL="http://tts.test" \
    N8N_URL="http://n8n.test" \
        bash "$SCRIPT" --health-only 2>&1
)"
RC=$?

[[ "$RC" -eq 0 ]] || { echo "[FAIL] health-only exit: $RC"; exit 1; }
[[ "$OUT" == *"Core services are ready"* ]] || { echo "[FAIL] readiness summary missing"; exit 1; }
if grep -q '/v1/chat/completions' "$CURL_LOG"; then
    echo "[FAIL] health-only sent a model prompt"
    exit 1
fi
if [[ -e "$JQ_LOG" ]]; then
    echo "[FAIL] health-only invoked jq"
    exit 1
fi

echo "[PASS] health-only checks services without jq, prompts, or interaction"
