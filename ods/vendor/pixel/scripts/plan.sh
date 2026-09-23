#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/common.sh
source "$ROOT/scripts/lib/common.sh"
pixel_load_env
bash "$ROOT/scripts/preflight.sh" --phase plan "$@"

if [[ ${PIXEL_SKIP_NPM_CI:-0} != 1 ]]; then
  [[ ${PIXEL_SOURCE_BROKER_ENABLED:-1} == 0 ]] || (cd "$ROOT/plugin" && npm ci --omit=dev --ignore-scripts)
  [[ ${PIXEL_OPS_BROKER_ENABLED:-0} == 0 ]] || (cd "$ROOT/plugin-ops" && npm ci --omit=dev --ignore-scripts)
  [[ ${PIXEL_FRONTIER_BROKER_ENABLED:-0} == 0 ]] || (cd "$ROOT/plugin-frontier" && npm ci --omit=dev --ignore-scripts)
fi
install -d -m 700 "$ROOT/dist"
node "$ROOT/scripts/release-identity.mjs" --output "$ROOT/dist/release-identity.json" >/dev/null
export PIXEL_PLUGIN_PATH="$PIXEL_INSTALL_DIR/current/plugin"
export PIXEL_OPS_PLUGIN_PATH="$PIXEL_INSTALL_DIR/current/plugin-ops"
export PIXEL_FRONTIER_PLUGIN_PATH="$PIXEL_INSTALL_DIR/current/plugin-frontier"
candidate="$ROOT/dist/openclaw.json"
validation="$ROOT/dist/openclaw.validation.json"
validation_state=''
cleanup_plan() {
  rm -f -- "$validation"
  if [[ -n "$validation_state" && "$validation_state" == /tmp/pixel-openclaw-validation.* ]]; then
    rm -rf -- "$validation_state"
  fi
}
trap cleanup_plan EXIT
node "$ROOT/scripts/render-config.mjs" "$candidate" >/dev/null
validation_plugins=()
declare -A validation_plugin_seen=()
add_validation_plugin() {
  local supplied=$1 resolved
  [[ "$supplied" == /* && "$supplied" != *$'\n'* && "$supplied" != *$'\r'* ]] || pixel_die "Validation plugin path is unsafe"
  resolved=$(realpath -e "$supplied") || pixel_die "Validation plugin path is absent: $supplied"
  [[ -d "$resolved" ]] || pixel_die "Validation plugin path is not a directory: $resolved"
  [[ -n ${validation_plugin_seen[$resolved]+x} ]] && return 0
  validation_plugin_seen[$resolved]=1
  validation_plugins+=("$resolved")
}
[[ ${PIXEL_SOURCE_BROKER_ENABLED:-1} == 0 ]] || add_validation_plugin "$ROOT/plugin"
[[ ${PIXEL_OPS_BROKER_ENABLED:-0} == 0 ]] || add_validation_plugin "$ROOT/plugin-ops"
[[ ${PIXEL_FRONTIER_BROKER_ENABLED:-0} == 0 ]] || add_validation_plugin "$ROOT/plugin-frontier"
while IFS= read -r -d '' path; do
  [[ "$path" == "$PIXEL_INSTALL_DIR/current/plugin" || "$path" == "$PIXEL_INSTALL_DIR/current/plugin-ops" || "$path" == "$PIXEL_INSTALL_DIR/current/plugin-frontier" ]] && continue
  add_validation_plugin "$path"
done < <(jq -j '.plugins.load.paths[]? | ., "\u0000"' "$candidate")
for plugin_id in discord searxng llama-cpp; do
  jq -e --arg id "$plugin_id" '.plugins.allow | index($id)' "$candidate" >/dev/null || continue
  details=$("$OPENCLAW_BIN" plugins inspect "$plugin_id" --json 2>/dev/null) || pixel_die "Cannot inspect validation plugin: $plugin_id"
  root=$(jq -er '.plugin.rootDir | select(type == "string")' <<<"$details") || pixel_die "Validation plugin has no root: $plugin_id"
  add_validation_plugin "$root"
done
[[ ${#validation_plugins[@]} -gt 0 ]] || pixel_die "No validation plugin roots were resolved"
node "$ROOT/scripts/render-validation-config.mjs" "$candidate" "$validation" "${validation_plugins[@]}"
validation_state=$(mktemp -d /tmp/pixel-openclaw-validation.XXXXXX)
chmod 700 "$validation_state"
OPENCLAW_STATE_DIR="$validation_state" OPENCLAW_CONFIG_PATH="$validation" "$OPENCLAW_BIN" config validate
chmod 600 "$candidate"
(cd "$ROOT/dist" && sha256sum "$(basename "$candidate")" > openclaw.sha256)
(cd "$ROOT" && {
  printf '%s\0' VERSION RELEASE-MANIFEST.json OPENCLAW-COMPATIBILITY.json QUALIFICATION-MATRIX.json pixel
  find control deploy profiles schemas scripts -type f -print0
  find plugin -path plugin/node_modules -prune -o -type f -print0
  find plugin-ops -path plugin-ops/node_modules -prune -o -type f -print0
  find plugin-frontier -path plugin-frontier/node_modules -prune -o -type f -print0
} | LC_ALL=C sort -zu | xargs -0 sha256sum) > "$ROOT/dist/source-runtime.sha256"
(cd "$ROOT" && {
  printf '%s\0' .env VERSION RELEASE-MANIFEST.json OPENCLAW-COMPATIBILITY.json QUALIFICATION-MATRIX.json pixel \
    dist/release-identity.json dist/source-runtime.sha256 \
    .generated/deployment.json .generated/gateway.env .generated/openclaw-gateway.service \
    .generated/web-courier.env .generated/pixel-web-courier.service \
    .generated/source-broker.env .generated/pixel-source-broker.service \
    .generated/pixel-source-broker.timer .generated/pixel-source-action@.service \
    .generated/pixel-source-reconcile@.service \
    .generated/ops-broker.env .generated/ops-policy.json .generated/pixel-ops-broker.service \
    .generated/frontier-broker.env .generated/frontier-policy.json .generated/pixel-frontier-broker.service
  find .generated/workspace control deploy scripts -type f -print0
  find plugin -path plugin/node_modules -prune -o -type f -print0
  find plugin-ops -path plugin-ops/node_modules -prune -o -type f -print0
  find plugin-frontier -path plugin-frontier/node_modules -prune -o -type f -print0
} | LC_ALL=C sort -zu | xargs -0 sha256sum) > "$ROOT/dist/deployment.sha256"
pixel_log "Deployment plan (secrets omitted):"
node "$ROOT/scripts/summarize-plan.mjs" "$candidate"
pixel_log "Candidate: $candidate"
pixel_log "Reviewed hash: $(awk '{print $1}' "$ROOT/dist/openclaw.sha256")"
pixel_log "Reviewed deployment inputs: $(sha256sum "$ROOT/dist/deployment.sha256" | awk '{print $1}')"
pixel_log "Next: review the summary and run ./pixel apply --confirm"
