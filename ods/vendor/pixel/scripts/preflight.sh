#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/common.sh
source "$ROOT/scripts/lib/common.sh"

phase=plan
skip_endpoints=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --phase) phase=${2:?--phase requires plan, apply, or verify}; shift 2 ;;
    --skip-endpoints) skip_endpoints=1; shift ;;
    --help|-h) echo "Usage: scripts/preflight.sh [--phase plan|apply|verify] [--skip-endpoints]"; exit 0 ;;
    *) pixel_die "Unknown preflight option: $1" ;;
  esac
done
case "$phase" in plan|apply|verify) ;; *) pixel_die "Invalid phase: $phase" ;; esac

pixel_require_linux
pixel_load_env
source_version=$(tr -d '[:space:]' < "$ROOT/VERSION")
[[ ${PIXEL_RELEASE_VERSION:-} == "$source_version" ]] || pixel_die "Generated deployment version ${PIXEL_RELEASE_VERSION:-unset} does not match source $source_version; rerun ./pixel configure --answers ... --force"
for command in node npm docker jq curl git sha256sum python3 systemctl; do pixel_require_command "$command"; done
node_major=$(node -p 'Number(process.versions.node.split(".")[0])')
[[ "$node_major" -ge 22 ]] || pixel_die "Node.js 22 or newer is required"
[[ -x "$OPENCLAW_BIN" ]] || pixel_die "OPENCLAW_BIN is not executable: $OPENCLAW_BIN"
[[ "$(pixel_openclaw_version)" == "$PIXEL_OPENCLAW_VERSION" ]] || pixel_die "Expected OpenClaw $PIXEL_OPENCLAW_VERSION; found $(pixel_openclaw_version)"
case "$PIXEL_DEPLOYMENT_PROFILE" in prepared|reference) ;; *) pixel_die "PIXEL_DEPLOYMENT_PROFILE must be prepared or reference" ;; esac
for name in OPENCLAW_HOME PIXEL_INSTALL_DIR PIXEL_AGENT_ID PIXEL_AGENT_NAME PIXEL_WORKSPACE PIXEL_MODEL_PROVIDER PIXEL_MODEL_ID PIXEL_MODEL_NAME PIXEL_MODEL_BASE_URL PIXEL_MODEL_API_KEY PIXEL_MODEL_CONTEXT_WINDOW PIXEL_MODEL_MAX_TOKENS PIXEL_EMBEDDING_MODEL PIXEL_EMBEDDING_CACHE PIXEL_SANDBOX_IMAGE PIXEL_GOOGLE_TOKEN_PATH PIXEL_TIME_ZONE PIXEL_SOURCE_PROJECTION_DIR PIXEL_ACTION_PROPOSAL_DIR; do
  [[ -n "${!name:-}" ]] || pixel_die "Missing setting: $name"
done
web_search_provider=${PIXEL_WEB_SEARCH_PROVIDER:-searxng}
case "$web_search_provider" in
  searxng)
    [[ -n ${PIXEL_SEARXNG_BASE_URL:-} ]] || pixel_die "Missing setting: PIXEL_SEARXNG_BASE_URL"
    ;;
  parallel-free)
    jq -e 'type == "array" and ([.[] | select(.id == "parallel" and
      (.path | type == "string" and startswith("/") and . != "/") and
      (.sha256 | type == "string" and test("^[0-9a-f]{64}$")))] | length == 1)' \
      <<<"${PIXEL_GATEWAY_EXTENSIONS:-[]}" >/dev/null \
      || pixel_die "parallel-free requires a path and digest bound parallel gateway extension"
    ;;
  *) pixel_die "PIXEL_WEB_SEARCH_PROVIDER must be searxng or parallel-free" ;;
