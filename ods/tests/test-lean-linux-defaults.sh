#!/usr/bin/env bash
# Fresh CLI installs should stay small; reruns recover installed selections.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/installers/lib/installed-feature-state.sh"
source "$ROOT/installers/lib/external-services.sh"
defaults="$(sed -n '/^DRY_RUN=false$/,/^INTERACTIVE=true$/p' "$ROOT/install-core.sh")"
[[ -n "$defaults" ]] || { echo 'FAIL: installer defaults block missing' >&2; exit 1; }

check_defaults() (
    local existing="$1" expected="$2" dir
    dir="$(mktemp -d)"
    trap 'rm -f -- "$dir/.env"; rmdir -- "$dir"' EXIT
    INSTALL_DIR="$dir"
    [[ "$existing" == true ]] && : >"$dir/.env"
    eval "$defaults"
    [[ "$ODS_EXISTING_INSTALL" == "$existing" ]] || exit 1
    for flag in ENABLE_VOICE ENABLE_WORKFLOWS ENABLE_RAG ENABLE_RECOMMENDED \
                ENABLE_HERMES ENABLE_COMFYUI ENABLE_APE ENABLE_PERPLEXICA \
                ENABLE_PRIVACY_SHIELD; do
        [[ "${!flag}" == "$expected" ]] || {
            echo "FAIL: $flag=${!flag} on existing=$existing" >&2
            exit 1
        }
    done
    [[ "$ENABLE_OPENCODE" == false && "$ENABLE_OPENCLAW" == false ]]
)

check_defaults false false
check_defaults true true
echo 'PASS: Linux fresh and markerless legacy installer defaults are distinct'
