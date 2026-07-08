#!/bin/bash
# memory-shepherd.sh — Periodic memory baseline reset for LLM agents
# Usage: memory-shepherd.sh [agent-name|all]
set -euo pipefail

# Cross-platform stat helpers — BSD (Darwin) / GNU diverge on -c vs -f
_stat_mtime() {
    if [[ "$(uname -s)" == "Darwin" ]]; then
        stat -f %m "$1"
    else
        stat -c %Y "$1"
    fi
}

_stat_size() {
    if [[ "$(uname -s)" == "Darwin" ]]; then
        stat -f %z "$1"
    else
        stat -c %s "$1"
    fi
}

TIMESTAMP=$(date '+%Y-%m-%d_%H%M')
LOCKFILE=/tmp/memory-shepherd.lock
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Logging ────────────────────────────────────────────────────────────

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [memory-shepherd] $1"; }

# ── Lock Management ────────────────────────────────────────────────────

cleanup_lock() { rm -f "$LOCKFILE"; }
trap cleanup_lock EXIT

if [ -f "$LOCKFILE" ]; then
    lock_age=$(( $(date +%s) - $(_stat_mtime "$LOCKFILE") ))
    if [ "$lock_age" -gt 120 ]; then
        log "WARN: Stale lock (age: ${lock_age}s) — removing"
        rm -f "$LOCKFILE"
    else
        log "Another reset running (lock age: ${lock_age}s) — exiting"
        exit 0
    fi
fi
echo $$ > "$LOCKFILE"

# ── Config Parser ──────────────────────────────────────────────────────

declare -A CONFIG
AGENTS=()

find_config() {
    if [ -n "${MEMORY_SHEPHERD_CONF:-}" ] && [ -f "$MEMORY_SHEPHERD_CONF" ]; then
        echo "$MEMORY_SHEPHERD_CONF"
    elif [ -f "$SCRIPT_DIR/memory-shepherd.conf" ]; then
        echo "$SCRIPT_DIR/memory-shepherd.conf"
    elif [ -f "/etc/memory-shepherd/memory-shepherd.conf" ]; then
        echo "/etc/memory-shepherd/memory-shepherd.conf"
    else
        return 1
    fi
}

