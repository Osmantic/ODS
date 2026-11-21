#!/usr/bin/env bash
# The shared manager now waits for verified shutdown before bootstrap. This
# wrapper must forward the full operation and propagate manager failures.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
mkdir -p "$tmp/Install & Spaces/bin"
export BRIDGE_TEST_ARGS="$tmp/args.json"
cat > "$tmp/Install & Spaces/bin/macos_bridge_service.py" <<'PY'
import json,os,sys
with open(os.environ['BRIDGE_TEST_ARGS'],'w') as stream:
    json.dump(sys.argv[1:],stream)
sys.exit(int(os.environ.get('BRIDGE_TEST_FAIL','0')))
PY
ai_err() { printf 'ERROR %s\n' "$*" >&2; }
ai_ok() { :; }
source "$root/installers/macos/lib/bridge-manager.sh"
run_bridge() {
    macos_configure_port_bridge "$1" com.ods.test "$tmp/bridge.plist" "$tmp/bridge.log" \
        "test bridge" 192.168.64.1 8080 8081 192.168.64.2 "$tmp/Install & Spaces"
}
run_bridge true
python3 - "$BRIDGE_TEST_ARGS" "$tmp" <<'PY'
import json,sys
from pathlib import Path
args=json.loads(Path(sys.argv[1]).read_text())
root=Path(sys.argv[2])
assert args == ['configure',str(root/'Install & Spaces'),'/usr/bin/python3','com.ods.test',
                str(root/'bridge.plist'),str(root/'bridge.log'),
                str(root/'Install & Spaces/bin/ods-macos-llm-bridge.py'),
                '--listen-host','192.168.64.1','--listen-port','8080',
                '--target-port','8081','--allow-peer','192.168.64.2'], args
PY
run_bridge false
python3 - "$BRIDGE_TEST_ARGS" <<'PY'
import json,sys
from pathlib import Path
args=json.loads(Path(sys.argv[1]).read_text())
assert args[0]=='stop' and len(args)==6, args
PY
export BRIDGE_TEST_FAIL=7
if run_bridge true; then
    echo 'bridge manager failure was accepted' >&2
    exit 1
fi
if run_bridge false; then
    echo 'bridge stop failure was accepted' >&2
    exit 1
fi
echo 'macOS bridge manager dispatch contract passed'
