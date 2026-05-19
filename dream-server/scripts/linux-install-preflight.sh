#!/usr/bin/env bash
# Linux install environment preflight — structured checks with stable IDs and JSON output.
# Use before or during install when services are not yet up (unlike ./dream-preflight.sh).
#
# Usage:
#   ./scripts/linux-install-preflight.sh              # human-readable report
#   ./scripts/linux-install-preflight.sh --json       # JSON on stdout
#   ./scripts/linux-install-preflight.sh --json-file /tmp/report.json
#   ./scripts/linux-install-preflight.sh --strict     # exit 1 if any warn or fail
#
# Also reachable as: ./dream-preflight.sh --install-env [same args]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

OUTPUT_MODE="human"
JSON_FILE=""
STRICT=false
DREAM_ROOT="${DREAM_ROOT:-$ROOT_DIR}"
MIN_DISK_GB_FREE="${MIN_DISK_GB_FREE:-15}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --json)
            OUTPUT_MODE="json"
            shift
            ;;
        --json-file)
            JSON_FILE="${2:-}"
            OUTPUT_MODE="json"
            shift 2
            ;;
        --strict)
            STRICT=true
            shift
            ;;
        --dream-root)
            DREAM_ROOT="${2:-}"
            shift 2
            ;;
        --min-disk-gb)
            MIN_DISK_GB_FREE="${2:-}"
            shift 2
            ;;
        -h|--help)
            sed -n '1,20p' "$0" | tail -n +2
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 2
            ;;
    esac
done

CHECKS_JSONL="$(mktemp)"
trap 'rm -f "$CHECKS_JSONL"' EXIT

append_check() {
    # id, status, message, remediation — safe for special characters via env
    export LP_ID="$1" LP_STATUS="$2" LP_MSG="$3" LP_FIX="${4:-}"
    python3 -c '
import json, os
print(json.dumps({
    "id": os.environ["LP_ID"],
    "status": os.environ["LP_STATUS"],
    "message": os.environ["LP_MSG"],
    "remediation": os.environ.get("LP_FIX", ""),
}))
' >>"$CHECKS_JSONL"
}

# --- Distro fingerprint (from /etc/os-release) ---
DISTRO_ID=""
DISTRO_VERSION_ID=""
DISTRO_PRETTY=""
DISTRO_LIKE=""
if [[ -f /etc/os-release ]]; then
    # shellcheck source=/dev/null
    source /etc/os-release
    DISTRO_ID="${ID:-}"
    DISTRO_VERSION_ID="${VERSION_ID:-}"
    DISTRO_PRETTY="${PRETTY_NAME:-}"
    DISTRO_LIKE="${ID_LIKE:-}"
    append_check "DISTRO_INFO" "pass" \
        "Linux distro: ${PRETTY_NAME:-unknown} (ID=${ID:-?}, VERSION_ID=${VERSION_ID:-?})" \
        ""
else
    append_check "DISTRO_INFO" "fail" \
        "/etc/os-release not found — installer expects a Linux environment" \
        "Run on a supported Linux distribution or use the platform-specific installer."
fi

KERNEL="$(uname -r 2>/dev/null || echo unknown)"
append_check "KERNEL_INFO" "pass" "Kernel: $KERNEL" ""

# --- curl (used by service preflight and many scripts) ---
if command -v curl >/dev/null 2>&1; then
    append_check "CURL_INSTALLED" "pass" "curl is available" ""
else
    append_check "CURL_INSTALLED" "warn" \
        "curl not found — installer and health checks expect it" \
        "Install curl (e.g. apt install curl / dnf install curl) and re-run."
fi

# --- Docker CLI ---
if ! command -v docker >/dev/null 2>&1; then
    append_check "DOCKER_INSTALLED" "fail" \
        "Docker CLI not found in PATH" \
        "Install Docker Engine and ensure your user can run docker (see LINUX-TROUBLESHOOTING-GUIDE.md#docker_installed)."
else
    DV="$(docker --version 2>/dev/null | head -1 || true)"
    append_check "DOCKER_INSTALLED" "pass" "Docker CLI: ${DV:-present}" ""
fi

# --- Docker daemon ---
DOCKER_INFO_OK=false
if command -v docker >/dev/null 2>&1; then
    if docker info >/dev/null 2>&1; then
        DOCKER_INFO_OK=true
        append_check "DOCKER_DAEMON" "pass" "Docker daemon is reachable" ""
    else
        append_check "DOCKER_DAEMON" "fail" \
            "Docker daemon not running or not accessible" \
            "Start the service (e.g. sudo systemctl start docker) or log in to Docker Desktop; add your user to the docker group if permission denied (see LINUX-TROUBLESHOOTING-GUIDE.md#docker_daemon)."
    fi
else
    append_check "DOCKER_DAEMON" "fail" "Skipped — Docker CLI missing" ""
fi

# --- Docker Compose v2 / v1 ---
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    CV="$(docker compose version 2>/dev/null | head -1 || true)"
    append_check "COMPOSE_CLI" "pass" "Compose: $CV" ""
