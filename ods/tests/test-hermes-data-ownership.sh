#!/usr/bin/env bash
# Regression guard: Linux reinstall must not chown Hermes HERMES_HOME back to
# the wrong user. Hermes remaps its container user to the UID/GID persisted in
# .env and needs data/hermes mounted as /opt/data with matching ownership.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE06="$ROOT_DIR/installers/phases/06-directories.sh"
MACOS_ENV="$ROOT_DIR/installers/macos/lib/env-generator.sh"

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

grep -Fq 'sudo chown -R "$_phase06_compose_uid:$_phase06_compose_gid" "$INSTALL_DIR/data/hermes"' "$PHASE06" \
    || fail "phase 06 must align data/hermes with the persisted compose IDs"

grep -Fq 'UID=${_phase06_compose_uid}' "$PHASE06" \
    || fail "phase 06 must persist the host UID for Docker Compose"

grep -Fq 'GID=${_phase06_compose_gid}' "$PHASE06" \
    || fail "phase 06 must persist the host GID for Docker Compose"

grep -Fq 'upsert_env_value "$env_path" "UID" "$host_uid"' "$MACOS_ENV" \
    || fail "macOS upgrades must backfill the host UID"

grep -Fq 'upsert_env_value "$env_path" "GID" "$host_gid"' "$MACOS_ENV" \
    || fail "macOS upgrades must backfill the host GID"

grep -Fq 'sudo chmod 700 "$INSTALL_DIR/data/hermes"' "$PHASE06" \
    || fail "phase 06 must preserve Hermes private HERMES_HOME mode"

grep -Fq '[[ "${ENABLE_HERMES:-false}" == "true" && "$_data_dir" == "$INSTALL_DIR/data/hermes/" ]] && continue' "$PHASE06" \
    || fail "generic data-dir ownership repair must skip data/hermes only when Hermes is enabled"

grep -Fq '[[ "${ENABLE_HERMES:-false}" == "true" && "$_d" == "$INSTALL_DIR/data/hermes/" ]] && continue' "$PHASE06" \
    || fail "bootstrap writability check must allow container-owned data/hermes when Hermes is enabled"

grep -Fq 'PermissionError' "$PHASE06" \
    || fail "phase 06 comment should document the Hermes web/Talk failure mode"

echo "test-hermes-data-ownership: ok"
