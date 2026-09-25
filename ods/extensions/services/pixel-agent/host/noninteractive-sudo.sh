#!/usr/bin/env bash
set -euo pipefail

# Pixel's direct privileged lifecycle path performs an interactive `sudo -v`
# preflight before executing its reviewed fixed sudo commands. ODS runs that
# lifecycle from a noninteractive installer and must never leave a hidden
# password prompt waiting on /dev/tty. This adapter is placed on PATH only for
# the reviewed Pixel apply: validation is a no-op, while every real command is
# delegated to the trusted system sudo binary with fail-fast semantics.
sudo_entry=/usr/bin/sudo
[[ -e "$sudo_entry" && -x "$sudo_entry" ]] || exit 126
[[ "$(stat -c '%U:%G' -- "$sudo_entry")" == root:root ]] || exit 126

# Ubuntu's sudo-rs transition exposes /usr/bin/sudo through a root-owned
# alternatives link whose canonical binary is /usr/lib/cargo/bin/sudo. Accept
# only that observed system target or the traditional binary, then validate the
# canonical file and every parent directory before execution. This preserves
# the fixed trusted path while supporting both implementations; PATH remains
# irrelevant and user-writable link targets still fail closed.
trusted_sudo="$(readlink -e -- "$sudo_entry")" || exit 126
case "$trusted_sudo" in
    /usr/bin/sudo|/usr/lib/cargo/bin/sudo) ;;
    *) exit 126 ;;
esac
[[ -f "$trusted_sudo" && ! -L "$trusted_sudo" && -x "$trusted_sudo" ]] || exit 126
[[ "$(stat -Lc '%U:%G' -- "$trusted_sudo")" == root:root ]] || exit 126
(( (8#$(stat -Lc '%a' -- "$trusted_sudo") & 0022) == 0 )) || exit 126

trusted_parent="$(dirname -- "$trusted_sudo")"
while [[ "$trusted_parent" != / ]]; do
    [[ "$(stat -Lc '%U:%G' -- "$trusted_parent")" == root:root ]] || exit 126
    (( (8#$(stat -Lc '%a' -- "$trusted_parent") & 0022) == 0 )) || exit 126
    trusted_parent="$(dirname -- "$trusted_parent")"
done
(( $# > 0 )) || exit 2

if [[ $# == 1 && "$1" == -v ]]; then
    exit 0
fi

exec "$trusted_sudo" -n "$@"