esac
pixel_release_operator_preflight
pixel_safe_absolute_dir "$OPENCLAW_HOME" OPENCLAW_HOME
pixel_safe_absolute_dir "$PIXEL_INSTALL_DIR" PIXEL_INSTALL_DIR
pixel_safe_absolute_dir "$PIXEL_WORKSPACE" PIXEL_WORKSPACE
[[ ${PIXEL_WEB_COURIER_ENABLED:-1} == 0 || ${PIXEL_WEB_COURIER_ENABLED:-1} == 1 ]] || pixel_die "PIXEL_WEB_COURIER_ENABLED must be 0 or 1"
if [[ $phase == apply && -z ${PIXEL_COURIER_SYSTEMCTL_BIN:-} ]]; then pixel_require_command sudo; fi
[[ ${PIXEL_SOURCE_BROKER_ENABLED:-1} == 0 || ${PIXEL_SOURCE_BROKER_ENABLED:-1} == 1 ]] || pixel_die "PIXEL_SOURCE_BROKER_ENABLED must be 0 or 1"
[[ ${PIXEL_OPS_BROKER_ENABLED:-0} == 0 || ${PIXEL_OPS_BROKER_ENABLED:-0} == 1 ]] || pixel_die "PIXEL_OPS_BROKER_ENABLED must be 0 or 1"
[[ ${PIXEL_FRONTIER_BROKER_ENABLED:-0} == 0 || ${PIXEL_FRONTIER_BROKER_ENABLED:-0} == 1 ]] || pixel_die "PIXEL_FRONTIER_BROKER_ENABLED must be 0 or 1"
for limb in EMAIL CALENDAR SOCIAL WEB OPERATIONS FRONTIER; do value=PIXEL_LIMB_${limb}_ENABLED; [[ ${!value:-0} == 0 || ${!value:-0} == 1 ]] || pixel_die "$value must be 0 or 1"; done
[[ ${PIXEL_MODEL_REASONING:-1} == 0 || ${PIXEL_MODEL_REASONING:-1} == 1 ]] || pixel_die "PIXEL_MODEL_REASONING must be 0 or 1"
[[ -f "$ROOT/.generated/deployment.json" && -f "$ROOT/.generated/gateway.env" && -f "$ROOT/.generated/openclaw-gateway.service" && -f "$ROOT/.generated/web-courier.env" && -f "$ROOT/.generated/pixel-web-courier.service" && -f "$ROOT/.generated/source-broker.env" && -f "$ROOT/.generated/pixel-source-broker.service" && -f "$ROOT/.generated/pixel-source-broker.timer" && -f "$ROOT/.generated/pixel-source-action@.service" && -f "$ROOT/.generated/pixel-source-reconcile@.service" && -f "$ROOT/.generated/ops-broker.env" && -f "$ROOT/.generated/ops-policy.json" && -f "$ROOT/.generated/pixel-ops-broker.service" && -f "$ROOT/.generated/frontier-broker.env" && -f "$ROOT/.generated/frontier-policy.json" && -f "$ROOT/.generated/pixel-frontier-broker.service" && -d "$ROOT/.generated/workspace" ]] || pixel_die "Generated onboarding files are missing; run ./pixel configure"
ops_policy_test=$(mktemp -d)
python3 "$ROOT/deploy/ops-broker/broker.py" --policy "$ROOT/.generated/ops-policy.json" --state "$ops_policy_test" --once >/dev/null
rm -rf -- "$ops_policy_test"
frontier_policy_test=$(mktemp -d)
python3 "$ROOT/deploy/frontier-broker/broker.py" --policy "$ROOT/.generated/frontier-policy.json" --state "$frontier_policy_test" --validate-policy >/dev/null
rm -rf -- "$frontier_policy_test"
if [[ ${PIXEL_WEB_COURIER_ENABLED:-1} == 1 ]]; then
  for name in PIXEL_WEB_COURIER_UNIT PIXEL_COURIER_SYSTEMD_DIR PIXEL_WEB_COURIER_LOG_PATH PIXEL_WEB_COURIER_BROWSER_PATH PIXEL_WEB_COURIER_WHEELHOUSE; do
    [[ -n "${!name:-}" ]] || pixel_die "Missing setting: $name"
  done
  pixel_safe_absolute_dir "$PIXEL_WEB_COURIER_BROWSER_PATH" PIXEL_WEB_COURIER_BROWSER_PATH
  pixel_safe_absolute_dir "$PIXEL_WEB_COURIER_WHEELHOUSE" PIXEL_WEB_COURIER_WHEELHOUSE
  pixel_safe_absolute_dir "$PIXEL_COURIER_SYSTEMD_DIR" PIXEL_COURIER_SYSTEMD_DIR
  [[ "$PIXEL_WEB_COURIER_UNIT" =~ ^[A-Za-z0-9_.@-]+\.service$ ]] || pixel_die "Unsafe Web Courier system unit name"
  [[ "$PIXEL_WEB_COURIER_BROWSER_PATH" == "$PIXEL_INSTALL_DIR/"* ]] || pixel_die "Web Courier browser cache must be under PIXEL_INSTALL_DIR"
  [[ "$PIXEL_WEB_COURIER_WHEELHOUSE" == "$PIXEL_INSTALL_DIR/"* ]] || pixel_die "Web Courier wheelhouse must be under PIXEL_INSTALL_DIR"
  requirements_hash=$(sha256sum "$ROOT/deploy/web-courier/requirements.lock" | awk '{print $1}')
  [[ -f "$PIXEL_WEB_COURIER_WHEELHOUSE/.requirements.sha256" ]] || pixel_die "Web Courier wheel set is absent (run ./pixel bootstrap --apply)"
  [[ $(cat "$PIXEL_WEB_COURIER_WHEELHOUSE/.requirements.sha256") == "$requirements_hash" ]] || pixel_die "Web Courier wheel set is stale (run ./pixel bootstrap --apply)"
  courier_bootstrap_python="$PIXEL_INSTALL_DIR/bootstrap/web-courier/venv/bin/python"
  [[ -x "$courier_bootstrap_python" ]] || pixel_die "Web Courier verification runtime is absent (run ./pixel bootstrap --apply)"
  wheel_verify=$(mktemp -d)
  if ! "$courier_bootstrap_python" -m pip download --disable-pip-version-check --no-index --no-deps --only-binary=:all: --require-hashes --find-links "$PIXEL_WEB_COURIER_WHEELHOUSE" --dest "$wheel_verify" --requirement "$ROOT/deploy/web-courier/requirements.lock" >/dev/null 2>&1; then
    rm -rf -- "$wheel_verify"
    pixel_die "Web Courier wheel set failed the release hash contract (run ./pixel bootstrap --apply)"
  fi
  rm -rf -- "$wheel_verify"
  [[ -f "$PIXEL_WEB_COURIER_BROWSER_PATH/.pixel-chromium-ready" ]] || pixel_die "Web Courier Chromium is absent (run ./pixel bootstrap --apply)"
  [[ $(cat "$PIXEL_WEB_COURIER_BROWSER_PATH/.pixel-chromium-ready") == "$requirements_hash" ]] || pixel_die "Web Courier Chromium is stale (run ./pixel bootstrap --apply)"
  find "$PIXEL_WEB_COURIER_BROWSER_PATH" -type f \( -name chrome -o -name chrome-headless-shell \) -perm -u+x -print -quit | grep -q . || pixel_die "Web Courier Chromium executable is missing (run ./pixel bootstrap --apply)"
