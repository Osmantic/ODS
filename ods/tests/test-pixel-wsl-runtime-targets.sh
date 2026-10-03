#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
eval "$(sed -n '/^_ods_pixel_prepare_wsl_runtime_targets() {/,/^}/p' "$root/installers/lib/pixel-host-install.sh")"
scratch="$(mktemp -d)"
trap 'rm -rf -- "$scratch"' EXIT
calls="$scratch/calls"
ai_bad() { printf '%s\n' "$*" >&2; }
ods_sudo() {
    [[ "$*" == install\ -d\ -o\ root\ -g\ root\ -m\ 0755\ --\ * ]]
    printf '%s\n' "${@: -1}" >> "$calls"
    mkdir -p -- "${@: -1}"
}
_ods_pixel_prepare_wsl_runtime_targets "$scratch/runtime"
[[ "$(wc -l < "$calls")" == 3 ]]
[[ -d "$scratch/runtime/ingress" && -d "$scratch/runtime/preview" ]]
# Live bind targets already exist: no privileged install/chown/chmod at all.
: > "$calls"
printf 'socket placeholder\n' > "$scratch/runtime/ingress/owned-socket"
_ods_pixel_prepare_wsl_runtime_targets "$scratch/runtime"
[[ ! -s "$calls" && -f "$scratch/runtime/ingress/owned-socket" ]]
# Reject a non-directory and a symlink instead of adopting their destinations.
mkdir "$scratch/invalid"
touch "$scratch/invalid/ingress"
if _ods_pixel_prepare_wsl_runtime_targets "$scratch/invalid"; then exit 1; fi
ln -s "$scratch/runtime" "$scratch/link"
if _ods_pixel_prepare_wsl_runtime_targets "$scratch/link"; then exit 1; fi
echo 'Pixel WSL runtime targets: fresh creation, rerun preservation and unsafe paths passed'
