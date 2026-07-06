#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# ODS - Session Cleanup Script
# https://github.com/Osmantic/ODS
#
# Prevents context overflow crashes by automatically managing
# session file lifecycle. When a session file exceeds the size
# threshold, it's deleted and its reference removed from
# sessions.json, forcing the gateway to create a fresh session.
#
# The agent doesn't notice — it just gets a clean context window.
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

# ── Configuration ──────────────────────────────────────────────
# Strix Halo: OpenClaw runs in Docker, sessions are in data volume
OPENCLAW_DIR="${OPENCLAW_DIR:-$HOME/ods/data/openclaw/home}"
SESSIONS_DIR="${SESSIONS_DIR:-$OPENCLAW_DIR/agents/main/sessions}"
SESSIONS_JSON="$SESSIONS_DIR/sessions.json"
MAX_SIZE="${MAX_SIZE:-256000}"

usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Prevents context overflow by pruning OpenClaw session files: removes inactive"
    echo "sessions and deletes bloated ones (over size threshold), then updates"
    echo "sessions.json so the gateway creates a fresh session."
    echo ""
    echo "Options:"
    echo "  -h, --help   Show this help and exit."
    echo ""
    echo "Environment:"
    echo "  OPENCLAW_DIR   Base OpenClaw dir (default: \$HOME/ods/data/openclaw/home)"
    echo "  SESSIONS_DIR   Sessions directory (default: \$OPENCLAW_DIR/agents/main/sessions)"
    echo "  MAX_SIZE       Max session file size in bytes (default: 256000)"
    echo ""
    echo "Exit: 0 (always; missing paths are skipped with a log message)."
}

case "${1:-}" in
    -h|--help) usage; exit 0 ;;
esac

# ── Preflight ──────────────────────────────────────────────────
if [ ! -f "$SESSIONS_JSON" ]; then
    echo "[$(date)] No sessions.json found at $SESSIONS_JSON, skipping"
    exit 0
fi

if [ ! -d "$SESSIONS_DIR" ]; then
    echo "[$(date)] Sessions directory not found at $SESSIONS_DIR, skipping"
    exit 0
fi

# ── Extract active session IDs (portable: no grep -P) ─────────
ACTIVE_IDS_EXIT=0
ACTIVE_IDS=$(grep -oE '"sessionId"[[:space:]]*:[[:space:]]*"[^"]+"' "$SESSIONS_JSON" 2>&1 | sed -E 's/.*"sessionId"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/') || ACTIVE_IDS_EXIT=$?
if [[ $ACTIVE_IDS_EXIT -ne 0 ]]; then
    ACTIVE_IDS=""
fi

echo "[$(date)] Session cleanup starting"
echo "[$(date)] Sessions dir: $SESSIONS_DIR"
echo "[$(date)] Max size threshold: $MAX_SIZE bytes"
echo "[$(date)] Active sessions found: $(echo "$ACTIVE_IDS" | wc -w)"

# ── Clean up debris ────────────────────────────────────────────
DELETED_EXIT=0
DELETED_COUNT=$(find "$SESSIONS_DIR" -name '*.deleted.*' -delete -print 2>&1 | wc -l) || DELETED_EXIT=$?
if [[ $DELETED_EXIT -ne 0 ]]; then
    DELETED_COUNT=0
fi

BAK_EXIT=0
BAK_COUNT=$(find "$SESSIONS_DIR" -name '*.bak*' -not -name '*.bak-cleanup' -delete -print 2>&1 | wc -l) || BAK_EXIT=$?
if [[ $BAK_EXIT -ne 0 ]]; then
    BAK_COUNT=0
fi

if [ "$DELETED_COUNT" -gt 0 ] || [ "$BAK_COUNT" -gt 0 ]; then
    echo "[$(date)] Cleaned up $DELETED_COUNT .deleted files, $BAK_COUNT .bak files"
fi

# ── Process session files ──────────────────────────────────────
WIPE_IDS=""
REMOVED_INACTIVE=0
REMOVED_BLOATED=0

