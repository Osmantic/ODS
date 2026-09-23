#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
sed '$d' "$ROOT/ods-update.sh" > "$TMP/functions.sh"
source "$TMP/functions.sh"
INSTALL_DIR="$TMP/install";VERSION_FILE="$INSTALL_DIR/.version";mkdir -p "$INSTALL_DIR";printf '{}\n' > "$VERSION_FILE"
log_info(){ :; };log_ok(){ :; };log_warn(){ :; };log_error(){ :; }
get_current_version(){ printf 2.6.0; };ensure_source_checkout_for_update(){ return 0; };snapshot_pre_update(){ printf '%s' "$TMP/snapshot"; }
resolve_compose_flags(){ printf '%s' "-f 'custom stack/compose.yaml'"; }
_restore_snapshot(){ return 0; }
git(){ case "$1" in branch) printf main;; describe) printf v2.6.0;; esac; }
validate_compose(){
 local -a args=("$@")
 [[ ${args[0]} == -f && ${args[1]} == 'custom stack/compose.yaml' ]] || return 1
 [[ ${args[2]} == up || ${args[2]} == down ]] || return 1
 printf '%s\n' "$1" "$2" "$3" >> "$TMP/calls"
}
docker(){ [[ $1 == compose ]];shift;validate_compose "$@" || exit 91;[[ ${FORCE_V1:-false} != true ]]; }
docker-compose(){ validate_compose "$@" || exit 92; }
wait_for_healthy(){ [[ ${HEALTH_FAILURE:-false} != true ]]; }
for FORCE_V1 in false true; do
 HEALTH_FAILURE=false
 cmd_update
 HEALTH_FAILURE=true
 if cmd_update;then echo 'FAIL health failure reported success';exit 1;fi
done
# Malformed quoting must not dispatch a partial or differently split command.
resolve_compose_flags(){ printf '%s' "-f 'unterminated"; }
before=$(wc -l < "$TMP/calls")
if cmd_update;then echo 'FAIL malformed quote accepted';exit 1;fi
[[ $(wc -l < "$TMP/calls") == "$before" ]]
echo 'PASS quoted compose paths survive source update and automatic rollback with v2/v1; malformed quotes fail closed'
