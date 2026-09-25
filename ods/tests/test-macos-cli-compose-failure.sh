#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

INSTALL_DIR="$TMP_DIR/install"
BIN_DIR="$TMP_DIR/bin"
mkdir -p "$INSTALL_DIR" "$BIN_DIR"
touch "$INSTALL_DIR/docker-compose.yml" "$INSTALL_DIR/.env"

cat > "$BIN_DIR/docker" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == "info" ]]; then
  exit 0
fi
if [[ "${1:-}" == "compose" ]]; then
  exit 17
fi
exit 0
EOF
chmod +x "$BIN_DIR/docker"

if PATH="$BIN_DIR:$PATH" ODS_INSTALL_DIR="$INSTALL_DIR" \
    bash "$ROOT_DIR/installers/macos/ods-macos.sh" start open-webui \
    >"$TMP_DIR/output" 2>&1; then
  echo "FAIL: macOS start reported success after Compose failure" >&2
  cat "$TMP_DIR/output" >&2
  exit 1
fi

grep -q 'Failed to start open-webui' "$TMP_DIR/output" \
  || { echo "FAIL: start failure was not reported" >&2; cat "$TMP_DIR/output" >&2; exit 1; }

if PATH="$BIN_DIR:$PATH" ODS_INSTALL_DIR="$INSTALL_DIR" \
    bash "$ROOT_DIR/installers/macos/ods-macos.sh" restart open-webui \
    >"$TMP_DIR/restart-output" 2>&1; then
  echo "FAIL: macOS restart reported success after Compose failure" >&2
  cat "$TMP_DIR/restart-output" >&2
  exit 1
fi

grep -q 'Failed to restart open-webui' "$TMP_DIR/restart-output" \
  || { echo "FAIL: restart failure was not reported" >&2; cat "$TMP_DIR/restart-output" >&2; exit 1; }

echo "PASS: macOS start and restart fail closed when Compose fails"
