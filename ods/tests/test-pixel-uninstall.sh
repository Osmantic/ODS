#!/usr/bin/env bash
set -euo pipefail

# Fixtures model private, non-group-writable Pixel release custody regardless
# of the developer's shell umask. Keep the production permission guard strict.
umask 077

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../lib/pixel-uninstall.sh
source "$ROOT_DIR/lib/pixel-uninstall.sh"

PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); printf '[PASS] %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf '[FAIL] %s\n' "$1" >&2; }
log_info() { :; }
log_ok() { :; }
log_error() { :; }

TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "$TEST_ROOT"' EXIT
MOCK_BIN="$TEST_ROOT/bin"
SYSTEMD_DIR="$TEST_ROOT/systemd"
OPS_DROPIN_DIR="$SYSTEMD_DIR/pixel-ops-broker.service.d"
OPS_DROPIN="$OPS_DROPIN_DIR/10-ods-host-observation.conf"
ETC_DIR="$TEST_ROOT/etc"
LIBEXEC_DIR="$TEST_ROOT/libexec"
HOME_DIR="$TEST_ROOT/home"
INSTALL_DIR="$TEST_ROOT/install"
SYSTEMCTL_LOG="$TEST_ROOT/systemctl.log"
DOCKER_LOG="$TEST_ROOT/docker.log"
DOCKER_STATE="$TEST_ROOT/docker-live-state"
OPS_ENV="$TEST_ROOT/pixel-ops-broker.env"
OPS_POLICY_DIR="$TEST_ROOT/etc/pixel-ops-broker"
OPS_POLICY="$OPS_POLICY_DIR/policy.json"
OPS_INSTALL="$TEST_ROOT/opt/pixel-ops-broker"
OPS_STATE="$TEST_ROOT/var/lib/pixel-ops-broker"
PREVIEW_STATE="$TEST_ROOT/var/lib/ods-pixel-preview"
ACCESS_STATE="$TEST_ROOT/var/lib/ods-pixel-access"
ACCESS_PROBE_BASE="$TEST_ROOT/var/lib/ods-pixel-access-probes"
OPS_IDENTITY_LOG="$TEST_ROOT/ops-identity.log"
OPS_PASSWD_STATE="$TEST_ROOT/ops-passwd"
OPS_GROUP_STATE="$TEST_ROOT/ops-group"
mkdir -p "$MOCK_BIN" "$SYSTEMD_DIR" "$ETC_DIR" "$LIBEXEC_DIR" "$HOME_DIR"

cat >"$MOCK_BIN/sudo" <<'SH'
#!/usr/bin/env bash
exec "$@"
SH
cat >"$MOCK_BIN/systemctl" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$SYSTEMCTL_LOG"
if [[ "${SYSTEMCTL_FAIL_DISABLE:-false}" == "true" && "${1:-}" == "disable" ]]; then
    exit 1
fi
if [[ "${ACCESS_STOP_FAIL:-false}" == true && "$*" == 'disable --now ods-pixel-access.service' ]]; then exit 1; fi
if [[ "${ACCESS_STILL_ACTIVE:-false}" == true && "$*" == 'is-active --quiet ods-pixel-access.service' ]]; then exit 0; fi
case " $* " in
    *" is-active --quiet "*) exit 1 ;;
esac
exit 0
SH
cat >"$MOCK_BIN/docker" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$DOCKER_LOG"
image_id="sha256:$(printf 'd%.0s' {1..64})"
container_id="$(printf 'e%.0s' {1..64})"
case "${1:-} ${2:-}" in
    "image inspect")
        reference="${*: -1}"
        if [[ "$reference" == openclaw-sandbox:test && ! -e "$DOCKER_STATE" ]]; then
            exit 1
        fi
        if [[ " $* " == *" --format "* ]]; then
            printf '%s|4.3.14|%s|sandbox\n' "$image_id" "$(id -u)"
        else
            printf '%s\n' '[]'
        fi
        ;;
    "image rm")
        rm -f -- "$DOCKER_STATE"
        ;;
    "ps -aq")
        printf '%s\n' "$container_id"
        ;;
    "inspect --format")
        printf '/pixel-sbx-agent-pixel-test|1|agent:pixel|%s\n' "$image_id"
        ;;
    "rm -f") ;;
    *) exit 1 ;;
esac
SH
cat >"$MOCK_BIN/getent" <<'SH'
#!/usr/bin/env bash
case "${1:-}:${2:-}" in
    passwd:pixel-ops-broker) [[ -f "$OPS_PASSWD_STATE" ]] && cat "$OPS_PASSWD_STATE" ;;
    group:pixel-ops) [[ -f "$OPS_GROUP_STATE" ]] && cat "$OPS_GROUP_STATE" ;;
    *) exec /usr/bin/getent "$@" ;;
esac
SH
cat >"$MOCK_BIN/userdel" <<'SH'
#!/usr/bin/env bash
[[ "${1:-}" == pixel-ops-broker && -f "$OPS_PASSWD_STATE" ]] || exit 1
printf 'userdel %s\n' "$1" >>"$OPS_IDENTITY_LOG"
rm -f -- "$OPS_PASSWD_STATE"
SH
cat >"$MOCK_BIN/groupdel" <<'SH'
#!/usr/bin/env bash
[[ "${1:-}" == pixel-ops && -f "$OPS_GROUP_STATE" ]] || exit 1
printf 'groupdel %s\n' "$1" >>"$OPS_IDENTITY_LOG"
rm -f -- "$OPS_GROUP_STATE"
SH
chmod +x "$MOCK_BIN/sudo" "$MOCK_BIN/systemctl" "$MOCK_BIN/docker" \
    "$MOCK_BIN/getent" "$MOCK_BIN/userdel" "$MOCK_BIN/groupdel"
export PATH="$MOCK_BIN:$PATH" SYSTEMCTL_LOG DOCKER_LOG DOCKER_STATE
export OPS_IDENTITY_LOG OPS_PASSWD_STATE OPS_GROUP_STATE
export ODS_PIXEL_UNINSTALL_SYSTEMD_DIR="$SYSTEMD_DIR"
export ODS_PIXEL_UNINSTALL_ETC_DIR="$ETC_DIR"
export ODS_PIXEL_UNINSTALL_LIBEXEC_DIR="$LIBEXEC_DIR"
export ODS_PIXEL_UNINSTALL_OPS_ENV="$OPS_ENV"
export ODS_PIXEL_UNINSTALL_OPS_POLICY="$OPS_POLICY"
export ODS_PIXEL_UNINSTALL_OPS_INSTALL_DIR="$OPS_INSTALL"
export ODS_PIXEL_UNINSTALL_OPS_STATE_DIR="$OPS_STATE"
export ODS_PIXEL_UNINSTALL_PREVIEW_STATE_DIR="$PREVIEW_STATE"
export ODS_PIXEL_UNINSTALL_ACCESS_STATE_DIR="$ACCESS_STATE"
export ODS_PIXEL_UNINSTALL_ACCESS_PROBE_DIR="$ACCESS_PROBE_BASE"
ODS_PIXEL_UNINSTALL_ROOT_UID="$(id -u)"
ODS_PIXEL_UNINSTALL_ROOT_GID="$(id -g)"
ODS_PIXEL_UNINSTALL_OPS_UID="$(id -u)"
ODS_PIXEL_UNINSTALL_OPS_GID="$(id -g)"
export ODS_PIXEL_UNINSTALL_ROOT_UID ODS_PIXEL_UNINSTALL_ROOT_GID \
    ODS_PIXEL_UNINSTALL_OPS_UID ODS_PIXEL_UNINSTALL_OPS_GID

if python3 - "$ROOT_DIR/ods-uninstall.sh" <<'PY'
import pathlib
import sys

text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
hook = 'ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME"'
assert '. "$SCRIPT_DIR/lib/pixel-uninstall.sh"' in text
assert hook in text
assert text.index(hook) < text.index("# 1. Stop and remove Docker containers")
PY
then
    pass "ODS uninstaller invokes managed Pixel cleanup before broader mutation"
else
    fail "ODS uninstaller does not safely integrate managed Pixel cleanup"
fi

if python3 - "$ROOT_DIR/install-core.sh" "$ROOT_DIR/installers/phases/06-directories.sh" <<'PY'
import pathlib
import sys

core = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
phase = pathlib.Path(sys.argv[2]).read_text(encoding="utf-8")
hook = 'ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME"'
assert 'source "$SCRIPT_DIR/lib/pixel-uninstall.sh"' in core
assert '"${ENABLE_PIXEL_RUNTIME:-false}" != "true"' in phase
assert hook in phase
assert phase.index(hook) < phase.index('_phase06_step "copy-source"')
assert '_ods_pixel_source_transition_required' in phase
assert '_phase06_step "rebind-pixel-source"' in phase
assert phase.index('_phase06_step "rebind-pixel-source"') < phase.index('_phase06_step "copy-source"')
PY
then
    pass "Pixel reruns retire disabled or superseded managed host runtimes before source replacement"
else
    fail "Pixel rerun does not safely deactivate managed host runtime before source replacement"
fi

if python3 - "$ROOT_DIR/lib/pixel-uninstall.sh" <<'PY'
import pathlib
import sys

text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
assert 'owner_gid="$(id -g)"' in text
assert '"$root_uid" "$root_gid" "$owner_uid" "$owner_gid"' in text
assert "info.st_gid != owner_gid" in text
assert "info.st_gid != os.getgid()" not in text
PY
then
    pass "root Operations validation compares legacy source custody with the captured owner primary group"
else
    fail "root Operations validation confuses its process group with the Pixel owner group"
fi

if logger_output="$(bash -c '
    unset -f log_info log_ok log_error 2>/dev/null || true
    source "$1"
    declare -F log_info log_ok log_error >/dev/null
    ods_pixel_uninstall_managed relative-path /tmp
' _ "$ROOT_DIR/lib/pixel-uninstall.sh" 2>&1)"; then
    fail "Pixel uninstall unexpectedly accepted an invalid install directory"
elif [[ "$logger_output" == *"Refusing Pixel cleanup for an invalid ODS install directory"* \
    && "$logger_output" != *"command not found"* ]]; then
    pass "Pixel uninstall supplies safe fallback logging for install-core callers"
else
    fail "Pixel uninstall fallback logging is unavailable"
fi

# Contract regression for the root-owned access coordinator. This service is
# outside the checkout and uses Restart=on-failure, so uninstall must retire it.
if python3 - "$ROOT_DIR/lib/pixel-uninstall.sh" <<'PY'
import pathlib
import sys

text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
required = (
    'access_unit="$systemd_dir/ods-pixel-access.service"',
    'systemctl disable --now ods-pixel-access.service',
    'systemctl is-active --quiet ods-pixel-access.service',
    '_ods_pixel_access_validate_or_remove verify',
    '_ods_pixel_access_validate_or_remove remove',
)
missing = [item for item in required if item not in text]
if missing:
    raise SystemExit("missing access-service uninstall contract: " + ", ".join(missing))
PY
then
    pass "uninstall contract retires the root-owned Pixel access service"
else
    fail "uninstall contract omits the root-owned Pixel access service"
fi

write_fixture() {
    rm -rf "$SYSTEMD_DIR" "$ETC_DIR" "$LIBEXEC_DIR" "$HOME_DIR" "$INSTALL_DIR" \
        "$OPS_POLICY_DIR" "$OPS_INSTALL" "$OPS_STATE" "$PREVIEW_STATE" \
        "$ACCESS_STATE" "$ACCESS_PROBE_BASE"
    rm -f -- "$OPS_ENV" "$OPS_IDENTITY_LOG" "$OPS_PASSWD_STATE" "$OPS_GROUP_STATE"
    : >"$SYSTEMCTL_LOG"
    : >"$DOCKER_LOG"
    rm -f -- "$DOCKER_STATE"
    mkdir -p \
        "$SYSTEMD_DIR" "$ETC_DIR" "$LIBEXEC_DIR" \
        "$HOME_DIR/.config/ods" "$HOME_DIR/.config/pixel-agent" \
        "$HOME_DIR/.config/pixel-deployment" "$HOME_DIR/.openclaw" \
        "$INSTALL_DIR/data/pixel" \
        "$INSTALL_DIR/extensions/services/pixel-agent/host" \
        "$INSTALL_DIR/extensions/services/pixel-agent/plugin"

    printf '%s\n' 'console.log("managed ingress");' \
        >"$INSTALL_DIR/extensions/services/pixel-agent/host/pixel_ingress.mjs"
    cp "$INSTALL_DIR/extensions/services/pixel-agent/host/pixel_ingress.mjs" \
        "$LIBEXEC_DIR/ods-pixel-ingress.mjs"
    chmod 0755 "$LIBEXEC_DIR/ods-pixel-ingress.mjs"

    cat >"$HOME_DIR/.config/ods/pixel-managed.json" <<JSON
{"schema_version":1,"manager":"ods","state":"ready","install_dir":"$INSTALL_DIR","pixel_source_ref":"d2a2b6be552126f294fb30ee5fb46872acf82c89"}
JSON
    cat >"$HOME_DIR/.openclaw/openclaw.json" <<JSON
{"plugins":{"load":{"paths":["$INSTALL_DIR/extensions/services/pixel-agent/plugin"]}}}
JSON
    printf '%s\n' 'PIXEL_GATEWAY_TOKEN=test-only' >"$HOME_DIR/.config/pixel-agent/gateway.env"
    cat >"$HOME_DIR/.config/pixel-deployment/onboarding.json" <<JSON
{"gatewayExtensions":[{"id":"pixel-ods","path":"$INSTALL_DIR/extensions/services/pixel-agent/plugin"}]}
JSON
    cp "$HOME_DIR/.config/pixel-deployment/onboarding.json" \
        "$INSTALL_DIR/data/pixel/onboarding.json"
    printf '%s\n' 'preserve me' >"$HOME_DIR/.openclaw/openclaw.json.bak"
    chmod 0600 \
        "$HOME_DIR/.config/ods/pixel-managed.json" \
        "$HOME_DIR/.openclaw/openclaw.json" \
        "$HOME_DIR/.config/pixel-agent/gateway.env" \
        "$HOME_DIR/.config/pixel-deployment/onboarding.json" \
        "$INSTALL_DIR/data/pixel/onboarding.json"

    cat >"$SYSTEMD_DIR/openclaw-gateway.service" <<UNIT
[Unit]
Description=OpenClaw Gateway - Pixel
[Service]
BindReadOnlyPaths=$INSTALL_DIR/extensions/services/pixel-agent/plugin
UNIT
    cat >"$SYSTEMD_DIR/pixel-ingress.service" <<UNIT
[Unit]
Description=Pixel Agent host ingress (ODS -> Pixel gateway)
[Service]
ExecStart=/usr/bin/env node $LIBEXEC_DIR/ods-pixel-ingress.mjs
EnvironmentFile=$ETC_DIR/pixel-agent.env
UNIT
    cat >"$ETC_DIR/pixel-agent.env" <<'ENV'
PIXEL_INGRESS_SOCKET=/run/ods-pixel/pixel-ingress.sock
PIXEL_GATEWAY_TOKEN_FILE=/run/ods-pixel/openclaw.json
PIXEL_STATUS_FILE=/run/ods-pixel/ods-status.json
ENV
    chmod 0644 "$SYSTEMD_DIR/openclaw-gateway.service" "$SYSTEMD_DIR/pixel-ingress.service"
    chmod 0640 "$ETC_DIR/pixel-agent.env"
}

