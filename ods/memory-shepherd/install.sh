#!/bin/bash
# install.sh — Generate and install systemd timers for memory-shepherd
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHEPHERD="$SCRIPT_DIR/memory-shepherd.sh"
DRY_RUN=false
PREFIX=""
USER_MODE=false

# ── Usage ──────────────────────────────────────────────────────────────

usage() {
    cat <<EOF
Usage: install.sh [OPTIONS]

Generate and install systemd timers for memory-shepherd.

Options:
  --prefix DIR    Systemd unit file directory
                  Default: /etc/systemd/system (root) or ~/.config/systemd/user (non-root)
  --dry-run       Show what would be installed without making changes
  -h, --help      Show this help

The installer reads memory-shepherd.conf to discover agents and creates
a systemd timer + service pair for each one, plus an "all" timer.
EOF
    exit 0
}

# ── Parse Args ─────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        --prefix)  PREFIX="$2"; shift 2 ;;
        --dry-run) DRY_RUN=true; shift ;;
        -h|--help) usage ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# ── Detect Mode ────────────────────────────────────────────────────────

if [ -z "$PREFIX" ]; then
    if [ "$(id -u)" -eq 0 ]; then
        PREFIX="/etc/systemd/system"
    else
        PREFIX="$HOME/.config/systemd/user"
        USER_MODE=true
    fi
else
    # If custom prefix is under user config, use user mode
    [[ "$PREFIX" == *".config/systemd/user"* ]] && USER_MODE=true
fi

SYSTEMCTL_FLAG=""
$USER_MODE && SYSTEMCTL_FLAG="--user"

# ── Config Parser (minimal — just need agent names) ────────────────────

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

CONF_FILE=$(find_config) || {
    echo "ERROR: No memory-shepherd.conf found." >&2
    echo "Create one from memory-shepherd.conf.example first." >&2
    exit 1
}

parse_config "$CONF_FILE"

