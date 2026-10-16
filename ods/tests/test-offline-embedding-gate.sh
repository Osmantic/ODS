#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE="$ROOT_DIR/installers/phases/09-offline.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# The download must name a fixed revision and carry the hash it is checked against.
grep -Eq '^    EMBED_URL="https://huggingface\.co/nomic-ai/nomic-embed-text-v1\.5-GGUF/resolve/[0-9a-f]{40}/nomic-embed-text-v1\.5\.Q4_K_M\.gguf"$' "$PHASE" \
    || { echo "FAIL: offline embedding URL is not pinned to a 40-hex revision" >&2; exit 1; }
pinned="$(sed -n 's/^    EMBED_SHA256="\([0-9a-f]\{64\}\)"$/\1/p' "$PHASE")"
[[ -n "$pinned" ]] || { echo "FAIL: offline embedding has no pinned SHA-256" >&2; exit 1; }

mkdir -p "$tmp/bin" "$tmp/install"
embed="$tmp/install/models/embeddings/nomic-embed-text-v1.5.Q4_K_M.gguf"
cat > "$tmp/run-phase.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="__ROOT__/installers"
INSTALL_DIR="__INSTALL__"
OFFLINE_MODE=true DRY_RUN=false ENABLE_VOICE=false
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
sed -i "s#__ROOT__#$ROOT_DIR#g; s#__INSTALL__#$tmp/install#g" "$tmp/run-phase.sh"
chmod +x "$tmp/run-phase.sh"

# Stand-in for the real 84 MB asset: sha256sum reports the pinned hash for
# this payload only, and the real digest for anything else.
printf 'GGUF\0pinned-test-payload' > "$tmp/good.gguf"
cat > "$tmp/bin/sha256sum" <<EOF
#!/usr/bin/env bash
file="\${@: -1}"
if cmp -s -- "\$file" "$tmp/good.gguf"; then
    echo "$pinned  \$file"
else
    exec /usr/bin/sha256sum "\$@"
fi
EOF
chmod +x "$tmp/bin/sha256sum"

# Fake curl that writes $1 to its -o target.
serve() {
    cat > "$tmp/bin/curl" <<EOF
#!/usr/bin/env bash
while ((\$#)); do
    [[ "\$1" == "-o" ]] && { out="\$2"; shift 2; continue; }
    shift
done
cp -- "$1" "\$out"
EOF
    chmod +x "$tmp/bin/curl"
}
run_phase() { PATH="$tmp/bin:/usr/bin:/bin" "$tmp/run-phase.sh" >/dev/null 2>&1; }

# HTTP/download failure must fail closed and must not advertise offline readiness.
cat > "$tmp/bin/curl" <<'EOF'
#!/usr/bin/env bash
exit 22
EOF
chmod +x "$tmp/bin/curl"
if run_phase; then
    echo "FAIL: failed download was accepted" >&2
    exit 1
fi
[[ ! -e "$tmp/install/.offline-mode" ]]
[[ ! -e "$embed" ]]

# A failed rerun must invalidate a stale readiness marker as well.
touch "$tmp/install/.offline-mode"
if run_phase; then
    echo "FAIL: failed rerun was accepted" >&2
    exit 1
fi
[[ ! -e "$tmp/install/.offline-mode" ]]

# A GGUF file that does not match the pinned hash (e.g. an upstream
# re-upload) must be rejected, not installed.
printf 'GGUF\0re-uploaded-payload' > "$tmp/other.gguf"
serve "$tmp/other.gguf"
if run_phase; then
    echo "FAIL: GGUF with the wrong SHA-256 was accepted" >&2
    exit 1
fi
[[ ! -e "$tmp/install/.offline-mode" ]]
[[ ! -e "$embed" ]]
compgen -G "$embed.tmp.*" >/dev/null && { echo "FAIL: rejected download left a temp file" >&2; exit 1; }

# The pinned payload is installed atomically and enables the marker.
serve "$tmp/good.gguf"
run_phase
[[ -e "$tmp/install/.offline-mode" ]]
cmp -s -- "$embed" "$tmp/good.gguf"

# A previously installed file that no longer matches is replaced on rerun.
cp -- "$tmp/other.gguf" "$embed"
rm -f -- "$tmp/install/.offline-mode"
run_phase
[[ -e "$tmp/install/.offline-mode" ]]
cmp -s -- "$embed" "$tmp/good.gguf"

# A matching file is kept without downloading again.
cat > "$tmp/bin/curl" <<'EOF'
#!/usr/bin/env bash
exit 22
EOF
chmod +x "$tmp/bin/curl"
run_phase
[[ -e "$tmp/install/.offline-mode" ]]
echo "PASS: offline embedding gate"
