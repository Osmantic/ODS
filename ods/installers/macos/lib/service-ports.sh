#!/bin/bash
# Resolve published ports without sourcing the owner's .env as shell code.
# Support both the source tree and the installed CLI's flattened lib directory.
_macos_ports_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$_macos_ports_dir/safe-env.sh" ]]; then
    source "$_macos_ports_dir/safe-env.sh"
else
    source "$_macos_ports_dir/../../../lib/safe-env.sh"
fi
unset _macos_ports_dir

macos_service_port() {
    local env_file="$1" key="$2" fallback="$3" value line saved_key
    # Compose gives the process environment precedence, including an explicitly
    # empty value (which selects the ${PORT:-default} default, not the saved port).
    if [[ "${!key+x}" == x ]]; then
        value="${!key}"
    else
        value=""
        if [[ -f "$env_file" ]]; then
            while IFS= read -r line || [[ -n "$line" ]]; do
                [[ "$line" == *=* ]] || continue
                saved_key="${line%%=*}"
                saved_key="${saved_key#"${saved_key%%[![:space:]]*}"}"
                saved_key="${saved_key%"${saved_key##*[![:space:]]}"}"
                [[ "$saved_key" == "$key" ]] || continue
                value="$(safe_env_decode_value "${line#*=}")"
            done < "$env_file"
        fi
    fi
    value="${value:-$fallback}"
    if [[ ! "$value" =~ ^[1-9][0-9]{0,4}$ ]] || (( value > 65535 )); then
        printf 'Invalid %s: expected a port from 1 to 65535.\n' "$key" >&2
        return 1
    fi
    printf '%s' "$value"
}
