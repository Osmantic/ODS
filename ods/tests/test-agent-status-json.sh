#!/usr/bin/env bash
# Public CLI contract: host-agent status is available as one JSON document.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
INSTALL_DIR="$TMP_DIR/install"
BIN_DIR="$TMP_DIR/bin"
trap 'rm -rf "$TMP_DIR"' EXIT

mkdir -p "$INSTALL_DIR" "$BIN_DIR"
cp "$ROOT_DIR/docker-compose.base.yml" "$INSTALL_DIR/docker-compose.base.yml"
cat > "$INSTALL_DIR/.env" <<'EOF'
ODS_AGENT_BIND=127.0.0.1
ODS_AGENT_PORT=7788
EOF
cat > "$BIN_DIR/curl" <<'EOF'
#!/usr/bin/env bash
if [[ "${MOCK_AGENT_DOWN:-false}" == "true" ]]; then
    exit 7
fi
printf '%s\n' '{"status":"ok","version":"2.6.0","operations":3}'
EOF
chmod +x "$BIN_DIR/curl"

running=$(PATH="$BIN_DIR:$PATH" ODS_HOME="$INSTALL_DIR" NO_COLOR=1 \
    "$ROOT_DIR/ods-cli" agent status --json)
python3 - "$running" <<'PYEOF'
import json
import sys

status = json.loads(sys.argv[1])
assert status["status"] == "running"
assert status["port"] == "7788"
assert status["daemon_type"] in {"none", "systemd", "launchd"}
assert status["health"] == {"status": "ok", "version": "2.6.0", "operations": 3}
PYEOF

unreachable=$(PATH="$BIN_DIR:$PATH" MOCK_AGENT_DOWN=true ODS_HOME="$INSTALL_DIR" NO_COLOR=1 \
    "$ROOT_DIR/ods-cli" agent status --json)
python3 - "$unreachable" <<'PYEOF'
import json
import sys

status = json.loads(sys.argv[1])
assert status["status"] == "unreachable"
assert status["port"] == "7788"
assert status["health"] is None
PYEOF

echo "[PASS] agent status JSON output"
