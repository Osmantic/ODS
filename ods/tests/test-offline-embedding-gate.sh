#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

mkdir -p "$tmp/bin" "$tmp/install"
cat > "$tmp/run-phase.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="__ROOT__/installers"
INSTALL_DIR="__INSTALL__"
OFFLINE_MODE=true DRY_RUN=false ENABLE_OPENCLAW=false ENABLE_VOICE=false
LOG_FILE="$INSTALL_DIR/install.log"
ods_progress() { :; }
chapter() { :; }
ai() { :; }
ai_ok() { :; }
ai_warn() { :; }
log() { :; }
_sed_i() { :; }
error() { echo "$1" >&2; exit 1; }
source "$SCRIPT_DIR/phases/09-offline.sh"
EOF
# BSD sed (macOS) requires an argument to -i; a bare -i swallows the script as
# the backup suffix. `-i.bak` is accepted by both, so write a suffix and drop
# the file. Same form already used by test-validate-env.sh.
sed -i.bak "s#__ROOT__#$ROOT_DIR#g; s#__INSTALL__#$tmp/install#g" "$tmp/run-phase.sh"
rm -f "$tmp/run-phase.sh.bak"
chmod +x "$tmp/run-phase.sh"

# HTTP/download failure must fail closed and must not advertise offline readiness.
cat > "$tmp/bin/curl" <<'EOF'
#!/usr/bin/env bash
exit 22
EOF
chmod +x "$tmp/bin/curl"
if PATH="$tmp/bin:/usr/bin:/bin" "$tmp/run-phase.sh" >/dev/null 2>&1; then
    echo "FAIL: failed download was accepted" >&2
    exit 1
fi
[[ ! -e "$tmp/install/.offline-mode" ]]
[[ ! -e "$tmp/install/models/embeddings/nomic-embed-text-v1.5.Q4_K_M.gguf" ]]

# A failed rerun must invalidate a stale readiness marker as well.
touch "$tmp/install/.offline-mode"
if PATH="$tmp/bin:/usr/bin:/bin" "$tmp/run-phase.sh" >/dev/null 2>&1; then
    echo "FAIL: failed rerun was accepted" >&2
    exit 1
fi
[[ ! -e "$tmp/install/.offline-mode" ]]

# A valid GGUF response is installed atomically and enables the marker.
cat > "$tmp/bin/curl" <<'EOF'
#!/usr/bin/env bash
while (($#)); do
    [[ "$1" == "-o" ]] && { out="$2"; shift 2; continue; }
    shift
done
printf 'GGUF\0valid-test-payload' > "$out"
EOF
chmod +x "$tmp/bin/curl"
PATH="$tmp/bin:/usr/bin:/bin" "$tmp/run-phase.sh" >/dev/null
[[ -e "$tmp/install/.offline-mode" ]]
head -c 4 "$tmp/install/models/embeddings/nomic-embed-text-v1.5.Q4_K_M.gguf" | grep -qx GGUF
echo "PASS: offline embedding gate"