parse_config() {
    local conf_file="$1"
    local section=""
    while IFS= read -r line; do
        # Strip comments and whitespace
        line="${line%%#*}"
        line="${line#"${line%%[![:space:]]*}"}"
        line="${line%"${line##*[![:space:]]}"}"
        [[ -z "$line" ]] && continue

        if [[ "$line" =~ ^\[([a-zA-Z0-9_-]+)\]$ ]]; then
            section="${BASH_REMATCH[1]}"
            if [[ "$section" != "general" ]]; then
                AGENTS+=("$section")
            fi
            continue
        fi

        if [[ "$line" =~ ^([a-zA-Z_][a-zA-Z0-9_]*)=(.*)$ ]]; then
            CONFIG["${section}.${BASH_REMATCH[1]}"]="${BASH_REMATCH[2]}"
        fi
    done < "$conf_file"
}

cfg() {
    local key="${1}.${2}"
    local default="${3:-}"
    echo "${CONFIG[$key]:-$default}"
}

# ── Load Config ────────────────────────────────────────────────────────

CONF_FILE=$(find_config) || {
    echo "ERROR: No config file found." >&2
    echo "Searched: \$MEMORY_SHEPHERD_CONF, ./memory-shepherd.conf, /etc/memory-shepherd/memory-shepherd.conf" >&2
    exit 1
}

parse_config "$CONF_FILE"
log "Loaded config from $CONF_FILE (${#AGENTS[@]} agents)"

# ── Global Settings ────────────────────────────────────────────────────

BASELINE_DIR=$(cfg general baseline_dir "$SCRIPT_DIR/baselines")
ARCHIVE_DIR=$(cfg general archive_dir "$SCRIPT_DIR/archives")
MAX_MEMORY_SIZE=$(cfg general max_memory_size 16384)
ARCHIVE_RETENTION_DAYS=$(cfg general archive_retention_days 30)
SEPARATOR=$(cfg general separator "---")
MIN_BASELINE_SIZE=$(cfg general min_baseline_size 500)

# Resolve relative paths against script directory
[[ "$BASELINE_DIR" != /* ]] && BASELINE_DIR="$SCRIPT_DIR/$BASELINE_DIR"
[[ "$ARCHIVE_DIR" != /* ]] && ARCHIVE_DIR="$SCRIPT_DIR/$ARCHIVE_DIR"

# ── Reset Functions ────────────────────────────────────────────────────

reset_agent() {
    local agent="$1"
    local memory_file="$2"
    local baseline="$3"
    local archive_dir="$4"

    if [ ! -f "$baseline" ]; then
        log "ERROR: Baseline missing for $agent at $baseline — skipping"
        return 0
    fi

    local baseline_size
    baseline_size=$(_stat_size "$baseline")
    if [ "$baseline_size" -lt "$MIN_BASELINE_SIZE" ]; then
        log "ERROR: Baseline for $agent is suspiciously small (${baseline_size} bytes, min: ${MIN_BASELINE_SIZE}) — skipping"
        return 0
    fi

    if [ ! -f "$memory_file" ]; then
        log "WARN: No memory file for $agent — creating from baseline"
        cp "$baseline" "$memory_file"
        return 0
    fi

    local memory_size
    memory_size=$(_stat_size "$memory_file")
    if [ "$memory_size" -gt "$MAX_MEMORY_SIZE" ]; then
        log "WARN: Memory file for $agent is ${memory_size} bytes (over limit) — forcing reset"
    fi

    local separator_line
    separator_line=$(grep -n "^${SEPARATOR}$" "$memory_file" | tail -1 | cut -d: -f1 || echo "")

    if [ -n "$separator_line" ]; then
        local total_lines
        total_lines=$(wc -l < "$memory_file")
        if [ "$separator_line" -lt "$total_lines" ]; then
            local scratch
            scratch=$(tail -n +"$(($separator_line + 1))" "$memory_file" | sed '/^## Scratch Notes/d' | sed '/^[[:space:]]*$/d')
            if [ -n "$scratch" ]; then
                mkdir -p "$archive_dir"
                local archive_file="$archive_dir/${TIMESTAMP}.md"
                printf "# %s scratch notes — archived %s\n\n%s\n" "$agent" "$TIMESTAMP" "$scratch" > "$archive_file"
                log "Archived scratch notes for $agent ($(echo "$scratch" | wc -l) lines)"
            else
                log "No scratch notes for $agent"
            fi
        else
            log "No scratch notes for $agent"
        fi
    else
        mkdir -p "$archive_dir"
        cp "$memory_file" "$archive_dir/${TIMESTAMP}-full-backup.md"
        log "WARN: No separator in $agent memory — backed up entire file before reset"
    fi

    local tmpfile="${memory_file}.reset-tmp"
    cp "$baseline" "$tmpfile"
    mv -f "$tmpfile" "$memory_file"
    log "Reset $agent MEMORY.md to baseline (${baseline_size} bytes)"
}

reset_remote_agent() {
    local agent="$1"
    local remote_host="$2"
    local remote_user="$3"
    local remote_memory="$4"
    local baseline="$5"
    local archive_dir="$6"

    if [ ! -f "$baseline" ]; then
        log "ERROR: Baseline missing for $agent at $baseline — skipping"
        return 0
    fi

    local baseline_size
    baseline_size=$(_stat_size "$baseline")
    if [ "$baseline_size" -lt "$MIN_BASELINE_SIZE" ]; then
        log "ERROR: Baseline for $agent is suspiciously small (${baseline_size} bytes, min: ${MIN_BASELINE_SIZE}) — skipping"
        return 0
    fi

    # Fetch current memory from remote
    local tmpfile="/tmp/memory-shepherd-${agent}-current.md"
    if ! scp -q "${remote_user}@${remote_host}:${remote_memory}" "$tmpfile" 2>/dev/null; then
        log "WARN: No memory file for $agent on $remote_host — pushing baseline"
        scp -q "$baseline" "${remote_user}@${remote_host}:${remote_memory}"
        return 0
    fi

    local memory_size
    memory_size=$(_stat_size "$tmpfile")
    if [ "$memory_size" -gt "$MAX_MEMORY_SIZE" ]; then
        log "WARN: Memory file for $agent is ${memory_size} bytes (over limit) — forcing reset"
    fi

    # Extract and archive scratch notes locally
    local separator_line
    separator_line=$(grep -n "^${SEPARATOR}$" "$tmpfile" | tail -1 | cut -d: -f1 || echo "")

    if [ -n "$separator_line" ]; then
        local total_lines
        total_lines=$(wc -l < "$tmpfile")
        if [ "$separator_line" -lt "$total_lines" ]; then
            local scratch
            scratch=$(tail -n +"$(($separator_line + 1))" "$tmpfile" | sed '/^## Scratch Notes/d' | sed '/^[[:space:]]*$/d')
            if [ -n "$scratch" ]; then
                mkdir -p "$archive_dir"
                local archive_file="$archive_dir/${TIMESTAMP}.md"
                printf "# %s scratch notes — archived %s\n\n%s\n" "$agent" "$TIMESTAMP" "$scratch" > "$archive_file"
                log "Archived scratch notes for $agent ($(echo "$scratch" | wc -l) lines)"
            else
                log "No scratch notes for $agent"
            fi
        else
            log "No scratch notes for $agent"
        fi
    else
        mkdir -p "$archive_dir"
        cp "$tmpfile" "$archive_dir/${TIMESTAMP}-full-backup.md"
        log "WARN: No separator in $agent memory — backed up entire file before reset"
    fi

    # Push baseline to remote
    scp -q "$baseline" "${remote_user}@${remote_host}:${remote_memory}"
    log "Reset $agent MEMORY.md on $remote_host to baseline (${baseline_size} bytes)"
    rm -f "$tmpfile"
}

# ── Dispatch ───────────────────────────────────────────────────────────

process_agent() {
    local agent="$1"

    local memory_file
    memory_file=$(cfg "$agent" memory_file "")
    local baseline_name
    baseline_name=$(cfg "$agent" baseline "")
    local archive_subdir
    archive_subdir=$(cfg "$agent" archive_subdir "$agent")
    local archive_path="$ARCHIVE_DIR/$archive_subdir"

    if [ -z "$baseline_name" ]; then
        log "ERROR: No baseline defined for agent '$agent' — skipping"
        return 0
    fi

    local baseline_path="$BASELINE_DIR/$baseline_name"
    local remote_host
    remote_host=$(cfg "$agent" remote_host "")

    if [ -n "$remote_host" ]; then
        local remote_user
        remote_user=$(cfg "$agent" remote_user "$(whoami)")
        local remote_memory
        remote_memory=$(cfg "$agent" remote_memory "")

        if [ -z "$remote_memory" ]; then
            log "ERROR: remote_host set for '$agent' but no remote_memory — skipping"
            return 0
        fi

        reset_remote_agent "$agent" "$remote_host" "$remote_user" "$remote_memory" "$baseline_path" "$archive_path"
    else
        if [ -z "$memory_file" ]; then
            log "ERROR: No memory_file defined for agent '$agent' — skipping"
            return 0
        fi

        reset_agent "$agent" "$memory_file" "$baseline_path" "$archive_path"
    fi
}

# ── Main ───────────────────────────────────────────────────────────────

TARGET="${1:-all}"

if [ "$TARGET" = "all" ]; then
    if [ ${#AGENTS[@]} -eq 0 ]; then
        log "No agents defined in config"
        exit 0
    fi
    for agent in "${AGENTS[@]}"; do
        process_agent "$agent"
    done
else
    # Check if the agent exists in config
    found=false
    for agent in "${AGENTS[@]}"; do
        if [ "$agent" = "$TARGET" ]; then
            found=true
            break
        fi
    done

    if [ "$found" = false ]; then
        echo "ERROR: Unknown agent '$TARGET'" >&2
        echo "Available agents: ${AGENTS[*]}" >&2
        echo "Usage: memory-shepherd.sh [agent-name|all]" >&2
        exit 1
    fi

    process_agent "$TARGET"
fi

# ── Cleanup ────────────────────────────────────────────────────────────

# Purge old archives
find "$ARCHIVE_DIR" -name "*.md" -mtime +"$ARCHIVE_RETENTION_DAYS" -delete 2>/dev/null || true

# Rotate log if over 1MB
local_log="$ARCHIVE_DIR/reset.log"
if [ -f "$local_log" ] && [ "$(_stat_size "$local_log" 2>/dev/null || echo 0)" -gt 1048576 ]; then
    mv "$local_log" "$local_log.old"
    log "Rotated log file"
fi

log "Done"
