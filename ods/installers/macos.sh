#!/bin/bash
# ODS macOS installer (doctor/preflight MVP).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPORT_FILE="${PREFLIGHT_REPORT_FILE:-/tmp/ods-preflight-macos.json}"
DOCTOR_FILE="${DOCTOR_REPORT_FILE:-/tmp/ods-doctor-macos.json}"
NO_DELEGATE=false
DELEGATE_LINUX_SIM=false
PASSTHROUGH_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --report)
            REPORT_FILE="${2:-$REPORT_FILE}"
            shift 2
            ;;
        --doctor-report)
            DOCTOR_FILE="${2:-$DOCTOR_FILE}"
            shift 2
            ;;
        --no-delegate)
            NO_DELEGATE=true
            shift
            ;;
        --delegate-linux-sim)
            DELEGATE_LINUX_SIM=true
            shift
            ;;
        *)
            PASSTHROUGH_ARGS+=("$1")
            shift
            ;;
    esac
done

echo "ODS macOS installer (MVP)"
echo ""

[[ -f "$SCRIPT_DIR/lib/safe-env.sh" ]] && . "$SCRIPT_DIR/lib/safe-env.sh"

ARCH="$(uname -m 2>/dev/null || echo unknown)"
if [[ "$ARCH" == "arm64" ]]; then
    echo "[OK] Apple Silicon detected: $ARCH"
else
    echo "[WARN] Non-Apple-Silicon architecture detected: $ARCH"
fi

if command -v docker >/dev/null 2>&1; then
    if docker version >/dev/null 2>&1; then
        echo "[OK] Docker Desktop engine reachable"
    else
        echo "[WARN] Docker installed but daemon not reachable"
    fi
else
    echo "[WARN] Docker CLI not found. Install Docker Desktop first."
fi

if [[ -x "$SCRIPT_DIR/scripts/preflight-engine.sh" ]]; then
    echo ""
    echo "Running macOS preflight..."
    RAM_GB="$(sysctl -n hw.memsize 2>/dev/null | awk '{print int($1/1024/1024/1024)}' || true)"
    if [[ -z "$RAM_GB" || "$RAM_GB" == "0" ]]; then
        RAM_GB="$(grep MemTotal /proc/meminfo 2>/dev/null | awk '{print int($2/1024/1024)}' || echo 16)"
    fi
    DISK_GB="$(df -g "$HOME" 2>/dev/null | tail -1 | awk '{print $4}' || true)"
    if [[ -z "$DISK_GB" || "$DISK_GB" == "0" ]]; then
        DISK_GB="$(df -BG "$HOME" 2>/dev/null | tail -1 | awk '{gsub(/G/, "", $4); print int($4)}' || echo 50)"
    fi
    PREFLIGHT_ENV="$("$SCRIPT_DIR/scripts/preflight-engine.sh" \
        --report "$REPORT_FILE" \
        --tier "T1" \
        --ram-gb "$RAM_GB" \
        --disk-gb "$DISK_GB" \
        --gpu-backend "apple" \
        --gpu-vram-mb 0 \
        --gpu-name "Apple Silicon" \
        --platform-id "macos" \
        --compose-overlays "docker-compose.base.yml,docker-compose.amd.yml" \
        --script-dir "$SCRIPT_DIR" \
        --env)"
    load_env_from_output <<< "$PREFLIGHT_ENV"
    echo "[INFO] Preflight report: $REPORT_FILE"
    echo "[INFO] Blockers: ${PREFLIGHT_BLOCKERS:-0}  Warnings: ${PREFLIGHT_WARNINGS:-0}"
    PYTHON_CMD="python3"
    if [[ -f "$SCRIPT_DIR/lib/python-cmd.sh" ]]; then
        . "$SCRIPT_DIR/lib/python-cmd.sh"
        PYTHON_CMD="$(ods_detect_python_cmd)"
    elif command -v python >/dev/null 2>&1; then
        PYTHON_CMD="python"
    fi

    "$PYTHON_CMD" - "$REPORT_FILE" << 'PY'
import json
import sys

path = sys.argv[1]
try:
    data = json.load(open(path, "r", encoding="utf-8"))
except Exception:
    raise SystemExit(0)
for check in data.get("checks", []):
    status = check.get("status")
    if status not in {"blocker", "warn"}:
        continue
    label = "BLOCKER" if status == "blocker" else "WARN"
    print(f"  - {label}: {check.get('message','')}")
    action = check.get("action", "")
    if action:
        print(f"    Suggestion: {action}")
PY
fi

# scripts/ods-doctor.sh sources lib/service-registry.sh, which needs Bash 4+.
# macOS ships Bash 3.2, so run the doctor through a modern Bash when one is
# available and say so when it is not. Never claim a report that was not written.
_ods_doctor_bash_is_modern() {
    [[ -x "$1" ]] || return 1
    "$1" -c '[ "${BASH_VERSINFO[0]:-0}" -ge 4 ]' >/dev/null 2>&1
}

DOCTOR_BASH=""
_doctor_bash_candidates=("${BASH:-}")
if command -v brew >/dev/null 2>&1; then
    _brew_prefix="$(brew --prefix 2>/dev/null || true)"
    [[ -n "$_brew_prefix" ]] && _doctor_bash_candidates+=("$_brew_prefix/bin/bash")
fi
_doctor_bash_candidates+=(/opt/homebrew/bin/bash /usr/local/bin/bash)
for _candidate in "${_doctor_bash_candidates[@]}"; do
    if [[ -n "$_candidate" ]] && _ods_doctor_bash_is_modern "$_candidate"; then
        DOCTOR_BASH="$_candidate"
        break
    fi
done

if [[ -x "$SCRIPT_DIR/scripts/ods-doctor.sh" ]]; then
    if [[ -z "$DOCTOR_BASH" ]]; then
        echo "[WARN] Doctor report skipped: scripts/ods-doctor.sh requires Bash 4+ (found ${BASH_VERSION})."
        echo "       Install one with 'brew install bash' and re-run this script."
    else
        _doctor_rc=0
        _doctor_err="$("$DOCTOR_BASH" "$SCRIPT_DIR/scripts/ods-doctor.sh" "$DOCTOR_FILE" 2>&1 >/dev/null)" || _doctor_rc=$?
        if [[ "$_doctor_rc" -eq 0 && -f "$DOCTOR_FILE" ]]; then
            echo "[INFO] Doctor report: $DOCTOR_FILE"
        else
            echo "[WARN] Doctor report was not written: scripts/ods-doctor.sh exited ${_doctor_rc}."
            [[ -n "$_doctor_err" ]] && printf '%s\n' "$_doctor_err" | tail -n 3 | sed 's/^/       /'
        fi
    fi
fi

echo ""
echo "Current macOS status:"
echo "  - Installer preflight is implemented."
echo "  - Full macOS runtime path remains experimental."
echo "  - Recommended production path: Linux or Windows+WSL2."
echo ""
echo "References:"
echo "  - docs/SUPPORT-MATRIX.md"
echo "  - docs/PREFLIGHT-ENGINE.md"
echo ""

if [[ "${PREFLIGHT_BLOCKERS:-1}" -gt 0 ]]; then
    exit 2
fi

if $DELEGATE_LINUX_SIM && ! $NO_DELEGATE; then
    echo "Starting delegated installer dry-run to verify compose/runtime wiring..."
    bash "$SCRIPT_DIR/install-core.sh" --dry-run --non-interactive --skip-docker "${PASSTHROUGH_ARGS[@]}" || true
fi

exit 0
