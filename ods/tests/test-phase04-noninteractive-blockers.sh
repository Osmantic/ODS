#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE="$ROOT_DIR/installers/phases/04-requirements.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

mkdir -p "$TMP_DIR/ods/scripts" "$TMP_DIR/ods/lib"
cat > "$TMP_DIR/ods/scripts/preflight-engine.sh" <<'STUB'
#!/usr/bin/env bash
echo 'PREFLIGHT_BLOCKERS=1'
echo 'PREFLIGHT_WARNINGS=0'
STUB
chmod +x "$TMP_DIR/ods/scripts/preflight-engine.sh"
cat > "$TMP_DIR/ods/lib/safe-env.sh" <<'STUB'
load_env_from_output() {
    local line
    while IFS= read -r line; do eval "$line"; done
}
STUB
cat > "$TMP_DIR/ods/lib/service-registry.sh" <<'STUB'
declare -A SERVICE_PORTS=()
STUB
cat > "$TMP_DIR/preflight.json" <<'STUB'
{"checks":[{"status":"blocker","message":"Disk 1GB is below required minimum (15GB)"}]}
STUB

run_requirements_phase() (
    local dry_run="$1"
    export SCRIPT_DIR="$TMP_DIR/ods"
    export INSTALL_DIR="$TMP_DIR/install"
    export LOG_FILE="$TMP_DIR/install.log"
    export PREFLIGHT_REPORT_FILE="$TMP_DIR/preflight.json"
    export TIER=0 RAM_GB=1 DISK_AVAIL=1 GPU_BACKEND=cpu GPU_VRAM=0
    export GPU_NAME=none GPU_COUNT=0 INTERACTIVE=false DRY_RUN="$dry_run"
    export ENABLE_VOICE=false ENABLE_WORKFLOWS=false ENABLE_RAG=false
    export ENABLE_QDRANT=false ENABLE_COMFYUI=false

    tier_rank() { echo 0; }
    chapter() { :; }
    ods_progress() { :; }
    log() { :; }
    warn() { :; }
    error() { printf 'ERROR: %s\n' "$*"; }
    ai_ok() { :; }
    ai_bad() { :; }
    ai_warn() { :; }

    source "$PHASE"
    printf 'PHASE05_REACHED\n'
)

if run_requirements_phase false >"$TMP_DIR/noninteractive.out" 2>&1; then
    echo "FAIL: non-interactive install continued despite hard preflight blocker"
    exit 1
fi
if grep -Fq 'PHASE05_REACHED' "$TMP_DIR/noninteractive.out"; then
    echo "FAIL: Phase 04 returned success after a hard preflight blocker"
    exit 1
fi
if ! grep -Fq 'non-interactive installation with unmet requirements' "$TMP_DIR/noninteractive.out"; then
    echo "FAIL: blocker failure did not explain why installation stopped"
    exit 1
fi
echo "PASS: non-interactive install stops before Phase 05 on a hard blocker"

if ! run_requirements_phase true >"$TMP_DIR/dry-run.out" 2>&1; then
    echo "FAIL: dry-run should continue simulating after a hard blocker"
    exit 1
fi
if ! grep -Fq 'PHASE05_REACHED' "$TMP_DIR/dry-run.out"; then
    echo "FAIL: dry-run did not continue to the next simulated phase"
    exit 1
fi
echo "PASS: dry-run continues simulation after a hard blocker"
