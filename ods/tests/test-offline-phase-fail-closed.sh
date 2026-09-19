#!/usr/bin/env bash
# Regression for issue #5112: the offline-mode marker must only exist when the
# required offline assets actually staged, and a failed embeddings download
# must fail the phase — not degrade to a warning a disconnected user can never
# act on. Hermetic: curl and the installer UI helpers are stubbed.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE="$ROOT_DIR/installers/phases/09-offline.sh"

fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

[[ -f "$PHASE" ]] || fail "phase file missing: $PHASE"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
BIN="$TMP/bin"
INSTALL_DIR="$TMP/install"
mkdir -p "$BIN" "$INSTALL_DIR/models/embeddings"
echo "BRAVE_API_KEY=x" > "$INSTALL_DIR/.env"

# run_phase <bin_dir> — source the phase with stubbed helpers.
run_phase() {
    local bin_dir="$1"
    PATH="$bin_dir:$PATH" \
    INSTALL_DIR="$INSTALL_DIR" \
    OFFLINE_MODE=true DRY_RUN=false \
    ENABLE_OPENCLAW=false ENABLE_VOICE=false \
    LOG_FILE="$TMP/install.log" \
    bash -c '
        ods_progress() { :; }; chapter() { :; }
        ai() { :; }; ai_ok() { :; }; ai_warn() { :; }; ai_bad() { :; }
        log() { :; }; _sed_i() { sed -i "$@"; }
        export -f ods_progress chapter ai ai_ok ai_warn ai_bad log _sed_i
        set -e
        source "$1"
        ' _ "$PHASE"
}

EMBED="$INSTALL_DIR/models/embeddings/nomic-embed-text-v1.5.Q4_K_M.gguf"
MARKER="$INSTALL_DIR/.offline-mode"

# ── Scenario 1: download fails — phase must fail and leave no marker ──
cat > "$BIN/curl" <<'SH'
#!/usr/bin/env bash
# Simulate a mid-transfer failure: a partial .part file exists briefly.
out=""
for ((i = 1; i <= $#; i++)); do
    [[ "${!i}" == "-o" ]] && { i=$((i + 1)); out="${!i}"; }
done
[[ -n "$out" ]] && printf 'partial' > "$out"
exit 22
SH
chmod +x "$BIN/curl"

if run_phase "$BIN" >"$TMP/fail.out" 2>&1; then
    cat "$TMP/fail.out"
    fail "phase should fail when the embeddings download fails"
fi
pass "phase fails closed on download failure"

[[ ! -e "$MARKER" ]] \
    || fail "offline marker exists even though the embeddings download failed"
pass "no offline marker after a failed download"

[[ ! -e "${EMBED}.part" && ! -e "$EMBED" ]] \
    || fail "stale .part or model file left behind after failure"
pass "partial download is cleaned up on failure"

# ── Scenario 2: download succeeds — marker appears after the asset ──
cat > "$BIN/curl" <<'SH'
#!/usr/bin/env bash
out=""
for ((i = 1; i <= $#; i++)); do
    [[ "${!i}" == "-o" ]] && { i=$((i + 1)); out="${!i}"; }
done
[[ -n "$out" ]] && printf 'gguf-bytes' > "$out"
exit 0
SH
chmod +x "$BIN/curl"

run_phase "$BIN" >"$TMP/ok.out" 2>&1 \
    || { cat "$TMP/ok.out"; fail "phase should succeed when download succeeds"; }
[[ -e "$MARKER" ]] || fail "offline marker missing after a successful download"
[[ -s "$EMBED" && ! -e "${EMBED}.part" ]] \
    || fail "staged model was not moved into place"
pass "marker created only after the embeddings asset staged"

# ── Scenario 3: rerun over a 0-byte leftover re-downloads ──
rm -f "$MARKER"
: > "$EMBED"
run_phase "$BIN" >"$TMP/rerun.out" 2>&1 \
    || fail "rerun over a 0-byte model should re-download"
[[ "$(cat "$EMBED")" == "gguf-bytes" ]] \
    || fail "0-byte leftover was treated as a completed download"
pass "0-byte leftovers do not masquerade as a completed download"

echo "[PASS] offline phase fails closed and marks only complete installs"