write_access_fixture() {
    write_fixture
    local access_program="$LIBEXEC_DIR/ods-pixel-access"
    local access_dropin_dir="$SYSTEMD_DIR/openclaw-gateway.service.d"
    local access_runtime_state="$HOME_DIR/.openclaw/.ods-access-runtime"
    local owner_uid relative source relay_digest
    owner_uid="$(id -u)"
    mkdir -p "$INSTALL_DIR/bin/pixel_settings" "$INSTALL_DIR/bin/pixel_provider" \
        "$access_program/pixel_settings" "$access_program/pixel_provider" \
        "$access_dropin_dir" "$ACCESS_STATE" "$ACCESS_PROBE_BASE/$owner_uid" \
        "$access_runtime_state"
    local -a access_sources=(
        "extensions/services/pixel-agent/host/access_mode_server.py"
        "extensions/services/pixel-agent/host/access_mode_worker.py"
        "extensions/services/pixel-agent/host/pixel_access_mode.py"
        "extensions/services/pixel-agent/host/access_mode_config.py"
        "extensions/services/pixel-agent/host/settings_transaction.py"
        "extensions/services/pixel-agent/host/provider_transaction.py"
        "extensions/services/pixel-agent/host/model_transaction.py"
        "bin/pixel_access_bridge.py"
        "bin/pixel_access_client.py"
        "bin/pixel_access_reconcile.py"
        "bin/pixel_model_transition.py"
        "bin/pixel_access_protocol.py"
        "bin/pixel_model_contract.py"
        "bin/pixel_model_coordinator.py"
        "bin/pixel_settings/__init__.py"
        "bin/pixel_settings/contract.py"
        "bin/pixel_settings/projection.py"
        "bin/pixel_settings/runtime.py"
        "bin/pixel_settings/coordinator.py"
        "bin/pixel_provider/__init__.py"
        "bin/pixel_provider/config.py"
        "bin/pixel_provider/store.py"
        "bin/pixel_provider/activation_config.py"
        "bin/pixel_provider/managed_deployment.py"
        "bin/pixel_provider/service_environment.py"
        "bin/pixel_provider/service_activation.py"
        "bin/pixel_provider/runtime_custody.py"
        "bin/pixel_provider/coordinator.py"
    )
    for relative in "${access_sources[@]}"; do
        source="$ROOT_DIR/$relative"
        cp "$source" "$INSTALL_DIR/$relative"
        case "$relative" in
            extensions/services/pixel-agent/host/*) cp "$source" "$access_program/${relative##*/}" ;;
            bin/pixel_settings/*) cp "$source" "$access_program/pixel_settings/${relative##*/}" ;;
            bin/pixel_provider/*) cp "$source" "$access_program/pixel_provider/${relative##*/}" ;;
            bin/*) cp "$source" "$access_program/${relative##*/}" ;;
        esac
    done
    cp "$ROOT_DIR/extensions/services/pixel-agent/host/ods-pixel-access.service" \
        "$INSTALL_DIR/extensions/services/pixel-agent/host/ods-pixel-access.service"
    cp "$INSTALL_DIR/extensions/services/pixel-agent/host/ods-pixel-access.service" \
        "$SYSTEMD_DIR/ods-pixel-access.service"
    printf '%064d' 0 > "$ETC_DIR/pixel-access-relay.key"
    relay_digest="$(sha256sum "$ETC_DIR/pixel-access-relay.key" | cut -d' ' -f1)"
    cat > "$ETC_DIR/pixel-access.json" <<JSON
{"install_dir":"$INSTALL_DIR","owner":"$(id -un)","openclaw_bin":"$MOCK_BIN/openclaw","gateway_port":18789,"settings_data_dir":null,"edge_owner_key_sha256":"$relay_digest"}
JSON
    : > "$ACCESS_STATE/lock"
    printf '%s\n' '{"boundary":"fixture"}' > "$ACCESS_STATE/service-baseline.json"
    printf '%s\n' '[Service]' 'ProtectSystem=false' 'ProtectHome=false' \
        > "$access_dropin_dir/90-ods-full-access.conf"
    printf '%s\n' fixture > "$ACCESS_PROBE_BASE/$owner_uid/sentinel-123e4567-e89b-12d3-a456-426614174000"
    printf '%s\n' '{"version":1,"phase":"held","revision":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","tokenHash":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}' \
        > "$access_runtime_state/state.json"
    printf '%s\n' '{"pid":999999}' > "$access_runtime_state/process.json"
    : > "$access_runtime_state/.process-claim"
    chmod 0755 "$access_program" "$access_program/pixel_settings" "$access_program/pixel_provider"
    chmod 0644 "$access_program"/*.py "$access_program/pixel_settings"/*.py \
        "$access_program/pixel_provider"/*.py "$SYSTEMD_DIR/ods-pixel-access.service" \
        "$access_dropin_dir/90-ods-full-access.conf"
    chmod 0600 "$ETC_DIR/pixel-access.json" "$ETC_DIR/pixel-access-relay.key" "$ACCESS_STATE/lock" \
        "$ACCESS_STATE/service-baseline.json"
    chmod 0700 "$ACCESS_STATE" "$ACCESS_PROBE_BASE/$owner_uid"
    chmod 0711 "$ACCESS_PROBE_BASE"
    chmod 0600 "$ACCESS_PROBE_BASE/$owner_uid/sentinel-123e4567-e89b-12d3-a456-426614174000"
    chmod 0600 "$access_runtime_state/state.json" "$access_runtime_state/process.json" \
        "$access_runtime_state/.process-claim"
}

write_provider_service_fixture() {
    local provider_env="$ETC_DIR/pixel-provider.env"
    local provider_dropin="$SYSTEMD_DIR/openclaw-gateway.service.d/95-ods-provider.conf"
    printf '%s\n' 'ODS_PROVIDER_FIXTURE=1' > "$provider_env"
    printf '[Service]\nEnvironmentFile=%s\nBindPaths=%s\n' \
        "$provider_env" "$INSTALL_DIR/data/pixel-providers" > "$provider_dropin"
    chmod 0600 "$provider_env"
    chmod 0644 "$provider_dropin"
    python3 - "$provider_env" "$provider_dropin" "$ACCESS_STATE/provider-root-managed.json" <<'PY'
import json, pathlib, sys
environment, dropin, record = map(pathlib.Path, sys.argv[1:])
image = lambda path: {'hex': path.read_bytes().hex(), 'mode': path.stat().st_mode & 0o777}
record.write_text(json.dumps({'plan': {},
    'baseline': {'environment': None, 'dropin': None},
    'environment': {'environment': image(environment), 'dropin': image(dropin)}},
    sort_keys=True) + '\n', encoding='utf-8')
PY
    chmod 0600 "$ACCESS_STATE/provider-root-managed.json"
}

write_active_fixture() {
    write_fixture
    local pixel_install="$HOME_DIR/.local/share/pixel"
    local exec_control="$HOME_DIR/.openclaw/.ods-exec-control"
    local release="$pixel_install/releases/4.3.14"
    local source_ref="d2a2b6be552126f294fb30ee5fb46872acf82c89"
    local source_tree image_id
    source_tree="$(printf 'a%.0s' {1..40})"
    image_id="sha256:$(printf 'd%.0s' {1..64})"
    mkdir -m 0700 "$exec_control"
    printf '%s\n' '#!/bin/sh' >"$exec_control/cancellable-exec.sh"
    printf '%s\n' '#!/bin/sh' >"$exec_control/sudo"
    chmod 0500 "$exec_control/cancellable-exec.sh" "$exec_control/sudo"
    : >"$exec_control/$(printf 'e%.0s' {1..64}).cancel"
    chmod 0600 "$exec_control/$(printf 'e%.0s' {1..64}).cancel"
    mkdir -p "$release"
    cat >"$release/release-identity.json" <<JSON
{"kind":"pixel-release-source-identity","pixel":"4.3.14","source":{"state":"git-clean","commit":"$source_ref","tree":"$source_tree"}}
JSON
    printf '%s  %s\n' "$(sha256sum "$release/release-identity.json" | awk '{print $1}')" release-identity.json \
        >"$release/install-manifest.sha256"
    local identity_sha256 manifest_sha256 config_sha256 contract_sha256
    identity_sha256="$(sha256sum "$release/release-identity.json" | awk '{print $1}')"
    manifest_sha256="$(sha256sum "$release/install-manifest.sha256" | awk '{print $1}')"
    cat >"$pixel_install/runtime-attestation.json" <<JSON
{"kind":"pixel-runtime-attestation","status":"verified","pixel":"4.3.14","source":{"state":"git-clean","commit":"$source_ref","tree":"$source_tree"},"release":{"sourceIdentitySha256":"$identity_sha256","installManifestSha256":"$manifest_sha256"}}
JSON
    chmod 0600 "$pixel_install/runtime-attestation.json"
    : >"$pixel_install/.deployment.lock"
    chmod 0600 "$pixel_install/.deployment.lock"
    ln -s "$release" "$pixel_install/current"
    config_sha256="$(python3 - "$HOME_DIR/.openclaw/openclaw.json" <<'PY'
import hashlib, json, pathlib, sys
value = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
print(hashlib.sha256(b"ods-pixel-openclaw-v1\0" + canonical).hexdigest())
PY
)"
    contract_sha256="$(python3 - "$HOME_DIR/.config/pixel-deployment/onboarding.json" <<'PY'
import hashlib, pathlib, sys
print(hashlib.sha256(b"ods-pixel-contract-v1\0" + pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())
PY
)"
    cat >"$HOME_DIR/.config/ods/pixel-managed.json" <<JSON
{"schema_version":2,"manager":"ods","state":"ready","initial_active_state":"absent","install_dir":"$INSTALL_DIR","pixel_source_ref":"$source_ref","contract_sha256":"$contract_sha256","configuration_sha256":"$config_sha256","active_release_version":"4.3.14","release_identity_sha256":"$identity_sha256","install_manifest_sha256":"$manifest_sha256","sandbox_image":"openclaw-sandbox:test","sandbox_image_id":"$image_id"}
JSON
    chmod 0600 "$HOME_DIR/.config/ods/pixel-managed.json"
    : >"$DOCKER_STATE"
    : >"$DOCKER_LOG"
}

write_ops_fixture() {
    write_active_fixture
    local source_ref="d2a2b6be552126f294fb30ee5fb46872acf82c89"
    local source="$INSTALL_DIR/data/pixel/source-$source_ref"
    local release="$HOME_DIR/.local/share/pixel/releases/4.3.14"
    local uid gid contract_sha256
    uid="$(id -u)"
    gid="$(id -g)"
    mkdir -p "$source/.generated" "$source/deploy/ops-broker" "$INSTALL_DIR/bin" "$INSTALL_DIR/data/pixel" \
        "$INSTALL_DIR/extensions/services/pixel-agent/host"
    cp "$ROOT_DIR/bin/ods-pixel-approve" "$INSTALL_DIR/bin/ods-pixel-approve"
    chmod 0755 "$INSTALL_DIR/bin/ods-pixel-approve"
    cat >"$INSTALL_DIR/data/pixel/operations-policy.json" <<JSON
{"schemaVersion":2,"deployment":"ods-default","download":{"stagingRoot":"$OPS_STATE/artifacts"},"targets":{"broker":{"backend":"local","writableRoots":["$OPS_STATE/artifacts"]}},"authority":{"defaultLevel":"propose"}}
JSON
    chmod 0600 "$INSTALL_DIR/data/pixel/operations-policy.json"
    cp "$INSTALL_DIR/data/pixel/operations-policy.json" "$source/.generated/ops-policy.json"
    chmod 0600 "$source/.generated/ops-policy.json"
    cat >"$INSTALL_DIR/data/pixel/extension-catalog.json" <<'JSON'
{"extensions":[{"category":"optional","dependsOn":[],"description":"Fixture extension.","featureNames":[],"gpuBackends":[],"id":"fixture","name":"Fixture","optionalConfiguration":[],"requiredConfiguration":[],"tags":[]}],"kind":"ods-pixel-extension-catalog","schemaVersion":1,"sourceSha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
JSON
    chmod 0600 "$INSTALL_DIR/data/pixel/extension-catalog.json"
    cp "$ROOT_DIR/extensions/services/pixel-agent/host/extension_search.py" \
        "$INSTALL_DIR/extensions/services/pixel-agent/host/extension_search.py"
    cp "$ROOT_DIR/extensions/services/pixel-agent/host/extension_manager.py" \
        "$INSTALL_DIR/extensions/services/pixel-agent/host/extension_manager.py"
    cp "$ROOT_DIR/extensions/services/pixel-agent/host/artifact_promoter.py" \
        "$INSTALL_DIR/extensions/services/pixel-agent/host/artifact_promoter.py"
    cp "$ROOT_DIR/extensions/services/pixel-agent/host/workspace_preview.py" \
        "$INSTALL_DIR/extensions/services/pixel-agent/host/workspace_preview.py"
    cp "$ROOT_DIR/extensions/services/pixel-agent/host/system_observe.py" \
        "$INSTALL_DIR/extensions/services/pixel-agent/host/system_observe.py"
    cp "$ROOT_DIR/extensions/services/pixel-agent/host/pixel-ops-broker-ods.conf" \
        "$INSTALL_DIR/extensions/services/pixel-agent/host/pixel-ops-broker-ods.conf"
    python3 - "$ROOT_DIR/extensions/services/pixel-agent/host/pixel-extension-manager.service" \
        "$INSTALL_DIR/data/pixel/extension-manager.service" "$INSTALL_DIR" "$(id -un)" <<'PY'
import pathlib, sys
source, target, install_dir, owner = sys.argv[1:]
text = pathlib.Path(source).read_text(encoding="utf-8")
text = (text.replace("__PIXEL_SERVICE_USER__", owner)
            .replace("__ODS_INSTALL_DIR__", install_dir)
            .replace("__ODS_DASHBOARD_PORT__", "3002"))
pathlib.Path(target).write_text(text, encoding="utf-8")
PY
    python3 - "$ROOT_DIR/extensions/services/pixel-agent/host/pixel-artifact-promoter.service" \
        "$INSTALL_DIR/data/pixel/artifact-promoter.service" "$HOME_DIR" "$(id -un)" <<'PY'
import pathlib, sys
source, target, home, owner = sys.argv[1:]
text = pathlib.Path(source).read_text(encoding="utf-8")
text = (text.replace("__PIXEL_SERVICE_USER__", owner)
            .replace("__PIXEL_WORKSPACE__", str(pathlib.Path(home) / ".openclaw/workspace-pixel")))
pathlib.Path(target).write_text(text, encoding="utf-8")
PY
    python3 - "$ROOT_DIR/extensions/services/pixel-agent/host/pixel-workspace-preview.service" \
        "$INSTALL_DIR/data/pixel/workspace-preview.service" "$HOME_DIR" "$(id -un)" <<'PY'
import pathlib, sys
source, target, home, owner = sys.argv[1:]
text = pathlib.Path(source).read_text(encoding="utf-8")
text = (text.replace("__PIXEL_SERVICE_USER__", owner)
            .replace("__PIXEL_WORKSPACE__", str(pathlib.Path(home) / ".openclaw/workspace-pixel"))
            .replace("__PIXEL_PREVIEW_PORT__", "9437"))
pathlib.Path(target).write_text(text, encoding="utf-8")
PY
    chmod 0644 "$INSTALL_DIR/extensions/services/pixel-agent/host/extension_search.py" \
        "$INSTALL_DIR/extensions/services/pixel-agent/host/extension_manager.py"
    chmod 0644 "$INSTALL_DIR/extensions/services/pixel-agent/host/artifact_promoter.py"
    chmod 0644 "$INSTALL_DIR/extensions/services/pixel-agent/host/workspace_preview.py"
    chmod 0644 "$INSTALL_DIR/extensions/services/pixel-agent/host/system_observe.py"
    chmod 0644 "$INSTALL_DIR/extensions/services/pixel-agent/host/pixel-ops-broker-ods.conf"
    chmod 0600 "$INSTALL_DIR/data/pixel/extension-manager.service" \
        "$INSTALL_DIR/data/pixel/artifact-promoter.service" \
        "$INSTALL_DIR/data/pixel/workspace-preview.service"
    cat >"$source/.generated/pixel-ops-broker.service" <<UNIT
[Unit]
Description=Pixel Operations Broker - isolated fleet execution and workflow service
[Service]
User=pixel-ops-broker
Group=pixel-ops
ExecStart="$OPS_INSTALL/broker.py"
WorkingDirectory=$OPS_STATE
EnvironmentFile=$OPS_ENV
[Install]
WantedBy=multi-user.target
UNIT
    cat >"$source/.generated/ops-broker.env" <<ENV
PIXEL_OPS_POLICY_PATH='$OPS_POLICY'
PIXEL_OPS_STATE_DIR='$OPS_STATE'
PYTHONDONTWRITEBYTECODE='1'
ENV
    printf '%s\n' '#!/usr/bin/env python3' 'print("managed broker")' \
        >"$source/deploy/ops-broker/broker.py"
    chmod 0600 "$source/.generated/pixel-ops-broker.service" \
        "$source/.generated/ops-broker.env"
    chmod 0644 "$source/deploy/ops-broker/broker.py"

    mkdir -p "$OPS_POLICY_DIR" "$OPS_INSTALL" "$OPS_STATE" "$OPS_DROPIN_DIR"
    cp "$source/.generated/pixel-ops-broker.service" "$SYSTEMD_DIR/pixel-ops-broker.service"
    cp "$INSTALL_DIR/extensions/services/pixel-agent/host/pixel-ops-broker-ods.conf" "$OPS_DROPIN"
    cp "$source/.generated/ops-broker.env" "$OPS_ENV"
    cp "$source/deploy/ops-broker/broker.py" "$OPS_INSTALL/broker.py"
    cp "$INSTALL_DIR/extensions/services/pixel-agent/host/extension_search.py" \
        "$OPS_INSTALL/ods-extension-search.py"
    cp "$INSTALL_DIR/data/pixel/extension-catalog.json" \
        "$OPS_INSTALL/ods-extension-catalog.json"
    cp "$INSTALL_DIR/extensions/services/pixel-agent/host/extension_manager.py" \
        "$OPS_INSTALL/ods-extension-manager.py"
    cp "$INSTALL_DIR/extensions/services/pixel-agent/host/extension_manager.py" \
        "$LIBEXEC_DIR/ods-pixel-extension-manager.py"
    cp "$INSTALL_DIR/data/pixel/extension-manager.service" \
        "$SYSTEMD_DIR/pixel-extension-manager.service"
    cp "$INSTALL_DIR/extensions/services/pixel-agent/host/artifact_promoter.py" \
        "$LIBEXEC_DIR/ods-pixel-artifact-promoter.py"
    cp "$INSTALL_DIR/data/pixel/artifact-promoter.service" \
        "$SYSTEMD_DIR/pixel-artifact-promoter.service"
    cp "$INSTALL_DIR/extensions/services/pixel-agent/host/workspace_preview.py" \
        "$LIBEXEC_DIR/ods-pixel-workspace-preview.py"
    cp "$INSTALL_DIR/extensions/services/pixel-agent/host/system_observe.py" \
        "$LIBEXEC_DIR/ods-pixel-system-observe.py"
    cp "$INSTALL_DIR/data/pixel/workspace-preview.service" \
        "$SYSTEMD_DIR/pixel-workspace-preview.service"
    cp "$INSTALL_DIR/data/pixel/operations-policy.json" "$OPS_POLICY"
    chmod 0644 "$SYSTEMD_DIR/pixel-ops-broker.service"
    chmod 0755 "$OPS_DROPIN_DIR"
    chmod 0644 "$OPS_DROPIN"
    chmod 0640 "$OPS_ENV" "$OPS_POLICY"
    chmod 0755 "$OPS_INSTALL" "$OPS_INSTALL/broker.py" \
        "$OPS_INSTALL/ods-extension-search.py" "$OPS_INSTALL/ods-extension-manager.py" \
        "$LIBEXEC_DIR/ods-pixel-extension-manager.py" "$OPS_POLICY_DIR"
    chmod 0755 "$LIBEXEC_DIR/ods-pixel-artifact-promoter.py"
    chmod 0755 "$LIBEXEC_DIR/ods-pixel-workspace-preview.py"
    chmod 0755 "$LIBEXEC_DIR/ods-pixel-system-observe.py"
    chmod 0644 "$SYSTEMD_DIR/pixel-extension-manager.service" \
        "$SYSTEMD_DIR/pixel-artifact-promoter.service" \
        "$SYSTEMD_DIR/pixel-workspace-preview.service"
    chmod 0640 "$OPS_INSTALL/ods-extension-catalog.json"
    chmod 0750 "$OPS_STATE"
    mkdir -m 2770 "$OPS_STATE/requests" "$OPS_STATE/cancel"
    mkdir -m 2750 "$OPS_STATE/results" "$OPS_STATE/events" "$OPS_STATE/artifacts"
    mkdir -m 0700 "$OPS_STATE/private" "$OPS_STATE/authority"
    printf '%s\n' '{"status":"succeeded"}' >"$OPS_STATE/results/ops-test.json"
    chmod 0640 "$OPS_STATE/results/ops-test.json"
    mkdir -p "$PREVIEW_STATE"
    chmod 0700 "$PREVIEW_STATE"
    mkdir -m 0700 "$PREVIEW_STATE/site-0123456789abcdef01234567"
    printf '%s\n' '<!doctype html><title>fixture</title>' \
        >"$PREVIEW_STATE/site-0123456789abcdef01234567/index.html"
    chmod 0400 "$PREVIEW_STATE/site-0123456789abcdef01234567/index.html"

    printf 'pixel-ops-broker:x:%s:%s:Pixel Operations Broker:%s:/usr/sbin/nologin\n' \
        "$uid" "$gid" "$OPS_STATE" >"$OPS_PASSWD_STATE"
    printf 'pixel-ops:x:%s:\n' "$gid" >"$OPS_GROUP_STATE"
    (
        cd "$source"
        sha256sum \
            .generated/pixel-ops-broker.service \
            .generated/ops-broker.env \
            .generated/ops-policy.json \
            >"$release/deployment-inputs.sha256"
    )
    chmod 0600 "$release/deployment-inputs.sha256"
    printf '%s  %s\n' \
        "$(sha256sum "$release/deployment-inputs.sha256" | awk '{print $1}')" \
        ./deployment-inputs.sha256 >>"$release/install-manifest.sha256"
    python3 - \
        "$HOME_DIR/.config/ods/pixel-managed.json" \
        "$HOME_DIR/.local/share/pixel/runtime-attestation.json" \
        "$release/install-manifest.sha256" <<'PY'
import hashlib
import json
import pathlib
import sys

marker_path, attestation_path, manifest_path = map(pathlib.Path, sys.argv[1:])
manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
marker = json.loads(marker_path.read_text(encoding="utf-8"))
marker["install_manifest_sha256"] = manifest_sha256
marker_path.write_text(json.dumps(marker, sort_keys=True, separators=(",", ":")) + "\n")
attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
attestation["release"]["installManifestSha256"] = manifest_sha256
attestation_path.write_text(json.dumps(attestation, sort_keys=True, separators=(",", ":")) + "\n")
PY
    chmod 0600 \
        "$HOME_DIR/.config/ods/pixel-managed.json" \
        "$HOME_DIR/.local/share/pixel/runtime-attestation.json"
    python3 - "$HOME_DIR/.config/pixel-deployment/onboarding.json" \
        "$INSTALL_DIR/data/pixel/operations-policy.json" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text())
value["capabilityProfile"] = "engineering-operator"
value["operationsLimbEnabled"] = True
value["operationsPolicyFile"] = sys.argv[2]
path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
PY
    chmod 0600 "$HOME_DIR/.config/pixel-deployment/onboarding.json"
    python3 - "$HOME_DIR/.config/pixel-deployment/onboarding.json" \
        "$INSTALL_DIR/data/pixel/onboarding.json" <<'PY'
import json, pathlib, sys
source, target = map(pathlib.Path, sys.argv[1:])
target.write_text(json.dumps(json.loads(source.read_text()), indent=2, sort_keys=True) + "\n")
PY
    chmod 0600 "$INSTALL_DIR/data/pixel/onboarding.json"
    contract_sha256="$(python3 - "$INSTALL_DIR/data/pixel/onboarding.json" \
        "$INSTALL_DIR/data/pixel/operations-policy.json" \
        "$INSTALL_DIR/data/pixel/extension-catalog.json" \
        "$INSTALL_DIR/extensions/services/pixel-agent/host/extension_search.py" \
        "$INSTALL_DIR/extensions/services/pixel-agent/host/extension_manager.py" \
        "$INSTALL_DIR/data/pixel/extension-manager.service" \
        "$INSTALL_DIR/bin/ods-pixel-approve" \
        "$INSTALL_DIR/extensions/services/pixel-agent/host/artifact_promoter.py" \
        "$INSTALL_DIR/data/pixel/artifact-promoter.service" \
        "$INSTALL_DIR/extensions/services/pixel-agent/host/pixel-ops-broker-ods.conf" \
        "$INSTALL_DIR/extensions/services/pixel-agent/host/workspace_preview.py" \
        "$INSTALL_DIR/data/pixel/workspace-preview.service" \
        "$INSTALL_DIR/extensions/services/pixel-agent/host/system_observe.py" <<'PY'
import hashlib, pathlib, sys
digest = hashlib.sha256()
digest.update(b"ods-pixel-contract-v9\0")
for raw in sys.argv[1:]:
    payload = pathlib.Path(raw).read_bytes()
    digest.update(len(payload).to_bytes(8, "big"))
    digest.update(payload)
print(digest.hexdigest())
PY
)"
    python3 - "$HOME_DIR/.config/ods/pixel-managed.json" "$contract_sha256" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text())
value["contract_sha256"] = sys.argv[2]
path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
PY
    chmod 0600 "$HOME_DIR/.config/ods/pixel-managed.json"
    : >"$SYSTEMCTL_LOG"
    : >"$OPS_IDENTITY_LOG"
}

write_interrupted_ops_receipt_fixture() {
    write_ops_fixture
    local source_ref="d2a2b6be552126f294fb30ee5fb46872acf82c89"
    local source="$INSTALL_DIR/data/pixel/source-$source_ref"
    python3 - "$HOME_DIR/.config/ods/pixel-managed.json" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
original = json.loads(path.read_text(encoding="utf-8"))
path.write_text(json.dumps({
    "schema_version": 2,
    "manager": "ods",
    "state": "installing",
    "initial_active_state": "absent",
    "install_dir": original["install_dir"],
    "pixel_source_ref": original["pixel_source_ref"],
}, sort_keys=True, separators=(",", ":")) + "\n")
PY
    chmod 0600 "$HOME_DIR/.config/ods/pixel-managed.json"
    rm -f -- \
        "$HOME_DIR/.local/share/pixel/runtime-attestation.json" \
        "$source/.generated/pixel-ops-broker.service" \
        "$source/.generated/ops-broker.env" \
        "$source/.generated/ops-policy.json" \
        "$INSTALL_DIR/data/pixel/operations-policy.json" \
        "$INSTALL_DIR/data/pixel/extension-catalog.json" \
        "$INSTALL_DIR/data/pixel/extension-manager.service" \
        "$INSTALL_DIR/data/pixel/artifact-promoter.service" \
        "$INSTALL_DIR/data/pixel/workspace-preview.service" \
        "$INSTALL_DIR/bin/ods-pixel-approve" \
        "$INSTALL_DIR/extensions/services/pixel-agent/host/extension_search.py" \
        "$INSTALL_DIR/extensions/services/pixel-agent/host/artifact_promoter.py" \
        "$INSTALL_DIR/extensions/services/pixel-agent/host/system_observe.py" \
        "$INSTALL_DIR/extensions/services/pixel-agent/host/pixel-ops-broker-ods.conf" \
        "$OPS_DROPIN" \
        "$OPS_INSTALL/ods-extension-search.py" \
        "$OPS_INSTALL/ods-extension-catalog.json" \
        "$OPS_INSTALL/ods-extension-manager.py" \
        "$LIBEXEC_DIR/ods-pixel-extension-manager.py" \
        "$LIBEXEC_DIR/ods-pixel-artifact-promoter.py" \
        "$LIBEXEC_DIR/ods-pixel-workspace-preview.py" \
        "$LIBEXEC_DIR/ods-pixel-system-observe.py" \
        "$SYSTEMD_DIR/pixel-extension-manager.service" \
        "$SYSTEMD_DIR/pixel-artifact-promoter.service" \
        "$SYSTEMD_DIR/pixel-workspace-preview.service"
    rmdir -- "$OPS_DROPIN_DIR"
    : >"$SYSTEMCTL_LOG"
    : >"$OPS_IDENTITY_LOG"
    : >"$DOCKER_LOG"
}

write_fixture
rm -f -- "$SYSTEMD_DIR/openclaw-gateway.service" "$SYSTEMD_DIR/pixel-ingress.service" \
    "$ETC_DIR/pixel-agent.env" "$LIBEXEC_DIR/ods-pixel-ingress.mjs" \
    "$HOME_DIR/.config/pixel-agent/gateway.env"
cat >"$HOME_DIR/.config/ods/pixel-managed.json" <<JSON
{"schema_version":2,"manager":"ods","state":"installing","initial_active_state":"absent","install_dir":"$INSTALL_DIR","pixel_source_ref":"d2a2b6be552126f294fb30ee5fb46872acf82c89"}
JSON
cat >"$HOME_DIR/.openclaw/openclaw.json" <<'JSON'
{"gateway":{"http":{"endpoints":{"chatCompletions":{"enabled":true}}}}}
JSON
chmod 0600 "$HOME_DIR/.config/ods/pixel-managed.json" "$HOME_DIR/.openclaw/openclaw.json"
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
    [[ ! -e "$HOME_DIR/.config/ods/pixel-managed.json" \
        && ! -e "$HOME_DIR/.openclaw/openclaw.json" \
        && ! -e "$HOME_DIR/.config/pixel-deployment/onboarding.json" \
        && ! -s "$SYSTEMCTL_LOG" ]] \
        && pass "failed pre-apply Pixel bootstrap state is removed without claiming an ambient config" \
        || fail "failed pre-apply Pixel cleanup left managed state or touched system services"
else
    fail "failed pre-apply Pixel bootstrap state could not be cleaned"
fi

write_fixture
rm -f -- "$SYSTEMD_DIR/openclaw-gateway.service" "$SYSTEMD_DIR/pixel-ingress.service" \
    "$ETC_DIR/pixel-agent.env" "$LIBEXEC_DIR/ods-pixel-ingress.mjs" \
    "$HOME_DIR/.config/pixel-agent/gateway.env"
cat >"$HOME_DIR/.config/ods/pixel-managed.json" <<JSON
{"schema_version":2,"manager":"ods","state":"installing","initial_active_state":"absent","install_dir":"$INSTALL_DIR","pixel_source_ref":"d2a2b6be552126f294fb30ee5fb46872acf82c89"}
JSON
cat >"$HOME_DIR/.openclaw/openclaw.json" <<'JSON'
{"gateway":{"http":{"endpoints":{"chatCompletions":{"enabled":true}}}},"ambient":true}
JSON
printf '%s\n' '# inert install-tree source' \
    >"$INSTALL_DIR/extensions/services/pixel-agent/host/extension_manager.py"
printf '%s\n' '# inert install-tree source' \
    >"$INSTALL_DIR/extensions/services/pixel-agent/host/workspace_preview.py"
chmod 0644 \
    "$INSTALL_DIR/extensions/services/pixel-agent/host/extension_manager.py" \
    "$INSTALL_DIR/extensions/services/pixel-agent/host/workspace_preview.py"
chmod 0600 "$HOME_DIR/.config/ods/pixel-managed.json" "$HOME_DIR/.openclaw/openclaw.json"
pre_apply_config_sha="$(sha256sum "$HOME_DIR/.openclaw/openclaw.json" | awk '{print $1}')"
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
    retired_config="$(find "$HOME_DIR/.openclaw/retired-ods-configs" -mindepth 2 -maxdepth 2 \
        -type f -name openclaw.json -print -quit 2>/dev/null)"
    [[ ! -e "$HOME_DIR/.config/ods/pixel-managed.json" \
        && ! -e "$HOME_DIR/.openclaw/openclaw.json" \
        && -n "$retired_config" && -f "$retired_config" && ! -L "$retired_config" \
        && "$(sha256sum "$retired_config" | awk '{print $1}')" == "$pre_apply_config_sha" \
        && "$(stat -c '%a' "$HOME_DIR/.openclaw/retired-ods-configs")" == 700 \
        && ! -s "$SYSTEMCTL_LOG" ]] \
        && pass "modified pre-apply OpenClaw config is privately retired while inert ODS state is removed" \
        || fail "modified pre-apply cleanup did not preserve unbound OpenClaw config exactly"
else
    fail "modified pre-apply OpenClaw config blocked inert ODS state cleanup"
fi

write_fixture
printf '%s\n' '# source without generated owner unit' \
    >"$INSTALL_DIR/extensions/services/pixel-agent/host/extension_manager.py"
chmod 0644 "$INSTALL_DIR/extensions/services/pixel-agent/host/extension_manager.py"
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
    fail "ready Pixel marker accepted an incomplete extension-manager contract"
else
    [[ -e "$HOME_DIR/.config/ods/pixel-managed.json" \
        && -e "$HOME_DIR/.openclaw/openclaw.json" \
        && ! -s "$SYSTEMCTL_LOG" ]] \
        && pass "source-only extension contract is allowed only for an inactive installing attempt" \
        || fail "incomplete ready extension contract caused mutation"
fi

write_fixture
rm -f -- "$SYSTEMD_DIR/openclaw-gateway.service" "$SYSTEMD_DIR/pixel-ingress.service" \
    "$ETC_DIR/pixel-agent.env" "$LIBEXEC_DIR/ods-pixel-ingress.mjs" \
    "$HOME_DIR/.config/pixel-agent/gateway.env"
cat >"$HOME_DIR/.config/ods/pixel-managed.json" <<JSON
{"schema_version":2,"manager":"ods","state":"installing","initial_active_state":"absent","install_dir":"$INSTALL_DIR","pixel_source_ref":"d2a2b6be552126f294fb30ee5fb46872acf82c89"}
JSON
cat >"$HOME_DIR/.openclaw/openclaw.json" <<'JSON'
{"gateway":{"http":{"endpoints":{"chatCompletions":{"enabled":true}}}},"ambient":true}
JSON
chmod 0600 "$HOME_DIR/.config/ods/pixel-managed.json" "$HOME_DIR/.openclaw/openclaw.json"
mkdir -m 0700 "$TEST_ROOT/foreign-config-archive"
ln -s "$TEST_ROOT/foreign-config-archive" "$HOME_DIR/.openclaw/retired-ods-configs"
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
    fail "symlinked OpenClaw config recovery root was accepted"
else
    [[ -e "$HOME_DIR/.config/ods/pixel-managed.json" \
        && -e "$HOME_DIR/.openclaw/openclaw.json" \
        && -L "$HOME_DIR/.openclaw/retired-ods-configs" \
        && ! -s "$SYSTEMCTL_LOG" ]] \
        && pass "unsafe OpenClaw config recovery root fails before mutation" \
        || fail "unsafe OpenClaw config recovery root caused mutation"
fi

write_fixture
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
    [[ ! -e "$HOME_DIR/.config/ods/pixel-managed.json" \
        && ! -e "$HOME_DIR/.openclaw/openclaw.json" \
        && ! -e "$SYSTEMD_DIR/openclaw-gateway.service" \
        && ! -e "$SYSTEMD_DIR/pixel-ingress.service" \
        && ! -e "$ETC_DIR/pixel-agent.env" \
        && ! -e "$LIBEXEC_DIR/ods-pixel-ingress.mjs" \
        && -e "$HOME_DIR/.openclaw/openclaw.json.bak" ]] \
        && pass "exact ODS-managed Pixel deployment is removed without deleting backups" \
        || fail "managed cleanup left targets or removed an unowned backup"
    if [[ "$(sed -n '1p' "$SYSTEMCTL_LOG")" == "disable --now pixel-ingress.service" \
        && "$(sed -n '2p' "$SYSTEMCTL_LOG")" == "disable --now openclaw-gateway.service" ]]; then
        pass "managed cleanup stops ingress before the gateway"
    else
        fail "managed cleanup did not enforce the exact Pixel service shutdown order"
    fi
else
    fail "valid managed Pixel cleanup failed"
fi

write_fixture
rm -f -- "$SYSTEMD_DIR/pixel-ingress.service" "$ETC_DIR/pixel-agent.env" \
    "$LIBEXEC_DIR/ods-pixel-ingress.mjs"
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
    if [[ "$(sed -n '1p' "$SYSTEMCTL_LOG")" == "disable --now openclaw-gateway.service" \
        && "$(grep -c '^disable --now pixel-ingress.service$' "$SYSTEMCTL_LOG" || true)" == 0 \
        && ! -e "$HOME_DIR/.config/ods/pixel-managed.json" \
        && ! -e "$SYSTEMD_DIR/openclaw-gateway.service" ]]; then
        pass "interrupted first install with no ingress unit is safely removed"
    else
        fail "gateway-only interrupted install cleanup was incomplete or touched absent ingress"
    fi
else
    fail "interrupted first install with no ingress unit could not be cleaned"
fi

write_active_fixture
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
    pixel_install="$HOME_DIR/.local/share/pixel"
    shopt -s nullglob
    retired_release_matches=("$pixel_install"/retired-ods-releases/4.3.14-*.????????/release)
    shopt -u nullglob
    [[ ! -e "$pixel_install/current" && ! -L "$pixel_install/current" \
        && ! -e "$pixel_install/runtime-attestation.json" \
        && ! -e "$pixel_install/.ods-uninstall-current" \
        && ! -e "$pixel_install/.ods-uninstall-runtime-attestation" \
        && ! -e "$pixel_install/releases/4.3.14" \
        && ${#retired_release_matches[@]} -eq 1 \
        && -f "${retired_release_matches[0]}/release-identity.json" \
        && "$(stat -c '%a' "$pixel_install/retired-ods-releases")" == 700 \
        && -f "$pixel_install/.deployment.lock" \
        && ! -e "$HOME_DIR/.config/ods/pixel-managed.json" \
        && ! -e "$HOME_DIR/.openclaw/.ods-exec-control" \
        && ! -e "$DOCKER_STATE" ]] \
        && pass "fully bound ODS Pixel is deactivated while its exact release is privately retired" \
        || fail "fully bound ODS Pixel active-state cleanup was incomplete or over-broad"
    grep -Fq 'rm -f' "$DOCKER_LOG" \
        && grep -Fq 'image rm -- openclaw-sandbox:test' "$DOCKER_LOG" \
        && pass "managed sandbox containers and exact live tag are retired" \
        || fail "managed Pixel Docker state was not retired exactly"
else
    fail "fully bound ODS Pixel active state was not safely deactivated"
fi

write_ops_fixture
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
    if [[ ! -e "$SYSTEMD_DIR/pixel-ops-broker.service" \
        && ! -e "$OPS_DROPIN_DIR" \
        && ! -e "$SYSTEMD_DIR/pixel-extension-manager.service" \
        && ! -e "$LIBEXEC_DIR/ods-pixel-extension-manager.py" \
        && ! -e "$SYSTEMD_DIR/pixel-artifact-promoter.service" \
        && ! -e "$LIBEXEC_DIR/ods-pixel-artifact-promoter.py" \
        && ! -e "$SYSTEMD_DIR/pixel-workspace-preview.service" \
        && ! -e "$LIBEXEC_DIR/ods-pixel-workspace-preview.py" \
        && ! -e "$LIBEXEC_DIR/ods-pixel-system-observe.py" \
        && ! -e "$PREVIEW_STATE" \
        && ! -e "$OPS_ENV" && ! -e "$OPS_POLICY_DIR" \
        && ! -e "$OPS_INSTALL" && ! -e "$OPS_STATE" \
        && ! -e "$INSTALL_DIR/data/pixel/operations-policy.json" \
        && ! -e "$INSTALL_DIR/data/pixel/extension-catalog.json" \
        && ! -e "$INSTALL_DIR/data/pixel/extension-manager.service" \
        && ! -e "$INSTALL_DIR/data/pixel/artifact-promoter.service" \
        && ! -e "$INSTALL_DIR/data/pixel/workspace-preview.service" \
        && ! -e "$OPS_PASSWD_STATE" && ! -e "$OPS_GROUP_STATE" \
        && "$(sed -n '1p' "$SYSTEMCTL_LOG")" == "disable --now pixel-ingress.service" \
        && "$(sed -n '2p' "$SYSTEMCTL_LOG")" == "disable --now pixel-extension-manager.service" \
        && "$(sed -n '3p' "$SYSTEMCTL_LOG")" == "disable --now pixel-artifact-promoter.service" \
        && "$(sed -n '4p' "$SYSTEMCTL_LOG")" == "disable --now pixel-workspace-preview.service" \
        && "$(sed -n '5p' "$SYSTEMCTL_LOG")" == "disable --now openclaw-gateway.service" \
        && "$(sed -n '6p' "$SYSTEMCTL_LOG")" == "disable --now pixel-ops-broker.service" \
        && "$(sed -n '1p' "$OPS_IDENTITY_LOG")" == "userdel pixel-ops-broker" \
        && "$(sed -n '2p' "$OPS_IDENTITY_LOG")" == "groupdel pixel-ops" ]]; then
        pass "verified Operations Broker service, authority state, and identities are removed in bounded order"
    else
        fail "Operations Broker cleanup left privileged state or removed it out of order"
    fi
else
    fail "verified Operations Broker deployment could not be removed"
fi

write_interrupted_ops_receipt_fixture
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
    if [[ ! -e "$SYSTEMD_DIR/pixel-ops-broker.service" \
        && ! -e "$OPS_ENV" && ! -e "$OPS_POLICY_DIR" \
        && ! -e "$OPS_INSTALL" && ! -e "$OPS_STATE" \
        && ! -e "$OPS_PASSWD_STATE" && ! -e "$OPS_GROUP_STATE" \
        && ! -e "$HOME_DIR/.config/ods/pixel-managed.json" \
        && ! -e "$HOME_DIR/.local/share/pixel/current" \
        && -e "$DOCKER_STATE" ]]; then
        pass "interrupted Operations cleanup uses the release-bound deployment receipt without removing unbound images"
    else
        fail "interrupted receipt-bound Operations cleanup left partial or over-broad state"
    fi
else
    fail "interrupted Operations cleanup rejected its exact release-bound deployment receipt"
fi

for receipt_failure in missing receipt-drift duplicate unit-drift ready-source-missing; do
    if [[ "$receipt_failure" == ready-source-missing ]]; then
        write_ops_fixture
        source="$INSTALL_DIR/data/pixel/source-d2a2b6be552126f294fb30ee5fb46872acf82c89"
        rm -f -- "$source/.generated/pixel-ops-broker.service"
    else
        write_interrupted_ops_receipt_fixture
        release="$HOME_DIR/.local/share/pixel/releases/4.3.14"
        receipt="$release/deployment-inputs.sha256"
        case "$receipt_failure" in
            missing)
                rm -f -- "$receipt"
                ;;
            receipt-drift)
                printf '%s\n' '# drift' >>"$receipt"
                ;;
            duplicate)
                duplicate_line="$(grep -F '  .generated/pixel-ops-broker.service' "$receipt")"
                printf '%s\n' "$duplicate_line" >>"$receipt"
                receipt_sha256="$(sha256sum "$receipt" | awk '{print $1}')"
                python3 - "$release/install-manifest.sha256" "$receipt_sha256" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
digest = sys.argv[2]
lines = path.read_text(encoding="ascii").splitlines()
matches = [index for index, line in enumerate(lines) if line.endswith("  ./deployment-inputs.sha256")]
if len(matches) != 1:
    raise SystemExit("fixture release manifest lacks one deployment-input receipt")
lines[matches[0]] = f"{digest}  ./deployment-inputs.sha256"
path.write_text("\n".join(lines) + "\n", encoding="ascii")
PY
                ;;
            unit-drift)
                printf '%s\n' '# drift' >>"$SYSTEMD_DIR/pixel-ops-broker.service"
                ;;
        esac
    fi
    if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
        fail "interrupted Operations receipt failure $receipt_failure was accepted"
    else
        [[ -L "$HOME_DIR/.local/share/pixel/current" \
            && -e "$HOME_DIR/.config/ods/pixel-managed.json" \
            && -e "$SYSTEMD_DIR/pixel-ops-broker.service" \
            && -e "$OPS_STATE" && -e "$OPS_PASSWD_STATE" \
            && ! -s "$SYSTEMCTL_LOG" && ! -s "$DOCKER_LOG" ]] \
            && pass "interrupted Operations receipt failure $receipt_failure fails closed before mutation" \
            || fail "interrupted Operations receipt failure $receipt_failure caused partial mutation"
    fi
done

write_ops_fixture
python3 - "$HOME_DIR/.config/ods/pixel-managed.json" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text())
value["state"] = "installing"
value["requested_source_ref"] = value["pixel_source_ref"]
value["requested_contract_sha256"] = value["contract_sha256"]
value["contract_sha256"] = "f" * 64
path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
PY
chmod 0600 "$HOME_DIR/.config/ods/pixel-managed.json"
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
    [[ ! -e "$HOME_DIR/.config/ods/pixel-managed.json" \
        && ! -e "$OPS_INSTALL" && ! -e "$LIBEXEC_DIR/ods-pixel-system-observe.py" ]] \
        && pass "same-source installing transition accepts its complete replacement v9 contract after full root-byte validation" \
        || fail "same-source installing transition cleanup was incomplete"
else
    fail "same-source installing transition retained a stale prior contract and could not be cleaned"
fi

for partial_unit in pixel-extension-manager.service pixel-artifact-promoter.service pixel-workspace-preview.service; do
    write_ops_fixture
    python3 - "$HOME_DIR/.config/ods/pixel-managed.json" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text())
value["state"] = "installing"
path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
PY
    chmod 0600 "$HOME_DIR/.config/ods/pixel-managed.json"
    rm -f -- "$SYSTEMD_DIR/$partial_unit"
    if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
        [[ ! -e "$HOME_DIR/.config/ods/pixel-managed.json" && ! -e "$OPS_INSTALL" ]] \
            && pass "interrupted installing Pixel $partial_unit program-only state is resumably removed" \
            || fail "interrupted installing Pixel $partial_unit cleanup was incomplete"
    else
        fail "interrupted installing Pixel $partial_unit program-only state was not resumable"
    fi
done

write_ops_fixture
python3 - "$HOME_DIR/.config/ods/pixel-managed.json" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text())
value["state"] = "installing"
value["requested_source_ref"] = value["pixel_source_ref"]
value["contract_sha256"] = "f" * 64
path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
PY
chmod 0600 "$HOME_DIR/.config/ods/pixel-managed.json"
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
    fail "same-source installing transition accepted an unbound replacement contract"
else
    [[ -e "$HOME_DIR/.config/ods/pixel-managed.json" \
        && -e "$OPS_INSTALL" && -e "$LIBEXEC_DIR/ods-pixel-system-observe.py" \
        && ! -s "$SYSTEMCTL_LOG" && ! -s "$DOCKER_LOG" ]] \
        && pass "same-source installing transition requires its requested contract binding" \
        || fail "unbound same-source installing transition caused partial mutation"
fi

write_ops_fixture
python3 - "$HOME_DIR/.config/ods/pixel-managed.json" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text())
value["state"] = "installing"
value["requested_source_ref"] = "e" * 40
value["requested_contract_sha256"] = value["contract_sha256"]
value["contract_sha256"] = "f" * 64
path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
PY
chmod 0600 "$HOME_DIR/.config/ods/pixel-managed.json"
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
    fail "cross-source installing transition accepted an unbound replacement contract"
else
    [[ -e "$HOME_DIR/.config/ods/pixel-managed.json" \
        && -e "$OPS_INSTALL" && -e "$LIBEXEC_DIR/ods-pixel-system-observe.py" \
        && ! -s "$SYSTEMCTL_LOG" && ! -s "$DOCKER_LOG" ]] \
        && pass "cross-source installing transition remains bound to its prior verified contract" \
        || fail "cross-source installing transition caused partial mutation"
fi

write_ops_fixture
rm -f -- "$LIBEXEC_DIR/ods-pixel-system-observe.py"
legacy_contract_sha256="$(python3 - \
    "$INSTALL_DIR/data/pixel/onboarding.json" \
    "$INSTALL_DIR/data/pixel/operations-policy.json" \
    "$INSTALL_DIR/data/pixel/extension-catalog.json" \
    "$INSTALL_DIR/extensions/services/pixel-agent/host/extension_search.py" \
    "$INSTALL_DIR/extensions/services/pixel-agent/host/extension_manager.py" \
    "$INSTALL_DIR/data/pixel/extension-manager.service" \
    "$INSTALL_DIR/bin/ods-pixel-approve" \
    "$INSTALL_DIR/extensions/services/pixel-agent/host/artifact_promoter.py" \
    "$INSTALL_DIR/data/pixel/artifact-promoter.service" \
    "$INSTALL_DIR/extensions/services/pixel-agent/host/pixel-ops-broker-ods.conf" \
    "$INSTALL_DIR/extensions/services/pixel-agent/host/workspace_preview.py" \
    "$INSTALL_DIR/data/pixel/workspace-preview.service" <<'PY'
import hashlib, pathlib, sys
digest = hashlib.sha256()
digest.update(b"ods-pixel-contract-v8\0")
for raw in sys.argv[1:]:
    payload = pathlib.Path(raw).read_bytes()
    digest.update(len(payload).to_bytes(8, "big"))
    digest.update(payload)
print(digest.hexdigest())
PY
)"
python3 - "$HOME_DIR/.config/ods/pixel-managed.json" "$legacy_contract_sha256" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text())
value["contract_sha256"] = sys.argv[2]
path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
PY
chmod 0600 "$HOME_DIR/.config/ods/pixel-managed.json"
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
    [[ ! -e "$HOME_DIR/.config/ods/pixel-managed.json" && ! -e "$OPS_INSTALL" ]] \
        && pass "legacy v8 Operations deployment remains removable after the observer source ships" \
        || fail "legacy v8 Operations cleanup was incomplete"
else
    fail "legacy v8 Operations deployment was blocked by the new observer source"
fi

write_ops_fixture
chmod 0664 "$INSTALL_DIR/data/pixel/source-d2a2b6be552126f294fb30ee5fb46872acf82c89/deploy/ops-broker/broker.py"
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
    [[ ! -e "$OPS_INSTALL" && ! -e "$OPS_STATE" \
        && ! -e "$HOME_DIR/.config/ods/pixel-managed.json" ]] \
        && pass "legacy owner-primary-group writable Pixel broker source is removed only after exact root-byte verification" \
        || fail "legacy owner-primary-group writable Pixel broker cleanup was incomplete"
else
    fail "legacy owner-primary-group writable Pixel broker source could not be safely removed"
fi

for profile_mode in 0600 0640 0644; do
    write_ops_fixture
    for profile in .bash_logout .bashrc .profile; do
        printf '%s\n' fixture >"$OPS_STATE/$profile"
        chmod "$profile_mode" "$OPS_STATE/$profile"
    done
    if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
        [[ ! -e "$OPS_STATE" && ! -e "$HOME_DIR/.config/ods/pixel-managed.json" ]] \
            && pass "bounded mode-$profile_mode system-account profiles are retired with the managed Operations state" \
            || fail "bounded mode-$profile_mode Operations profiles left partial managed state"
    else
        fail "bounded mode-$profile_mode Operations system-account profiles blocked safe cleanup"
    fi
done

for drift_target in program broker-source-mode public-state-file onboarding-source onboarding-live extension-program extension-catalog extension-manager-client \
    extension-manager-program extension-manager-unit extension-manager-owner-unit approval-helper \
    artifact-promoter-program artifact-promoter-unit artifact-promoter-owner-unit \
    workspace-preview-program workspace-preview-unit workspace-preview-owner-unit workspace-preview-state \
    system-observer-program system-observer-source \
    unit dropin dropin-source environment policy; do
    write_ops_fixture
    case "$drift_target" in
        program) printf '%s\n' '# drift' >>"$OPS_INSTALL/broker.py" ;;
        broker-source-mode) chmod 0666 "$INSTALL_DIR/data/pixel/source-d2a2b6be552126f294fb30ee5fb46872acf82c89/deploy/ops-broker/broker.py" ;;
        public-state-file) printf '%s\n' unexpected >"$OPS_STATE/notes.txt"; chmod 0644 "$OPS_STATE/notes.txt" ;;
        onboarding-source) printf '%s\n' ' ' >>"$INSTALL_DIR/data/pixel/onboarding.json" ;;
        onboarding-live) python3 - "$HOME_DIR/.config/pixel-deployment/onboarding.json" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text())
value["capabilityProfile"] = "drifted"
path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
PY
            ;;
        extension-program) printf '%s\n' '# drift' >>"$OPS_INSTALL/ods-extension-search.py" ;;
        extension-catalog) printf '%s\n' ' ' >>"$OPS_INSTALL/ods-extension-catalog.json" ;;
        extension-manager-client) printf '%s\n' '# drift' >>"$OPS_INSTALL/ods-extension-manager.py" ;;
        extension-manager-program) printf '%s\n' '# drift' >>"$LIBEXEC_DIR/ods-pixel-extension-manager.py" ;;
        extension-manager-unit) printf '%s\n' '# drift' >>"$SYSTEMD_DIR/pixel-extension-manager.service" ;;
        extension-manager-owner-unit) printf '%s\n' '# drift' >>"$INSTALL_DIR/data/pixel/extension-manager.service" ;;
        approval-helper) printf '%s\n' '# drift' >>"$INSTALL_DIR/bin/ods-pixel-approve" ;;
        artifact-promoter-program) printf '%s\n' '# drift' >>"$LIBEXEC_DIR/ods-pixel-artifact-promoter.py" ;;
        artifact-promoter-unit) printf '%s\n' '# drift' >>"$SYSTEMD_DIR/pixel-artifact-promoter.service" ;;
        artifact-promoter-owner-unit) printf '%s\n' '# drift' >>"$INSTALL_DIR/data/pixel/artifact-promoter.service" ;;
        workspace-preview-program) printf '%s\n' '# drift' >>"$LIBEXEC_DIR/ods-pixel-workspace-preview.py" ;;
        workspace-preview-unit) printf '%s\n' '# drift' >>"$SYSTEMD_DIR/pixel-workspace-preview.service" ;;
        workspace-preview-owner-unit) printf '%s\n' '# drift' >>"$INSTALL_DIR/data/pixel/workspace-preview.service" ;;
        workspace-preview-state) chmod 0600 "$PREVIEW_STATE/site-0123456789abcdef01234567/index.html" ;;
        system-observer-program) printf '%s\n' '# drift' >>"$LIBEXEC_DIR/ods-pixel-system-observe.py" ;;
        system-observer-source) printf '%s\n' '# drift' >>"$INSTALL_DIR/extensions/services/pixel-agent/host/system_observe.py" ;;
        unit) printf '%s\n' '# drift' >>"$SYSTEMD_DIR/pixel-ops-broker.service" ;;
        dropin) printf '%s\n' '# drift' >>"$OPS_DROPIN" ;;
        dropin-source) printf '%s\n' '# drift' >>"$INSTALL_DIR/extensions/services/pixel-agent/host/pixel-ops-broker-ods.conf" ;;
        environment) printf '%s\n' 'UNEXPECTED=1' >>"$OPS_ENV" ;;
        policy) printf '%s\n' ' ' >>"$OPS_POLICY" ;;
    esac
    if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
        fail "drifted Operations Broker $drift_target was accepted"
    else
        [[ -L "$HOME_DIR/.local/share/pixel/current" \
            && -e "$SYSTEMD_DIR/pixel-ops-broker.service" \
            && -e "$OPS_STATE" && -e "$OPS_PASSWD_STATE" \
            && ! -s "$SYSTEMCTL_LOG" && ! -s "$DOCKER_LOG" ]] \
            && pass "Operations Broker $drift_target drift fails before service or Docker mutation" \
            || fail "Operations Broker $drift_target drift caused partial cleanup"
    fi
done

for unsafe_state in symlink hardlink; do
    write_ops_fixture
    if [[ "$unsafe_state" == symlink ]]; then
        ln -s /etc/passwd "$OPS_STATE/requests/escaped"
    else
        ln "$OPS_STATE/results/ops-test.json" "$OPS_STATE/results/ops-test-linked.json"
    fi
    if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
        fail "unsafe Operations Broker $unsafe_state state was accepted"
    else
        [[ -L "$HOME_DIR/.local/share/pixel/current" \
            && -e "$OPS_STATE" && -e "$SYSTEMD_DIR/pixel-ops-broker.service" \
            && ! -s "$SYSTEMCTL_LOG" && ! -s "$DOCKER_LOG" ]] \
            && pass "Operations Broker $unsafe_state state fails before mutation" \
            || fail "Operations Broker $unsafe_state refusal caused partial cleanup"
    fi
done

write_ops_fixture
rm -f -- "$OPS_ENV"
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
    fail "partial ready Operations Broker deployment was accepted"
else
    [[ -L "$HOME_DIR/.local/share/pixel/current" \
        && -e "$SYSTEMD_DIR/pixel-ops-broker.service" && -e "$OPS_STATE" \
        && ! -s "$SYSTEMCTL_LOG" && ! -s "$DOCKER_LOG" ]] \
        && pass "partial ready Operations Broker deployment fails closed before mutation" \
        || fail "partial ready Operations Broker refusal caused mutation"
fi

write_ops_fixture
rm -f -- "$SYSTEMD_DIR/pixel-ops-broker.service" "$OPS_ENV" "$OPS_POLICY" \
    "$OPS_DROPIN" \
    "$OPS_INSTALL/broker.py" "$OPS_INSTALL/ods-extension-search.py" \
    "$OPS_INSTALL/ods-extension-catalog.json" "$OPS_INSTALL/ods-extension-manager.py" \
    "$OPS_PASSWD_STATE" "$OPS_GROUP_STATE"
rmdir -- "$OPS_DROPIN_DIR" "$OPS_POLICY_DIR" "$OPS_INSTALL"
rm -rf -- "$OPS_STATE"
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
    fail "owner-policy-only ready Operations Broker deployment was accepted"
else
    [[ -L "$HOME_DIR/.local/share/pixel/current" \
        && -e "$INSTALL_DIR/data/pixel/operations-policy.json" \
        && ! -s "$SYSTEMCTL_LOG" && ! -s "$DOCKER_LOG" ]] \
        && pass "owner-policy-only ready Operations deployment fails closed before mutation" \
        || fail "owner-policy-only ready Operations refusal caused mutation"
fi

write_ops_fixture
python3 - "$HOME_DIR/.config/ods/pixel-managed.json" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text())
value["state"] = "installing"
path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
PY
chmod 0600 "$HOME_DIR/.config/ods/pixel-managed.json"
rm -f -- "$SYSTEMD_DIR/pixel-ops-broker.service" "$OPS_ENV" "$OPS_POLICY" \
    "$OPS_DROPIN" \
    "$OPS_INSTALL/broker.py" "$OPS_INSTALL/ods-extension-search.py" \
    "$OPS_INSTALL/ods-extension-catalog.json" "$OPS_INSTALL/ods-extension-manager.py" \
    "$OPS_PASSWD_STATE"
rmdir -- "$OPS_DROPIN_DIR" "$OPS_POLICY_DIR" "$OPS_INSTALL"
rm -rf -- "$OPS_STATE"
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
    [[ ! -e "$OPS_GROUP_STATE" && ! -e "$INSTALL_DIR/data/pixel/operations-policy.json" \
        && ! -e "$INSTALL_DIR/data/pixel/extension-catalog.json" ]] \
        && pass "interrupted installing Operations group-only state is resumably removed" \
        || fail "installing Operations partial cleanup left managed state"
else
    fail "interrupted installing Operations group-only state was not resumable"
fi

write_active_fixture
printf '%s\n' 'unexpected' >"$HOME_DIR/.openclaw/.ods-exec-control/unowned-artifact"
chmod 0600 "$HOME_DIR/.openclaw/.ods-exec-control/unowned-artifact"
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
    fail "unexpected Pixel execution-control artifact was accepted"
else
    [[ -L "$HOME_DIR/.local/share/pixel/current" \
        && -e "$HOME_DIR/.config/ods/pixel-managed.json" \
        && -e "$HOME_DIR/.openclaw/.ods-exec-control/unowned-artifact" \
        && ! -s "$SYSTEMCTL_LOG" && ! -s "$DOCKER_LOG" ]] \
        && pass "unsafe Pixel execution-control drift fails before service or Docker mutation" \
        || fail "execution-control cleanup refusal caused partial mutation"
fi

write_active_fixture
chmod 0500 "$HOME_DIR/.openclaw/.ods-exec-control"
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
    fail "non-writable Pixel execution-control root was accepted"
else
    [[ -L "$HOME_DIR/.local/share/pixel/current" \
        && -e "$HOME_DIR/.config/ods/pixel-managed.json" \
        && -e "$HOME_DIR/.openclaw/.ods-exec-control/cancellable-exec.sh" \
        && ! -s "$SYSTEMCTL_LOG" && ! -s "$DOCKER_LOG" ]] \
        && pass "non-writable execution-control root fails before mutation" \
        || fail "execution-control mode refusal caused partial mutation"
fi
chmod 0700 "$HOME_DIR/.openclaw/.ods-exec-control"

write_active_fixture
printf '%s\n' '{"tampered":true}' >"$HOME_DIR/.local/share/pixel/runtime-attestation.json"
chmod 0600 "$HOME_DIR/.local/share/pixel/runtime-attestation.json"
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
    fail "tampered active Pixel attestation was accepted"
else
    [[ -L "$HOME_DIR/.local/share/pixel/current" \
        && -e "$HOME_DIR/.config/ods/pixel-managed.json" \
        && ! -s "$SYSTEMCTL_LOG" && ! -s "$DOCKER_LOG" ]] \
        && pass "tampered active Pixel attestation fails before service or Docker mutation" \
        || fail "tampered active Pixel attestation caused partial cleanup"
fi

write_active_fixture
mv -T "$HOME_DIR/.local/share/pixel/runtime-attestation.json" \
    "$HOME_DIR/.local/share/pixel/.ods-uninstall-runtime-attestation"
mv -T "$HOME_DIR/.local/share/pixel/current" \
    "$HOME_DIR/.local/share/pixel/.ods-uninstall-current"
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
    [[ ! -e "$HOME_DIR/.local/share/pixel/.ods-uninstall-current" \
        && ! -e "$HOME_DIR/.local/share/pixel/.ods-uninstall-runtime-attestation" \
        && ! -e "$DOCKER_STATE" ]] \
        && pass "interrupted staged Pixel deactivation resumes to a clean inactive state" \
        || fail "staged Pixel deactivation did not reconcile exactly"
else
    fail "staged Pixel deactivation could not resume safely"
fi

for interrupted_step in attestation link; do
    write_active_fixture
    if [[ "$interrupted_step" == attestation ]]; then
        mv -T "$HOME_DIR/.local/share/pixel/runtime-attestation.json" \
            "$HOME_DIR/.local/share/pixel/.ods-uninstall-runtime-attestation"
    else
        mv -T "$HOME_DIR/.local/share/pixel/current" \
            "$HOME_DIR/.local/share/pixel/.ods-uninstall-current"
    fi
    if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR" \
        && [[ ! -e "$HOME_DIR/.local/share/pixel/current" \
            && ! -e "$HOME_DIR/.local/share/pixel/runtime-attestation.json" \
            && ! -e "$HOME_DIR/.local/share/pixel/.ods-uninstall-current" \
            && ! -e "$HOME_DIR/.local/share/pixel/.ods-uninstall-runtime-attestation" \
            && ! -e "$DOCKER_STATE" ]]; then
        pass "deactivation resumes after only the $interrupted_step move completed"
    else
        fail "deactivation could not resume after the $interrupted_step move"
    fi
done

write_active_fixture
python3 - "$HOME_DIR/.config/ods/pixel-managed.json" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text())
value = {
    "schema_version": 2,
    "manager": "ods",
    "state": "installing",
    "initial_active_state": "absent",
    "install_dir": value["install_dir"],
    "pixel_source_ref": value["pixel_source_ref"],
}
path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
PY
chmod 0600 "$HOME_DIR/.config/ods/pixel-managed.json"
rm -f -- "$HOME_DIR/.local/share/pixel/runtime-attestation.json"
printf '%s\n' '# source shipped before generated owner unit' \
    >"$INSTALL_DIR/extensions/services/pixel-agent/host/extension_manager.py"
printf '%s\n' '# source shipped before generated owner unit' \
    >"$INSTALL_DIR/extensions/services/pixel-agent/host/workspace_preview.py"
chmod 0644 \
    "$INSTALL_DIR/extensions/services/pixel-agent/host/extension_manager.py" \
    "$INSTALL_DIR/extensions/services/pixel-agent/host/workspace_preview.py"
unbound_config_sha="$(sha256sum "$HOME_DIR/.openclaw/openclaw.json" | awk '{print $1}')"
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR" \
    && [[ ! -e "$HOME_DIR/.local/share/pixel/current" \
        && ! -e "$HOME_DIR/.local/share/pixel/runtime-attestation.json" \
        && ! -e "$HOME_DIR/.local/share/pixel/.ods-uninstall-current" \
        && ! -e "$HOME_DIR/.local/share/pixel/.ods-uninstall-runtime-attestation" \
        && ! -e "$HOME_DIR/.local/share/pixel/releases/4.3.14" \
        && ! -e "$HOME_DIR/.config/ods/pixel-managed.json" \
        && -e "$DOCKER_STATE" ]]; then
    unbound_config="$(find "$HOME_DIR/.openclaw/retired-ods-configs" -mindepth 2 -maxdepth 2 \
        -type f -name openclaw.json -print -quit 2>/dev/null)"
    if [[ -n "$unbound_config" \
        && "$(sha256sum "$unbound_config" | awk '{print $1}')" == "$unbound_config_sha" \
        && "$(grep -c '^image rm -- ' "$DOCKER_LOG" || true)" == 0 ]]; then
        pass "minimal interrupted Pixel state with source-only lifecycle files is retired while unbound state is preserved"
    else
        fail "minimal interrupted Pixel cleanup did not preserve unbound state exactly"
    fi
else
    fail "minimal interrupted Pixel active link without attestation was not resumable"
fi

write_active_fixture
python3 - "$HOME_DIR/.config/ods/pixel-managed.json" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text())
value = {
    "schema_version": 2,
    "manager": "ods",
    "state": "installing",
    "initial_active_state": "absent",
    "install_dir": value["install_dir"],
    "pixel_source_ref": value["pixel_source_ref"],
    "active_release_version": "9.9.9",
}
path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
PY
chmod 0600 "$HOME_DIR/.config/ods/pixel-managed.json"
rm -f -- "$HOME_DIR/.local/share/pixel/runtime-attestation.json"
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
    fail "partially enriched unattested Pixel marker was accepted"
else
    [[ -L "$HOME_DIR/.local/share/pixel/current" \
        && -e "$HOME_DIR/.config/ods/pixel-managed.json" \
        && -e "$DOCKER_STATE" && ! -s "$SYSTEMCTL_LOG" && ! -s "$DOCKER_LOG" ]] \
        && pass "partially enriched unattested Pixel marker fails closed before mutation" \
        || fail "partial unattested marker refusal caused mutation"
fi

write_active_fixture
pixel_install="$HOME_DIR/.local/share/pixel"
rm -f -- "$pixel_install/runtime-attestation.json"
mv -T "$pixel_install/current" "$pixel_install/.ods-uninstall-current"
mkdir -m 0700 "$pixel_install/retired-ods-releases"
identity_sha="$(sha256sum "$pixel_install/releases/4.3.14/release-identity.json" | awk '{print $1}')"
manifest_sha="$(sha256sum "$pixel_install/releases/4.3.14/install-manifest.sha256" | awk '{print $1}')"
retired_container="$(mktemp -d \
    "$pixel_install/retired-ods-releases/4.3.14-${identity_sha:0:12}.XXXXXXXX")"
retired_release="$retired_container/release"
python3 - "$HOME_DIR/.config/ods/pixel-managed.json" "$identity_sha" "$manifest_sha" \
    "$retired_release" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
original = json.loads(path.read_text())
value = {
    "schema_version": 2,
    "manager": "ods",
    "state": "deactivating",
    "initial_active_state": "absent",
    "install_dir": original["install_dir"],
    "pixel_source_ref": original["pixel_source_ref"],
    "active_release_version": "4.3.14",
    "release_identity_sha256": sys.argv[2],
    "install_manifest_sha256": sys.argv[3],
    "sandbox_image_id": "sha256:" + "d" * 64,
    "sandbox_image_state": "preserved-unbound",
    "retired_release_path": sys.argv[4],
    "runtime_attestation_state": "absent",
}
path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
PY
chmod 0600 "$HOME_DIR/.config/ods/pixel-managed.json"
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR" \
    && [[ -d "$retired_release" \
        && ! -e "$pixel_install/releases/4.3.14" \
        && ! -e "$pixel_install/.ods-uninstall-current" \
        && ! -e "$HOME_DIR/.config/ods/pixel-managed.json" \
        && -e "$DOCKER_STATE" ]]; then
    pass "derived unattested deactivation resumes while preserving unbound shared image tags"
else
    fail "derived unattested deactivation marker was not resumable"
fi

write_active_fixture
python3 - "$HOME_DIR/.config/ods/pixel-managed.json" \
    "$HOME_DIR/.local/share/pixel/releases/4.3.14/release-identity.json" <<'PY'
import json, pathlib, sys
marker = pathlib.Path(sys.argv[1])
original = json.loads(marker.read_text())
marker.write_text(json.dumps({
    "schema_version": 2,
    "manager": "ods",
    "state": "installing",
    "initial_active_state": "absent",
    "install_dir": original["install_dir"],
    "pixel_source_ref": original["pixel_source_ref"],
}, sort_keys=True, separators=(",", ":")) + "\n")
identity = pathlib.Path(sys.argv[2])
value = json.loads(identity.read_text())
value["source"]["commit"] = "f" * 40
identity.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
PY
chmod 0600 "$HOME_DIR/.config/ods/pixel-managed.json"
rm -f -- "$HOME_DIR/.local/share/pixel/runtime-attestation.json"
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
    fail "minimal unattested Pixel marker accepted a mismatched release source"
else
    [[ -L "$HOME_DIR/.local/share/pixel/current" \
        && -e "$HOME_DIR/.config/ods/pixel-managed.json" \
        && -e "$DOCKER_STATE" && ! -s "$SYSTEMCTL_LOG" && ! -s "$DOCKER_LOG" ]] \
        && pass "minimal unattested release source mismatch fails closed before mutation" \
        || fail "minimal unattested source mismatch caused mutation"
fi

write_active_fixture
rm -f -- "$HOME_DIR/.local/share/pixel/runtime-attestation.json"
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
    fail "ready Pixel active link without attestation was accepted"
else
    [[ -L "$HOME_DIR/.local/share/pixel/current" \
        && -d "$HOME_DIR/.local/share/pixel/releases/4.3.14" \
        && -e "$HOME_DIR/.config/ods/pixel-managed.json" \
        && -e "$DOCKER_STATE" && ! -s "$SYSTEMCTL_LOG" && ! -s "$DOCKER_LOG" ]] \
        && pass "ready Pixel state without attestation fails closed before mutation" \
        || fail "ready Pixel state without attestation caused partial cleanup"
fi

write_active_fixture
pixel_install="$HOME_DIR/.local/share/pixel"
rm -f -- "$pixel_install/runtime-attestation.json"
mv -T "$pixel_install/current" "$pixel_install/.ods-uninstall-current"
mkdir -m 0700 "$pixel_install/retired-ods-releases"
identity_prefix="$(python3 - "$HOME_DIR/.config/ods/pixel-managed.json" <<'PY'
import json, pathlib, sys
print(json.loads(pathlib.Path(sys.argv[1]).read_text())["release_identity_sha256"][:12])
PY
)"
retired_container="$(mktemp -d \
    "$pixel_install/retired-ods-releases/4.3.14-${identity_prefix}.XXXXXXXX")"
retired_release="$retired_container/release"
python3 - "$HOME_DIR/.config/ods/pixel-managed.json" "$retired_release" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text())
value["state"] = "deactivating"
value["retired_release_path"] = sys.argv[2]
value["runtime_attestation_state"] = "absent"
path.write_text(json.dumps(value) + "\n")
PY
chmod 0600 "$HOME_DIR/.config/ods/pixel-managed.json"
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR" \
    && [[ -d "$retired_release" \
        && ! -e "$pixel_install/releases/4.3.14" \
        && ! -e "$pixel_install/.ods-uninstall-current" \
        && ! -e "$HOME_DIR/.config/ods/pixel-managed.json" \
        && ! -e "$DOCKER_STATE" ]]; then
    pass "unattested Pixel deactivation resumes after active-link staging"
else
    fail "unattested Pixel deactivation could not resume after active-link staging"
fi

for archive_step in before-release-move after-release-move after-active-state-cleanup; do
    write_active_fixture
    pixel_install="$HOME_DIR/.local/share/pixel"
    mv -T "$pixel_install/runtime-attestation.json" \
        "$pixel_install/.ods-uninstall-runtime-attestation"
    mv -T "$pixel_install/current" "$pixel_install/.ods-uninstall-current"
    mkdir -m 0700 "$pixel_install/retired-ods-releases"
    identity_prefix="$(python3 - "$HOME_DIR/.config/ods/pixel-managed.json" <<'PY'
import json, pathlib, sys
print(json.loads(pathlib.Path(sys.argv[1]).read_text())["release_identity_sha256"][:12])
PY
)"
    retired_container="$(mktemp -d \
        "$pixel_install/retired-ods-releases/4.3.14-${identity_prefix}.XXXXXXXX")"
    retired_release="$retired_container/release"
    python3 - "$HOME_DIR/.config/ods/pixel-managed.json" "$retired_release" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text())
value["state"] = "deactivating"
value["retired_release_path"] = sys.argv[2]
path.write_text(json.dumps(value) + "\n")
PY
    chmod 0600 "$HOME_DIR/.config/ods/pixel-managed.json"
    if [[ "$archive_step" != before-release-move ]]; then
        mv -T "$pixel_install/releases/4.3.14" "$retired_release"
    fi
    if [[ "$archive_step" == after-active-state-cleanup ]]; then
        rm -f -- "$pixel_install/.ods-uninstall-current" \
            "$pixel_install/.ods-uninstall-runtime-attestation" \
            "$HOME_DIR/.openclaw/openclaw.json" \
            "$HOME_DIR/.config/pixel-agent/gateway.env" \
            "$HOME_DIR/.config/pixel-deployment/onboarding.json"
    fi
    if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR" \
        && [[ -d "$retired_release" \
            && ! -e "$pixel_install/releases/4.3.14" \
            && ! -e "$pixel_install/.ods-uninstall-current" \
            && ! -e "$pixel_install/.ods-uninstall-runtime-attestation" \
            && ! -e "$HOME_DIR/.config/ods/pixel-managed.json" \
            && ! -e "$DOCKER_STATE" ]]; then
        pass "deactivation resumes from $archive_step archive state"
    else
        fail "deactivation could not resume from $archive_step archive state"
    fi
done

write_active_fixture
mkdir -m 0770 "$HOME_DIR/.local/share/pixel/retired-ods-releases"
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
    fail "unsafe retired release root was accepted"
else
    [[ -L "$HOME_DIR/.local/share/pixel/current" \
        && -e "$HOME_DIR/.config/ods/pixel-managed.json" \
        && -e "$DOCKER_STATE" && ! -s "$SYSTEMCTL_LOG" && ! -s "$DOCKER_LOG" ]] \
        && pass "unsafe retired release root fails before service or Docker mutation" \
        || fail "unsafe retired release root caused partial cleanup"
fi

write_active_fixture
python3 - "$HOME_DIR/.config/ods/pixel-managed.json" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text())
value["schema_version"] = 1
value.pop("initial_active_state", None)
path.write_text(json.dumps(value) + "\n")
PY
chmod 0600 "$HOME_DIR/.config/ods/pixel-managed.json"
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
    fail "legacy marker without a pre-install absence proof deactivated Pixel"
else
    [[ -L "$HOME_DIR/.local/share/pixel/current" && -e "$DOCKER_STATE" \
        && ! -s "$SYSTEMCTL_LOG" && ! -s "$DOCKER_LOG" ]] \
        && pass "legacy marker cannot claim or deactivate an active Pixel release" \
        || fail "legacy marker active-state refusal caused mutation"
fi

write_fixture
python3 - "$HOME_DIR/.config/ods/pixel-managed.json" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text())
value["install_dir"] = "/tmp/different-ods-install"
path.write_text(json.dumps(value) + "\n")
PY
chmod 0600 "$HOME_DIR/.config/ods/pixel-managed.json"
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
    fail "mismatched management marker was accepted"
else
    [[ -e "$HOME_DIR/.openclaw/openclaw.json" && -e "$SYSTEMD_DIR/openclaw-gateway.service" \
        && ! -s "$SYSTEMCTL_LOG" ]] \
        && pass "mismatched marker fails before any mutation" \
        || fail "mismatched marker mutated managed targets"
fi

write_fixture
printf '%s\n' 'drifted program' >"$LIBEXEC_DIR/ods-pixel-ingress.mjs"
chmod 0755 "$LIBEXEC_DIR/ods-pixel-ingress.mjs"
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
    fail "drifted root-owned ingress program was accepted"
else
    [[ -e "$HOME_DIR/.config/ods/pixel-managed.json" && -e "$LIBEXEC_DIR/ods-pixel-ingress.mjs" \
        && ! -s "$SYSTEMCTL_LOG" ]] \
        && pass "root artifact drift fails before any mutation" \
        || fail "root artifact drift caused partial cleanup"
fi

write_access_fixture
printf '%s\n' '[Service]' 'Environment=OPERATOR_OWNED=1' \
    > "$SYSTEMD_DIR/openclaw-gateway.service.d/99-operator.conf"
chmod 0644 "$SYSTEMD_DIR/openclaw-gateway.service.d/99-operator.conf"
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR" \
    && [[ ! -e "$SYSTEMD_DIR/ods-pixel-access.service" \
        && ! -e "$LIBEXEC_DIR/ods-pixel-access" \
        && ! -e "$ETC_DIR/pixel-access.json" \
        && ! -e "$ETC_DIR/pixel-access-relay.key" \
        && ! -e "$ACCESS_STATE" \
        && ! -e "$ACCESS_PROBE_BASE/$(id -u)" \
        && ! -e "$HOME_DIR/.openclaw/.ods-access-runtime" \
        && ! -e "$SYSTEMD_DIR/openclaw-gateway.service.d/90-ods-full-access.conf" \
        && -e "$SYSTEMD_DIR/openclaw-gateway.service.d/99-operator.conf" ]]; then
    access_stop_line="$(grep -n '^disable --now ods-pixel-access.service$' "$SYSTEMCTL_LOG" | cut -d: -f1)"
    gateway_stop_line="$(grep -n '^disable --now openclaw-gateway.service$' "$SYSTEMCTL_LOG" | cut -d: -f1)"
    if [[ "$access_stop_line" =~ ^[0-9]+$ && "$gateway_stop_line" =~ ^[0-9]+$ \
        && "$access_stop_line" -lt "$gateway_stop_line" ]]; then
        pass "Pixel access cleanup is exact, owner-scoped, and stops its coordinator before the gateway"
    else
        fail "Pixel access services were not stopped in the required order"
    fi
else
    fail "verified Pixel access artifacts were not removed completely"
fi

write_access_fixture
write_provider_service_fixture
printf '%s\n' '[Service]' 'Environment=OPERATOR_OWNED=1' \
    > "$SYSTEMD_DIR/openclaw-gateway.service.d/99-operator.conf"
chmod 0644 "$SYSTEMD_DIR/openclaw-gateway.service.d/99-operator.conf"
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR" \
    && [[ ! -e "$ETC_DIR/pixel-provider.env" \
        && ! -e "$SYSTEMD_DIR/openclaw-gateway.service.d/95-ods-provider.conf" \
        && ! -e "$ACCESS_STATE" \
        && -e "$SYSTEMD_DIR/openclaw-gateway.service.d/99-operator.conf" ]]; then
    pass "provider service files leave with their verified access receipt; operator drop-in remains"
else
    fail "provider service files outlived their managed access receipt"
fi

for scenario in environment-drift dropin-drift missing-receipt deactivated-receipt; do
    write_access_fixture
    write_provider_service_fixture
    case "$scenario" in
        environment-drift) printf '%s\n' '# drift' >> "$ETC_DIR/pixel-provider.env" ;;
        dropin-drift) printf '%s\n' '# drift' >> "$SYSTEMD_DIR/openclaw-gateway.service.d/95-ods-provider.conf" ;;
        missing-receipt) rm -f -- "$ACCESS_STATE/provider-root-managed.json" ;;
        deactivated-receipt) printf 'null\n' > "$ACCESS_STATE/provider-root-managed.json" ;;
    esac
    if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
        fail "unbound provider service $scenario was accepted"
    else
        [[ -e "$ETC_DIR/pixel-provider.env" \
            && -e "$SYSTEMD_DIR/openclaw-gateway.service.d/95-ods-provider.conf" \
            && -e "$ACCESS_STATE" && -e "$HOME_DIR/.config/ods/pixel-managed.json" \
            && ! -s "$SYSTEMCTL_LOG" ]] \
            && pass "unbound provider service $scenario fails before mutation" \
            || fail "unbound provider service $scenario caused partial cleanup"
    fi
done

write_access_fixture
printf 'null\n' > "$ACCESS_STATE/provider-root-managed.json"
chmod 0600 "$ACCESS_STATE/provider-root-managed.json"
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR" \
    && [[ ! -e "$ACCESS_STATE" && ! -e "$ETC_DIR/pixel-provider.env" ]]; then
    pass "deactivated provider receipt without service files is removable"
else
    fail "deactivated provider receipt blocked safe Pixel cleanup"
fi

write_access_fixture
python3 - "$HOME_DIR/.config/ods/pixel-managed.json" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text())
value["state"] = "installing"
path.write_text(json.dumps(value) + "\n")
PY
chmod 0600 "$HOME_DIR/.config/ods/pixel-managed.json"
rm -f "$SYSTEMD_DIR/ods-pixel-access.service" \
    "$LIBEXEC_DIR/ods-pixel-access/pixel_provider/coordinator.py"
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR" \
    && [[ ! -e "$LIBEXEC_DIR/ods-pixel-access" && ! -e "$ACCESS_STATE" ]]; then
    pass "exact interrupted Pixel access installation is resumable"
else
    fail "exact interrupted Pixel access installation was not cleaned"
fi

write_access_fixture
printf '%s\n' '# drifted' > "$LIBEXEC_DIR/ods-pixel-access/pixel_access_bridge.py"
chmod 0644 "$LIBEXEC_DIR/ods-pixel-access/pixel_access_bridge.py"
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
    fail "drifted Pixel access program was accepted"
else
    [[ -e "$LIBEXEC_DIR/ods-pixel-access/pixel_access_bridge.py" \
        && -e "$ACCESS_STATE" && ! -s "$SYSTEMCTL_LOG" ]] \
        && pass "Pixel access program drift fails before service mutation" \
        || fail "Pixel access program drift caused partial cleanup"
fi

write_access_fixture
python3 - "$ETC_DIR/pixel-access.json" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text())
value["install_dir"] = "/tmp/foreign-ods"
path.write_text(json.dumps(value) + "\n")
PY
chmod 0600 "$ETC_DIR/pixel-access.json"
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
    fail "foreign Pixel access configuration was accepted"
else
    [[ -e "$SYSTEMD_DIR/ods-pixel-access.service" && -e "$ACCESS_STATE" \
        && ! -s "$SYSTEMCTL_LOG" ]] \
        && pass "foreign Pixel access configuration fails before mutation" \
        || fail "foreign Pixel access configuration caused partial cleanup"
fi

write_access_fixture
printf '%s\n' 'foreign' > "$LIBEXEC_DIR/ods-pixel-access/operator-owned.txt"
chmod 0644 "$LIBEXEC_DIR/ods-pixel-access/operator-owned.txt"
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
    fail "foreign Pixel access program artifact was accepted"
else
    [[ -e "$LIBEXEC_DIR/ods-pixel-access/operator-owned.txt" && -e "$ACCESS_STATE" \
        && ! -s "$SYSTEMCTL_LOG" ]] \
        && pass "foreign Pixel access artifact fails before mutation" \
        || fail "foreign Pixel access artifact caused partial cleanup"
fi

write_fixture
chmod 0644 "$HOME_DIR/.config/ods/pixel-managed.json"
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
    fail "non-private management marker was accepted"
else
    [[ -e "$HOME_DIR/.config/ods/pixel-managed.json" && -e "$SYSTEMD_DIR/openclaw-gateway.service" \
        && ! -s "$SYSTEMCTL_LOG" ]] \
        && pass "unsafe marker permissions fail before any mutation" \
        || fail "unsafe marker permissions caused partial cleanup"
fi

write_fixture
export SYSTEMCTL_FAIL_DISABLE=true
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
    fail "system service stop failure was ignored"
else
    [[ -e "$HOME_DIR/.config/ods/pixel-managed.json" && -e "$HOME_DIR/.openclaw/openclaw.json" \
        && -e "$SYSTEMD_DIR/openclaw-gateway.service" && -e "$LIBEXEC_DIR/ods-pixel-ingress.mjs" ]] \
        && pass "service stop failure leaves every managed artifact in place" \
        || fail "service stop failure caused partial cleanup"
fi
unset SYSTEMCTL_FAIL_DISABLE

write_fixture
rm -f -- "$SYSTEMD_DIR/openclaw-gateway.service" "$SYSTEMD_DIR/pixel-ingress.service" \
    "$ETC_DIR/pixel-agent.env" "$LIBEXEC_DIR/ods-pixel-ingress.mjs"
mv "$MOCK_BIN/sudo" "$MOCK_BIN/sudo.disabled"
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
    [[ ! -e "$HOME_DIR/.config/ods/pixel-managed.json" \
        && ! -e "$HOME_DIR/.openclaw/openclaw.json" ]] \
        && pass "owner-only cleanup succeeds without sudo after root artifacts are already absent" \
        || fail "owner-only cleanup left managed user artifacts"
else
    fail "owner-only cleanup unnecessarily required sudo"
fi
mv "$MOCK_BIN/sudo.disabled" "$MOCK_BIN/sudo"

write_fixture
rm -f "$HOME_DIR/.config/ods/pixel-managed.json"
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
    [[ -e "$HOME_DIR/.openclaw/openclaw.json" && -e "$SYSTEMD_DIR/openclaw-gateway.service" \
        && ! -s "$SYSTEMCTL_LOG" ]] \
        && pass "ambient Pixel without an ODS marker is untouched" \
        || fail "ambient Pixel was mutated"
else
    fail "ambient Pixel no-op returned failure"
fi

write_access_fixture
if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
    [[ ! -e "$ACCESS_STATE" && ! -e "$LIBEXEC_DIR/ods-pixel-access" \
        && ! -e "$ETC_DIR/pixel-access.json" && ! -e "$SYSTEMD_DIR/ods-pixel-access.service" ]] \
        && [[ "$(head -n 1 "$SYSTEMCTL_LOG")" == 'disable --now ods-pixel-access.service' ]] \
        && pass "access coordinator stops first and its verified artifacts are removed" \
        || fail "access coordinator cleanup was incomplete or out of order"
else
    fail "verified access coordinator could not be removed"
fi

for scenario in foreign modified_unit modified_program relay_key state_symlink pending_transition stop_failure still_active; do
    write_access_fixture
    case "$scenario" in
        foreign) printf '{"install_dir":"/another-install","owner":"nobody"}\n' > "$ETC_DIR/pixel-access.json" ;;
        modified_unit) printf '\n# custom unit\n' >> "$SYSTEMD_DIR/ods-pixel-access.service" ;;
        modified_program) printf 'operator changes\n' > "$LIBEXEC_DIR/ods-pixel-access/access_mode_server.py" ;;
        relay_key) printf 'changed\n' > "$ETC_DIR/pixel-access-relay.key" ;;
        state_symlink) mv "$ACCESS_STATE" "$ACCESS_STATE-outside"; ln -s "$ACCESS_STATE-outside" "$ACCESS_STATE" ;;
        pending_transition) printf '{}\n' > "$ACCESS_STATE/transition.json"; chmod 0600 "$ACCESS_STATE/transition.json" ;;
        stop_failure) export ACCESS_STOP_FAIL=true ;;
        still_active) export ACCESS_STILL_ACTIVE=true ;;
    esac
    if ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME_DIR"; then
        fail "unsafe access cleanup was accepted: $scenario"
    else
        [[ -e "$ETC_DIR/pixel-access.json" && -e "$LIBEXEC_DIR/ods-pixel-access/access_mode_server.py" \
            && -e "$ACCESS_STATE/service-baseline.json" && -e "$SYSTEMD_DIR/openclaw-gateway.service" \
            && -e "$HOME_DIR/.config/ods/pixel-managed.json" ]] \
            && pass "access cleanup preserves artifacts on $scenario" \
            || fail "access cleanup lost recovery artifacts on $scenario"
    fi
    unset ACCESS_STOP_FAIL ACCESS_STILL_ACTIVE
done

printf 'Results: %d passed, %d failed\n' "$PASS" "$FAIL"
(( FAIL == 0 ))
