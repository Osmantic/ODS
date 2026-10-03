#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/common.sh
source "$ROOT/scripts/lib/common.sh"
pixel_load_env
printf '%-12s %-9s %s\n' LIMB STATUS BOUNDARY
printf '%-12s %-9s %s\n' email "$([[ ${PIXEL_LIMB_EMAIL_ENABLED:-1} == 1 ]] && echo enabled || echo disabled)" "sanitized projection"
printf '%-12s %-9s %s\n' calendar "$([[ ${PIXEL_LIMB_CALENDAR_ENABLED:-1} == 1 ]] && echo enabled || echo disabled)" "projection + separately approved actuator"
printf '%-12s %-9s %s\n' social "$([[ ${PIXEL_LIMB_SOCIAL_ENABLED:-0} == 1 ]] && echo enabled || echo disabled)" "sanitized adapter projection"
printf '%-12s %-9s %s\n' web "$([[ ${PIXEL_LIMB_WEB_ENABLED:-1} == 1 ]] && echo enabled || echo disabled)" "policy-enforced courier"
printf '%-12s %-9s %s\n' operations "$([[ ${PIXEL_LIMB_OPERATIONS_ENABLED:-0} == 1 ]] && echo enabled || echo disabled)" "isolated policy and execution broker"
printf '%-12s %-9s %s\n' frontier "$([[ ${PIXEL_LIMB_FRONTIER_ENABLED:-0} == 1 ]] && echo enabled || echo disabled)" "privacy-compiled expert review broker"
