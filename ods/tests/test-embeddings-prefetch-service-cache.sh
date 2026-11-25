#!/usr/bin/env bash
# ============================================================================
# Embeddings prefetch on installer reruns
# ============================================================================
# Embeddings added from Extensions after install have the TEI container
# download the model into data/embeddings as root. A later installer rerun
# (update) must leave that service-owned cache alone instead of failing on
# "Permission denied", while a fresh install still prefetches and still stops
# when the prefetch fails.
# ============================================================================

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PASS=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1" >&2; exit 1; }

function_block() {
    awk -v signature="^${1}[(][)]" '
        $0 ~ signature { in_block=1 }
        in_block { print }
        in_block && /^}/ { exit }
    ' "installers/phases/11-services.sh"
}

tmp="$(mktemp -d)"
cleanup() { chmod -R u+w "$tmp" 2>/dev/null || true; rm -rf "$tmp"; }  # chmod: restore the read-only fixture so rm can delete it
trap cleanup EXIT

# Installer UI and pip helpers are out of scope here.
ai() { echo "ai: $*"; }
ai_ok() { echo "ok: $*"; }
ai_warn() { echo "warn: $*"; }
ai_bad() { echo "bad: $*"; }
spin_task() { wait "$1"; }
ods_ensure_python_pip() { return 1; }
ods_python_pip_install_user() { return 1; }

# Python stand-in: imports succeed; the snapshot helper records the call and
# exits with STUB_HELPER_RC.
cat > "$tmp/python" <<'PY'
#!/usr/bin/env bash
[[ "$1" == "-c" ]] && exit 0
echo "$*" >> "$STUB_CALLS"
exit "${STUB_HELPER_RC:-0}"
PY
chmod +x "$tmp/python"

eval "$(function_block _phase11_prefetch_embeddings_model)"
declare -f _phase11_prefetch_embeddings_model >/dev/null || fail "could not load _phase11_prefetch_embeddings_model"

export ODS_PYTHON_CMD="$tmp/python" STUB_CALLS="$tmp/calls" LOG_FILE="$tmp/install.log"
export EMBEDDING_MODEL="BAAI/bge-base-en-v1.5" ENABLE_EMBEDDINGS=true
model_cache_name="models--BAAI--bge-base-en-v1.5"

new_install() {
    INSTALL_DIR="$tmp/install-$1"
    mkdir -p "$INSTALL_DIR/scripts"
    : > "$INSTALL_DIR/scripts/download-hf-snapshot.py"
    rm -f "$STUB_CALLS"
}

echo "=== Embeddings prefetch on installer reruns ==="

new_install fresh
STUB_HELPER_RC=0 _phase11_prefetch_embeddings_model > "$tmp/out" \
    || fail "fresh install prefetch should succeed"
grep -q "data/embeddings" "$STUB_CALLS" || fail "fresh install should run the snapshot helper into data/embeddings"
pass "fresh install prefetches the model"

new_install fresh-fail
if STUB_HELPER_RC=1 _phase11_prefetch_embeddings_model > "$tmp/out"; then
    fail "a failed prefetch with no cached model must stop the install"
fi
grep -q "Embeddings model prefetch failed." "$tmp/out" || fail "failed prefetch should say so"
pass "failed prefetch with nothing cached still stops the install"

new_install user-cache
mkdir -p "$INSTALL_DIR/data/embeddings/$model_cache_name/snapshots/rev"
STUB_HELPER_RC=0 _phase11_prefetch_embeddings_model > "$tmp/out" \
    || fail "rerun over an installer-owned cache should succeed"
[[ -s "$STUB_CALLS" ]] || fail "rerun over an installer-owned cache should refresh it"
pass "rerun refreshes a cache the installer owns"

if [[ "$(id -u)" == 0 ]]; then
    echo "  SKIP: service-owned cache case (root can write any directory)"
else
    new_install service-cache
    mkdir -p "$INSTALL_DIR/data/embeddings/$model_cache_name/snapshots/rev"
    chmod 555 "$INSTALL_DIR/data/embeddings/$model_cache_name"
    STUB_HELPER_RC=1 _phase11_prefetch_embeddings_model > "$tmp/out" \
        || fail "rerun over a service-owned cache must not stop the install"
    [[ ! -e "$STUB_CALLS" ]] || fail "rerun must not try to write into the service-owned cache"
    grep -q "already cached by the Embeddings service" "$tmp/out" \
        || fail "rerun should say the Embeddings service owns the cache"
    pass "rerun leaves a service-owned cache alone"
fi

new_install disabled
ENABLE_EMBEDDINGS=false STUB_HELPER_RC=1 _phase11_prefetch_embeddings_model > "$tmp/out" \
    || fail "prefetch must be a no-op when Embeddings is not selected"
[[ ! -e "$STUB_CALLS" ]] || fail "no prefetch when Embeddings is not selected"
pass "no prefetch when Embeddings is not selected"

echo ""
echo "All $PASS checks passed"