fi
docker info >/dev/null 2>&1 || pixel_die "Docker daemon is unavailable"
expected_sandbox_uid=$(id -u)
[[ "$expected_sandbox_uid" =~ ^[1-9][0-9]*$ ]] || pixel_die "Pixel requires a non-root deployment owner"
if [[ $phase == verify ]]; then
  sandbox_reference=$PIXEL_SANDBOX_IMAGE
else
  sandbox_reference=$(pixel_sandbox_candidate_tag "$source_version" "$expected_sandbox_uid")
fi
pixel_validate_sandbox_image "$sandbox_reference" "$source_version" "$expected_sandbox_uid" >/dev/null \
  || pixel_die "Sandbox image $sandbox_reference differs from Pixel $source_version (run ./pixel bootstrap --apply)"
discord_version=$(jq -r '.openclawPlugins["@openclaw/discord"]' "$ROOT/RELEASE-MANIFEST.json")
discord_integrity=$(jq -r '.openclawPluginPackages.discord.integrity' "$ROOT/RELEASE-MANIFEST.json")
discord_sha256=$(jq -r '.openclawPluginPackages.discord.sha256' "$ROOT/RELEASE-MANIFEST.json")
searxng_version=$(jq -r '.openclawPlugins["@openclaw/searxng-plugin"]' "$ROOT/RELEASE-MANIFEST.json")
searxng_integrity=$(jq -r '.openclawPluginPackages.searxng.integrity' "$ROOT/RELEASE-MANIFEST.json")
searxng_sha256=$(jq -r '.openclawPluginPackages.searxng.sha256' "$ROOT/RELEASE-MANIFEST.json")
llama_version=$(jq -r '.openclawPlugins["@openclaw/llama-cpp-provider"]' "$ROOT/RELEASE-MANIFEST.json")
llama_integrity=$(jq -r '.openclawPluginPackages.llamaCpp.integrity' "$ROOT/RELEASE-MANIFEST.json")
llama_sha256=$(jq -r '.openclawPluginPackages.llamaCpp.sha256' "$ROOT/RELEASE-MANIFEST.json")
plugin_cache="$PIXEL_INSTALL_DIR/bootstrap/openclaw-plugins"
pixel_plugin_release_matches discord "$discord_version" "$discord_integrity" "$discord_sha256" "$plugin_cache/discord-$discord_version.tgz" || pixel_die "OpenClaw Discord plugin differs from the pinned release (run ./pixel bootstrap --apply)"
pixel_plugin_release_matches searxng "$searxng_version" "$searxng_integrity" "$searxng_sha256" "$plugin_cache/searxng-$searxng_version.tgz" || pixel_die "OpenClaw searxng plugin differs from the pinned release (run ./pixel bootstrap --apply)"
pixel_plugin_release_matches llama-cpp "$llama_version" "$llama_integrity" "$llama_sha256" "$plugin_cache/llama-cpp-$llama_version.tgz" || pixel_die "OpenClaw llama-cpp plugin differs from the pinned release (run ./pixel bootstrap --apply)"
if [[ ${PIXEL_SOURCE_BROKER_ENABLED:-1} == 1 && $phase != plan ]]; then
  [[ -r "$PIXEL_SOURCE_PROJECTION_DIR/email.json" && -r "$PIXEL_SOURCE_PROJECTION_DIR/calendar.json" && -r "$PIXEL_SOURCE_PROJECTION_DIR/social.json" ]] || pixel_die "Source Broker projections are unavailable (run ./pixel source-broker --confirm)"
