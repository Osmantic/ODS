#!/bin/bash
# ============================================================================
# ODS Installer — Forced-reinstall preflight
# ============================================================================
# get-ods.sh --force replaces an existing installation with the requested
# candidate: the candidate's uninstaller removes the old tree, then the
# candidate's installer runs. Removal is irreversible, so this entry point runs
# the candidate installer's environment preflight first, while the existing
# installation is still intact:
#
#   installers/reinstall-preflight.sh --install-dir DIR [--keep-models] \
#       -- [INSTALLER OPTIONS]
#
# The platform installer runs its own checks and thresholds in
# `install.sh --preflight-only` mode and makes no changes. Because the
# installation being replaced still occupies its filesystem, this script
# measures the space its removal returns and passes it as a credit. With
# --keep-models the model cache is renamed beside the install on the same
# filesystem, so those bytes stay in use and are not credited.
#
# Exit status is the installer's: 0 means the forced reinstall may proceed.
# Keep this file Bash 3.2-compatible: macOS runs it with /bin/bash before the
# macOS installer selects a newer shell.
# ============================================================================

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

usage() {
    echo "Usage: $0 --install-dir DIR [--keep-models] -- [INSTALLER OPTIONS]" >&2
    exit 2
}

install_dir=""
keep_models=false
while (( $# > 0 )); do
    case "$1" in
        --install-dir)
            (( $# >= 2 )) || usage
            install_dir="$2"
            shift 2
            ;;
        --keep-models) keep_models=true; shift ;;
        --) shift; break ;;
        *) usage ;;
    esac
done

if [[ "$install_dir" != /* || ! -d "$install_dir" || -L "$install_dir" ]]; then
    echo "[ERROR] The forced-reinstall preflight needs the absolute path of the installation being replaced." >&2
    exit 2
fi

# Allocated KiB under a path, on that path's filesystem only. du still prints
# a total when root-owned container data is unreadable; that under-count only
# makes the credit more conservative.
_used_kb() {
    local out
    out="$(du -skx -- "$1" 2>/dev/null || true)"
    out="${out%%[!0-9]*}"
    printf '%s\n' "${out:-0}"
}

# The candidate uninstaller removes the whole tree. Retained models move to a
# same-filesystem sibling and stay in use, and the candidate source is copied
# into the install path afterwards, so neither is credited.
reclaimable_kb="$(_used_kb "$install_dir")"
if [[ "$keep_models" == true && -d "$install_dir/data/models" && ! -L "$install_dir/data/models" ]]; then
    reclaimable_kb=$(( reclaimable_kb - $(_used_kb "$install_dir/data/models") ))
fi
reclaimable_kb=$(( reclaimable_kb - $(_used_kb "$ROOT") ))
if (( reclaimable_kb < 0 )); then
    reclaimable_kb=0
fi

if [[ "$keep_models" == true ]]; then
    echo "[INFO] Removing $install_dir returns about $(( reclaimable_kb / 1048576 )) GB to its filesystem; retained models stay in use."
else
    echo "[INFO] Removing $install_dir returns about $(( reclaimable_kb / 1048576 )) GB to its filesystem."
fi

# Point the installer at the tree being replaced. Installers honor the credit
# only together with --preflight-only.
export INSTALL_DIR="$install_dir"
export ODS_PREFLIGHT_RECLAIMABLE_KB="$reclaimable_kb"
exec bash "$ROOT/install.sh" --preflight-only "$@"
