#!/usr/bin/env bash
# Extension runtime check — non-core services with an on-disk compose fragment.
# Compares Docker container state to the service registry and optionally probes
# HTTP health endpoints (same paths/timeouts as the installer health phase).
#
# Usage:
#   scripts/extension-runtime-check.sh [--json] [ODS_ROOT]
#   ODS_ROOT defaults to the repository root (parent of scripts/).
#
# Environment:
#   EXTENSION_RUNTIME_CHECK_STRICT=1 — exit 1 if any health probe fails (running
#     container but endpoint not reachable). Default is non-blocking (exit 0).
#
# Requires: bash 4+, docker (optional — skips if daemon unreachable), curl for HTTP probes.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JSON_OUTPUT=false
REQUESTED_ROOT=""

usage() {
    cat <<'EOF'
Usage: scripts/extension-runtime-check.sh [--json] [ODS_ROOT]

Check non-core extension container and HTTP health state.

Options:
  --json      Emit a stable machine-readable report.
  -h, --help  Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --json) JSON_OUTPUT=true ;;
        -h|--help) usage; exit 0 ;;
        -*) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
        *)
            [[ -z "$REQUESTED_ROOT" ]] || { echo "Only one ODS_ROOT may be provided" >&2; exit 2; }
            REQUESTED_ROOT="$1"
            ;;
    esac
    shift
done

ODS_ROOT="$(cd "${REQUESTED_ROOT:-$ROOT_DIR}" && pwd)"
export SCRIPT_DIR="$ODS_ROOT"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info() { $JSON_OUTPUT || echo -e "${BLUE}[INFO]${NC} $1"; }
warn() { $JSON_OUTPUT || echo -e "${YELLOW}[WARN]${NC} $1"; }
ok_line() { $JSON_OUTPUT || echo -e "${GREEN}[OK]${NC} $1"; }
bad_line() { $JSON_OUTPUT || echo -e "${RED}[BAD]${NC} $1"; }

declare -a CHECK_IDS=() CHECK_NAMES=() CHECK_STATUSES=() CHECK_CONTAINERS=() CHECK_URLS=() CHECK_MESSAGES=()
DOCKER_STATUS="not_checked"
HAVE_CURL=false

record_check() {
    CHECK_IDS+=("$1")
    CHECK_NAMES+=("$2")
    CHECK_STATUSES+=("$3")
    CHECK_CONTAINERS+=("$4")
    CHECK_URLS+=("$5")
    CHECK_MESSAGES+=("$6")
}

