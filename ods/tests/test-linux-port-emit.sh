#!/usr/bin/env bash
set -euo pipefail

# Every service port knob that compose consumes and .env.example documents must
# be emitted by the generated .env. Today the phase-06 heredoc omits several
# documented keys (DASHBOARD_PORT, DASHBOARD_API_PORT, COMFYUI_PORT,
# TOKEN_SPY_PORT, APE_PORT, SHIELD_PORT, ODS_PROXY_PORT). An operator who adds
# e.g. DASHBOARD_PORT=4001 by hand loses it silently on the next install rerun
# because the file is regenerated from scratch. Emitting each key through
# _env_get_explicit_first gives the documented precedence:
#   explicit install-time env var > existing .env value > installer default.

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
phase="$root/installers/phases/06-directories.sh"

declare -A PORT_DEFAULTS=(
    [DASHBOARD_API_PORT]="3002"
    [DASHBOARD_PORT]="3001"
    [COMFYUI_PORT]="8188"
    [TOKEN_SPY_PORT]="3005"
    [APE_PORT]="7890"
    [SHIELD_PORT]="8085"
    [ODS_PROXY_PORT]="80"
)

fail() {
    printf '[FAIL] %s\n' "$1" >&2
    exit 1
}

for key in "${!PORT_DEFAULTS[@]}"; do
    grep -Eq "${key}_VALUE=\"\\\$\(_env_get_explicit_first ${key} \"${PORT_DEFAULTS[$key]}\"\)\"" "$phase" \
        || fail "${key} is not resolved through _env_get_explicit_first with default ${PORT_DEFAULTS[$key]}"
    grep -Eq "^${key}=\\\$\(dotenv_value \"\\\$\{${key}_VALUE\}\"\)" "$phase" \
        || fail "generated .env does not emit ${key} from its preserved value"
done

tr -d '\r' < "$phase" | bash -n

echo 'Port emission contract passed'