elif command -v docker-compose >/dev/null 2>&1; then
    CV="$(docker-compose version --short 2>/dev/null || docker-compose version 2>/dev/null | head -1)"
    append_check "COMPOSE_CLI" "pass" "docker-compose (legacy): $CV" ""
else
    append_check "COMPOSE_CLI" "fail" \
        "Neither 'docker compose' nor 'docker-compose' is available" \
        "Install Docker Compose v2 plugin or docker-compose; see LINUX-TROUBLESHOOTING-GUIDE.md#compose_cli."
fi

# --- NVIDIA Docker runtime (only if nvidia-smi works) ---
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi --query-gpu=name --format=csv,noheader >/dev/null 2>&1; then
    if [[ "$DOCKER_INFO_OK" == true ]]; then
        if docker info 2>/dev/null | grep -qi nvidia; then
            append_check "NVIDIA_CONTAINER_RUNTIME" "pass" "NVIDIA Container Toolkit / runtime visible to Docker" ""
        else
            append_check "NVIDIA_CONTAINER_RUNTIME" "warn" \
                "GPU visible to nvidia-smi but Docker does not report NVIDIA runtime" \
                "Install/configure nvidia-container-toolkit so GPU containers work (see LINUX-TROUBLESHOOTING-GUIDE.md#nvidia_container_runtime)."
        fi
    else
        append_check "NVIDIA_CONTAINER_RUNTIME" "warn" \
            "nvidia-smi works but Docker daemon check failed — could not verify NVIDIA runtime" \
            "Fix Docker daemon access first, then install nvidia-container-toolkit if needed."
    fi
else
    append_check "NVIDIA_CONTAINER_RUNTIME" "pass" "No NVIDIA GPU detected via nvidia-smi — check skipped" ""
fi

# --- Free disk space for Dream Server root ---
if [[ -d "$DREAM_ROOT" ]]; then
    # POSIX-friendly: df -P, parse available KB
    if DFOUT="$(df -Pk "$DREAM_ROOT" 2>/dev/null | tail -1)"; then
        AVAIL_KB="$(echo "$DFOUT" | awk '{print $4}')"
        if [[ "$AVAIL_KB" =~ ^[0-9]+$ ]]; then
            AVAIL_GB=$((AVAIL_KB / 1048576))
            if [[ "$AVAIL_GB" -ge "$MIN_DISK_GB_FREE" ]]; then
                append_check "DISK_SPACE" "pass" \
                    "Free space on $DREAM_ROOT: ~${AVAIL_GB}GB (min ${MIN_DISK_GB_FREE}GB)" ""
            else
                append_check "DISK_SPACE" "warn" \
                    "Low free space on $DREAM_ROOT: ~${AVAIL_GB}GB (recommended ≥${MIN_DISK_GB_FREE}GB free)" \
                    "Free disk space or set DREAM_ROOT to a volume with more room; see LINUX-TROUBLESHOOTING-GUIDE.md#disk_space."
            fi
        else
            append_check "DISK_SPACE" "warn" "Could not parse free space for $DREAM_ROOT" ""
        fi
    else
        append_check "DISK_SPACE" "warn" "df failed for $DREAM_ROOT" ""
    fi
else
    append_check "DISK_SPACE" "warn" "DREAM_ROOT does not exist yet: $DREAM_ROOT" \
        "Create the directory or run from the extracted dream-server tree."
fi

# --- cgroup v2 (optional signal) ---
if [[ -f /sys/fs/cgroup/cgroup.controllers ]]; then
    append_check "CGROUP_V2" "pass" "cgroup v2 detected (/sys/fs/cgroup/cgroup.controllers present)" ""
else
    append_check "CGROUP_V2" "warn" \
        "cgroup v2 not detected — some Docker/rootless setups may differ" \
        "Usually fine on modern distros; if Docker fails oddly, see LINUX-TROUBLESHOOTING-GUIDE.md#cgroups."
fi

# --- jq (installer often installs it; nice to have) ---
if command -v jq >/dev/null 2>&1; then
    append_check "JQ_INSTALLED" "pass" "jq available for JSON tooling" ""
else
    append_check "JQ_INSTALLED" "warn" \
        "jq not found — installer may install it; some scripts expect it" \
        "Install jq for smoother tooling (see INSTALL-TROUBLESHOOTING.md)."
fi

# --- Host firewall (UFW / firewalld) ---
# The dashboard host agent binds to the Docker bridge gateway (default
# 172.17.0.1:7710) and compose containers reach it via the host INPUT chain.
# Default-DROP firewalls block that traffic. Warn only — never fail.
if command -v ufw >/dev/null 2>&1 && systemctl is-active --quiet ufw 2>/dev/null; then
    append_check "FIREWALL_CHECK" "warn" \
        "UFW active — may block container→host:7710 traffic" \
        "Installer will auto-add a scoped rule for the actual dream-network subnet after compose starts."
