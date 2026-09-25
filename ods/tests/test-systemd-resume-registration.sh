#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="$ROOT_DIR/installers/lib/detection.sh"

grep -q 'if ! ods_sudo systemctl daemon-reload' "$SOURCE"
grep -q 'if ! ods_sudo systemctl enable "\${svc_name}.service"' "$SOURCE"
grep -q 'installation cannot safely resume after reboot' "$SOURCE"

echo '[PASS] Secure Boot auto-resume registration fails closed'
