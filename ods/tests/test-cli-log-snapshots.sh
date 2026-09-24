#!/usr/bin/env bash
# Public CLI contract: log snapshots forward filters without forcing follow mode.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
INSTALL_DIR="$TMP_DIR/install"
BIN_DIR="$TMP_DIR/bin"
ARGS_FILE="$TMP_DIR/docker-args"
trap 'rm -rf "$TMP_DIR"' EXIT

mkdir -p "$INSTALL_DIR" "$BIN_DIR"
cp "$ROOT_DIR/docker-compose.base.yml" "$INSTALL_DIR/docker-compose.base.yml"
: > "$INSTALL_DIR/.env"
cat > "$BIN_DIR/docker" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$@" > "$MOCK_DOCKER_ARGS"
EOF
chmod +x "$BIN_DIR/docker"

PATH="$BIN_DIR:$PATH" MOCK_DOCKER_ARGS="$ARGS_FILE" ODS_HOME="$INSTALL_DIR" NO_COLOR=1 \
    "$ROOT_DIR/ods-cli" logs llama-server 25 --since 30m --until 5m --timestamps --no-follow

grep -qx -- 'logs' "$ARGS_FILE"
grep -qx -- '--since' "$ARGS_FILE"
grep -qx -- '30m' "$ARGS_FILE"
grep -qx -- '--until' "$ARGS_FILE"
grep -qx -- '5m' "$ARGS_FILE"
grep -qx -- '--timestamps' "$ARGS_FILE"
grep -qx -- '--tail' "$ARGS_FILE"
grep -qx -- '25' "$ARGS_FILE"
grep -qx -- 'llama-server' "$ARGS_FILE"
if awk '$0 == "logs" { after_logs = 1; next } after_logs && $0 == "-f" { found = 1 } END { exit !found }' "$ARGS_FILE"; then
    echo "[FAIL] --no-follow still passed -f" >&2
    exit 1
fi

PATH="$BIN_DIR:$PATH" MOCK_DOCKER_ARGS="$ARGS_FILE" ODS_HOME="$INSTALL_DIR" NO_COLOR=1 \
    "$ROOT_DIR/ods-cli" logs llama-server
awk '$0 == "logs" { after_logs = 1; next } after_logs && $0 == "-f" { found = 1 } END { exit !found }' "$ARGS_FILE"
grep -qx -- '100' "$ARGS_FILE"

if PATH="$BIN_DIR:$PATH" MOCK_DOCKER_ARGS="$ARGS_FILE" ODS_HOME="$INSTALL_DIR" NO_COLOR=1 \
    "$ROOT_DIR/ods-cli" logs llama-server invalid --no-follow >/dev/null 2>&1; then
    echo "[FAIL] invalid line count was accepted" >&2
    exit 1
fi

echo "[PASS] CLI log snapshots and default follow mode"