elif command -v firewall-cmd >/dev/null 2>&1 && systemctl is-active --quiet firewalld 2>/dev/null; then
    append_check "FIREWALL_CHECK" "warn" \
        "firewalld active — may block container→host:7710 traffic" \
        "Installer will auto-add a scoped rule for the actual dream-network subnet after compose starts."
else
    append_check "FIREWALL_CHECK" "pass" \
        "No restrictive host firewall detected" ""
fi

# --- Compose files present (expected when run from repo tree) ---
if [[ -f "$ROOT_DIR/docker-compose.base.yml" ]] || [[ -f "$ROOT_DIR/docker-compose.yml" ]]; then
    append_check "COMPOSE_FILES" "pass" "Compose files present under dream-server root" ""
else
    append_check "COMPOSE_FILES" "warn" \
        "No docker-compose.base.yml or docker-compose.yml in $ROOT_DIR" \
        "Run this script from the extracted Dream Server source tree (dream-server/)."
fi

emit_report() {
    python3 - "$CHECKS_JSONL" "$ROOT_DIR" "$KERNEL" "$DISTRO_ID" "$DISTRO_VERSION_ID" "$DISTRO_PRETTY" "$DISTRO_LIKE" "$DREAM_ROOT" "$MIN_DISK_GB_FREE" <<'PY'
import json
import sys
from datetime import datetime, timezone

checks_path = sys.argv[1]
root_dir = sys.argv[2]
kernel = sys.argv[3]
distro_id = sys.argv[4]
distro_vid = sys.argv[5]
distro_pretty = sys.argv[6]
distro_like = sys.argv[7]
dream_root = sys.argv[8]
min_disk = sys.argv[9]

checks = []
with open(checks_path, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        checks.append(json.loads(line))

fail_n = sum(1 for c in checks if c["status"] == "fail")
warn_n = sum(1 for c in checks if c["status"] == "warn")
pass_n = sum(1 for c in checks if c["status"] == "pass")

exit_ok = fail_n == 0
report = {
    "schema_version": "1",
    "kind": "linux-install-preflight",
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "distro": {
        "id": distro_id,
        "version_id": distro_vid,
        "pretty_name": distro_pretty,
        "id_like": distro_like,
    },
    "kernel": kernel,
    "dream_root": dream_root,
    "min_disk_gb_free": min_disk,
    "checks": checks,
    "summary": {
        "pass": pass_n,
        "warn": warn_n,
        "fail": fail_n,
        "exit_ok": exit_ok,
    },
}
print(json.dumps(report, indent=2))
PY
}

REPORT_JSON="$(emit_report)"

EXIT_CODE=0
echo "$REPORT_JSON" | python3 -c '
import json,sys
r=json.load(sys.stdin)
s=r["summary"]
sys.exit(0 if s["exit_ok"] else 1)
' || EXIT_CODE=1

if [[ "$STRICT" == true ]]; then
    echo "$REPORT_JSON" | python3 -c '
import json,sys
r=json.load(sys.stdin)
s=r["summary"]
sys.exit(0 if s["fail"]==0 and s["warn"]==0 else 1)
' || EXIT_CODE=1
fi

if [[ "$OUTPUT_MODE" == "json" ]]; then
    echo "$REPORT_JSON"
    if [[ -n "$JSON_FILE" ]]; then
        printf '%s\n' "$REPORT_JSON" >"$JSON_FILE"
    fi
    exit "$EXIT_CODE"
fi

# --- Human output ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

echo -e "${BOLD}${CYAN}Linux install preflight${NC} (structured checks)"
echo "Dream root: $DREAM_ROOT"
echo ""

while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    mapfile -t _trip < <(python3 -c 'import json,sys; o=json.loads(sys.argv[1]); print(o["id"]); print(o["status"]); print(o["message"])' "$line")
    id="${_trip[0]}"
    st="${_trip[1]}"
    msg="${_trip[2]}"
    case "$st" in
        pass)  sig="${GREEN}PASS${NC}" ;;
        warn)  sig="${YELLOW}WARN${NC}" ;;
        fail)  sig="${RED}FAIL${NC}" ;;
        *)     sig="$st" ;;
    esac
    echo -e "[$sig] ${BOLD}${id}${NC}: $msg"
done <"$CHECKS_JSONL"

echo ""
echo -e "${BOLD}Summary${NC}"
echo "$REPORT_JSON" | python3 -c '
import json,sys
r=json.load(sys.stdin)
s=r["summary"]
print("  pass:", s["pass"], " warn:", s["warn"], " fail:", s["fail"])
print("  exit_ok:", "true" if s["exit_ok"] else "false")
'

if [[ "$EXIT_CODE" -eq 0 ]]; then
    echo -e "${GREEN}Preflight OK (no failures).${NC}"
else
    echo -e "${RED}Preflight failed — see FAIL checks and LINUX-TROUBLESHOOTING-GUIDE.md${NC}"
fi

if [[ -n "$JSON_FILE" ]]; then
    printf '%s\n' "$REPORT_JSON" >"$JSON_FILE"
    echo "JSON written to: $JSON_FILE"
fi

exit "$EXIT_CODE"
