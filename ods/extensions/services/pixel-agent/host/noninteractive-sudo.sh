#!/usr/bin/env bash
set -euo pipefail

# Pixel's direct privileged lifecycle path performs an interactive `sudo -v`
# preflight before executing its reviewed fixed sudo commands. ODS runs that
# lifecycle from a noninteractive installer and must never leave a hidden
# password prompt waiting on /dev/tty. This adapter is placed on PATH only for
# the reviewed Pixel apply: validation is a no-op, while every real command is
# delegated to the trusted system sudo binary with fail-fast semantics.
trusted_sudo=/usr/bin/sudo
[[ -f "$trusted_sudo" && ! -L "$trusted_sudo" && -x "$trusted_sudo" ]] || exit 126
[[ "$(stat -c '%U:%G' -- "$trusted_sudo")" == root:root ]] || exit 126
(( (8#$(stat -c '%a' -- "$trusted_sudo") & 0022) == 0 )) || exit 126
(( $# > 0 )) || exit 2

if [[ $# == 1 && "$1" == -v ]]; then
    exit 0
fi

exec "$trusted_sudo" -n "$@"
