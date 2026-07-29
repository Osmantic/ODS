#!/usr/bin/env bash
# Regression: CLI lifecycle commands must not race the background bootstrap
# model upgrade near hot-swap, but must also not block for hours while a large
# full-model download is still safely writing only its .part file.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

pass() { echo -e "  ${GREEN}PASS${NC} $1"; }
fail() {
    echo -e "  ${RED}FAIL${NC} $1" >&2
    [[ -f "${OUT:-}" ]] && sed 's/^/[ods] /' "$OUT" >&2
    [[ -f "${DOCKER_LOG:-}" ]] && sed 's/^/[docker] /' "$DOCKER_LOG" >&2
    exit 1
}

TMP="$(mktemp -d "${TMPDIR:-/tmp}/ods-cli-bootstrap-wait.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

INSTALL_DIR="$TMP/install"
BIN_DIR="$TMP/bin"
OUT="$TMP/ods.out"
DOCKER_LOG="$TMP/docker.log"
mkdir -p "$INSTALL_DIR/lib" "$INSTALL_DIR/bin" "$INSTALL_DIR/data" "$BIN_DIR"
cp "$ROOT_DIR/ods-cli" "$INSTALL_DIR/ods-cli"
cp "$ROOT_DIR"/lib/*.sh "$INSTALL_DIR/lib/"
: > "$INSTALL_DIR/docker-compose.base.yml"

cat > "$INSTALL_DIR/.env" <<'EOF'
ODS_VERSION=2.0.0
GPU_BACKEND=nvidia
TIER=4
LLM_MODEL=qwen3.5-2b
GGUF_FILE=Qwen3.5-2B-Q4_K_M.gguf
MAX_CONTEXT=65536
CTX_SIZE=65536
SHIELD_API_KEY=test-fixture-key
EOF

cat > "$INSTALL_DIR/data/bootstrap-status.json" <<'EOF'
{
  "status": "downloading",
  "model": "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
  "percent": 99.0,
  "bytesDownloaded": 99,
  "bytesTotal": 100,
  "speedBytesPerSec": 1,
  "eta": "",
  "updatedAt": "2026-07-19T00:00:00Z"
}
EOF

cat > "$BIN_DIR/docker" <<'SH'
#!/usr/bin/env bash
echo "DOCKER_ARGS: $*" >> "${DOCKER_LOG:?}"
case "${1:-}" in
    info)
        echo "8"
        ;;
    ps)
        exit 0
        ;;
    compose)
        if [[ " $* " == *" up "* ]]; then
            echo "COMPOSE_GGUF_FILE=${GGUF_FILE:-}" >> "${DOCKER_LOG:?}"
        fi
        exit 0
        ;;
esac
exit 0
SH
chmod +x "$BIN_DIR/docker"

cat > "$BIN_DIR/ps" <<'SH'
#!/usr/bin/env bash
if [[ "${ODS_FAKE_BOOTSTRAP_PROCESS:-}" == "1" && "${1:-}" == "-eo" && "${2:-}" == "args=" ]]; then
    echo "bash ${ODS_HOME:?}/scripts/bootstrap-upgrade.sh ${ODS_HOME:?} Qwen3.6-35B-A3B-UD-Q4_K_M.gguf --fixture"
    exit 0
fi
if [[ -x /bin/ps ]]; then
    exec /bin/ps "$@"
fi
exit 0
SH
chmod +x "$BIN_DIR/ps"

cat > "$BIN_DIR/sleep" <<'SH'
#!/usr/bin/env bash
cat > "${ODS_HOME:?}/data/bootstrap-status.json" <<'JSON'
{
  "status": "complete",
  "model": "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
  "percent": 100,
  "bytesDownloaded": 100,
  "bytesTotal": 100,
  "speedBytesPerSec": 0,
  "eta": "",
  "updatedAt": "2026-07-19T00:00:01Z"
}
JSON
awk '
  /^LLM_MODEL=/ { print "LLM_MODEL=qwen3.6-35b-a3b"; next }
  /^GGUF_FILE=/ { print "GGUF_FILE=Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"; next }
  /^MAX_CONTEXT=/ { print "MAX_CONTEXT=131072"; next }
  /^CTX_SIZE=/ { print "CTX_SIZE=131072"; next }
  { print }
' "${ODS_HOME:?}/.env" > "${ODS_HOME:?}/.env.tmp"
mv "${ODS_HOME:?}/.env.tmp" "${ODS_HOME:?}/.env"
exit 0
SH
chmod +x "$BIN_DIR/sleep"

echo ""
echo "=== CLI bootstrap compose wait test ==="
echo ""

grep -q 'ODS_CLI_BOOTSTRAP_COMPOSE_WAIT_SECONDS:-1800' "$ROOT_DIR/ods-cli" \
    || fail "bootstrap compose wait default must cover release lifecycle's 1800s update window"
grep -q 'max_wait=1800' "$ROOT_DIR/ods-cli" \
    || fail "invalid bootstrap compose wait override must fall back to 1800s"

pass "bootstrap compose wait default matches release lifecycle update window"

PATH="$BIN_DIR:$PATH" \
ODS_HOME="$INSTALL_DIR" \
DOCKER_LOG="$DOCKER_LOG" \
ODS_FAKE_BOOTSTRAP_PROCESS=1 \
ODS_CLI_BOOTSTRAP_COMPOSE_WAIT_INTERVAL=1 \
bash "$INSTALL_DIR/ods-cli" restart > "$OUT" 2>&1 || fail "ods restart failed"

grep -q 'Model Upgrade: download nearly complete; waiting before restart touches llama-server' "$OUT" \
    || fail "restart did not wait while bootstrap upgrade was near hot-swap"

grep -q 'COMPOSE_GGUF_FILE=Qwen3.6-35B-A3B-UD-Q4_K_M.gguf' "$DOCKER_LOG" \
    || fail "compose did not receive the reloaded full-model GGUF_FILE"

if grep -q 'COMPOSE_GGUF_FILE=Qwen3.5-2B-Q4_K_M.gguf' "$DOCKER_LOG"; then
    fail "compose still received the stale bootstrap GGUF_FILE"
fi

pass "restart waits for bootstrap upgrade and reloads .env before compose"

cat > "$INSTALL_DIR/.env" <<'EOF'
ODS_VERSION=2.0.0
GPU_BACKEND=nvidia
TIER=4
LLM_MODEL=qwen3.5-2b
GGUF_FILE=Qwen3.5-2B-Q4_K_M.gguf
MAX_CONTEXT=65536
CTX_SIZE=65536
SHIELD_API_KEY=test-fixture-key
EOF

cat > "$INSTALL_DIR/data/bootstrap-status.json" <<'EOF'
{
  "status": "downloading",
  "model": "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
  "percent": 0.4,
  "bytesDownloaded": 186867712,
  "bytesTotal": 48528320544,
  "speedBytesPerSec": 3041280,
  "eta": "264m 55s",
  "updatedAt": "2026-07-19T00:00:02Z"
}
EOF
: > "$DOCKER_LOG"

PATH="$BIN_DIR:$PATH" \
ODS_HOME="$INSTALL_DIR" \
DOCKER_LOG="$DOCKER_LOG" \
ODS_FAKE_BOOTSTRAP_PROCESS=1 \
ODS_CLI_BOOTSTRAP_COMPOSE_WAIT_INTERVAL=1 \
bash "$INSTALL_DIR/ods-cli" restart > "$OUT" 2>&1 || fail "ods restart blocked early background download"

grep -q 'Model Upgrade: downloading in background; continuing with restart before hot-swap begins' "$OUT" \
    || fail "restart did not continue during early background download"

grep -q 'COMPOSE_GGUF_FILE=Qwen3.5-2B-Q4_K_M.gguf' "$DOCKER_LOG" \
    || fail "compose did not continue with bootstrap GGUF during early background download"

if grep -q 'COMPOSE_GGUF_FILE=Qwen3.6-35B-A3B-UD-Q4_K_M.gguf' "$DOCKER_LOG"; then
    fail "compose unexpectedly waited for full-model GGUF during early background download"
fi

pass "restart proceeds during early background download without waiting for full model"

cat > "$INSTALL_DIR/.env" <<'EOF'
ODS_VERSION=2.0.0
GPU_BACKEND=nvidia
TIER=4
LLM_MODEL=qwen3.6-35b-a3b
GGUF_FILE=Qwen3.6-35B-A3B-UD-Q4_K_M.gguf
MAX_CONTEXT=131072
CTX_SIZE=131072
SHIELD_API_KEY=test-fixture-key
EOF

cat > "$INSTALL_DIR/data/bootstrap-status.json" <<'EOF'
{
  "status": "swapping",
  "model": "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
  "percent": 100,
  "bytesDownloaded": 100,
  "bytesTotal": 100,
  "speedBytesPerSec": 0,
  "eta": "",
  "updatedAt": "2026-07-19T00:00:02Z"
}
EOF
mkdir -p "$INSTALL_DIR/data/models"
dd if=/dev/zero of="$INSTALL_DIR/data/models/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf" bs=100 count=1 >/dev/null 2>&1
: > "$DOCKER_LOG"

PATH="$BIN_DIR:$PATH" \
ODS_HOME="$INSTALL_DIR" \
DOCKER_LOG="$DOCKER_LOG" \
ODS_CLI_BOOTSTRAP_COMPOSE_WAIT_SECONDS=0 \
bash "$INSTALL_DIR/ods-cli" restart > "$OUT" 2>&1 || fail "ods restart rejected stale settled bootstrap status"

grep -q 'stale (swapping) but the full model is already configured' "$OUT" \
    || fail "restart did not identify stale settled bootstrap status"

grep -q 'COMPOSE_GGUF_FILE=Qwen3.6-35B-A3B-UD-Q4_K_M.gguf' "$DOCKER_LOG" \
    || fail "restart did not continue with full-model GGUF after stale settled status"

pass "restart treats stale settled bootstrap status as safe"
