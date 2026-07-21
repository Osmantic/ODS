#!/bin/bash
# Token Spy Session Manager — cost-aware session cleanup
# Queries the token monitor API for real token economics instead of checking file sizes.
# Primary defense is your agent framework's native compaction. This script only
# intervenes as a safety valve when compaction fails and sessions exceed limits.
#
# Runs periodically via systemd timer or cron.
#
# Configure agents in the AGENTS array below. Format:
#   "agent-name|monitor-port|/path/to/sessions/dir"

set -euo pipefail

# ── Configuration ──────────────────────────────────────────────────────────────

MONITOR_HOST="${MONITOR_HOST:-127.0.0.1}"

# Define your agents here.
# Format: "agent-name|proxy-port|sessions-directory"
# Example:
#   AGENTS=(
#     "my-agent|9110|/home/user/.openclaw/agents/main/sessions"
#     "my-other-agent|9111|/home/user/other/.openclaw/agents/main/sessions"
#   )
AGENTS=(
  "openclaw|9110|~/ods/data/openclaw/home/agents/main/sessions"
)

# Remote agents: "agent-name|remote-host|remote-sessions-dir"
REMOTE_AGENTS=()

RECENT_MINUTES=15  # Protect sessions touched in last N minutes

# Dynamic settings: read from Token Monitor API (dashboard-editable)
# Falls back to defaults if the API is unreachable.
DEFAULT_CHAR_LIMIT=80000

# Remote agents use file-size limit (bytes) as proxy for char limit
REMOTE_FILE_SIZE_LIMIT=200000

get_agent_char_limit() {
  local agent="$1" port="$2"
  local limit

  local pycmd="python3"
  if [[ -f "$(dirname "$0")/../../../lib/python-cmd.sh" ]]; then
    . "$(dirname "$0")/../../../lib/python-cmd.sh"
    pycmd="$(ods_detect_python_cmd)"
  elif command -v python >/dev/null 2>&1; then
    pycmd="python"
  fi

  limit=$(curl -sf --max-time 3 "http://${MONITOR_HOST}:${port}/api/session-status?agent=${agent}" 2>/dev/null | "$pycmd" -c "import json,sys; print(json.load(sys.stdin).get('session_char_limit', $DEFAULT_CHAR_LIMIT))" 2>/dev/null || echo "$DEFAULT_CHAR_LIMIT")
  echo "$limit"
}

# ── Functions ──────────────────────────────────────────────────────────────────

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

query_status() {
  local agent="$1" port="$2"
  curl -sf --max-time 5 "http://${MONITOR_HOST}:${port}/api/session-status?agent=${agent}" 2>/dev/null || echo '{"recommendation":"unavailable"}'
}

# Emit every sessionId in a sessions.json, one per line.
#
# Must not assume one id per line: sessions.json is written by the agent
# framework, and a compact document puts every entry on a single line. A
# greedy `sed 's/.*"sessionId": *"\([^"]*\)".*/\1/'` then reports only the
# LAST id on that line, so every other live session reads as inactive and
# gets deleted. Match each occurrence instead, as scripts/session-cleanup.sh
# already does.
extract_active_ids() {
  local sessions_json="$1"
  grep -oE '"sessionId"[[:space:]]*:[[:space:]]*"[^"]+"' "$sessions_json" 2>/dev/null \
    | sed -E 's/.*"([^"]+)"[[:space:]]*$/\1/' || true
}