for f in "$SESSIONS_DIR"/*.jsonl; do
    [ -f "$f" ] || continue
    BASENAME=$(basename "$f" .jsonl)

    # Check if this session is active
    IS_ACTIVE=false
    for ID in $ACTIVE_IDS; do
        if [ "$BASENAME" = "$ID" ]; then
            IS_ACTIVE=true
            break
        fi
    done

    if [ "$IS_ACTIVE" = false ]; then
        SIZE=$(du -h "$f" | cut -f1)
        echo "[$(date)] Removing inactive session: $BASENAME ($SIZE)"
        rm -f "$f"
        REMOVED_INACTIVE=$((REMOVED_INACTIVE + 1))
    else
        # Portable stat: Linux uses -c%s, macOS uses -f%z
        stat_exit=0
        if [ "$(uname -s)" = "Darwin" ]; then
            SIZE_BYTES=$(stat -f%z "$f" 2>&1) || stat_exit=$?
        else
            SIZE_BYTES=$(stat -c%s "$f" 2>&1) || stat_exit=$?
        fi
        if [[ $stat_exit -ne 0 ]]; then
            SIZE_BYTES=0
        fi
        if [ "$SIZE_BYTES" -gt "$MAX_SIZE" ]; then
            SIZE=$(du -h "$f" | cut -f1)
            SIZE_LABEL=$(command -v numfmt >/dev/null 2>&1 && numfmt --to=iec "$MAX_SIZE" || echo "${MAX_SIZE}B")
            echo "[$(date)] Session $BASENAME is bloated ($SIZE > ${SIZE_LABEL}), deleting to force fresh session"
            rm -f "$f"
            WIPE_IDS="$WIPE_IDS $BASENAME"
            REMOVED_BLOATED=$((REMOVED_BLOATED + 1))
        fi
    fi
done

# ── Remove wiped session references from sessions.json ─────────
if [ -n "$WIPE_IDS" ]; then
    echo "[$(date)] Clearing session references from sessions.json for:$WIPE_IDS"
    cp "$SESSIONS_JSON" "$SESSIONS_JSON.bak-cleanup"

    for ID in $WIPE_IDS; do
        PYTHON_CMD="python3"
        if [[ -f "$(dirname "$0")/../lib/python-cmd.sh" ]]; then
            . "$(dirname "$0")/../lib/python-cmd.sh"
            PYTHON_CMD="$(ods_detect_python_cmd)"
        elif command -v python >/dev/null 2>&1; then
            PYTHON_CMD="python"
        fi

        "$PYTHON_CMD" -c "
import json, sys
sessions_file = sys.argv[1]
target_id = sys.argv[2]
with open(sessions_file, 'r') as f:
    data = json.load(f)
to_remove = [k for k, v in data.items() if isinstance(v, dict) and v.get('sessionId') == target_id]
for k in to_remove:
    del data[k]
    print(f'  Removed session key: {k}', file=sys.stderr)
with open(sessions_file, 'w') as f:
    json.dump(data, f, indent=2)
" "$SESSIONS_JSON" "$ID" 2>&1
    done

    # Clean up the backup
    rm -f "$SESSIONS_JSON.bak-cleanup"
fi

# ── Summary ────────────────────────────────────────────────────
echo "[$(date)] Cleanup complete: removed $REMOVED_INACTIVE inactive, $REMOVED_BLOATED bloated"
REMAINING_EXIT=0
REMAINING=$(find "$SESSIONS_DIR" -maxdepth 1 -name '*.jsonl' 2>&1 | wc -l) || REMAINING_EXIT=$?
if [[ $REMAINING_EXIT -ne 0 ]]; then
    REMAINING=0
fi
echo "[$(date)] Remaining session files: $REMAINING"
if [ "$REMAINING" -gt 0 ]; then
    ls_exit=0
    ls -lhS "$SESSIONS_DIR"/*.jsonl 2>&1 | while read -r line; do
        echo "  $line"
    done || ls_exit=$?
fi
