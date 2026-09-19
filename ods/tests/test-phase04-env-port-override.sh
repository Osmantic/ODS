#!/usr/bin/env bash
# Copyright (C) 2026 Lingga Louis Channels
# Licensed under the GNU Affero General Public License v3.0 (AGPLv3)
#
# Regression test: phase 04 (requirements) must probe the host ports the
# install will actually bind — i.e. manifest defaults overlaid with any
# preserved INSTALL_DIR/.env overrides — not stale defaults, and must not
# fall back to llama-server's container-internal port (8080) or crash on
# an unset ENABLE_COMFYUI.
#
# The fixture stubs lsof/ss/netstat/pgrep/docker so the port-conflict scan
# runs hermetically and every probed port is recorded.

set -euo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODS_ROOT="$(cd "${TEST_DIR}/.." && pwd)"
PHASE="${ODS_ROOT}/installers/phases/04-requirements.sh"

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

# --- fixture: minimal SCRIPT_DIR with the real libs + one manifest ----------
FAKE_ODS="$TMP_ROOT/ods"
mkdir -p "$FAKE_ODS/lib" "$FAKE_ODS/extensions/services/llama-server"
cp "$ODS_ROOT/lib/safe-env.sh" "$FAKE_ODS/lib/"
cp "$ODS_ROOT/lib/service-registry.sh" "$FAKE_ODS/lib/"
cp "$ODS_ROOT/lib/python-cmd.sh" "$FAKE_ODS/lib/"
cp "$ODS_ROOT/extensions/services/llama-server/manifest.yaml" \
    "$FAKE_ODS/extensions/services/llama-server/manifest.yaml"

INSTALL_DIR="$TMP_ROOT/install"
mkdir -p "$INSTALL_DIR" "$TMP_ROOT/home"

LSOF_LOG="$TMP_ROOT/lsof.log"
: >"$LSOF_LOG"

# --- stub bin: deterministic "nothing is listening" --------------------------
BIN="$TMP_ROOT/bin"
mkdir -p "$BIN"

cat >"$BIN/lsof" <<'EOF'
#!/usr/bin/env bash
for a in "$@"; do
    case "$a" in :*) echo "$a" >>"${LSOF_LOG:?}" ;; esac
done
exit 1
EOF
cat >"$BIN/ss" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
cat >"$BIN/netstat" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
cat >"$BIN/pgrep" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
cat >"$BIN/docker" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
chmod +x "$BIN"/*

# --- harness: replicates install-core's environment around the phase --------
cat >"$TMP_ROOT/harness.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$1"
INSTALL_DIR="$2"
PHASE_FILE="$3"
[[ "${4:-}" == "--no-comfyui-var" ]] || ENABLE_COMFYUI=false

LOG_FILE=/dev/null
PREFLIGHT_REPORT_FILE="$INSTALL_DIR/preflight.json"
TIER=2
RAM_GB=64
DISK_AVAIL=500
GPU_BACKEND=nvidia
GPU_VRAM=24000
GPU_NAME=test
GPU_COUNT=1
INTERACTIVE=false
DRY_RUN=false
ENABLE_VOICE=false
ENABLE_WORKFLOWS=false
ENABLE_RAG=false
ENABLE_QDRANT=false
EXTERNAL_LLM_URL=""
COMPOSE_PROJECT_NAME=ods
ODS_MODE=local

ods_progress() { :; }
chapter() { :; }
tier_rank() { echo 0; }
ai_ok() { echo "OK: $*"; }
ai_bad() { echo "BAD: $*"; }
ai_warn() { echo "WARN: $*"; }
ai() { :; }
log() { :; }
warn() { :; }
error() { echo "ERROR: $*" >&2; exit 1; }

source "$SCRIPT_DIR/lib/service-registry.sh"
sr_load
source "$PHASE_FILE"
EOF
chmod +x "$TMP_ROOT/harness.sh"

run_phase04() {
    : >"$LSOF_LOG"
    env -i \
        PATH="$BIN:/usr/bin:/bin" \
        HOME="$TMP_ROOT/home" \
        LSOF_LOG="$LSOF_LOG" \
        bash "$TMP_ROOT/harness.sh" "$FAKE_ODS" "$INSTALL_DIR" "$PHASE" "$@"
}

fail() { echo "FAIL: $*" >&2; exit 1; }

echo "=== phase-04 configured-port regression ==="

# Case 1: a preserved .env override must be the port that gets probed.
printf 'OLLAMA_PORT=12345\n' >"$INSTALL_DIR/.env"
run_phase04 >"$TMP_ROOT/out1.log" 2>&1 || fail "phase 04 exited nonzero with .env override"
grep -qx ':12345' "$LSOF_LOG" \
    || fail "configured OLLAMA_PORT=12345 was never probed (probed: $(tr '\n' ' ' <"$LSOF_LOG"))"
grep -qx ':11434' "$LSOF_LOG" \
    && fail "stale manifest default 11434 was probed despite .env override"
grep -qx ':3000' "$LSOF_LOG" \
    || fail "open-webui default port 3000 missing — scan did not run"
echo "PASS: .env OLLAMA_PORT=12345 is probed instead of manifest default"

# Case 2: without .env the llama-server fallback must be the host port 11434,
# not the container-internal 8080.
rm -f "$INSTALL_DIR/.env"
run_phase04 >"$TMP_ROOT/out2.log" 2>&1 || fail "phase 04 exited nonzero without .env"
grep -qx ':11434' "$LSOF_LOG" \
    || fail "host default 11434 was never probed (probed: $(tr '\n' ' ' <"$LSOF_LOG"))"
grep -qx ':8080' "$LSOF_LOG" \
    && fail "container-internal port 8080 was probed as a host port"
echo "PASS: llama-server host fallback is 11434, not 8080"

# Case 3: ENABLE_COMFYUI unset must not abort the phase under set -u.
if ! run_phase04 --no-comfyui-var >"$TMP_ROOT/out3.log" 2>&1; then
    cat "$TMP_ROOT/out3.log" >&2
    fail "phase 04 crashed with ENABLE_COMFYUI unset"
fi
echo "PASS: unset ENABLE_COMFYUI does not abort phase 04"

echo "PASS: all phase-04 port-resolution cases"
