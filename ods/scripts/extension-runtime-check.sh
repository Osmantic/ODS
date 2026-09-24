#!/usr/bin/env bash
# Extension runtime check — non-core services with an on-disk compose fragment.
# Compares Docker container state to the service registry and optionally probes
# HTTP health endpoints (same paths/timeouts as the installer health phase).
#
# Usage:
#   scripts/extension-runtime-check.sh [ODS_ROOT]
#   scripts/extension-runtime-check.sh [--service ID ...] [ODS_ROOT]
#   ODS_ROOT defaults to the repository root (parent of scripts/).
#
# Environment:
#   EXTENSION_RUNTIME_CHECK_STRICT=1 — exit 1 if any health probe fails (running
#     container but endpoint not reachable). Default is non-blocking (exit 0).
#
# Requires: bash 4+, docker (optional — skips if daemon unreachable), curl for HTTP probes.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT_ARG=""
TARGET_SERVICES=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --service)
            [[ -n "${2:-}" ]] || { echo "ERROR: --service requires an extension id" >&2; exit 2; }
            [[ "$2" =~ ^[A-Za-z0-9][A-Za-z0-9_-]*$ ]] || { echo "ERROR: invalid extension id: $2" >&2; exit 2; }
            TARGET_SERVICES+=("$2")
            shift 2
            ;;
        -h|--help)
            echo "Usage: extension-runtime-check.sh [--service ID ...] [ODS_ROOT]"
            exit 0
            ;;
        --*)
            echo "ERROR: unknown option: $1" >&2
            exit 2
            ;;
        *)
            [[ -z "$ROOT_ARG" ]] || { echo "ERROR: multiple ODS roots provided" >&2; exit 2; }
            ROOT_ARG="$1"
            shift
            ;;
    esac
done
ODS_ROOT="$(cd "${ROOT_ARG:-$ROOT_DIR}" && pwd)"
export SCRIPT_DIR="$ODS_ROOT"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info() { echo -e "${BLUE}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
ok_line() { echo -e "${GREEN}[OK]${NC} $1"; }
bad_line() { echo -e "${RED}[BAD]${NC} $1"; }

if [[ ! -f "$ODS_ROOT/lib/service-registry.sh" ]]; then
    warn "ODS root missing lib/service-registry.sh — skipping ($ODS_ROOT)"
    exit 0
fi

# shellcheck source=../lib/service-registry.sh
. "$ODS_ROOT/lib/service-registry.sh"

if [[ -f "$ODS_ROOT/lib/safe-env.sh" ]]; then
    # shellcheck source=../lib/safe-env.sh
    . "$ODS_ROOT/lib/safe-env.sh"
    [[ -f "$ODS_ROOT/.env" ]] && load_env_file "$ODS_ROOT/.env"
fi

sr_load
sr_resolve_ports

target_selected() {
    local sid="$1" target
    [[ ${#TARGET_SERVICES[@]} -eq 0 ]] && return 0
    for target in "${TARGET_SERVICES[@]}"; do
        [[ "$sid" == "$target" ]] && return 0
    done
    return 1
}

for target in "${TARGET_SERVICES[@]}"; do
    found=false
    for sid in "${SERVICE_IDS[@]}"; do
        [[ "$sid" == "$target" ]] && found=true && break
    done
    if [[ "$found" != "true" ]]; then
        warn "Unknown extension id: $target"
        exit 2
    fi
done

if [[ ${#SERVICE_IDS[@]} -eq 0 ]]; then
    info "No services in registry — nothing to check"
    exit 0
fi

if ! command -v docker >/dev/null 2>&1; then
    info "Extension runtime check — docker not in PATH (skipping)"
    exit 0
fi

if ! docker info >/dev/null 2>&1; then
    info "Extension runtime check — Docker daemon not reachable (skipping)"
    exit 0
fi

HAVE_CURL=false
command -v curl >/dev/null 2>&1 && HAVE_CURL=true

strict="${EXTENSION_RUNTIME_CHECK_STRICT:-0}"
had_health_fail=0

if [[ ${#TARGET_SERVICES[@]} -gt 0 ]]; then
    info "Extension runtime check — target(s): ${TARGET_SERVICES[*]} — root: $ODS_ROOT"
else
    info "Extension runtime check (non-core, compose enabled) — root: $ODS_ROOT"
fi

for sid in "${SERVICE_IDS[@]}"; do
    target_selected "$sid" || continue
    svc_category="${SERVICE_CATEGORIES[$sid]:-optional}"
    [[ "$svc_category" == "core" ]] && continue

    cf="${SERVICE_COMPOSE[$sid]:-}"
    [[ -z "$cf" || ! -f "$cf" ]] && continue

    cname="${SERVICE_CONTAINERS[$sid]:-ods-$sid}"
    disp="${SERVICE_NAMES[$sid]:-$sid}"

    if ! docker inspect "$cname" >/dev/null 2>&1; then
        info "[$sid] $disp — no container '$cname' (not in current compose stack or not started)"
        continue
    fi

    status="$(docker inspect -f '{{.State.Status}}' "$cname" 2>/dev/null || echo unknown)"
    if [[ "$status" != "running" ]]; then
        warn "[$sid] $disp — container exists but status=$status (try: docker logs $cname)"
        continue
    fi

    port="${SERVICE_PORTS[$sid]:-0}"
    health="${SERVICE_HEALTH[$sid]:-}"
    timeout_sec="${SERVICE_HEALTH_TIMEOUTS[$sid]:-5}"

    if [[ ! "$port" =~ ^[0-9]+$ ]] || [[ "$port" -le 0 ]]; then
        ok_line "[$sid] $disp — running (no external port to probe)"
        continue
    fi

    if [[ -z "$health" ]]; then
        ok_line "[$sid] $disp — running (no health path in manifest)"
        continue
    fi

    if ! $HAVE_CURL; then
        warn "[$sid] $disp — running; curl missing, cannot probe http://127.0.0.1:${port}${health}"
        continue
    fi

    url="http://127.0.0.1:${port}${health}"
    if curl -sf --max-time "$timeout_sec" "$url" >/dev/null; then
        ok_line "[$sid] $disp — running, health OK ($url)"
    else
        bad_line "[$sid] $disp — running but health failed ($url) — try: docker compose logs $sid"
        had_health_fail=1
    fi
done

if [[ "$strict" == "1" && "$had_health_fail" -ne 0 ]]; then
    exit 1
fi
exit 0
