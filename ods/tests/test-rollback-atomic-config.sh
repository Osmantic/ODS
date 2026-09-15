#!/usr/bin/env bash
# Regression: a failed rollback copy must not delete the live service config.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FIXTURE="$(mktemp -d)"
trap 'rm -rf "$FIXTURE"' EXIT

INSTALL_DIR="$FIXTURE/install"
mkdir -p "$INSTALL_DIR/config/litellm" "$FIXTURE/snapshot/config-litellm"
printf 'LIVE-CONFIG\n' > "$INSTALL_DIR/config/litellm/config.yaml"
printf 'SNAPSHOT-CONFIG\n' > "$FIXTURE/snapshot/config-litellm/config.yaml"
printf '{}' > "$FIXTURE/snapshot/snapshot.json"

ODS_UPDATE_SOURCE_ONLY=true INSTALL_DIR="$INSTALL_DIR" \
    source "$SCRIPT_DIR/../ods-update.sh"

cp() {
    # Simulate a disk/write failure while staging the snapshot.
    if [[ "$*" == *"config-litellm"* ]]; then
        return 1
    fi
    command cp "$@"
}

if _restore_snapshot "$FIXTURE/snapshot" >/dev/null 2>&1; then
    echo "FAIL: rollback unexpectedly succeeded"
    exit 1
fi

[[ "$(<"$INSTALL_DIR/config/litellm/config.yaml")" == "LIVE-CONFIG" ]] \
    || { echo "FAIL: live config was not preserved"; exit 1; }
echo "PASS: failed rollback preserves live extension config"
