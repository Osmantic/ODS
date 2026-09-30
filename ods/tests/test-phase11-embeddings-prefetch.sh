#!/usr/bin/env bash
# The optional embeddings cache warm-up must not abort an otherwise valid install.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE11="$ROOT_DIR/installers/phases/11-services.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

# Stop at the next declaration so condition blocks cannot truncate this function.
eval "$(sed -n '/^_phase11_prefetch_embeddings_model() {/,/^_phase11_model_file_valid() {/p' "$PHASE11" | sed '$d')"
declare -F _phase11_prefetch_embeddings_model >/dev/null || fail "could not extract embeddings prefetch handler"

INSTALL_DIR="$TMP_DIR/install"
LOG_FILE="$TMP_DIR/install.log"
MOCK_PYTHON="$TMP_DIR/python"
COMPOSE_UP_MARKER="$TMP_DIR/compose-up.called"
mkdir -p "$INSTALL_DIR/scripts"
touch "$INSTALL_DIR/scripts/download-hf-snapshot.py"

cat > "$MOCK_PYTHON" <<'MOCK'
#!/usr/bin/env bash
if [[ "${1:-}" == "-c" ]]; then
    exit "${MOCK_IMPORT_RC:-1}"
fi
exit "${MOCK_HELPER_RC:-0}"
MOCK
chmod +x "$MOCK_PYTHON"

export INSTALL_DIR LOG_FILE ENABLE_EMBEDDINGS=true ODS_EMBEDDINGS_PREFETCH=true
export ODS_PYTHON_CMD="$MOCK_PYTHON"
ai() { printf 'INFO: %s\n' "$*" >> "$LOG_FILE"; }
ai_warn() { printf 'WARN: %s\n' "$*" >> "$LOG_FILE"; }
ai_bad() { printf 'ERROR: %s\n' "$*" >> "$LOG_FILE"; }
ods_ensure_python_pip() { return "${MOCK_PIP_RC:-1}"; }
ods_python_pip_install_user() { return "${MOCK_PIP_INSTALL_RC:-1}"; }
spin_task() { local pid="$1" rc=0; wait "$pid" || rc=$?; return "$rc"; }
launch_compose() { : > "$COMPOSE_UP_MARKER"; }

# A missing pip/dependency is recoverable: TEI can fetch the model itself.
export MOCK_IMPORT_RC=1 MOCK_PIP_RC=1
_phase11_prefetch_embeddings_model || fail "missing optional prefetch dependency aborted the install"
grep -qF 'WARN: Could not install huggingface_hub[hf_xet] for optional embeddings prefetch.' "$LOG_FILE" || fail "missing dependency did not produce an optional-prefetch warning"
grep -qF 'Skipping embeddings prefetch; TEI will download the model when it starts.' "$LOG_FILE" || fail "missing dependency did not explain the TEI fallback"
_phase11_prefetch_embeddings_model && launch_compose
[[ -e "$COMPOSE_UP_MARKER" ]] || fail "compose launch was blocked by an unavailable optional prefetch dependency"
pass "missing optional prefetch dependency permits service launch"

# Once the dependency is available, a failed snapshot prefetch remains fatal.
: > "$LOG_FILE"
rm -f "$COMPOSE_UP_MARKER"
export MOCK_IMPORT_RC=0 MOCK_HELPER_RC=1
if _phase11_prefetch_embeddings_model; then
    launch_compose
    fail "failed embeddings snapshot prefetch returned success"
fi
grep -qF 'ERROR: Embeddings model prefetch failed.' "$LOG_FILE" || fail "failed snapshot prefetch was not reported"
[[ ! -e "$COMPOSE_UP_MARKER" ]] || fail "compose launched after a real embeddings snapshot prefetch failure"
pass "real snapshot prefetch failures remain fatal"

prefetch_line="$(grep -nF 'if ! _phase11_prefetch_embeddings_model; then' "$PHASE11" | head -1 | cut -d: -f1)"
compose_line="$(grep -n 'up -d --remove-orphans --no-build --pull never >>' "$PHASE11" | head -1 | cut -d: -f1)"
[[ -n "$prefetch_line" && -n "$compose_line" && "$prefetch_line" -lt "$compose_line" ]] || fail "embeddings prefetch guard must remain before compose launch"
pass "Phase 11 preserves the pre-launch prefetch boundary"
