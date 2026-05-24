#!/usr/bin/env bash
# Regression guard: Linux reinstall must not chown Hermes HERMES_HOME back to
# the host user. Hermes's dashboard/Talk path runs as uid 10000 and needs
# data/hermes mounted as /opt/data with that owner.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE06="$ROOT_DIR/installers/phases/06-directories.sh"

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

grep -Fq 'sudo chown -R 10000:10000 "$INSTALL_DIR/data/hermes"' "$PHASE06" \
    || fail "phase 06 must restore data/hermes to Hermes uid 10000"

grep -Fq 'sudo chmod 700 "$INSTALL_DIR/data/hermes"' "$PHASE06" \
    || fail "phase 06 must preserve Hermes private HERMES_HOME mode"

grep -Fq '[[ "${ENABLE_HERMES:-false}" == "true" && "$_data_dir" == "$INSTALL_DIR/data/hermes/" ]] && continue' "$PHASE06" \
    || fail "generic data-dir ownership repair must skip data/hermes only when Hermes is enabled"

grep -Fq '[[ "${ENABLE_HERMES:-false}" == "true" && "$_d" == "$INSTALL_DIR/data/hermes/" ]] && continue' "$PHASE06" \
    || fail "bootstrap writability check must allow container-owned data/hermes when Hermes is enabled"

grep -Fq 'PermissionError' "$PHASE06" \
    || fail "phase 06 comment should document the Hermes web/Talk failure mode"

echo "test-hermes-data-ownership: ok"
