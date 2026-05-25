#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: prune-hermes-slash-workers.sh [--force] [--dry-run] [--max-count N] [--max-age-seconds N] [--container NAME]

Finds Hermes tui_gateway.slash_worker children inside the Hermes container and
prunes only workers that exceed the age/count policy. Dry-run is the default.

Environment:
  HERMES_SLASH_WORKER_MAX_COUNT        Default: 8
  HERMES_SLASH_WORKER_MAX_AGE_SECONDS  Default: 3600
  HERMES_CONTAINER                     Default: dream-hermes
EOF
}

MAX_COUNT="${HERMES_SLASH_WORKER_MAX_COUNT:-8}"
MAX_AGE_SECONDS="${HERMES_SLASH_WORKER_MAX_AGE_SECONDS:-3600}"
CONTAINER="${HERMES_CONTAINER:-dream-hermes}"
FORCE=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --force)
            FORCE=1
            shift
            ;;
        --dry-run)
            FORCE=0
            shift
            ;;
        --max-count)
            MAX_COUNT="${2:-}"
            shift 2
            ;;
        --max-age-seconds)
            MAX_AGE_SECONDS="${2:-}"
            shift 2
            ;;
        --container)
            CONTAINER="${2:-}"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "[FAIL] Unknown argument: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

if [[ ! "$MAX_COUNT" =~ ^[0-9]+$ || "$MAX_COUNT" -lt 1 ]]; then
    echo "[FAIL] --max-count must be a positive integer" >&2
    exit 1
fi
if [[ ! "$MAX_AGE_SECONDS" =~ ^[0-9]+$ ]]; then
    echo "[FAIL] --max-age-seconds must be a non-negative integer" >&2
    exit 1
fi
if [[ -z "$CONTAINER" ]]; then
    echo "[FAIL] --container must not be empty" >&2
    exit 1
fi

collect_workers() {
    if [[ -n "${DREAM_HERMES_SLASH_WORKER_PS_FIXTURE:-}" ]]; then
        cat "$DREAM_HERMES_SLASH_WORKER_PS_FIXTURE"
        return 0
    fi

    if ! command -v docker >/dev/null 2>&1; then
        echo "[FAIL] docker CLI not found" >&2
        return 1
    fi
    if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$CONTAINER"; then
        echo "[INFO] $CONTAINER is not running; nothing to prune"
        return 0
    fi

    docker exec "$CONTAINER" sh -c \
        "ps -eo pid=,etimes=,args= 2>/dev/null || ps -ef 2>/dev/null" \
        | grep '[t]ui_gateway[.]slash_worker' || true
}

WORKERS_FILE="$(mktemp)"
CANDIDATES_FILE="$(mktemp)"
OVERAGE_FILE="$(mktemp)"
trap 'rm -f "$WORKERS_FILE" "$CANDIDATES_FILE" "$OVERAGE_FILE"' EXIT

collect_workers | awk '
    $0 ~ /tui_gateway[.]slash_worker/ {
        pid = $1
        age = $2
        if (pid !~ /^[0-9]+$/) {
            next
        }
        if (age !~ /^[0-9]+$/) {
            age = -1
        }
        print pid "\t" age "\t" $0
    }
' > "$WORKERS_FILE"

WORKER_COUNT="$(wc -l < "$WORKERS_FILE" | tr -d ' ')"
if [[ "$WORKER_COUNT" -eq 0 ]]; then
    echo "[PASS] no Hermes slash workers found"
    exit 0
fi

awk -F '\t' -v max_age="$MAX_AGE_SECONDS" '$2 >= max_age {print}' \
    "$WORKERS_FILE" > "$CANDIDATES_FILE"

if [[ "$WORKER_COUNT" -gt "$MAX_COUNT" ]]; then
    OVERAGE=$(( WORKER_COUNT - MAX_COUNT ))
    sort -t "$(printf '\t')" -k2,2nr "$WORKERS_FILE" \
        | head -n "$OVERAGE" > "$OVERAGE_FILE"
    cat "$OVERAGE_FILE" >> "$CANDIDATES_FILE"
fi

sort -t "$(printf '\t')" -k1,1n -u "$CANDIDATES_FILE" -o "$CANDIDATES_FILE"
CANDIDATE_COUNT="$(wc -l < "$CANDIDATES_FILE" | tr -d ' ')"

echo "[INFO] found $WORKER_COUNT Hermes slash_worker process(es); policy max-count=$MAX_COUNT max-age=${MAX_AGE_SECONDS}s"
if [[ "$CANDIDATE_COUNT" -eq 0 ]]; then
    echo "[PASS] no slash workers exceed the prune policy"
    exit 0
fi

echo "[INFO] $CANDIDATE_COUNT slash worker(s) selected for pruning:"
awk -F '\t' '{printf "  pid=%s age=%ss %s\n", $1, $2, $3}' "$CANDIDATES_FILE"

if [[ "$FORCE" -ne 1 ]]; then
    echo "[DRY-RUN] rerun with --force to kill selected workers"
    exit 0
fi

if [[ -n "${DREAM_HERMES_SLASH_WORKER_PS_FIXTURE:-}" ]]; then
    echo "[DRY-RUN] fixture mode is read-only; not killing processes"
    exit 0
fi

awk -F '\t' '{print $1}' "$CANDIDATES_FILE" \
    | docker exec -i "$CONTAINER" sh -c 'while read -r pid; do kill "$pid" 2>/dev/null || true; done'

echo "[PASS] requested termination for $CANDIDATE_COUNT Hermes slash_worker process(es)"
