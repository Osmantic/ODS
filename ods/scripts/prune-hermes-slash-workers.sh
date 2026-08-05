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
  HERMES_CONTAINER                     Default: ods-hermes
EOF
}

MAX_COUNT="${HERMES_SLASH_WORKER_MAX_COUNT:-8}"
MAX_AGE_SECONDS="${HERMES_SLASH_WORKER_MAX_AGE_SECONDS:-3600}"
CONTAINER="${HERMES_CONTAINER:-ods-hermes}"
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

# Turn one process-table layout into the normalized row this script works on:
#
#     pid <TAB> age_seconds <TAB> original ps line
#
# `has_age=1` means the caller asked ps for an elapsed-time column, so field 2
# holds seconds. `has_age=0` means the layout only has `pid` then the command,
# and age is recorded as -1 ("unknown") rather than being read out of whichever
# column happens to sit in position 2.
normalize_workers() {
    local has_age="$1"
    awk -v has_age="$has_age" '
        $0 !~ /tui_gateway[.]slash_worker/ {
            next
        }
        {
            pid = $1
            if (pid !~ /^[0-9]+$/) {
                next
            }
            age = -1
            if (has_age == "1" && $2 ~ /^[0-9]+$/) {
                age = $2
            }
            print pid "\t" age "\t" $0
        }
    '
}

collect_workers() {
    if [[ -n "${ODS_HERMES_SLASH_WORKER_PS_FIXTURE:-}" ]]; then
        normalize_workers 1 < "$ODS_HERMES_SLASH_WORKER_PS_FIXTURE"
        return 0
    fi

    if ! command -v docker >/dev/null 2>&1; then
        echo "[FAIL] docker CLI not found" >&2
        return 1
    fi
    if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$CONTAINER"; then
        # stderr, not stdout: stdout is the normalized worker stream.
        echo "[INFO] $CONTAINER is not running; nothing to prune" >&2
        return 0
    fi

    # Each ps layout is requested in its own `docker exec` so the caller knows
    # which columns came back. Asking the container to pick via `A || B` hid
    # that from us: the shapes differ (`ps -ef` leads with UID, not PID), so a
    # fallback line was read as pid=<uid>, age=<pid> — which silently pruned
    # nothing on images that print a UID name, and targeted an unrelated pid on
    # images that print a numeric UID.
    local raw

    # procps: pid, elapsed seconds, full command line.
    if raw="$(docker exec "$CONTAINER" sh -c 'ps -eo pid=,etimes=,args=' 2>/dev/null)" \
        && [[ -n "$raw" ]]; then
        printf '%s\n' "$raw" | normalize_workers 1
        return 0
    fi

    # POSIX/busybox: same leading pid column, no elapsed-time column available.
    if raw="$(docker exec "$CONTAINER" sh -c 'ps -eo pid=,args=' 2>/dev/null)" \
        && [[ -n "$raw" ]]; then
        printf '%s\n' "$raw" | normalize_workers 0
        return 0
    fi

    echo "[WARN] could not read the process table inside $CONTAINER" >&2
    return 1
}

WORKERS_FILE="$(mktemp)"
CANDIDATES_FILE="$(mktemp)"
OVERAGE_FILE="$(mktemp)"
trap 'rm -f "$WORKERS_FILE" "$CANDIDATES_FILE" "$OVERAGE_FILE"' EXIT

_workers_tmp="$(mktemp "${WORKERS_FILE}.XXXXXX.tmp")"
collect_workers > "$_workers_tmp"
mv -f "$_workers_tmp" "$WORKERS_FILE"

WORKER_COUNT="$(wc -l < "$WORKERS_FILE" | tr -d ' ')"
if [[ "$WORKER_COUNT" -eq 0 ]]; then
    echo "[PASS] no Hermes slash workers found"
    exit 0
fi

awk -F '\t' -v max_age="$MAX_AGE_SECONDS" '$2 >= max_age {print}' \
    "$WORKERS_FILE" > "$CANDIDATES_FILE"

if [[ "$WORKER_COUNT" -gt "$MAX_COUNT" ]]; then
    OVERAGE=$(( WORKER_COUNT - MAX_COUNT ))
    # Oldest first. When the container could not report elapsed time every age
    # is -1, so break the tie on the lowest pid — the longest-lived worker —
    # instead of leaving the pick to sort order.
    sort -t "$(printf '\t')" -k2,2nr -k1,1n "$WORKERS_FILE" \
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

if [[ -n "${ODS_HERMES_SLASH_WORKER_PS_FIXTURE:-}" ]]; then
    echo "[DRY-RUN] fixture mode is read-only; not killing processes"
    exit 0
fi

awk -F '\t' '{print $1}' "$CANDIDATES_FILE" \
    | docker exec -i "$CONTAINER" sh -c 'while read -r pid; do kill "$pid" 2>/dev/null || true; done'

echo "[PASS] requested termination for $CANDIDATE_COUNT Hermes slash_worker process(es)"
