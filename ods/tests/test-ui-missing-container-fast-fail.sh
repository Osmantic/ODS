#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

export LOG_FILE="$TMP_DIR/install.log"
touch "$LOG_FILE" "$TMP_DIR/curl.calls" "$TMP_DIR/sleep.calls"

cat > "$TMP_DIR/docker" <<'MOCK'
#!/usr/bin/env bash
if [[ "${1:-}" == "inspect" ]]; then
    # Docker 29.8 may write a blank line before its failing diagnostic.
    printf '\n'
    exit 1
fi
exit 2
MOCK

cat > "$TMP_DIR/curl" <<MOCK
#!/usr/bin/env bash
printf 'call\n' >> '$TMP_DIR/curl.calls'
exit 7
MOCK

cat > "$TMP_DIR/timeout" <<'MOCK'
#!/usr/bin/env bash
shift
exec "$@"
MOCK

chmod +x "$TMP_DIR/docker" "$TMP_DIR/curl" "$TMP_DIR/timeout"
export PATH="$TMP_DIR:$PATH"
export DOCKER_CMD="$TMP_DIR/docker"

GRN=""; BGRN=""; DGRN=""; AMB=""; WHT=""; RED=""; NC=""; CURSOR=""
VERSION="test"; INTERACTIVE="false"; DRY_RUN=false

sleep() {
    printf '%s\n' "$1" >> "$TMP_DIR/sleep.calls"
}

# shellcheck source=/dev/null
source "$ROOT_DIR/installers/lib/ui.sh"

if check_service "ComfyUI" "http://127.0.0.1:8188/" 3 1 "ods-comfyui"; then
    echo "[FAIL] missing container was reported healthy" >&2
    exit 1
fi

[[ "$(wc -l < "$TMP_DIR/curl.calls")" -eq 1 ]] || {
    echo "[FAIL] missing container was probed more than once" >&2
    exit 1
}
[[ ! -s "$TMP_DIR/sleep.calls" ]] || {
    echo "[FAIL] missing container entered the retry sleep loop" >&2
    exit 1
}

echo "[PASS] Docker 29 blank-line inspect failures stop health retries immediately"