fi
if [[ ${PIXEL_OPS_BROKER_ENABLED:-0} == 1 && $phase != plan ]]; then
  pixel_courier_systemctl_read is-active --quiet "$PIXEL_OPS_BROKER_UNIT" || pixel_die "Operations Broker service is not active (run ./pixel ops-broker --confirm)"
  [[ -r "$PIXEL_OPS_INVENTORY_PATH" ]] || pixel_die "Operations inventory is unavailable"
  if pixel_release_operator_enabled; then
    pixel_release_operator_probe ops "Gateway owner can read Operations authority state"
  else
    ! pixel_user_can_access "$PIXEL_OPS_READER_USER" r "$PIXEL_OPS_BROKER_STATE_DIR/private" || pixel_die "Gateway owner can read Operations private state"
    ! pixel_user_can_access "$PIXEL_OPS_READER_USER" r "$PIXEL_OPS_POLICY_PATH" || pixel_die "Gateway owner can read Operations policy"
  fi
fi
if [[ ${PIXEL_FRONTIER_BROKER_ENABLED:-0} == 1 && $phase != plan ]]; then
  pixel_courier_systemctl_read is-active --quiet "$PIXEL_FRONTIER_BROKER_UNIT" || pixel_die "Frontier Broker service is not active (run ./pixel frontier-broker --confirm)"
  if pixel_release_operator_enabled; then
    pixel_release_operator_probe frontier "Gateway owner can read Frontier authority state"
  else
    ! pixel_user_can_access "$PIXEL_FRONTIER_READER_USER" r "$PIXEL_FRONTIER_CREDENTIAL_PATH" || pixel_die "Gateway owner can read Frontier provider credential"
    ! pixel_user_can_access "$PIXEL_FRONTIER_READER_USER" r "$PIXEL_FRONTIER_POLICY_PATH" || pixel_die "Gateway owner can read Frontier policy"
    ! pixel_user_can_access "$PIXEL_FRONTIER_READER_USER" r "$PIXEL_FRONTIER_BROKER_STATE_DIR/plans" || pixel_die "Gateway owner can read Frontier plans"
  fi
fi

if [[ ${PIXEL_SKIP_ENDPOINT_CHECKS:-0} == 1 || $skip_endpoints == 1 ]]; then
  pixel_warn "Endpoint checks skipped by explicit configuration"
else
  if [[ "$web_search_provider" == searxng ]]; then
    curl --fail --silent --max-time 8 "$PIXEL_SEARXNG_BASE_URL/search?q=pixel-preflight&format=json" | jq -e '.results | type == "array"' >/dev/null || pixel_die "SearXNG preflight failed: $PIXEL_SEARXNG_BASE_URL"
  fi
  model_url=${PIXEL_MODEL_BASE_URL%/}/models
  model_key=$PIXEL_MODEL_API_KEY
  if [[ "$model_key" == preserve-existing ]]; then
    model_key=$(jq -r --arg provider "$PIXEL_MODEL_PROVIDER" '.models.providers[$provider].apiKey // empty' "$OPENCLAW_HOME/openclaw.json")
    [[ -n "$model_key" ]] || pixel_die "Existing model key is unavailable for PIXEL_MODEL_API_KEY=preserve-existing"
  fi
  model_headers=(); [[ "$model_key" == local-no-auth ]] || model_headers=(-H "Authorization: Bearer $model_key")
  curl --fail --silent --max-time 15 "${model_headers[@]}" "$model_url" | jq -e '.data | type == "array" and length > 0' >/dev/null || pixel_die "Model endpoint preflight failed: $model_url"
fi

bash "$ROOT/scripts/check-no-secrets.sh"
pixel_log "Preflight passed for phase=$phase profile=$PIXEL_DEPLOYMENT_PROFILE"
