#!/usr/bin/env bash
# GAIA post_install hook - Linux bind-mount uid alignment.
#
# The GAIA container runs as uid/gid 10001. On native Linux Docker, bind
# mounts preserve host ownership, so data/gaia must be owned by 10001:10001
# before the container can write first-run state.

set -euo pipefail

INSTALL_DIR="${1:-}"
GAIA_DATA_DIR="$INSTALL_DIR/data/gaia"
GAIA_OWNER="10001:10001"

log() {
    echo "gaia post_install: $*" >&2
}

if [[ -z "$INSTALL_DIR" ]]; then
    log "ERROR: INSTALL_DIR (arg 1) is required"
    exit 2
fi

PLATFORM="$(uname -s)"
if [[ "$PLATFORM" != "Linux" ]]; then
    log "Skipping chown on $PLATFORM (uid alignment only needed on native Linux Docker)"
    exit 0
fi

SUDO=()
if [[ "$(id -u)" -ne 0 ]]; then
    if command -v sudo >/dev/null 2>&1; then
        SUDO=(sudo -n)
    else
        log "ERROR: sudo is unavailable and this hook is not running as root."
        log "Run manually: sudo mkdir -p '$GAIA_DATA_DIR' && sudo chown -R $GAIA_OWNER '$GAIA_DATA_DIR', then retry the install."
        exit 1
    fi
fi

if [[ ! -d "$GAIA_DATA_DIR" ]]; then
    log "creating $GAIA_DATA_DIR"
    if ! "${SUDO[@]}" mkdir -p "$GAIA_DATA_DIR"; then
        log "ERROR: failed to create $GAIA_DATA_DIR"
        exit 1
    fi
fi

log "chown -R $GAIA_OWNER $GAIA_DATA_DIR"
if ! "${SUDO[@]}" chown -R "$GAIA_OWNER" "$GAIA_DATA_DIR"; then
    log "ERROR: failed to chown $GAIA_DATA_DIR."
    log "Run manually: sudo chown -R $GAIA_OWNER '$GAIA_DATA_DIR', then retry the install."
    exit 1
fi

log "done"
