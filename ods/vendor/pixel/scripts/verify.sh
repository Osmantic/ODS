#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/common.sh
source "$ROOT/scripts/lib/common.sh"
# shellcheck source=scripts/lib/broker-bytes.sh
source "$ROOT/scripts/lib/broker-bytes.sh"
# shellcheck source=scripts/lib/release-build.sh
source "$ROOT/scripts/lib/release-build.sh"

expected_install_dir=''
while [[ $# -gt 0 ]]; do
  case "$1" in
    --expected-install-dir)
      [[ $# -ge 2 ]] || pixel_die "--expected-install-dir requires an absolute non-root path"
      expected_install_dir=$2; shift 2 ;;
    --help|-h) echo "Usage: ./pixel verify [--expected-install-dir ABSOLUTE]"; exit 0 ;;
    *) pixel_die "Unknown verify option: $1" ;;
  esac
done

pixel_load_env
runtime_attestation="$PIXEL_INSTALL_DIR/runtime-attestation.json"
pixel_invalidate_runtime_attestation "$runtime_attestation"
if [[ -n "$expected_install_dir" ]]; then
  # Fail closed: load the normal trusted configuration and require its configured
  # PIXEL_INSTALL_DIR to resolve exactly to the expected safe non-root path. This never
  # overrides configuration; it only asserts that verification ran against the intended
  # install directory.
  pixel_safe_absolute_dir "$expected_install_dir" "--expected-install-dir"
  [[ "$(realpath -m -- "$expected_install_dir")" == "$(realpath -m -- "$PIXEL_INSTALL_DIR")" ]] ||
    pixel_die "Configured PIXEL_INSTALL_DIR does not match --expected-install-dir"
fi
skip=(); [[ ${PIXEL_SKIP_ENDPOINT_CHECKS:-0} == 1 ]] && skip=(--skip-endpoints)
bash "$ROOT/scripts/preflight.sh" --phase verify "${skip[@]}"
[[ -f "$OPENCLAW_HOME/openclaw.json" ]] || pixel_die "OpenClaw configuration is not installed"
OPENCLAW_CONFIG_PATH="$OPENCLAW_HOME/openclaw.json" "$OPENCLAW_BIN" config validate
if [[ ${PIXEL_SOURCE_BROKER_ENABLED:-1} == 1 ]]; then pixel_custom_plugin_release_matches pixel-source-broker plugin "$PIXEL_RELEASE_VERSION" || pixel_die "Pixel Source Broker is not loaded from the active release"; fi
if [[ ${PIXEL_OPS_BROKER_ENABLED:-0} == 1 ]]; then pixel_custom_plugin_release_matches pixel-operations-broker plugin-ops "$PIXEL_RELEASE_VERSION" || pixel_die "Pixel Operations Broker is not loaded from the active release"; fi
if [[ ${PIXEL_FRONTIER_BROKER_ENABLED:-0} == 1 ]]; then pixel_custom_plugin_release_matches pixel-frontier-broker plugin-frontier "$PIXEL_RELEASE_VERSION" || pixel_die "Pixel Frontier Broker is not loaded from the active release"; fi
"$OPENCLAW_BIN" agents list | grep -F "$PIXEL_AGENT_ID" >/dev/null || pixel_die "Pixel agent is absent"
pixel_systemctl_read is-active --quiet "$PIXEL_SYSTEMD_UNIT" || pixel_die "Gateway service is not active"
if [[ ${PIXEL_SKIP_ENDPOINT_CHECKS:-0} != 1 ]]; then
  gateway_property() { pixel_systemctl_read show "$PIXEL_SYSTEMD_UNIT" -p "$1" --value; }
  [[ $(gateway_property User) == "$(id -un)" ]] || pixel_die "Gateway service does not run as the unprivileged deployment owner"
  [[ $(gateway_property NoNewPrivileges) == yes ]] || pixel_die "Gateway service lost NoNewPrivileges"
  [[ $(gateway_property PrivateDevices) == yes ]] || pixel_die "Gateway service can access host devices"
  [[ $(gateway_property PrivateTmp) == yes ]] || pixel_die "Gateway service does not have a private temporary directory"
  [[ $(gateway_property ProtectHome) == tmpfs ]] || pixel_die "Gateway service can see unrelated home state"
  [[ $(gateway_property ProtectSystem) == strict ]] || pixel_die "Gateway service system paths are writable"
  [[ $(gateway_property RestrictNamespaces) == yes ]] || pixel_die "Gateway service can create namespaces"
  [[ $(gateway_property UMask) == 0077 ]] || pixel_die "Gateway service has an unsafe file creation mask"
  [[ -z $(gateway_property CapabilityBoundingSet) ]] || pixel_die "Gateway service retained Linux capabilities"
  gateway_unit_path="${PIXEL_GATEWAY_SYSTEMD_DIR:-/etc/systemd/system}/$PIXEL_SYSTEMD_UNIT"
  [[ $(stat -c '%U:%G:%a' "$gateway_unit_path") == root:root:644 ]] || pixel_die "Gateway unit ownership or mode is unsafe"
  mapfile -t gateway_listeners < <(ss -H -ltn "sport = :$PIXEL_GATEWAY_PORT" | awk '{print $4}')
  [[ ${#gateway_listeners[@]} -gt 0 ]] || pixel_die "Gateway has no TCP listener"
  for listener in "${gateway_listeners[@]}"; do
    [[ "$listener" == "127.0.0.1:$PIXEL_GATEWAY_PORT" || "$listener" == "[::1]:$PIXEL_GATEWAY_PORT" ]] || pixel_die "Gateway listener escaped loopback: $listener"
  done
  for authorization in none invalid; do
    auth_args=()
    [[ "$authorization" == invalid ]] && auth_args=(-H 'Authorization: Bearer pixel-invalid-token-probe')
    status=$(curl -sS -o /dev/null -w '%{http_code}' "${auth_args[@]}" -H 'Content-Type: application/json' -d '{}' "http://127.0.0.1:$PIXEL_GATEWAY_PORT/v1/chat/completions")
    [[ "$status" == 401 || "$status" == 404 ]] || pixel_die "Gateway accepted a $authorization-token API probe (HTTP $status)"
  done
fi
if [[ ${PIXEL_WEB_COURIER_ENABLED:-1} == 1 ]]; then
  courier_unit=${PIXEL_WEB_COURIER_UNIT:-pixel-web-courier.service}
  pixel_courier_systemctl_read is-active --quiet "$courier_unit" || pixel_die "Web Courier system service is not active"
  [[ -x "$PIXEL_INSTALL_DIR/current/web-courier/.venv/bin/python" ]] || pixel_die "Web Courier runtime is missing"
  PYTHONDONTWRITEBYTECODE=1 "$PIXEL_INSTALL_DIR/current/web-courier/.venv/bin/python" -B -m pip check >/dev/null || pixel_die "Web Courier Python environment is inconsistent"
  [[ -x "$PIXEL_WORKSPACE/scripts/browse.sh" ]] || pixel_die "Web Courier workspace client is missing"
  # The policy probe is expected to return the exact refusal envelope and exit 3. Requiring
  # both prevents ordinary page content containing the same words from satisfying verify.
  probe_status=0
  probe=$(PIXEL_WEB_COURIER_RESPONSE_TIMEOUT=20 bash "$PIXEL_WORKSPACE/scripts/browse.sh" "http://127.0.0.1/" text 0 2>/dev/null) || probe_status=$?
  [[ $probe_status == 3 ]] || pixel_die "Web Courier did not return the policy-refusal status for a loopback request"
  [[ "${probe%%$'\n'*}" == '# Request refused by policy' ]] || pixel_die "Web Courier did not return the policy-refusal envelope for a loopback request"
fi
if [[ ${PIXEL_SOURCE_BROKER_ENABLED:-1} == 1 ]]; then
  broker_unit=${PIXEL_SOURCE_BROKER_UNIT:-pixel-source-broker.service}
  broker_timer=${PIXEL_SOURCE_BROKER_TIMER:-pixel-source-broker.timer}
  pixel_courier_systemctl_read is-active --quiet "$broker_timer" || pixel_die "Source Broker timer is not active"
  if [[ ${PIXEL_CALENDAR_DIRECT_ENABLED:-0} == 1 ]]; then
    direct_path=${PIXEL_SOURCE_DIRECT_PATH_UNIT:-pixel-source-direct.path}
    pixel_courier_systemctl_read is-active --quiet "$direct_path" || pixel_die "Bounded direct Calendar watcher is not active"
  fi
  [[ ! -r "$PIXEL_SOURCE_TOKEN_PATH" ]] || pixel_die "Gateway owner can still read the isolated Google credential"
  ! grep -q '^PIXEL_GOOGLE_TOKEN_PATH=' "${XDG_CONFIG_HOME:-$HOME/.config}/pixel-agent/gateway.env" || pixel_die "Gateway environment still references a Google credential"
  for source in email calendar social; do
    jq -e '.schemaVersion == 1 and .boundary.projectionOnly == true and .boundary.rawContentStored == false and (.records | type == "array")' "$PIXEL_SOURCE_PROJECTION_DIR/$source.json" >/dev/null || pixel_die "Invalid Source Broker projection: $source"
  done
  jq -e ".enabled == $([[ ${PIXEL_LIMB_EMAIL_ENABLED:-1} == 1 ]] && echo true || echo false)" "$PIXEL_SOURCE_PROJECTION_DIR/email.json" >/dev/null || pixel_die "Email projection does not match limb state"
  jq -e ".enabled == $([[ ${PIXEL_LIMB_CALENDAR_ENABLED:-1} == 1 ]] && echo true || echo false)" "$PIXEL_SOURCE_PROJECTION_DIR/calendar.json" >/dev/null || pixel_die "Calendar projection does not match limb state"
  jq -e ".enabled == $([[ ${PIXEL_LIMB_SOCIAL_ENABLED:-0} == 1 ]] && echo true || echo false)" "$PIXEL_SOURCE_PROJECTION_DIR/social.json" >/dev/null || pixel_die "Social projection does not match limb state"
  pixel_courier_systemctl_read show "$broker_unit" -p Result --value | grep -Fx success >/dev/null || pixel_die "Source Broker last refresh failed"
fi
active_release=$(realpath -e "$PIXEL_INSTALL_DIR/current") || pixel_die "Active release path is unavailable"
[[ "$active_release" == "$PIXEL_INSTALL_DIR/releases/$PIXEL_RELEASE_VERSION" ]] || pixel_die "Active release path does not match the configured release"
# Verify every installed+enabled broker's executable bytes hash-match the active release.
pixel_broker_bytes_verify "$active_release"
for attestation_file in release-identity.json deployment-inputs.sha256 source-runtime.sha256 install-manifest.sha256; do
  [[ -f "$active_release/$attestation_file" && ! -L "$active_release/$attestation_file" ]] || pixel_die "Installed release attestation is missing or linked: $attestation_file"
done
(cd "$active_release" && sha256sum -c install-manifest.sha256) >/dev/null || pixel_die "Installed release bytes do not match their deployment manifest"
pixel_release_tree_sha "$active_release" >/dev/null || pixel_die "Installed release tree contains unmanifested or unsafe entries"
verify_identity_tmp=$(mktemp "${TMPDIR:-/tmp}/pixel-verify-release-identity.XXXXXXXX")
trap 'rm -f -- "$verify_identity_tmp"' EXIT
node "$ROOT/scripts/release-identity.mjs" --output "$verify_identity_tmp" >/dev/null
cmp -s "$verify_identity_tmp" "$active_release/release-identity.json" || {
  rm -f -- "$verify_identity_tmp"
  pixel_die "Installed release source identity does not match the active source"
}
rm -f -- "$verify_identity_tmp"
trap - EXIT
(cd "$ROOT" && sha256sum -c "$active_release/source-runtime.sha256") >/dev/null || pixel_die "Active runtime source drifted after apply"
if [[ ${PIXEL_OPS_BROKER_ENABLED:-0} == 1 ]]; then
  pixel_courier_systemctl_read is-active --quiet "$PIXEL_OPS_BROKER_UNIT" || pixel_die "Operations Broker system service is not active"
  jq -e '.schemaVersion == 2 and (.targets | type == "array") and (.actions | type == "array") and (.authority | type == "object")' "$PIXEL_OPS_INVENTORY_PATH" >/dev/null || pixel_die "Operations inventory is invalid"
  if pixel_release_operator_enabled; then
    pixel_release_operator_probe ops "Gateway owner can read Operations authority state"
  else
    ! pixel_user_can_access "$PIXEL_OPS_READER_USER" r "$PIXEL_OPS_BROKER_STATE_DIR/private" || pixel_die "Gateway owner can read Operations private state"
    ! pixel_user_can_access "$PIXEL_OPS_READER_USER" r "$PIXEL_OPS_BROKER_STATE_DIR/.ssh" || pixel_die "Gateway owner can read Operations SSH state"
    ! pixel_user_can_access "$PIXEL_OPS_READER_USER" r "$PIXEL_OPS_POLICY_PATH" || pixel_die "Gateway owner can read Operations policy"
    ! pixel_user_can_access "$PIXEL_OPS_READER_USER" r "$PIXEL_OPS_BROKER_STATE_DIR/plans" || pixel_die "Gateway owner can read immutable Operations plans"
    ! pixel_user_can_access "$PIXEL_OPS_READER_USER" r "$PIXEL_OPS_BROKER_STATE_DIR/approvals" || pixel_die "Gateway owner can read Operations approvals"
  fi
  if grep -Eq '^PIXEL_OPS_(POLICY_PATH|SSH|PRIVATE|CREDENTIAL)=' "${XDG_CONFIG_HOME:-$HOME/.config}/pixel-agent/gateway.env"; then pixel_die "Gateway environment exposes Operations authority"; fi
fi
if [[ ${PIXEL_FRONTIER_BROKER_ENABLED:-0} == 1 ]]; then
  pixel_courier_systemctl_read is-active --quiet "$PIXEL_FRONTIER_BROKER_UNIT" || pixel_die "Frontier Broker system service is not active"
  if pixel_release_operator_enabled; then
    pixel_release_operator_probe frontier "Gateway owner can read Frontier authority state"
  else
    pixel_user_can_access "$PIXEL_FRONTIER_READER_USER" w "$PIXEL_FRONTIER_REQUEST_DIR" || pixel_die "Gateway owner cannot publish Frontier requests"
    pixel_user_can_access "$PIXEL_FRONTIER_READER_USER" r "$PIXEL_FRONTIER_RESULT_DIR" || pixel_die "Gateway owner cannot read Frontier results"
    ! pixel_user_can_access "$PIXEL_FRONTIER_READER_USER" r "$PIXEL_FRONTIER_CREDENTIAL_PATH" || pixel_die "Gateway owner can read Frontier provider credential"
    ! pixel_user_can_access "$PIXEL_FRONTIER_READER_USER" r "$PIXEL_FRONTIER_POLICY_PATH" || pixel_die "Gateway owner can read Frontier policy"
    if [[ ${PIXEL_FRONTIER_AUTH_MODE:-api-key} == chatgpt ]]; then
      pixel_user_can_access "$PIXEL_FRONTIER_BROKER_USER" w "$PIXEL_FRONTIER_CREDENTIAL_PATH" || pixel_die "Frontier worker cannot refresh its ChatGPT auth cache"
      ! pixel_user_can_access "$PIXEL_FRONTIER_BROKER_USER" w "$PIXEL_FRONTIER_BROKER_STATE_DIR/private" || pixel_die "Frontier worker can replace its ChatGPT auth directory"
    else
      ! pixel_user_can_access "$PIXEL_FRONTIER_BROKER_USER" w "$(dirname "$PIXEL_FRONTIER_CREDENTIAL_PATH")" || pixel_die "Frontier worker can replace its API key"
    fi
    for directory in private request-archive plans approvals authority runtime; do
      ! pixel_user_can_access "$PIXEL_FRONTIER_READER_USER" r "$PIXEL_FRONTIER_BROKER_STATE_DIR/$directory" || pixel_die "Gateway owner can read Frontier $directory state"
    done
  fi
  if grep -Eq '^PIXEL_FRONTIER_(POLICY|CREDENTIAL)' "${XDG_CONFIG_HOME:-$HOME/.config}/pixel-agent/gateway.env"; then pixel_die "Gateway environment exposes Frontier authority or credentials"; fi
fi
[[ -L "$PIXEL_INSTALL_DIR/current" && -f "$PIXEL_INSTALL_DIR/current/VERSION" ]] || pixel_die "Versioned Pixel release pointer is missing"
[[ $(cat "$PIXEL_INSTALL_DIR/current/VERSION") == "$PIXEL_RELEASE_VERSION" ]] || pixel_die "Active release version does not match configuration"
mapfile -t sandbox_containers < <(pixel_agent_sandbox_container_ids)
if [[ ${#sandbox_containers[@]} -gt 1 ]]; then pixel_die "Multiple agent-scoped sandbox containers are present"; fi
if [[ ${#sandbox_containers[@]} == 1 ]]; then
  container=${sandbox_containers[0]}
  pixel_validate_agent_sandbox_identity "$container"
  expected_sandbox_image=$(docker image inspect --format '{{.Id}}' "$PIXEL_SANDBOX_IMAGE")
  expected_sandbox_uid=$(id -u)
  sandbox_inspect=$(docker inspect "$container") || pixel_die "Agent sandbox container cannot be inspected"
  sandbox_lifecycle=$(printf '%s\n' "$sandbox_inspect" | jq -er '
    .[0].State as $state |
    if (
      $state.Status == "running" and
      $state.Running == true and
      ($state.Paused // false) == false and
      ($state.Restarting // false) == false and
      ($state.OOMKilled // false) == false and
      ($state.Dead // false) == false and
      ($state.Pid // 0) > 0 and
      (($state.Error // "") == "")
    ) then "running"
    elif (
      $state.Status == "exited" and
      $state.Running == false and
      ($state.Paused // false) == false and
      ($state.Restarting // false) == false and
      ($state.Dead // false) == false and
      ($state.OOMKilled // false) == false and
      ($state.Pid // 0) == 0 and
      (($state.ExitCode == 0) or ($state.ExitCode == 130) or ($state.ExitCode == 143)) and
      (($state.Error // "") == "")
    ) then "dormant"
    else error("unsafe sandbox lifecycle state")
    end
  ') || pixel_die "Agent sandbox container has an unsafe or ambiguous lifecycle state"
  printf '%s\n' "$sandbox_inspect" | jq -e --arg workspace "$PIXEL_WORKSPACE" --arg image "$expected_sandbox_image" --arg version "$PIXEL_RELEASE_VERSION" --arg uid "$expected_sandbox_uid" '
    .[0] as $c |
    $c.Image == $image and
    $c.Config.Labels["org.osmantic.pixel.sandbox-version"] == $version and
    $c.Config.Labels["org.osmantic.pixel.sandbox-uid"] == $uid and
    $c.Config.User == "sandbox" and
    $c.HostConfig.NetworkMode == "none" and
    $c.HostConfig.ReadonlyRootfs == true and
    $c.HostConfig.Privileged == false and
    (($c.HostConfig.CapDrop // []) | index("ALL") != null) and
    (($c.HostConfig.SecurityOpt // []) | index("no-new-privileges") != null) and
    (($c.HostConfig.Devices // []) | length == 0) and
    $c.HostConfig.RestartPolicy.Name == "no" and
    ($c.HostConfig.PidMode != "host") and ($c.HostConfig.IpcMode != "host") and
    ($c.HostConfig.UTSMode != "host") and ($c.HostConfig.CgroupnsMode != "host") and
    ($c.HostConfig.PidsLimit > 0 and $c.HostConfig.PidsLimit <= 1024) and
    ($c.HostConfig.Memory > 0) and
    all($c.Mounts[]?;
      .Source != "/" and .Source != "/var/run/docker.sock" and
      (.Source | startswith("/etc/")) == false and
      (.Source | startswith("/var/lib/pixel-source-broker")) == false and
      (.Source | startswith("/var/lib/pixel-ops-broker")) == false and
      (.Source | startswith("/var/lib/pixel-frontier-broker")) == false and
      (.Source | contains("/.ssh")) == false
    )
  ' >/dev/null || pixel_die "Agent sandbox violates the filesystem, process, device, privilege, restart, or network boundary"
  if [[ $sandbox_lifecycle == dormant ]]; then
    pixel_warn "Agent sandbox container is dormant after a recognized lifecycle boundary; static confinement is verified, but runtime isolation inspection requires one live tool turn"
  fi
else
  pixel_warn "No agent sandbox container exists; runtime isolation inspection requires one live tool turn"
fi
endpoint_state=verified; [[ ${PIXEL_SKIP_ENDPOINT_CHECKS:-0} == 1 ]] && endpoint_state=skipped
node "$ROOT/scripts/runtime-attestation.mjs" \
  --source-root "$ROOT" --active-release "$PIXEL_INSTALL_DIR/current" \
  --configuration "$OPENCLAW_HOME/openclaw.json" --output "$runtime_attestation" \
  --endpoint-state "$endpoint_state" >/dev/null
pixel_log "Verification passed for Pixel $PIXEL_RELEASE_VERSION"