clean_inactive() {
  local sessions_dir="$1"
  local sessions_json="${sessions_dir}/sessions.json"

  find "$sessions_dir" -name '*.deleted.*' -delete 2>/dev/null || true
  find "$sessions_dir" -name '*.bak*' -mmin +60 -delete 2>/dev/null || true

  [ -f "$sessions_json" ] || return 0

  local active_ids
  active_ids=$(extract_active_ids "$sessions_json")

  # No ids out of sessions.json means the parse failed or the file is a
  # partial write (a crash mid-rewrite leaves 0 bytes), not that every
  # session is idle. Deleting on that reading wipes live sessions, so any
  # empty extraction fails closed — matching the remote-path guard.
  if [ -z "$active_ids" ]; then
    log "  [WARN] No session ids parsed from $sessions_json — skipping cleanup"
    return 0
  fi

  for f in "$sessions_dir"/*.jsonl; do
    [ -f "$f" ] || continue
    local basename
    basename=$(basename "$f" .jsonl)

    local is_active=false
    for id in $active_ids; do
      [ "$basename" = "$id" ] && { is_active=true; break; }
    done

    if [ "$is_active" = false ]; then
      local size_h
      size_h=$(du -h "$f" | cut -f1)
      log "  [CLEANUP] Removing inactive session: $basename ($size_h)"
      rm -f "$f"
    fi
  done
}

kill_session() {
  local sessions_dir="$1" session_id="$2" reason="$3"
  local sessions_json="${sessions_dir}/sessions.json"

  local f="${sessions_dir}/${session_id}.jsonl"
  if [ -f "$f" ]; then
    local size_h
    size_h=$(du -h "$f" | cut -f1)
    log "  [KILL] Removing session $session_id ($size_h) — $reason"
    rm -f "$f"
  fi

  if [ -f "$sessions_json" ]; then
    cp "$sessions_json" "${sessions_json}.bak-manager"
    local pycmd="python3"
    if [[ -f "$(dirname "$0")/../../../lib/python-cmd.sh" ]]; then
      . "$(dirname "$0")/../../../lib/python-cmd.sh"
      pycmd="$(ods_detect_python_cmd)"
    elif command -v python >/dev/null 2>&1; then
      pycmd="python"
    fi

    "$pycmd" -c "
import json, sys
with open('$sessions_json', 'r') as f:
    data = json.load(f)
to_remove = [k for k, v in data.items() if isinstance(v, dict) and v.get('sessionId') == '$session_id']
for k in to_remove:
    del data[k]
    print(f'  Removed session key: {k}', file=sys.stderr)
with open('$sessions_json', 'w') as f:
    json.dump(data, f, indent=2)
" 2>&1
  fi
}

enforce_count_limit() {
  local sessions_dir="$1"
  local max_sessions=5
  local now
  now=$(date +%s)

  local remaining=()
  while IFS= read -r f; do
    remaining+=("$f")
  done < <(ls -t "$sessions_dir"/*.jsonl 2>/dev/null)

  local count=${#remaining[@]}
  if [ "$count" -le "$max_sessions" ]; then
    return 0
  fi

  log "  [COUNT] $count sessions exceed max of $max_sessions, trimming oldest"
  for (( i=max_sessions; i<count; i++ )); do
    local f="${remaining[$i]}"
    local basename
    basename=$(basename "$f" .jsonl)
    local mtime
    mtime=$(stat -c%Y "$f" 2>/dev/null || echo 0)
    local age_mins=$(( (now - mtime) / 60 ))

    if [ "$age_mins" -le "$RECENT_MINUTES" ]; then
      log "  [COUNT] Skipping $basename — touched ${age_mins}m ago (hot)"
      continue
    fi

    kill_session "$sessions_dir" "$basename" "excess session (${age_mins}m old)"
  done
}

# ── Remote Agent Management ────────────────────────────────────────────────────

manage_remote_agent() {
  local agent="$1" host="$2" remote_dir="$3"
  local size_limit="$REMOTE_FILE_SIZE_LIMIT"
  local max_sessions=5

  log "Checking $agent (remote: $host, local model, \$0.00/turn)"

  local remote_info
  # Unquoted heredoc: ${remote_dir} must expand locally before remote execution
  remote_info=$(ssh -o ConnectTimeout=5 -o BatchMode=yes "${host}" bash << REMOTESCRIPT
    SESSIONS_DIR="${remote_dir}"
    if [ ! -d "\$SESSIONS_DIR" ]; then
      echo "NO_DIR"
      exit 0
    fi
    echo "SESSION_LIST_START"
    for f in "\$SESSIONS_DIR"/*.jsonl; do
      [ -f "\$f" ] || continue
      sid=\$(basename "\$f" .jsonl)
      sz=\$(stat -c%s "\$f" 2>/dev/null || echo 0)
      mt=\$(stat -c%Y "\$f" 2>/dev/null || echo 0)
      echo "\${sid}|\${sz}|\${mt}"
    done
    echo "SESSION_LIST_END"
    if [ -f "\$SESSIONS_DIR/sessions.json" ]; then
      echo "ACTIVE_IDS_START"
      # Per-occurrence match: a compact sessions.json holds every entry on one
      # line, and a greedy sed would report only the last id there.
      grep -oE '"sessionId"[[:space:]]*:[[:space:]]*"[^"]+"' "\$SESSIONS_DIR/sessions.json" 2>/dev/null \
        | sed -E 's/.*"([^"]+)"[[:space:]]*\$/\1/' || true
      echo "ACTIVE_IDS_END"
    fi
    echo "TOTAL_SIZE=\$(du -sb "\$SESSIONS_DIR" 2>/dev/null | cut -f1)"
    find "\$SESSIONS_DIR" -name '*.deleted.*' -delete 2>/dev/null || true
    find "\$SESSIONS_DIR" -name '*.bak*' -mmin +60 -delete 2>/dev/null || true
REMOTESCRIPT
) || remote_info="SSH_FAILED"

  if [ "$remote_info" = "SSH_FAILED" ]; then
    log "  [WARN] SSH to $host failed — skipping $agent"
    return 0
  fi

  if echo "$remote_info" | grep -q "NO_DIR"; then
    log "  [OK] No sessions directory on $host"
    return 0
  fi

  local total_size
  total_size=$(echo "$remote_info" | grep "^TOTAL_SIZE=" | cut -d= -f2)
  log "  Total sessions size: $(( ${total_size:-0} / 1024 ))KB (cost: \$0.00)"

  local active_ids=""
  if echo "$remote_info" | grep -q "ACTIVE_IDS_START"; then
    active_ids=$(echo "$remote_info" | sed -n '/ACTIVE_IDS_START/,/ACTIVE_IDS_END/p' | grep -vE '_START|_END')
    # The block is only emitted when the remote sessions.json exists, so an
    # empty one means the parse failed, not that every session is idle.
    if [ -z "$active_ids" ]; then
      log "  [WARN] No session ids parsed from $host sessions.json — skipping $agent"
      return 0
    fi
  fi

  local now
  now=$(date +%s)
  local session_count=0
  local to_remove=()

  while IFS='|' read -r sid size mtime; do
    [ -z "$sid" ] && continue
    session_count=$((session_count + 1))

    local is_active=false
    for aid in $active_ids; do
      [ "$sid" = "$aid" ] && { is_active=true; break; }
    done

    if [ "$is_active" = false ]; then
      to_remove+=("$sid")
      log "  [CLEANUP] Inactive session: $sid ($(( size / 1024 ))KB)"
      continue
    fi

    if [ "$size" -gt "$size_limit" ]; then
      local age_mins=$(( (now - mtime) / 60 ))
      if [ "$age_mins" -gt "$RECENT_MINUTES" ]; then
        to_remove+=("$sid")
        log "  [KILL] Oversized session: $sid ($(( size / 1024 ))KB > $(( size_limit / 1024 ))KB)"
      else
        log "  [WARN] Oversized session $sid ($(( size / 1024 ))KB) but hot (${age_mins}m) — skipping"
      fi
    fi
  done < <(echo "$remote_info" | sed -n '/SESSION_LIST_START/,/SESSION_LIST_END/p' | grep -vE '_START|_END' | grep '|')

  log "  Sessions: $session_count total, ${#to_remove[@]} to remove"

  if [ "${#to_remove[@]}" -gt 0 ]; then
    local rm_args=""
    for sid in "${to_remove[@]}"; do
      rm_args="${rm_args} ${remote_dir}/${sid}.jsonl"
    done
    ssh -o ConnectTimeout=5 -o BatchMode=yes "${host}" "rm -f ${rm_args}" 2>/dev/null || true
    log "  [DONE] Removed ${#to_remove[@]} sessions on $host"
  else
    log "  [OK] No cleanup needed"
  fi

  log "  Done"
}

# ── Main Loop ──────────────────────────────────────────────────────────────────

if [ ${#AGENTS[@]} -eq 0 ]; then
  log "No agents configured in AGENTS array. Edit session-manager.sh to add your agents."
  exit 0
fi

log "=== Session Manager Start ==="

for agent_entry in "${AGENTS[@]}"; do
  IFS='|' read -r agent port sessions_dir <<< "$agent_entry"
  log "Checking $agent (port $port)"

  status_json=$(query_status "$agent" "$port")
  pycmd="python3"
  if [[ -f "$(dirname "$0")/../../../lib/python-cmd.sh" ]]; then
    . "$(dirname "$0")/../../../lib/python-cmd.sh"
    pycmd="$(ods_detect_python_cmd)"
  elif command -v python >/dev/null 2>&1; then
    pycmd="python"
  fi

  rec=$(echo "$status_json" | "$pycmd" -c "import json,sys; print(json.load(sys.stdin).get('recommendation','unknown'))" 2>/dev/null || echo "unknown")
  history=$(echo "$status_json" | "$pycmd" -c "import json,sys; print(json.load(sys.stdin).get('current_history_chars',0))" 2>/dev/null || echo "0")
  turns=$(echo "$status_json" | "$pycmd" -c "import json,sys; print(json.load(sys.stdin).get('current_session_turns',0))" 2>/dev/null || echo "0")
  session_cost=$(echo "$status_json" | "$pycmd" -c "import json,sys; print(json.load(sys.stdin).get('cost_since_last_reset',0))" 2>/dev/null || echo "0")

  char_limit=$(get_agent_char_limit "$agent" "$port")
  log "  Status: recommendation=$rec history=${history}ch / ${char_limit}ch limit | turns=$turns cost=\$${session_cost}"

  case "$rec" in
    healthy|no_data)
      log "  [OK] Session healthy, no action needed"
      ;;
    monitor)
      log "  [WATCH] Session growing, compaction should trigger soon"
      ;;
    compact_soon)
      log "  [WARN] Session approaching limit — compaction expected"
      ;;
    reset_recommended)
      log "  [CRITICAL] History exceeds ${char_limit}ch limit (at ${history}ch) — compaction may have failed"
      if [ -d "$sessions_dir" ]; then
        largest=$(ls -S "$sessions_dir"/*.jsonl 2>/dev/null | head -1)
        if [ -n "$largest" ]; then
          basename=$(basename "$largest" .jsonl)
          kill_session "$sessions_dir" "$basename" "safety valve: history=${history}ch, compaction failed"
        fi
      fi
      ;;
    cache_unstable)
      log "  [ALERT] Cache write percentage unusually high — possible cache thrashing"
      ;;
    unavailable)
      log "  [WARN] Token monitor unavailable on port $port — falling back to file cleanup only"
      ;;
    *)
      log "  [WARN] Unknown recommendation: $rec"
      ;;
  esac

  if [ -d "$sessions_dir" ]; then
    clean_inactive "$sessions_dir"
    enforce_count_limit "$sessions_dir"
  fi

  log "  Done"
done

# ── Remote Agents ──────────────────────────────────────────────────────────

for agent_entry in "${REMOTE_AGENTS[@]}"; do
  IFS='|' read -r agent host remote_dir <<< "$agent_entry"
  manage_remote_agent "$agent" "$host" "$remote_dir"
done

# ── Summary ────────────────────────────────────────────────────────────────

log "=== Session Manager Complete ==="
for agent_entry in "${AGENTS[@]}"; do
  IFS='|' read -r agent port sessions_dir <<< "$agent_entry"
  if [ -d "$sessions_dir" ]; then
    count=$(ls "$sessions_dir"/*.jsonl 2>/dev/null | wc -l)
    log "  $agent: $count sessions remaining"
    ls -lht "$sessions_dir"/*.jsonl 2>/dev/null | head -5 || true
  fi
done
for agent_entry in "${REMOTE_AGENTS[@]}"; do
  IFS='|' read -r agent host remote_dir <<< "$agent_entry"
  count=$(ssh -o ConnectTimeout=5 -o BatchMode=yes "${host}" "ls ${remote_dir}/*.jsonl 2>/dev/null | wc -l" 2>/dev/null || echo "?")
  log "  $agent (remote $host): $count sessions remaining"
done
