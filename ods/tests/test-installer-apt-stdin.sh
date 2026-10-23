#!/usr/bin/env bash
# Package work must not inherit the installer's terminal (sudo-rs/use_pty).
# Variables below are consumed by sourced functions and the bootstrap command.
# shellcheck disable=SC2034
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir "$tmp/bin"
cat >"$tmp/bin/apt-get" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if IFS= read -r line; then
    echo 'APT inherited installer input' >&2
    exit 77
fi
printf '%s\n' "$*" >>"$APT_CALLS"
exit "${APT_STATUS:-0}"
SH
chmod +x "$tmp/bin/apt-get"
export PATH="$tmp/bin:$PATH" APT_CALLS="$tmp/apt-calls"
LOG_FILE="$tmp/install.log"
log() { :; }; warn() { echo "$*" >&2; }; error() { echo "$*" >&2; return 1; }
source "$ROOT/installers/lib/packaging.sh"
PKG_MANAGER=apt
# Test the real abstraction without requiring root. Privilege admission is
# separately exercised with sudo-rs in the native PTY qualification.
_SUDO=''
pkg_update <<< 'installer input'
pkg_install example-package <<< 'installer input'
[[ "$(wc -l <"$APT_CALLS")" -eq 2 ]]
if APT_STATUS=42 pkg_install example-package <<< 'installer input'; then
    echo 'FAIL: package failure was swallowed' >&2; exit 1
else
    [[ $? -eq 42 ]]
fi
echo 'PASS: package abstraction closes stdin and preserves failure status'

# Execute phase 07's production NodeSource path with an inert downloaded
# script. Both that root shell and its nested apt must inherit EOF.
eval "$(sed -n '/^_phase07_install_linux_node() {$/,/^}$/p' "$ROOT/installers/phases/07-devtools.sh")"
ods_sudo_available() { return 0; }
ods_sudo() { [[ "$1" != -E ]] || shift; "$@"; }
ai() { :; }; ai_warn() { echo "$*" >&2; }
curl() {
    local output
    while (( $# )); do
        if [[ "$1" == -o ]]; then output="$2"; break; fi
        shift
    done
    printf '%s\n' '#!/bin/bash' 'apt-get update' >"${output:?}"
}
_phase07_install_linux_node Pixel <<< 'installer input'
[[ "$(wc -l <"$APT_CALLS")" -eq 5 ]]
echo 'PASS: NodeSource and nodejs package work receive EOF'

# Execute the production bootstrap invocation without its unrelated service
# setup. Its nested package installers must inherit EOF too.
bootstrap_call="$(sed -n '/^    if ! ods_pixel_run_as_owner .* bootstrap --apply /p' "$ROOT/installers/lib/pixel-host-install.sh")"
[[ -n "$bootstrap_call" ]]
bootstrap_call="${bootstrap_call#*if ! }"
bootstrap_call="${bootstrap_call%; then}"
owner=fixture home="$tmp" pixel_root="$tmp/pixel" pixel_log="$tmp/pixel.log"
ods_pixel_run_as_owner() {
    if IFS= read -r line; then echo 'Pixel bootstrap inherited installer input' >&2; return 77; fi
}
eval "$bootstrap_call" <<< 'installer input'
echo 'PASS: pinned Pixel bootstrap closes inherited package input'

# stdin-bearing privileged operations must still work.
source "$ROOT/installers/lib/sudo.sh"
ODS_SUDO_AVAILABLE=true
sudo() { [[ "${1:-}" != -n ]] || shift; "$@"; }
[[ "$(printf 'retained input' | ods_sudo cat)" == 'retained input' ]]
echo 'PASS: ordinary sudo commands retain their input'
