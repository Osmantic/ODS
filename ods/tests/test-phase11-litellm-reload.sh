#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE="$ROOT_DIR/installers/phases/11-services.sh"
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TEMP_DIR"' EXIT
fail() { echo "[FAIL] $*" >&2; exit 1; }
eval "$(sed -n '/^_phase11_refresh_litellm() {/,/^}$/p' "$PHASE")"
declare -F _phase11_refresh_litellm >/dev/null || fail 'installer has no gateway reload boundary'

LOG_FILE="$TEMP_DIR/install.log"
CALL_LOG="$TEMP_DIR/calls"
# Read by the extracted production function.
# shellcheck disable=SC2034
DOCKER_COMPOSE_CMD=mock_compose
# shellcheck disable=SC2034
COMPOSE_FLAGS_ARR=(-f 'a path/base.yml' -f external.yml)
ai() { :; }
ai_bad() { echo "$*" >> "$LOG_FILE"; }
mock_compose() {
    printf '%s\n' "$*" >> "$CALL_LOG"
    [[ "$1" == -f && "$2" == 'a path/base.yml' && "$3" == -f && "$4" == external.yml ]] || return 99
    shift 4
    if [[ "$*" == 'config --services' ]]; then
        printf '%s\n' "$SERVICES"
        return "$CONFIG_STATUS"
    fi
    [[ "$*" == 'up -d --no-deps --force-recreate --no-build --pull never litellm' ]] || return 98
    return "$RECREATE_STATUS"
}
SERVICES=$'dashboard\nlitellm\nsearxng'
CONFIG_STATUS=0 RECREATE_STATUS=0
_phase11_refresh_litellm || fail 'gateway reload failed'
grep -q 'up -d --no-deps --force-recreate --no-build --pull never litellm$' "$CALL_LOG" || fail 'old bind-mounted config can survive reinstall'
echo '[PASS] enabled gateway is recreated with the selected Compose stack only'

: > "$CALL_LOG"
SERVICES=$'dashboard\nlitellm-helper'
_phase11_refresh_litellm || fail 'disabled gateway should be a no-op'
! grep -q ' up ' "$CALL_LOG" || fail 'disabled gateway was launched'
echo '[PASS] absent service is not enabled by the repair'

: > "$CALL_LOG"
SERVICES=litellm CONFIG_STATUS=1
if _phase11_refresh_litellm; then fail 'service inventory error was ignored'; fi
! grep -q ' up ' "$CALL_LOG" || fail 'gateway changed after inventory error'
echo '[PASS] failed service inventory prevents mutation'

CONFIG_STATUS=0 RECREATE_STATUS=1
if _phase11_refresh_litellm; then fail 'failed gateway reload was ignored'; fi
echo '[PASS] gateway recreation failure propagates'

# Execute the real orchestration boundary, not a copy of its commands.
boundary="$(sed -n '/^    if ! _phase11_refresh_litellm; then/,/^    _phase11_write_compose_launch_record/p' "$PHASE")"
[[ -n "$boundary" ]] || fail 'gateway refresh is not wired before Pixel'
ods_pixel_install_default_agent() { echo pixel >> "$CALL_LOG"; }
_phase11_write_compose_launch_record() { echo launch >> "$CALL_LOG"; }
: > "$CALL_LOG"
if (eval "$boundary"); then fail 'installer continued after reload failed'; fi
! grep -qxE 'pixel|launch' "$CALL_LOG" || fail 'Pixel ran with an unrefreshed gateway'
RECREATE_STATUS=0
(eval "$boundary") || fail 'successful reload blocked Pixel'
[[ "$(tail -n 2 "$CALL_LOG")" == $'pixel\nlaunch' ]] || fail 'Pixel launch order changed'
echo '[PASS] installer gates Pixel review and stack launch on gateway refresh'