json_escape() {
    local value="${1-}"
    value=${value//\\/\\\\}
    value=${value//\"/\\\"}
    value=${value//$'\n'/\\n}
    value=${value//$'\r'/\\r}
    value=${value//$'\t'/\\t}
    printf '%s' "$value"
}

emit_json() {
    local healthy=0 unhealthy=0 skipped=0 i comma=""
    for i in "${!CHECK_STATUSES[@]}"; do
        case "${CHECK_STATUSES[$i]}" in
            healthy|running) healthy=$((healthy + 1)) ;;
            unhealthy|stopped) unhealthy=$((unhealthy + 1)) ;;
            *) skipped=$((skipped + 1)) ;;
        esac
    done
    printf '{"schema_version":"1","kind":"extension-runtime-check","root":"%s","strict":%s,' \
        "$(json_escape "$ODS_ROOT")" "$([[ "${EXTENSION_RUNTIME_CHECK_STRICT:-0}" == "1" ]] && echo true || echo false)"
    printf '"environment":{"docker":"%s","curl":"%s"},' \
        "$DOCKER_STATUS" "$($HAVE_CURL && echo available || echo unavailable)"
    printf '"checks":['
    for i in "${!CHECK_IDS[@]}"; do
        printf '%s{"service_id":"%s","name":"%s","status":"%s","container":"%s","url":"%s","message":"%s"}' \
            "$comma" "$(json_escape "${CHECK_IDS[$i]}")" "$(json_escape "${CHECK_NAMES[$i]}")" \
            "$(json_escape "${CHECK_STATUSES[$i]}")" "$(json_escape "${CHECK_CONTAINERS[$i]}")" \
            "$(json_escape "${CHECK_URLS[$i]}")" "$(json_escape "${CHECK_MESSAGES[$i]}")"
        comma=","
    done
    printf '],"summary":{"checked":%d,"healthy":%d,"unhealthy":%d,"skipped":%d}}\n' \
        "${#CHECK_IDS[@]}" "$healthy" "$unhealthy" "$skipped"
}

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

if [[ ${#SERVICE_IDS[@]} -eq 0 ]]; then
    info "No services in registry — nothing to check"
    $JSON_OUTPUT && emit_json
    exit 0
fi

if ! command -v docker >/dev/null 2>&1; then
    DOCKER_STATUS="unavailable"
    info "Extension runtime check — docker not in PATH (skipping)"
    $JSON_OUTPUT && emit_json
    exit 0
fi

if ! docker info >/dev/null 2>&1; then
    DOCKER_STATUS="unreachable"
    info "Extension runtime check — Docker daemon not reachable (skipping)"
    $JSON_OUTPUT && emit_json
    exit 0
fi

DOCKER_STATUS="available"
command -v curl >/dev/null 2>&1 && HAVE_CURL=true

strict="${EXTENSION_RUNTIME_CHECK_STRICT:-0}"
had_health_fail=0

info "Extension runtime check (non-core, compose enabled) — root: $ODS_ROOT"

for sid in "${SERVICE_IDS[@]}"; do
    svc_category="${SERVICE_CATEGORIES[$sid]:-optional}"
    [[ "$svc_category" == "core" ]] && continue

    cf="${SERVICE_COMPOSE[$sid]:-}"
    [[ -z "$cf" || ! -f "$cf" ]] && continue

    cname="${SERVICE_CONTAINERS[$sid]:-ods-$sid}"
    disp="${SERVICE_NAMES[$sid]:-$sid}"

    if ! docker inspect "$cname" >/dev/null 2>&1; then
        info "[$sid] $disp — no container '$cname' (not in current compose stack or not started)"
        record_check "$sid" "$disp" "not_started" "$cname" "" "container is not present"
        continue
    fi

    status="$(docker inspect -f '{{.State.Status}}' "$cname" 2>/dev/null || echo unknown)"
    if [[ "$status" != "running" ]]; then
        warn "[$sid] $disp — container exists but status=$status (try: docker logs $cname)"
        record_check "$sid" "$disp" "stopped" "$cname" "" "container status: $status"
        continue
    fi

    port="${SERVICE_PORTS[$sid]:-0}"
    health="${SERVICE_HEALTH[$sid]:-}"
    timeout_sec="${SERVICE_HEALTH_TIMEOUTS[$sid]:-5}"

    if [[ ! "$port" =~ ^[0-9]+$ ]] || [[ "$port" -le 0 ]]; then
        ok_line "[$sid] $disp — running (no external port to probe)"
        record_check "$sid" "$disp" "running" "$cname" "" "no external port to probe"
        continue
    fi

    if [[ -z "$health" ]]; then
        ok_line "[$sid] $disp — running (no health path in manifest)"
        record_check "$sid" "$disp" "running" "$cname" "" "no health path in manifest"
        continue
    fi

    if ! $HAVE_CURL; then
        warn "[$sid] $disp — running; curl missing, cannot probe http://127.0.0.1:${port}${health}"
        record_check "$sid" "$disp" "unprobed" "$cname" "http://127.0.0.1:${port}${health}" "curl is unavailable"
        continue
    fi

    url="http://127.0.0.1:${port}${health}"
    if curl -sf --max-time "$timeout_sec" "$url" >/dev/null; then
        ok_line "[$sid] $disp — running, health OK ($url)"
        record_check "$sid" "$disp" "healthy" "$cname" "$url" "HTTP health probe passed"
    else
        bad_line "[$sid] $disp — running but health failed ($url) — try: docker compose logs $sid"
        record_check "$sid" "$disp" "unhealthy" "$cname" "$url" "HTTP health probe failed"
        had_health_fail=1
    fi
done

$JSON_OUTPUT && emit_json

if [[ "$strict" == "1" && "$had_health_fail" -ne 0 ]]; then
    exit 1
fi
exit 0
