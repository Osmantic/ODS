#!/bin/bash
# uninstall.sh — Remove memory-shepherd systemd timers
set -euo pipefail

PREFIX=""
USER_MODE=false

# ── Usage ──────────────────────────────────────────────────────────────

usage() {
    cat <<EOF
Usage: uninstall.sh [OPTIONS]

Remove all memory-shepherd systemd timers and service files.
Does NOT remove config, baselines, or archives.

Options:
  --prefix DIR    Systemd unit file directory (must match what install.sh used)
  -h, --help      Show this help
EOF
    exit 0
}

# ── Parse Args ─────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        --prefix)  PREFIX="$2"; shift 2 ;;
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
    [[ "$PREFIX" == *".config/systemd/user"* ]] && USER_MODE=true
fi

SYSTEMCTL_FLAG=""
$USER_MODE && SYSTEMCTL_FLAG="--user"

# ── Find Units ─────────────────────────────────────────────────────────

TIMERS=()
SERVICES=()

for f in "$PREFIX"/memory-shepherd*.timer; do
    [ -f "$f" ] && TIMERS+=("$(basename "$f")")
done

for f in "$PREFIX"/memory-shepherd*.service; do
    [ -f "$f" ] && SERVICES+=("$(basename "$f")")
done

if [ ${#TIMERS[@]} -eq 0 ] && [ ${#SERVICES[@]} -eq 0 ]; then
    echo "No memory-shepherd units found in $PREFIX"
    exit 0
fi

echo "Found in $PREFIX:"
for t in "${TIMERS[@]}"; do echo "  timer:   $t"; done
for s in "${SERVICES[@]}"; do echo "  service: $s"; done
echo ""

# ── Stop and Disable ──────────────────────────────────────────────────

for timer in "${TIMERS[@]}"; do
    echo "Stopping and disabling $timer..."
    systemctl $SYSTEMCTL_FLAG stop "$timer" 2>/dev/null || true
    systemctl $SYSTEMCTL_FLAG disable "$timer" 2>/dev/null || true
done

for service in "${SERVICES[@]}"; do
    systemctl $SYSTEMCTL_FLAG stop "$service" 2>/dev/null || true
done

# ── Remove Files ──────────────────────────────────────────────────────

for timer in "${TIMERS[@]}"; do
    rm -f "$PREFIX/$timer"
    echo "Removed $PREFIX/$timer"
done

for service in "${SERVICES[@]}"; do
    rm -f "$PREFIX/$service"
    echo "Removed $PREFIX/$service"
done

systemctl $SYSTEMCTL_FLAG daemon-reload
echo ""
echo "Done. Removed ${#TIMERS[@]} timer(s) and ${#SERVICES[@]} service(s)."
echo "Config, baselines, and archives were NOT removed."
