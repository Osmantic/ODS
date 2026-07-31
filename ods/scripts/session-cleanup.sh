#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# ODS - Session Cleanup Script
# https://github.com/Osmantic/ODS
#
# Prevents context overflow crashes by automatically managing
# session file lifecycle. When a session file exceeds the size
# threshold, its reference is atomically removed from sessions.json
# before the file is deleted, forcing the gateway to create a fresh
# session without leaving a dangling index entry on interruption.
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
    echo "Exit: 0 on success or when paths are missing (skipped with a log message);"
    echo "      1 when Python or sessions.json validation fails before any session mutation."
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

# ── Python and index validation ────────────────────────────────
# Resolve and validate before any cleanup mutation. Treating malformed or
# partially-written JSON as an empty active set would otherwise delete every
# .jsonl file as inactive.
PYTHON_CMD="python3"
if [[ -f "$(dirname "$0")/../lib/python-cmd.sh" ]]; then
    . "$(dirname "$0")/../lib/python-cmd.sh"
    PYTHON_CMD="$(ods_detect_python_cmd)" || PYTHON_CMD=""
elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
fi
if [[ -z "$PYTHON_CMD" ]] || ! "$PYTHON_CMD" -c 'import json' >/dev/null 2>&1; then
    echo "[$(date)] ERROR: no usable Python found; refusing to prune sessions (sessions.json updates need it)" >&2
    exit 1
fi

ACTIVE_IDS_OUTPUT=""
ACTIVE_IDS_EXIT=0
ACTIVE_IDS_OUTPUT=$("$PYTHON_CMD" - "$SESSIONS_JSON" <<'PY'
import json
import sys

sessions_file = sys.argv[1]
try:
    with open(sessions_file, "r", encoding="utf-8") as handle:
        data = json.load(handle)
except (OSError, UnicodeError, json.JSONDecodeError) as exc:
    print(f"invalid sessions index: {exc}", file=sys.stderr)
    raise SystemExit(1)

if not isinstance(data, dict):
    print("invalid sessions index: root must be an object", file=sys.stderr)
    raise SystemExit(1)

for value in data.values():
    if not isinstance(value, dict):
        continue
    session_id = value.get("sessionId")
    if not isinstance(session_id, str) or not session_id:
        continue
    if "\n" in session_id or "\r" in session_id:
        print("invalid sessions index: sessionId contains a line break", file=sys.stderr)
        raise SystemExit(1)
    print(session_id)
PY
) || ACTIVE_IDS_EXIT=$?
if [[ $ACTIVE_IDS_EXIT -ne 0 ]]; then
    echo "[$(date)] ERROR: sessions.json is invalid; refusing to prune sessions" >&2
    exit 1
fi

ACTIVE_IDS=()
while IFS= read -r ID; do
    ID="${ID%$'\r'}"
    [[ -n "$ID" ]] && ACTIVE_IDS+=("$ID")
done <<< "$ACTIVE_IDS_OUTPUT"

echo "[$(date)] Session cleanup starting"
echo "[$(date)] Sessions dir: $SESSIONS_DIR"
echo "[$(date)] Max size threshold: $MAX_SIZE bytes"
echo "[$(date)] Active sessions found: ${#ACTIVE_IDS[@]}"

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
WIPE_IDS=()
WIPE_FILES=()
REMOVED_INACTIVE=0
REMOVED_BLOATED=0

for f in "$SESSIONS_DIR"/*.jsonl; do
    [ -f "$f" ] || continue
    BASENAME=$(basename "$f" .jsonl)

    # Check if this session is active
    IS_ACTIVE=false
    for ID in "${ACTIVE_IDS[@]}"; do
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
            echo "[$(date)] Session $BASENAME is bloated ($SIZE > ${SIZE_LABEL}), scheduling a fresh session"
            WIPE_IDS+=("$BASENAME")
            WIPE_FILES+=("$f")
        fi
    fi
done

# ── Commit index first, then delete unreferenced session files ─
if [[ ${#WIPE_IDS[@]} -gt 0 ]]; then
    echo "[$(date)] Clearing ${#WIPE_IDS[@]} session reference(s) from sessions.json"
    "$PYTHON_CMD" - "$SESSIONS_JSON" "${WIPE_IDS[@]}" <<'PY'
import json
import os
import stat
import sys
import tempfile

sessions_file = os.path.abspath(sys.argv[1])
target_ids = set(sys.argv[2:])
parent = os.path.dirname(sessions_file)
temp_path = None

with open(sessions_file, "r", encoding="utf-8") as handle:
    data = json.load(handle)
if not isinstance(data, dict):
    raise SystemExit("sessions index root must be an object")

to_remove = [
    key
    for key, value in data.items()
    if isinstance(value, dict) and value.get("sessionId") in target_ids
]
for key in to_remove:
    del data[key]
    print(f"  Removed session key: {key}", file=sys.stderr)

original_mode = stat.S_IMODE(os.stat(sessions_file).st_mode)
try:
    descriptor, temp_path = tempfile.mkstemp(
        prefix=".sessions.json.",
        suffix=".tmp",
        dir=parent,
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temp_path, original_mode)
    os.replace(temp_path, sessions_file)
    temp_path = None
finally:
    if temp_path is not None:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
PY

    for f in "${WIPE_FILES[@]}"; do
        if [[ -f "$f" ]]; then
            rm -f "$f"
            REMOVED_BLOATED=$((REMOVED_BLOATED + 1))
        fi
    done
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