if [ ${#AGENTS[@]} -eq 0 ]; then
    echo "ERROR: No agents defined in $CONF_FILE" >&2
    exit 1
fi

echo "Config:  $CONF_FILE"
echo "Agents:  ${AGENTS[*]}"
echo "Prefix:  $PREFIX"
echo "Mode:    $($USER_MODE && echo "user" || echo "system")"
echo ""

# ── Create Directories ─────────────────────────────────────────────────

BASELINE_DIR="${CONFIG[general.baseline_dir]:-./baselines}"
ARCHIVE_DIR="${CONFIG[general.archive_dir]:-./archives}"
[[ "$BASELINE_DIR" != /* ]] && BASELINE_DIR="$SCRIPT_DIR/$BASELINE_DIR"
[[ "$ARCHIVE_DIR" != /* ]] && ARCHIVE_DIR="$SCRIPT_DIR/$ARCHIVE_DIR"

if ! $DRY_RUN; then
    mkdir -p "$PREFIX" "$BASELINE_DIR" "$ARCHIVE_DIR"
    for agent in "${AGENTS[@]}"; do
        subdir="${CONFIG[${agent}.archive_subdir]:-$agent}"
        mkdir -p "$ARCHIVE_DIR/$subdir"
    done
fi

# ── Generate Units ─────────────────────────────────────────────────────

generate_service() {
    local name="$1"
    local target="$2"  # agent name or "all"
    local description="$3"

    cat <<EOF
[Unit]
Description=$description

[Service]
Type=oneshot
ExecStart=$SHEPHERD $target
EOF
}

generate_timer() {
    local name="$1"
    local description="$2"
    local on_calendar="$3"
    local randomized_delay="$4"

    cat <<EOF
[Unit]
Description=$description

[Timer]
OnCalendar=$on_calendar
RandomizedDelaySec=$randomized_delay
Persistent=true

[Install]
WantedBy=timers.target
EOF
}

INSTALLED_TIMERS=()

# Timer for "all" agents — runs every 3 hours with some jitter
SERVICE_NAME="memory-shepherd"
SERVICE_FILE="$PREFIX/${SERVICE_NAME}.service"
TIMER_FILE="$PREFIX/${SERVICE_NAME}.timer"

echo "--- memory-shepherd.service (resets all agents) ---"
SERVICE_CONTENT=$(generate_service "$SERVICE_NAME" "all" "Memory Shepherd — reset all agents")
TIMER_CONTENT=$(generate_timer "$SERVICE_NAME" "Memory Shepherd — periodic reset" "*-*-* 00/3:00:00" "5min")

if $DRY_RUN; then
    echo "$SERVICE_CONTENT"
    echo ""
    echo "--- memory-shepherd.timer ---"
    echo "$TIMER_CONTENT"
    echo ""
else
    echo "$SERVICE_CONTENT" > "$SERVICE_FILE"
    echo "$TIMER_CONTENT" > "$TIMER_FILE"
    echo "  Wrote $SERVICE_FILE"
    echo "  Wrote $TIMER_FILE"
fi
INSTALLED_TIMERS+=("${SERVICE_NAME}.timer")

# Per-agent timers — staggered by 10 minutes
stagger=0
for agent in "${AGENTS[@]}"; do
    SERVICE_NAME="memory-shepherd-${agent}"
    SERVICE_FILE="$PREFIX/${SERVICE_NAME}.service"
    TIMER_FILE="$PREFIX/${SERVICE_NAME}.timer"

    # Stagger: offset each agent by 10 minutes within the 3-hour window
    stagger_min=$((stagger * 10))
    if [ "$stagger_min" -eq 0 ]; then
        calendar="*-*-* 00/3:00:00"
    else
        calendar="*-*-* 00/3:${stagger_min}:00"
    fi

    echo ""
    echo "--- ${SERVICE_NAME}.service ---"
    SERVICE_CONTENT=$(generate_service "$SERVICE_NAME" "$agent" "Memory Shepherd — reset $agent")
    TIMER_CONTENT=$(generate_timer "$SERVICE_NAME" "Memory Shepherd — periodic reset for $agent" "$calendar" "2min")

    if $DRY_RUN; then
        echo "$SERVICE_CONTENT"
        echo ""
        echo "--- ${SERVICE_NAME}.timer ---"
        echo "$TIMER_CONTENT"
    else
        echo "$SERVICE_CONTENT" > "$SERVICE_FILE"
        echo "$TIMER_CONTENT" > "$TIMER_FILE"
        echo "  Wrote $SERVICE_FILE"
        echo "  Wrote $TIMER_FILE"
    fi
    INSTALLED_TIMERS+=("${SERVICE_NAME}.timer")
    stagger=$((stagger + 1))
done

# ── Enable Timers ──────────────────────────────────────────────────────

if $DRY_RUN; then
    echo ""
    echo "=== DRY RUN — no files written, no timers enabled ==="
    echo "Would install: ${INSTALLED_TIMERS[*]}"
else
    echo ""
    systemctl $SYSTEMCTL_FLAG daemon-reload

    # Only enable the "all" timer by default; per-agent timers are available but not auto-enabled
    systemctl $SYSTEMCTL_FLAG enable --now "memory-shepherd.timer"
    echo "Enabled: memory-shepherd.timer (resets all agents every 3 hours)"
    echo ""
    echo "Per-agent timers installed but not enabled (use if you want individual schedules):"
    for agent in "${AGENTS[@]}"; do
        echo "  systemctl $SYSTEMCTL_FLAG enable --now memory-shepherd-${agent}.timer"
    done
fi

# ── Summary ────────────────────────────────────────────────────────────

echo ""
echo "=== Summary ==="
echo "Config:        $CONF_FILE"
echo "Agents:        ${AGENTS[*]}"
echo "Baselines:     $BASELINE_DIR"
echo "Archives:      $ARCHIVE_DIR"
echo "Timer units:   $PREFIX/memory-shepherd*.{timer,service}"
echo ""
echo "Useful commands:"
echo "  memory-shepherd.sh all              # Manual reset (all agents)"
echo "  memory-shepherd.sh <agent-name>     # Manual reset (single agent)"
echo "  systemctl $SYSTEMCTL_FLAG list-timers | grep memory  # Check timer status"
echo "  journalctl $SYSTEMCTL_FLAG -u memory-shepherd        # View logs"
