#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
INSTALL_DIR="$TMP/install"
ODS_LOG_FILE="$TMP/install.log"
eval "$(sed -n '/^_ensure_macos_agent_python() {/,/^}$/p' "$ROOT/installers/macos/install-macos.sh")"
bootstrap() {
    [[ "$1 $2" == '-m venv' ]] || return 1
    mkdir -p "$3/bin"
    cat > "$3/bin/python" <<'PYTHON'
#!/bin/sh
case "$*" in
  '-c import yaml, huggingface_hub, hf_xet') test -f "${0}.installed" ;;
  '-m pip install --quiet pyyaml huggingface_hub[hf_xet]>=0.27') touch "${0}.installed" ;;
  *) exit 1 ;;
esac
PYTHON
    chmod +x "$3/bin/python"
}
_ensure_macos_agent_python bootstrap
[[ "$AGENT_PYTHON" == "$INSTALL_DIR/.venv/host-agent/bin/python" ]]
bootstrap() { return 1; }
_ensure_macos_agent_python bootstrap
rm "$AGENT_PYTHON.installed"
chmod -x "$AGENT_PYTHON"
if _ensure_macos_agent_python bootstrap; then
    echo 'Expected venv creation failure to propagate' >&2
    exit 1
fi
echo '[PASS] isolated host-agent runtime creation, reuse and failure handling'
