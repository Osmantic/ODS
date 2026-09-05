#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=installers/lib/pixel-host-install.sh
source "$ROOT/installers/lib/pixel-host-install.sh"

PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); printf 'PASS: %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL: %s\n' "$1" >&2; }
check() { if "$@"; then pass "$*"; else fail "$*"; fi; }

TEST_ROOT="$(mktemp -d)"
cleanup() {
    case "$TEST_ROOT" in /tmp/*|/var/tmp/*) rm -rf -- "$TEST_ROOT" ;; esac
}
trap cleanup EXIT

owner="$(id -un)"
ods_sudo_available() { return 1; }
ai_bad() { :; }
ai_ok() { :; }
ai() { :; }

probe_program="$TEST_ROOT/manager-probe.py"
probe_counter="$TEST_ROOT/manager-probe.count"
cat > "$probe_program" <<'PY'
import json, os, pathlib, sys

counter = pathlib.Path(os.environ["PIXEL_TEST_COUNTER"])
calls = int(counter.read_text() if counter.exists() else "0") + 1
counter.write_text(str(calls), encoding="utf-8")
if calls == 1 and os.environ.get("PIXEL_TEST_BAD") != "true":
    raise SystemExit(1)
extension_id = "wrong" if os.environ.get("PIXEL_TEST_BAD") == "true" else sys.argv[4]
print(json.dumps({
    "schemaVersion": 1,
    "kind": "ods-pixel-extension-lifecycle",
    "action": "inspect",
    "extensionId": extension_id,
    "outcome": "ready",
    "previousStatus": "not_installed",
    "currentStatus": "not_installed",
    "changed": False,
    "externalEffectOccurred": False,
    "requiredConfiguration": [],
    "optionalConfiguration": [],
    "missingConfiguration": [],
    "rollback": {"attempted": False, "succeeded": None},
    "boundary": "Scoped ODS extension lifecycle proxy; it grants no Docker, shell, credential, arbitrary HTTP, or data-purge authority.",
}))
PY
if (
    ods_sudo() { shift 2; "$@"; }
    export PIXEL_TEST_COUNTER="$probe_counter"
    _ods_pixel_wait_extension_manager_probe "$probe_program" crewai 3 0
); then
    pass "extension manager readiness retries a transient failure"
else
    fail "extension manager readiness retries a transient failure"
fi
check test "$(cat "$probe_counter")" = 2
rm -f -- "$probe_counter"
if (
    ods_sudo() { shift 2; "$@"; }
    export PIXEL_TEST_COUNTER="$probe_counter" PIXEL_TEST_BAD=true
    _ods_pixel_wait_extension_manager_probe "$probe_program" crewai 2 0
); then
    fail "extension manager readiness rejects structurally mismatched receipts"
else
    pass "extension manager readiness rejects structurally mismatched receipts"
fi
check test "$(cat "$probe_counter")" = 2

profile_root="$TEST_ROOT/ops-state-profiles"
mkdir -m 0750 "$profile_root"
for profile in .bash_logout .bashrc .profile; do
    printf '%s\n' fixture >"$profile_root/$profile"
    chmod 0644 "$profile_root/$profile"
done
if (
    ods_sudo() { "$@"; }
    _ods_pixel_harden_operations_state_profiles "$profile_root" "$owner"
); then
    [[ "$(stat -c '%a' "$profile_root/.bash_logout")" == 600 \
        && "$(stat -c '%a' "$profile_root/.bashrc")" == 600 \
        && "$(stat -c '%a' "$profile_root/.profile")" == 600 ]] \
        && pass "Operations Broker service profiles are hardened after Pixel account creation" \
        || fail "Operations Broker service profiles retained public modes"
else
    fail "Operations Broker service profiles could not be hardened"
fi

rm -rf -- "$profile_root"
mkdir -m 0750 "$profile_root"
printf '%s\n' fixture >"$profile_root/.profile"
chmod 0644 "$profile_root/.profile"
ln -s /etc/passwd "$profile_root/.bashrc"
if (
    ods_sudo() { "$@"; }
    _ods_pixel_harden_operations_state_profiles "$profile_root" "$owner"
); then
    fail "unsafe Operations Broker service profile was accepted"
elif [[ "$(stat -c '%a' "$profile_root/.profile")" == 644 ]]; then
    pass "Operations Broker profile hardening validates every target before mutation"
else
    fail "failed Operations Broker profile hardening caused partial mutation"
fi

home="$TEST_ROOT/home"
mkdir -p "$home/.openclaw"
printf '%s\n' '{"gateway":{"bind":"loopback"},"preserve":{"value":7}}' > "$home/.openclaw/openclaw.json"
chmod 0644 "$home/.openclaw/openclaw.json"

attempt_log="$TEST_ROOT/attempt-logs/pixel-install.log"
check test "$(_ods_pixel_prepare_attempt_log "$owner" "$home" "$attempt_log")" = "$attempt_log"
check test -f "$attempt_log"
check test ! -L "$attempt_log"
check test "$(stat -c '%a' "$attempt_log")" = 600
printf '%s\n' keep > "$TEST_ROOT/attempt-log-target"
rm -f -- "$attempt_log"
ln -s "$TEST_ROOT/attempt-log-target" "$attempt_log"
check test "$(_ods_pixel_prepare_attempt_log "$owner" "$home" "$attempt_log")" = "$attempt_log"
check test -f "$attempt_log"
check test ! -L "$attempt_log"
check test "$(cat "$TEST_ROOT/attempt-log-target")" = keep
rm -rf -- "$TEST_ROOT/attempt-logs"
mkdir -p "$TEST_ROOT/attempt-log-link-target"
ln -s "$TEST_ROOT/attempt-log-link-target" "$TEST_ROOT/attempt-logs"
if _ods_pixel_prepare_attempt_log "$owner" "$home" "$attempt_log" >/dev/null 2>&1; then
    fail "symlink Pixel attempt-log directory rejected"
else
    pass "symlink Pixel attempt-log directory rejected"
fi
rm -f -- "$TEST_ROOT/attempt-logs"

_ods_pixel_enable_chat_endpoint "$owner" "$home"
check python3 -c 'import json,sys; v=json.load(open(sys.argv[1])); assert v["gateway"]["http"]["endpoints"]["chatCompletions"]["enabled"] is True; assert v["preserve"]["value"] == 7' "$home/.openclaw/openclaw.json"
check test "$(stat -c '%a' "$home/.openclaw/openclaw.json")" = 600

rm -f "$home/.openclaw/openclaw.json"
printf '%s\n' '{}' > "$TEST_ROOT/symlink-target.json"
ln -s "$TEST_ROOT/symlink-target.json" "$home/.openclaw/openclaw.json"
if _ods_pixel_enable_chat_endpoint "$owner" "$home" >/dev/null 2>&1; then
    fail "symlink OpenClaw config rejected"
else
    pass "symlink OpenClaw config rejected"
fi
rm -f "$home/.openclaw/openclaw.json"

INSTALL_DIR="$TEST_ROOT/ods"
PIXEL_SOURCE_REF="$(printf 'b%.0s' {1..40})"
ODS_PIXEL_GATEWAY_UNIT_PATH="$TEST_ROOT/openclaw-gateway.service"
export INSTALL_DIR PIXEL_SOURCE_REF ODS_PIXEL_GATEWAY_UNIT_PATH
_ods_pixel_assert_managed_state "$owner" "$home"
marker="$home/.config/ods/pixel-managed.json"
check test "$(stat -c '%a' "$marker")" = 600
check test "$(stat -c '%a' "${marker%/*}")" = 700
check python3 -c 'import json,sys; v=json.load(open(sys.argv[1])); assert v == {"initial_active_state":"absent","install_dir":sys.argv[2],"manager":"ods","pixel_source_ref":sys.argv[3],"schema_version":2,"state":"installing"}' "$marker" "$INSTALL_DIR" "$PIXEL_SOURCE_REF"
if _ods_pixel_source_transition_required "$owner" "$home" "$PIXEL_SOURCE_REF"; then
    fail "matching Pixel source unexpectedly requires retirement"
else
    check test "$?" = 1
fi
next_source_ref="$(printf 'c%.0s' {1..40})"
check _ods_pixel_source_transition_required "$owner" "$home" "$next_source_ref"
python3 - "$marker" "$next_source_ref" <<'PY'
import json, pathlib, re, sys
path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text(encoding="utf-8"))
value["requested_source_ref"] = sys.argv[2]
path.write_text(json.dumps(value) + "\n", encoding="utf-8")
PY
chmod 0600 "$marker"
check _ods_pixel_source_transition_required "$owner" "$home" "$next_source_ref"
python3 - "$marker" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text(encoding="utf-8"))
value.pop("requested_source_ref", None)
path.write_text(json.dumps(value) + "\n", encoding="utf-8")
PY
chmod 0600 "$marker"
printf '%s\n' '{"gateway":{"http":{"endpoints":{"chatCompletions":{"enabled":true}}}}}' > "$home/.openclaw/openclaw.json"
chmod 0600 "$home/.openclaw/openclaw.json"
contract_sha256="$(printf 'c%.0s' {1..64})"
pixel_root="$TEST_ROOT/pixel-root"
release="$home/.local/share/pixel/releases/4.3.14"
mkdir -p "$pixel_root" "$release"
printf '%s\n' '{"sandboxImage":"openclaw-sandbox:test"}' > "$pixel_root/RELEASE-MANIFEST.json"
cat > "$release/release-identity.json" <<JSON
{"kind":"pixel-release-source-identity","pixel":"4.3.14","source":{"state":"git-clean","commit":"$PIXEL_SOURCE_REF","tree":"$(printf 'a%.0s' {1..40})"}}
JSON
printf '%s  %s\n' "$(sha256sum "$release/release-identity.json" | awk '{print $1}')" release-identity.json > "$release/install-manifest.sha256"
identity_sha256="$(sha256sum "$release/release-identity.json" | awk '{print $1}')"
manifest_sha256="$(sha256sum "$release/install-manifest.sha256" | awk '{print $1}')"
cat > "$home/.local/share/pixel/runtime-attestation.json" <<JSON
{"kind":"pixel-runtime-attestation","status":"verified","pixel":"4.3.14","source":{"state":"git-clean","commit":"$PIXEL_SOURCE_REF","tree":"$(printf 'a%.0s' {1..40})"},"release":{"sourceIdentitySha256":"$identity_sha256","installManifestSha256":"$manifest_sha256"}}
JSON
chmod 0600 "$home/.local/share/pixel/runtime-attestation.json"
ln -s "$release" "$home/.local/share/pixel/current"
mock_bin="$TEST_ROOT/mock-bin"
mkdir -p "$mock_bin"
cat > "$mock_bin/docker" <<SH
#!/usr/bin/env bash
if [[ "\$1 \$2" == "image inspect" ]]; then
    printf '%s\n' 'sha256:$(printf 'd%.0s' {1..64})'
    exit 0
fi
exit 1
SH
chmod +x "$mock_bin/docker"
PATH="$mock_bin:$PATH"
export PATH
_ods_pixel_mark_verified_installing "$owner" "$home" "$contract_sha256" "$pixel_root"
check python3 -c 'import json,sys; v=json.load(open(sys.argv[1])); assert v["state"] == "installing" and v["pixel_source_ref"] == sys.argv[2] and v["contract_sha256"] == sys.argv[3] and len(v["configuration_sha256"]) == 64 and v["active_release_version"] == "4.3.14" and len(v["release_identity_sha256"]) == 64 and len(v["install_manifest_sha256"]) == 64 and v["sandbox_image"] == "openclaw-sandbox:test" and v["sandbox_image_id"].startswith("sha256:")' "$marker" "$PIXEL_SOURCE_REF" "$contract_sha256"
check _ods_pixel_managed_contract_matches "$owner" "$home" "$contract_sha256"
check _ods_pixel_verified_source_matches "$owner" "$home"
_ods_pixel_mark_ready "$owner" "$home" "$contract_sha256" "$pixel_root"
check python3 -c 'import json,sys; v=json.load(open(sys.argv[1])); assert v["state"] == "ready" and v["pixel_source_ref"] == sys.argv[2] and v["contract_sha256"] == sys.argv[3] and len(v["configuration_sha256"]) == 64' "$marker" "$PIXEL_SOURCE_REF" "$contract_sha256"
check _ods_pixel_managed_contract_matches "$owner" "$home" "$contract_sha256"
if _ods_pixel_managed_contract_matches "$owner" "$home" "$(printf 'd%.0s' {1..64})"; then
    fail "mismatched managed Pixel contract rejected"
else
    pass "mismatched managed Pixel contract rejected"
fi
original_source_ref="$PIXEL_SOURCE_REF"
PIXEL_SOURCE_REF="$(printf 'e%.0s' {1..40})"
if _ods_pixel_managed_contract_matches "$owner" "$home" "$contract_sha256"; then
    fail "mismatched exact Pixel source commit rejected"
else
    pass "mismatched exact Pixel source commit rejected"
fi
if _ods_pixel_verified_source_matches "$owner" "$home"; then
    fail "mismatched verified Pixel source rejected for extension refresh"
else
    pass "mismatched verified Pixel source rejected for extension refresh"
fi
_ods_pixel_mark_installing "$owner" "$home"
check python3 -c 'import json,sys; v=json.load(open(sys.argv[1])); assert v["pixel_source_ref"] == sys.argv[2] and v["requested_source_ref"] == sys.argv[3] and v["state"] == "installing"' "$marker" "$original_source_ref" "$PIXEL_SOURCE_REF"
PIXEL_SOURCE_REF="$original_source_ref"
chmod 0644 "$marker"
if _ods_pixel_managed_contract_matches "$owner" "$home" "$contract_sha256"; then
    fail "unsafe managed Pixel marker mode rejected"
else
    pass "unsafe managed Pixel marker mode rejected"
fi
chmod 0600 "$marker"
cp "$home/.openclaw/openclaw.json" "$TEST_ROOT/openclaw.valid.json"
printf '%s\n' '{"gateway":{"http":{"endpoints":{"chatCompletions":{"enabled":false}}}}}' > "$home/.openclaw/openclaw.json"
if _ods_pixel_managed_contract_matches "$owner" "$home" "$contract_sha256"; then
    fail "drifted managed OpenClaw configuration rejected"
else
    pass "drifted managed OpenClaw configuration rejected"
fi
mv "$TEST_ROOT/openclaw.valid.json" "$home/.openclaw/openclaw.json"
candidate="$TEST_ROOT/openclaw.candidate.json"
printf '%s\n' '{"gateway":{"http":{"endpoints":{"chatCompletions":{"enabled":true}}}}}' > "$candidate"
chmod 0600 "$candidate"
check _ods_pixel_candidate_config_matches_live "$owner" "$home" "$candidate"
printf '%s\n' '{"gateway":{"http":{"endpoints":{"chatCompletions":{"enabled":false}}}}}' > "$candidate"
if _ods_pixel_candidate_config_matches_live "$owner" "$home" "$candidate"; then
    fail "drifted Pixel candidate config rejected"
else
    pass "drifted Pixel candidate config rejected"
fi
rm -f "$candidate"
ln -s "$home/.openclaw/openclaw.json" "$candidate"
if _ods_pixel_candidate_config_matches_live "$owner" "$home" "$candidate"; then
    fail "symlink Pixel candidate config rejected"
else
    pass "symlink Pixel candidate config rejected"
fi
rm -f "$candidate"
check _ods_pixel_assert_managed_state "$owner" "$home"
_ods_pixel_mark_installing "$owner" "$home"
check python3 -c 'import json,sys; v=json.load(open(sys.argv[1])); assert v["state"] == "installing" and v["pixel_source_ref"] == sys.argv[2] and v["contract_sha256"] == sys.argv[3]' "$marker" "$PIXEL_SOURCE_REF" "$contract_sha256"
check _ods_pixel_managed_contract_matches "$owner" "$home" "$contract_sha256"
_ods_pixel_mark_ready "$owner" "$home" "$contract_sha256" "$pixel_root"

ambient_home="$TEST_ROOT/ambient-home"
mkdir -p "$ambient_home/.openclaw"
printf '%s\n' '{}' > "$ambient_home/.openclaw/openclaw.json"
if _ods_pixel_assert_managed_state "$owner" "$ambient_home" >/dev/null 2>&1; then
    fail "ambient OpenClaw deployment rejected"
else
    pass "ambient OpenClaw deployment rejected"
fi
check test ! -e "$ambient_home/.config/ods/pixel-managed.json"

ambient_active_home="$TEST_ROOT/ambient-active-home"
mkdir -p "$ambient_active_home/.local/share/pixel/releases/4.3.14"
ln -s "$ambient_active_home/.local/share/pixel/releases/4.3.14" "$ambient_active_home/.local/share/pixel/current"
if _ods_pixel_assert_managed_state "$owner" "$ambient_active_home" >/dev/null 2>&1; then
    fail "ambient active Pixel release rejected before marker creation"
else
    pass "ambient active Pixel release rejected before marker creation"
fi
check test ! -e "$ambient_active_home/.config/ods/pixel-managed.json"

plugin_tree="$INSTALL_DIR/extensions/services/pixel-agent/plugin"
mkdir -p "$plugin_tree/nested"
printf '%s\n' '{"id":"pixel-ods"}' > "$plugin_tree/openclaw.plugin.json"
printf '%s\n' 'export default {};' > "$plugin_tree/nested/index.js"
chmod 0777 "$INSTALL_DIR" "$INSTALL_DIR/extensions" "$INSTALL_DIR/extensions/services" \
    "$INSTALL_DIR/extensions/services/pixel-agent" "$plugin_tree" \
    "$plugin_tree/nested" "$plugin_tree/openclaw.plugin.json" "$plugin_tree/nested/index.js"
check _ods_pixel_secure_plugin_tree "$owner" "$home" "$plugin_tree"
check test -z "$(find -P "$plugin_tree" -perm /022 -print -quit)"
check test "$(stat -c '%a' "$INSTALL_DIR")" = 755
check test "$(stat -c '%a' "$plugin_tree")" = 755
check test "$(stat -c '%a' "$plugin_tree/nested/index.js")" = 644
ln -s "$plugin_tree/openclaw.plugin.json" "$plugin_tree/linked.json"
if _ods_pixel_secure_plugin_tree "$owner" "$home" "$plugin_tree" >/dev/null 2>&1; then
    fail "symlink in ODS Pixel plugin tree rejected"
else
    pass "symlink in ODS Pixel plugin tree rejected"
fi
rm -f "$plugin_tree/linked.json"

exec_control_home="$TEST_ROOT/exec-control-home"
exec_control_source="$TEST_ROOT/cancellable-exec.sh"
exec_control_sudo_source="$TEST_ROOT/noninteractive-sudo.sh"
install -m 0644 "$ROOT/extensions/services/pixel-agent/host/cancellable-exec.sh" \
    "$exec_control_source"
install -m 0644 "$ROOT/extensions/services/pixel-agent/host/noninteractive-sudo.sh" \
    "$exec_control_sudo_source"
mkdir -m 0700 -p "$exec_control_home"
check _ods_pixel_install_exec_control "$owner" "$exec_control_home" \
    "$exec_control_source" "$exec_control_sudo_source"
check test -d "$exec_control_home/.openclaw"
check test "$(stat -c '%a' "$exec_control_home/.openclaw")" = 700
check test "$(stat -c '%a' "$exec_control_home/.openclaw/.ods-exec-control")" = 700
check test "$(stat -c '%a' "$exec_control_home/.openclaw/.ods-exec-control/cancellable-exec.sh")" = 500
check test "$(stat -c '%a' "$exec_control_home/.openclaw/.ods-exec-control/sudo")" = 500
exec_control_bad_home="$TEST_ROOT/exec-control-bad-home"
mkdir -m 0700 -p "$exec_control_bad_home/.openclaw" "$TEST_ROOT/exec-control-link-target"
ln -s "$TEST_ROOT/exec-control-link-target" \
    "$exec_control_bad_home/.openclaw/.ods-exec-control"
if _ods_pixel_install_exec_control "$owner" "$exec_control_bad_home" \
    "$exec_control_source" "$exec_control_sudo_source" >/dev/null 2>&1; then
    fail "symlink Pixel execution control root rejected"
else
    pass "symlink Pixel execution control root rejected"
fi
exec_control_bad_wrapper_home="$TEST_ROOT/exec-control-bad-wrapper-home"
mkdir -m 0700 -p "$exec_control_bad_wrapper_home/.openclaw/.ods-exec-control"
ln -s "$TEST_ROOT/exec-control-link-target" \
    "$exec_control_bad_wrapper_home/.openclaw/.ods-exec-control/cancellable-exec.sh"
if _ods_pixel_install_exec_control "$owner" "$exec_control_bad_wrapper_home" \
    "$exec_control_source" "$exec_control_sudo_source" >/dev/null 2>&1; then
    fail "symlink Pixel execution wrapper rejected"
else
    pass "symlink Pixel execution wrapper rejected"
fi
exec_control_bad_sudo_home="$TEST_ROOT/exec-control-bad-sudo-home"
mkdir -m 0700 -p "$exec_control_bad_sudo_home/.openclaw/.ods-exec-control"
ln -s "$TEST_ROOT/exec-control-link-target" \
    "$exec_control_bad_sudo_home/.openclaw/.ods-exec-control/sudo"
if _ods_pixel_install_exec_control "$owner" "$exec_control_bad_sudo_home" \
    "$exec_control_source" "$exec_control_sudo_source" >/dev/null 2>&1; then
    fail "symlink Pixel noninteractive sudo adapter rejected"
else
    pass "symlink Pixel noninteractive sudo adapter rejected"
fi

plugin_list_bin="$TEST_ROOT/openclaw-plugin-list"
cat > "$plugin_list_bin" <<SH
#!/usr/bin/env bash
printf '%s\n' '{"plugins":[{"id":"pixel-ods","status":"loaded","rootDir":"$plugin_tree","contracts":{"tools":["pixel_ods_status","pixel_ods_apps_list","pixel_ods_host_observe","pixel_ods_host_command_propose","pixel_ods_evidence_report","pixel_ods_evidence_readback","pixel_ods_web_extract","pixel_ods_download_promote","pixel_ods_workspace_preview"]}}]}'
SH
chmod 0755 "$plugin_list_bin"
check _ods_pixel_verify_plugin_loaded "$owner" "$home" "$plugin_list_bin" "$plugin_tree"
cat > "$plugin_list_bin" <<SH
#!/usr/bin/env bash
printf '%s\n' '{"plugins":[{"id":"pixel-ods","status":"blocked","rootDir":"$plugin_tree","contracts":{"tools":["pixel_ods_status","pixel_ods_apps_list","pixel_ods_host_observe","pixel_ods_host_command_propose","pixel_ods_evidence_report","pixel_ods_evidence_readback","pixel_ods_web_extract","pixel_ods_download_promote","pixel_ods_workspace_preview"]}}]}'
SH
if _ods_pixel_verify_plugin_loaded "$owner" "$home" "$plugin_list_bin" "$plugin_tree" >/dev/null 2>&1; then
    fail "blocked ODS Pixel plugin rejected"
else
    pass "blocked ODS Pixel plugin rejected"
fi

sandbox_recreate_bin="$TEST_ROOT/openclaw-sandbox-recreate"
cat > "$sandbox_recreate_bin" <<'SH'
#!/usr/bin/env bash
exit 0
SH
chmod 0755 "$sandbox_recreate_bin"
if (
    ods_pixel_run_as_owner() {
        if [[ "$3" == "$sandbox_recreate_bin" ]]; then
            [[ "$4 $5 $6 $7 $8" == "sandbox recreate --agent pixel --force" ]]
        elif [[ "$3 $4 $5 $6 $7" == "docker ps --all --quiet --filter" \
            && "$8" == 'name=^/pixel-sbx-agent-pixel-' ]]; then
            return 0
        else
            return 1
        fi
    }
    _ods_pixel_recreate_agent_sandbox "$owner" "$home" "$sandbox_recreate_bin"
); then
    pass "Pixel sandbox recreation proves no stale agent container remains"
else
    fail "Pixel sandbox recreation proves no stale agent container remains"
fi
if (
    ods_pixel_run_as_owner() {
        if [[ "$3" == "$sandbox_recreate_bin" ]]; then
            return 0
        fi
        printf '%s\n' deadbeefdead
    }
    _ods_pixel_recreate_agent_sandbox "$owner" "$home" "$sandbox_recreate_bin"
) >/dev/null 2>&1; then
    fail "Pixel sandbox recreation rejects a stale unregistered container"
else
    pass "Pixel sandbox recreation rejects a stale unregistered container"
fi

plugin_registry_bin="$TEST_ROOT/openclaw-plugin-registry"
cat > "$plugin_registry_bin" <<SH
#!/usr/bin/env bash
printf '%s\n' '{"refreshed":true,"registry":{"version":1,"refreshReason":"manual","plugins":[{"pluginId":"pixel-ods","enabled":true,"rootDir":"$plugin_tree","contributions":{"contracts":{"tools":["pixel_ods_apps_list","pixel_ods_host_observe","pixel_ods_host_command_propose","pixel_ods_evidence_report","pixel_ods_evidence_readback","pixel_ods_status","pixel_ods_web_extract","pixel_ods_download_promote","pixel_ods_workspace_preview"]}}}]}}'
SH
chmod 0755 "$plugin_registry_bin"
check _ods_pixel_refresh_plugin_registry "$owner" "$home" "$plugin_registry_bin" "$plugin_tree"
cat > "$plugin_registry_bin" <<SH
#!/usr/bin/env bash
printf '%s\n' '{"refreshed":true,"registry":{"version":1,"refreshReason":"manual","plugins":[{"pluginId":"pixel-ods","enabled":true,"rootDir":"$plugin_tree","contributions":{"contracts":{"tools":["pixel_ods_status"]}}}]}}'
SH
if _ods_pixel_refresh_plugin_registry "$owner" "$home" "$plugin_registry_bin" "$plugin_tree" >/dev/null 2>&1; then
    fail "stale ODS Pixel plugin registry rejected"
else
    pass "stale ODS Pixel plugin registry rejected"
fi

restart_probe="$TEST_ROOT/restart-probe"
mkdir -p "$restart_probe/pixel-root"
if (
    restart_state="$restart_probe/state"
    systemctl() {
        if [[ "$1" == show ]]; then
            if [[ -e "$restart_state" ]]; then
                printf '%s\n' 4242
            else
                : > "$restart_state"
                printf '%s\n' 0
            fi
        elif [[ "$1" == is-active ]]; then
            return 0
        else
            return 1
        fi
    }
    ods_sudo_available() { return 0; }
    ods_sudo() { [[ "$*" == "systemctl restart openclaw-gateway.service" ]]; }
    curl() { printf '%s\n' '{"ok":true,"status":"live"}'; }
    ods_pixel_run_as_owner() {
        [[ "$1" == "$owner" && "$2" == "$home" \
            && "$3" == "$restart_probe/pixel-root/pixel" && "$4" == verify ]]
    }
    _ods_pixel_restart_gateway_and_verify "$owner" "$home" "$restart_probe/pixel-root"
); then
    pass "privileged Pixel restart tolerates transient MainPID zero"
else
    fail "privileged Pixel restart tolerates transient MainPID zero"
fi

if (
    systemctl() {
        [[ "$1" == show ]] && printf '%s\n' 0
    }
    ods_sudo_available() { return 1; }
    _ods_pixel_restart_gateway_and_verify "$owner" "$home" "$restart_probe/pixel-root"
) >/dev/null 2>&1; then
    fail "unprivileged Pixel restart still rejects a missing owned process"
else
    pass "unprivileged Pixel restart still rejects a missing owned process"
fi

ingress_restart_answers="$restart_probe/onboarding.json"
ingress_restart_status="$restart_probe/ods-status.json"
python3 - "$ingress_restart_answers" "$ingress_restart_status" <<'PY'
import json, pathlib, sys

answers, status = map(pathlib.Path, sys.argv[1:])
answers.write_text(json.dumps({
    "modelProvider": "ods-gateway",
    "modelId": "ods/current",
    "modelName": "ODS Current (org/model:variant)",
    "modelContextWindow": 2_000_000,
}) + "\n")
status.write_text(json.dumps({
    "runtime": {"model": "org/model:variant", "context_length": 2_000_000},
}) + "\n")
PY
chmod 0600 "$ingress_restart_answers" "$ingress_restart_status"
if (
    ingress_restart_state="$restart_probe/ingress-restarted"
    systemctl() {
        if [[ "$1" == is-active ]]; then
            return 0
        fi
        if [[ "$1" == show && "$3" == -p && "$4" == MainPID ]]; then
            [[ -e "$ingress_restart_state" ]] && printf '%s\n' 222 || printf '%s\n' 111
            return 0
        fi
        return 1
    }
    ods_sudo_available() { return 0; }
    ods_sudo() {
        [[ "$*" == "systemctl restart pixel-ingress.service" ]] || return 1
        : > "$ingress_restart_state"
    }
    _ods_pixel_wait_ingress() { return 0; }
    ods_pixel_run_as_owner() {
        local run_owner="$1" run_home="$2"
        shift 2
        [[ "$run_owner" == "$owner" && "$run_home" == "$home" ]] || return 1
        if [[ "$1" == python3 && "$4" == /run/ods-pixel/ods-status.json ]]; then
            set -- "$1" "$2" "$3" "$ingress_restart_status"
        fi
        "$@"
    }
    _ods_pixel_restart_ingress_and_verify "$owner" "$home" "$ingress_restart_answers"
); then
    pass "Pixel ingress restart refreshes and verifies the selected runtime"
else
    fail "Pixel ingress restart refreshes and verifies the selected runtime"
fi

source_fixture="$TEST_ROOT/pixel-source-fixture"
mkdir -p "$source_fixture"
git -C "$source_fixture" init -q
printf '%s\n' fixture > "$source_fixture/pixel"
git -C "$source_fixture" add pixel
git -C "$source_fixture" -c user.name=test -c user.email=test@example.invalid commit -qm fixture
PIXEL_SOURCE_URL="$source_fixture"
PIXEL_SOURCE_REF="$(git -C "$source_fixture" rev-parse HEAD)"
source_checkout="$TEST_ROOT/pixel-checkouts/source-$PIXEL_SOURCE_REF"
saved_umask="$(umask)"
umask 0002
check test "$(_ods_pixel_source_checkout "$owner" "$home" "$source_checkout")" = "$source_checkout"
umask "$saved_umask"
check test "$(git -C "$source_checkout" rev-parse HEAD)" = "$PIXEL_SOURCE_REF"
check test -z "$(git -C "$source_checkout" status --porcelain)"
check test "$(stat -c '%a' "$source_checkout/pixel")" = 644

sudo_mask_probe="$TEST_ROOT/sudo-mask-probe"
if (
    ods_sudo_available() { return 0; }
    ods_sudo() {
        [[ "$1" == -u && "$3" == -- ]] || return 1
        shift 3
        umask 0002
        "$@"
    }
    ods_pixel_run_as_owner_with_umask "$owner" "$home" 0022 sh -c \
        'printf probe > "$1"' sh "$sudo_mask_probe"
); then
    check test "$(stat -c '%a' "$sudo_mask_probe")" = 644
else
    fail "owner process umask survives sudo policy reset"
fi

cat > "$mock_bin/git" <<'SH'
#!/usr/bin/env bash
sleep 10
SH
chmod +x "$mock_bin/git"
PIXEL_SOURCE_URL="https://github.com/Osmantic/Pixel.git"
PIXEL_SOURCE_REF="$(printf 'f%.0s' {1..40})"
timed_checkout="$TEST_ROOT/timed-checkouts/source-$PIXEL_SOURCE_REF"
if PATH="$mock_bin:$PATH" ODS_PIXEL_SOURCE_TIMEOUT_SECONDS=1 \
    _ods_pixel_source_checkout "$owner" "$home" "$timed_checkout" >/dev/null 2>&1; then
    fail "hung Pixel source clone is bounded and rejected"
elif [[ ! -e "$timed_checkout" ]] \
    && ! find "${timed_checkout%/*}" -mindepth 1 -print -quit | grep -q .; then
    pass "hung Pixel source clone is bounded and leaves no partial checkout"
else
    fail "failed Pixel source clone left a partial checkout"
fi

answers="$TEST_ROOT/onboarding.json"
export MAX_CONTEXT=32768
export LLM_MODEL=qwen-test
export LLAMA_REASONING=off
export OLLAMA_PORT=11434
export SEARXNG_PORT=8888
export LITELLM_PORT=4000
export LITELLM_KEY=test-litellm-secret
export ODS_MODEL_SWITCHBOARD=observe
digest="$(printf 'a%.0s' {1..64})"
mkdir -p "$INSTALL_DIR/bin" "$INSTALL_DIR/config" \
    "$INSTALL_DIR/extensions/library/services/notebook" \
    "$INSTALL_DIR/extensions/services/pixel-agent/host" "$INSTALL_DIR/data/pixel"
  cp "$ROOT/bin/ods-pixel-approve" "$INSTALL_DIR/bin/ods-pixel-approve"
  cp "$ROOT/extensions/services/pixel-agent/host/extension_search.py" \
      "$INSTALL_DIR/extensions/services/pixel-agent/host/extension_search.py"
  cp "$ROOT/extensions/services/pixel-agent/host/extension_manager.py" \
      "$INSTALL_DIR/extensions/services/pixel-agent/host/extension_manager.py"
  cp "$ROOT/extensions/services/pixel-agent/host/pixel-extension-manager.service" \
      "$INSTALL_DIR/extensions/services/pixel-agent/host/pixel-extension-manager.service"
  cp "$ROOT/extensions/services/pixel-agent/host/artifact_promoter.py" \
      "$INSTALL_DIR/extensions/services/pixel-agent/host/artifact_promoter.py"
  cp "$ROOT/extensions/services/pixel-agent/host/pixel-artifact-promoter.service" \
      "$INSTALL_DIR/extensions/services/pixel-agent/host/pixel-artifact-promoter.service"
  cp "$ROOT/extensions/services/pixel-agent/host/workspace_preview.py" \
      "$INSTALL_DIR/extensions/services/pixel-agent/host/workspace_preview.py"
  cp "$ROOT/extensions/services/pixel-agent/host/system_observe.py" \
      "$INSTALL_DIR/extensions/services/pixel-agent/host/system_observe.py"
  cp "$ROOT/extensions/services/pixel-agent/host/pixel-workspace-preview.service" \
      "$INSTALL_DIR/extensions/services/pixel-agent/host/pixel-workspace-preview.service"
  cp "$ROOT/extensions/services/pixel-agent/host/pixel-ops-broker-ods.conf" \
      "$INSTALL_DIR/extensions/services/pixel-agent/host/pixel-ops-broker-ods.conf"
  chmod 0644 "$INSTALL_DIR/extensions/services/pixel-agent/host/extension_search.py" \
      "$INSTALL_DIR/extensions/services/pixel-agent/host/extension_manager.py" \
      "$INSTALL_DIR/extensions/services/pixel-agent/host/pixel-extension-manager.service" \
      "$INSTALL_DIR/extensions/services/pixel-agent/host/artifact_promoter.py" \
      "$INSTALL_DIR/extensions/services/pixel-agent/host/pixel-artifact-promoter.service" \
      "$INSTALL_DIR/extensions/services/pixel-agent/host/workspace_preview.py" \
      "$INSTALL_DIR/extensions/services/pixel-agent/host/system_observe.py" \
      "$INSTALL_DIR/extensions/services/pixel-agent/host/pixel-workspace-preview.service" \
      "$INSTALL_DIR/extensions/services/pixel-agent/host/pixel-ops-broker-ods.conf"
  chmod 0755 "$INSTALL_DIR/bin/ods-pixel-approve"
printf '%s\n' 'services: {}' >"$INSTALL_DIR/extensions/library/services/notebook/compose.yaml"
cat >"$INSTALL_DIR/config/extensions-catalog.json" <<'JSON'
{"schema_version":"1.0.0","extensions":[{"id":"notebook","name":"Notebook Lab","description":"Private notebooks for data science.","category":"optional","gpu_backends":["nvidia","amd"],"compose_file":"compose.yaml","depends_on":["litellm"],"env_vars":[{"key":"NOTEBOOK_TOKEN","required":true},{"key":"NOTEBOOK_THEME","required":false}],"tags":["notebook","data-science"],"features":[{"name":"Interactive Notebooks"}]},{"id":"reference-only","name":"Reference Only","description":"Not currently deployable.","category":"optional","gpu_backends":[],"compose_file":"compose.yaml","depends_on":[],"env_vars":[],"tags":[],"features":[]}]}
JSON
extension_catalog="$INSTALL_DIR/data/pixel/extension-catalog.json"
extension_manager_unit="$INSTALL_DIR/data/pixel/extension-manager.service"
artifact_promoter_unit="$INSTALL_DIR/data/pixel/artifact-promoter.service"
workspace_preview_unit="$INSTALL_DIR/data/pixel/workspace-preview.service"
_ods_pixel_write_extension_catalog "$owner" "$home" "$extension_catalog"
_ods_pixel_write_extension_manager_unit "$owner" "$home" "$extension_manager_unit"
_ods_pixel_write_artifact_promoter_unit "$owner" "$home" "$artifact_promoter_unit"
_ods_pixel_write_workspace_preview_unit "$owner" "$home" "$workspace_preview_unit" 9437
check test "$(stat -c '%a' "$extension_catalog")" = 600
check test "$(stat -c '%a' "$extension_manager_unit")" = 600
check test "$(stat -c '%a' "$artifact_promoter_unit")" = 600
check test "$(stat -c '%a' "$workspace_preview_unit")" = 600
check grep -F "User=$owner" "$extension_manager_unit"
check grep -F "Group=pixel-ops" "$extension_manager_unit"
check grep -F "${INSTALL_DIR}/.env" "$extension_manager_unit"
check grep -F " 3002" "$extension_manager_unit"
check grep -F "CapabilityBoundingSet=" "$extension_manager_unit"
check grep -F "ProtectSystem=strict" "$extension_manager_unit"
check grep -F "ProtectProc=invisible" "$extension_manager_unit"
check grep -F "RestrictNamespaces=true" "$extension_manager_unit"
check grep -F "ReadOnlyPaths=/var/lib/pixel-ops-broker/results" "$extension_manager_unit"
check grep -F "InaccessiblePaths=/var/lib/pixel-ops-broker/plans" "$extension_manager_unit"
if grep -F "__PIXEL_" "$artifact_promoter_unit" >/dev/null; then
    fail "artifact promoter placeholders resolved"
else
    pass "artifact promoter placeholders resolved"
fi
check grep -F "User=root" "$artifact_promoter_unit"
check grep -F "$home/.openclaw/workspace-pixel" "$artifact_promoter_unit"
check grep -F "RestrictAddressFamilies=AF_UNIX" "$artifact_promoter_unit"
check grep -F "CapabilityBoundingSet=CAP_CHOWN CAP_DAC_OVERRIDE" "$artifact_promoter_unit"
check grep -F "ReadOnlyPaths=/var/lib/pixel-ops-broker/results /var/lib/pixel-ops-broker/artifacts" "$artifact_promoter_unit"
check grep -F "User=$owner" "$workspace_preview_unit"
check grep -F "Group=ods-pixel" "$workspace_preview_unit"
check grep -F " 9437" "$workspace_preview_unit"
check grep -F "RuntimeDirectoryMode=0750" "$workspace_preview_unit"
check grep -F "BindReadOnlyPaths=\"$home/.openclaw/workspace-pixel\"" "$workspace_preview_unit"
check grep -F "RestrictAddressFamilies=AF_UNIX AF_INET" "$workspace_preview_unit"
check grep -F "IPAddressAllow=localhost" "$workspace_preview_unit"
check grep -F 'HTTP_SOCKET_PATH = pathlib.Path("/run/ods-pixel-preview/http.sock")' \
    "$INSTALL_DIR/extensions/services/pixel-agent/host/workspace_preview.py"
check python3 -c '
import importlib.util,json,sys
catalog=json.load(open(sys.argv[1]))
assert catalog["schemaVersion"] == 1 and catalog["kind"] == "ods-pixel-extension-catalog"
assert len(catalog["sourceSha256"]) == 64
assert catalog["extensions"] == [{"category":"optional","dependsOn":["litellm"],"description":"Private notebooks for data science.","featureNames":["Interactive Notebooks"],"gpuBackends":["amd","nvidia"],"id":"notebook","name":"Notebook Lab","optionalConfiguration":["NOTEBOOK_THEME"],"requiredConfiguration":["NOTEBOOK_TOKEN"],"tags":["data-science","notebook"]}]
spec=importlib.util.spec_from_file_location("extension_search",sys.argv[2]); module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
assert [item["id"] for item in module._matches(catalog["extensions"],"data science")] == ["notebook"]
assert [item["id"] for item in module._matches(catalog["extensions"],"all")] == ["notebook"]
' "$extension_catalog" "$INSTALL_DIR/extensions/services/pixel-agent/host/extension_search.py"
printf '%s\n' '{}' >"$TEST_ROOT/extension-catalog-symlink-target.json"
ln -s "$TEST_ROOT/extension-catalog-symlink-target.json" "$TEST_ROOT/linked-extension-catalog.json"
if _ods_pixel_write_extension_catalog "$owner" "$home" "$TEST_ROOT/linked-extension-catalog.json" >/dev/null 2>&1; then
    fail "symlink extension catalog rejected"
else
    pass "symlink extension catalog rejected"
fi
printf '%s\n' 'unsafe' >"$TEST_ROOT/extension-manager-unit-target.service"
ln -s "$TEST_ROOT/extension-manager-unit-target.service" \
    "$TEST_ROOT/linked-extension-manager.service"
if _ods_pixel_write_extension_manager_unit "$owner" "$home" \
    "$TEST_ROOT/linked-extension-manager.service" >/dev/null 2>&1; then
    fail "symlink extension manager unit rejected"
else
    pass "symlink extension manager unit rejected"
fi
printf '%s\n' 'unsafe' >"$TEST_ROOT/artifact-promoter-unit-target.service"
ln -s "$TEST_ROOT/artifact-promoter-unit-target.service" \
    "$TEST_ROOT/linked-artifact-promoter.service"
if _ods_pixel_write_artifact_promoter_unit "$owner" "$home" \
    "$TEST_ROOT/linked-artifact-promoter.service" >/dev/null 2>&1; then
    fail "symlink artifact promoter unit rejected"
else
    pass "symlink artifact promoter unit rejected"
fi
printf '%s\n' 'unsafe' >"$TEST_ROOT/workspace-preview-unit-target.service"
ln -s "$TEST_ROOT/workspace-preview-unit-target.service" \
    "$TEST_ROOT/linked-workspace-preview.service"
if _ods_pixel_write_workspace_preview_unit "$owner" "$home" \
    "$TEST_ROOT/linked-workspace-preview.service" 9437 >/dev/null 2>&1; then
    fail "symlink workspace preview unit rejected"
else
    pass "symlink workspace preview unit rejected"
fi
operations_policy="$TEST_ROOT/operations-policy.json"
_ods_pixel_write_operations_policy "$owner" "$home" "$operations_policy"
check test "$(stat -c '%a' "$operations_policy")" = 600
check python3 -c '
import json,pathlib,socket,sys
v=json.load(open(sys.argv[1]))
assert v["schemaVersion"] == 2 and v["deployment"] == "ods-default"
assert v["download"]["stagingRoot"] == "/var/lib/pixel-ops-broker/artifacts"
assert v["download"]["maxBytes"] == 536870912 and v["download"]["maxRedirects"] == 5
assert {"example.com","github.com","githubusercontent.com","hf.co","huggingface.co","nodejs.org","npmjs.org","pypi.org","pythonhosted.org"} == set(v["download"]["allowedDomains"])
target=v["targets"]["ods-host"]
assert target["backend"] == "local" and target["expectedHostname"] == socket.gethostname()
assert target["allowRaw"] is True and target["writableRoots"] == [sys.argv[3]]
assert target["defaultCwd"] == "/var/lib/pixel-ops-broker"
assert target["allowedRoots"] == [sys.argv[2],sys.argv[3],"/var/lib/pixel-ops-broker","/run/ods-pixel-manager"]
assert set(target["capabilities"]) == {"inspect","manage-extensions","stage-download","approved-host-command"}
broker=v["targets"]["broker"]
assert broker["backend"] == "local" and broker["environment"] == "lab"
assert broker["expectedHostname"] == socket.gethostname() and broker["allowRaw"] is False
assert broker["allowedRoots"] == ["/var/lib/pixel-ops-broker"]
assert broker["writableRoots"] == ["/var/lib/pixel-ops-broker/artifacts"]
host_inventory={"host.uptime","host.processes","host.services","host.cpu","host.gpu","host.memory","host.storage","host.network-addresses","host.network-routes","host.listening-ports","host.tailscale","host.network-peer"}
assert set(v["actions"]) == {"host.identity","host.kernel","host.architecture","host.platform","host.os-release",*host_inventory,"ods.extensions.search","ods.extensions.list","ods.extensions.inspect","ods.extensions.install","ods.extensions.enable","ods.extensions.disable","ods.extensions.remove"}
for name in {"host.identity","host.kernel","host.architecture","host.platform","host.os-release",*host_inventory,"ods.extensions.search","ods.extensions.list","ods.extensions.inspect"}:
    assert v["actions"][name]["tier"] == "read" and v["actions"][name]["defaultAuthority"] == "observe"
assert v["actions"]["host.identity"]["argv"] == ["/usr/bin/hostname"]
assert v["actions"]["host.kernel"]["argv"] == ["/usr/bin/uname", "-sr"]
assert v["actions"]["host.architecture"]["argv"] == ["/usr/bin/uname", "-m"]
assert v["actions"]["host.platform"]["argv"] == ["/usr/bin/uname", "-a"]
assert v["actions"]["host.uptime"]["argv"] == ["/usr/bin/uptime"]
assert v["actions"]["host.os-release"]["argv"] == ["/usr/bin/cat", "/etc/os-release"]
assert pathlib.Path(v["actions"]["host.processes"]["argv"][0]).name == "ps"
assert v["actions"]["host.processes"]["argv"][1:] == ["-eo","pid=,ppid=,user=,stat=,%cpu=,%mem=,comm=","--sort=-%cpu"]
assert pathlib.Path(v["actions"]["host.services"]["argv"][0]).name == "systemctl"
assert v["actions"]["host.cpu"]["argv"][1:] == ["--json"]
assert v["actions"]["host.gpu"]["argv"] == [str(pathlib.Path("/usr/bin/python3").resolve()), "/usr/local/libexec/ods-pixel-system-observe.py", "gpu"]
assert v["actions"]["host.memory"]["argv"][1:] == ["--bytes"]
assert v["actions"]["host.storage"]["argv"][1:] == ["--block-size=1","--output=fstype,size,used,avail,pcent,target"]
assert v["actions"]["host.network-addresses"]["argv"][1:] == ["-j","address","show"]
assert v["actions"]["host.network-routes"]["argv"][1:] == ["-j","route","show"]
assert v["actions"]["host.listening-ports"]["argv"][1:] == ["-H","-lntu"]
assert v["actions"]["host.tailscale"]["argv"] == [str(pathlib.Path("/usr/bin/python3").resolve()), "/usr/local/libexec/ods-pixel-system-observe.py", "tailscale"]
network_peer=v["actions"]["host.network-peer"]
assert network_peer["parameters"] == {
    "peer":{"pattern":"^[A-Za-z0-9.:-]{1,253}$","maxLength":253},
    "ports":{"pattern":"^[0-9,]{1,47}$","maxLength":47},
}
assert network_peer["argv"] == [str(pathlib.Path("/usr/bin/python3").resolve()), "/usr/local/libexec/ods-pixel-system-observe.py", "network-peer", "{peer}", "{ports}"]
extension_search=v["actions"]["ods.extensions.search"]
assert extension_search["parameters"] == {"query":{"pattern":"^[A-Za-z0-9 _/+:#.-]{1,80}$","maxLength":80}}
assert extension_search["argv"] == [str(pathlib.Path("/usr/bin/python3").resolve()),"/opt/pixel-ops-broker/ods-extension-search.py","/opt/pixel-ops-broker/ods-extension-catalog.json","{query}"]
assert v["actions"]["ods.extensions.list"]["argv"] == [str(pathlib.Path("/usr/bin/python3").resolve()),"/opt/pixel-ops-broker/ods-extension-manager.py","client","/run/ods-pixel-manager/extension-manager.sock","list","all"]
parameter={"serviceId":{"pattern":"^([a-z0-9]|[a-z0-9][a-z0-9._-]{0,62}[a-z0-9])$","maxLength":64}}
for action in ("inspect","install","enable","disable","remove"):
    item=v["actions"][f"ods.extensions.{action}"]
    assert item["parameters"] == parameter
    assert item["argv"] == [str(pathlib.Path("/usr/bin/python3").resolve()),"/opt/pixel-ops-broker/ods-extension-manager.py","client","/run/ods-pixel-manager/extension-manager.sock",action,"{serviceId}"]
assert v["actions"]["ods.extensions.install"] | {"tier":"managed","effect":"manage","defaultAuthority":"propose","idempotent":True,"reversible":True,"rollbackAction":"ods.extensions.remove","verificationAction":"ods.extensions.inspect","timeoutSeconds":900,"exclusiveTarget":True} == v["actions"]["ods.extensions.install"]
assert v["actions"]["ods.extensions.enable"]["rollbackAction"] == "ods.extensions.disable"
assert v["actions"]["ods.extensions.disable"]["rollbackAction"] == "ods.extensions.enable"
assert v["actions"]["ods.extensions.remove"]["tier"] == "change"
assert v["actions"]["ods.extensions.remove"]["effect"] == "change"
assert v["actions"]["ods.extensions.remove"]["defaultAuthority"] == "propose"
assert v["actions"]["ods.extensions.remove"]["reversible"] is False
assert v["authority"]["defaultLevel"] == "propose"
assert v["authority"]["grants"] == [{"id":"ods-approved-downloads","level":"bounded-auto","actions":["download.stage"],"targets":["broker"],"tiers":["staging"],"environments":["lab"],"maxExecutions":100,"windowSeconds":86400,"maxConcurrent":2,"maxRuntimeSeconds":600,"maxFailures":10,"maxArtifactBytes":536870912}]
' "$operations_policy" "$INSTALL_DIR" "$home/.openclaw/workspace-pixel"
printf '%s\n' '{}' > "$TEST_ROOT/policy-symlink-target.json"
ln -s "$TEST_ROOT/policy-symlink-target.json" "$TEST_ROOT/linked-operations-policy.json"
if _ods_pixel_write_operations_policy "$owner" "$home" "$TEST_ROOT/linked-operations-policy.json" >/dev/null 2>&1; then
    fail "symlink Operations policy rejected"
else
    pass "symlink Operations policy rejected"
fi
_ods_pixel_write_onboarding "$owner" "$home" "$answers" /usr/bin/openclaw /opt/ods/pixel-plugin "$digest"
observed_contract_sha256="$(_ods_pixel_contract_sha256 "$owner" "$home" "$answers")"
check test "$observed_contract_sha256" = "$(_ods_pixel_contract_sha256 "$owner" "$home" "$answers")"
check test "${#observed_contract_sha256}" = 64
cp "$operations_policy" "$TEST_ROOT/operations-policy.original.json"
python3 - "$operations_policy" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text(encoding="utf-8"))
value["targets"]["ods-host"]["defaultCwd"] = "/var/lib/pixel-ops-broker/runtime"
path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
chmod 0600 "$operations_policy"
if [[ "$observed_contract_sha256" == "$(_ods_pixel_contract_sha256 "$owner" "$home" "$answers")" ]]; then
    fail "managed contract hash binds Operations policy bytes"
else
    pass "managed contract hash binds Operations policy bytes"
fi
mv "$TEST_ROOT/operations-policy.original.json" "$operations_policy"
chmod 0600 "$operations_policy"
cp "$extension_catalog" "$TEST_ROOT/extension-catalog.original.json"
python3 - "$extension_catalog" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text(encoding="utf-8"))
value["extensions"][0]["description"] = "Changed projection."
path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
PY
chmod 0600 "$extension_catalog"
if [[ "$observed_contract_sha256" == "$(_ods_pixel_contract_sha256 "$owner" "$home" "$answers")" ]]; then
    fail "managed contract hash binds extension catalog bytes"
else
    pass "managed contract hash binds extension catalog bytes"
fi
mv "$TEST_ROOT/extension-catalog.original.json" "$extension_catalog"
chmod 0600 "$extension_catalog"
extension_helper="$INSTALL_DIR/extensions/services/pixel-agent/host/extension_search.py"
cp "$extension_helper" "$TEST_ROOT/extension-search.original.py"
printf '\n# changed\n' >>"$extension_helper"
if [[ "$observed_contract_sha256" == "$(_ods_pixel_contract_sha256 "$owner" "$home" "$answers")" ]]; then
    fail "managed contract hash binds extension helper bytes"
else
    pass "managed contract hash binds extension helper bytes"
fi
mv "$TEST_ROOT/extension-search.original.py" "$extension_helper"
chmod 0644 "$extension_helper"
extension_manager="$INSTALL_DIR/extensions/services/pixel-agent/host/extension_manager.py"
cp "$extension_manager" "$TEST_ROOT/extension-manager.original.py"
printf '\n# changed\n' >>"$extension_manager"
if [[ "$observed_contract_sha256" == "$(_ods_pixel_contract_sha256 "$owner" "$home" "$answers")" ]]; then
    fail "managed contract hash binds extension manager bytes"
else
    pass "managed contract hash binds extension manager bytes"
fi
mv "$TEST_ROOT/extension-manager.original.py" "$extension_manager"
chmod 0644 "$extension_manager"
cp "$extension_manager_unit" "$TEST_ROOT/extension-manager-unit.original.service"
printf '\n# changed\n' >>"$extension_manager_unit"
if [[ "$observed_contract_sha256" == "$(_ods_pixel_contract_sha256 "$owner" "$home" "$answers")" ]]; then
    fail "managed contract hash binds rendered extension manager unit bytes"
else
    pass "managed contract hash binds rendered extension manager unit bytes"
fi
mv "$TEST_ROOT/extension-manager-unit.original.service" "$extension_manager_unit"
chmod 0600 "$extension_manager_unit"
approval_helper="$INSTALL_DIR/bin/ods-pixel-approve"
cp "$approval_helper" "$TEST_ROOT/ods-pixel-approve.original"
printf '\n# changed\n' >>"$approval_helper"
if [[ "$observed_contract_sha256" == "$(_ods_pixel_contract_sha256 "$owner" "$home" "$answers")" ]]; then
    fail "managed contract hash binds approval helper bytes"
else
    pass "managed contract hash binds approval helper bytes"
fi
mv "$TEST_ROOT/ods-pixel-approve.original" "$approval_helper"
chmod 0755 "$approval_helper"
artifact_promoter="$INSTALL_DIR/extensions/services/pixel-agent/host/artifact_promoter.py"
cp "$artifact_promoter" "$TEST_ROOT/artifact-promoter.original.py"
printf '\n# changed\n' >>"$artifact_promoter"
if [[ "$observed_contract_sha256" == "$(_ods_pixel_contract_sha256 "$owner" "$home" "$answers")" ]]; then
    fail "managed contract hash binds artifact promoter bytes"
else
    pass "managed contract hash binds artifact promoter bytes"
fi
mv "$TEST_ROOT/artifact-promoter.original.py" "$artifact_promoter"
chmod 0644 "$artifact_promoter"
cp "$artifact_promoter_unit" "$TEST_ROOT/artifact-promoter-unit.original.service"
printf '\n# changed\n' >>"$artifact_promoter_unit"
if [[ "$observed_contract_sha256" == "$(_ods_pixel_contract_sha256 "$owner" "$home" "$answers")" ]]; then
    fail "managed contract hash binds rendered artifact promoter unit bytes"
else
    pass "managed contract hash binds rendered artifact promoter unit bytes"
fi
mv "$TEST_ROOT/artifact-promoter-unit.original.service" "$artifact_promoter_unit"
chmod 0600 "$artifact_promoter_unit"
workspace_preview="$INSTALL_DIR/extensions/services/pixel-agent/host/workspace_preview.py"
cp "$workspace_preview" "$TEST_ROOT/workspace-preview.original.py"
printf '\n# changed\n' >>"$workspace_preview"
if [[ "$observed_contract_sha256" == "$(_ods_pixel_contract_sha256 "$owner" "$home" "$answers")" ]]; then
    fail "managed contract hash binds workspace preview bytes"
else
    pass "managed contract hash binds workspace preview bytes"
fi
mv "$TEST_ROOT/workspace-preview.original.py" "$workspace_preview"
chmod 0644 "$workspace_preview"
cp "$workspace_preview_unit" "$TEST_ROOT/workspace-preview-unit.original.service"
printf '\n# changed\n' >>"$workspace_preview_unit"
if [[ "$observed_contract_sha256" == "$(_ods_pixel_contract_sha256 "$owner" "$home" "$answers")" ]]; then
    fail "managed contract hash binds rendered workspace preview unit bytes"
else
    pass "managed contract hash binds rendered workspace preview unit bytes"
fi
mv "$TEST_ROOT/workspace-preview-unit.original.service" "$workspace_preview_unit"
chmod 0600 "$workspace_preview_unit"
operations_service_dropin="$INSTALL_DIR/extensions/services/pixel-agent/host/pixel-ops-broker-ods.conf"
cp "$operations_service_dropin" "$TEST_ROOT/pixel-ops-broker-ods.original.conf"
printf '\n# changed\n' >>"$operations_service_dropin"
if [[ "$observed_contract_sha256" == "$(_ods_pixel_contract_sha256 "$owner" "$home" "$answers")" ]]; then
    fail "managed contract hash binds Operations service drop-in bytes"
else
    pass "managed contract hash binds Operations service drop-in bytes"
fi
mv "$TEST_ROOT/pixel-ops-broker-ods.original.conf" "$operations_service_dropin"
chmod 0644 "$operations_service_dropin"
installed_policy="$TEST_ROOT/installed-operations-policy.json"
cp "$operations_policy" "$installed_policy"
chmod 0640 "$installed_policy"
if (
    ods_sudo() { "$@"; }
    _ods_pixel_verify_operations_policy_custody "$owner" "$home" "$operations_policy" \
        "$installed_policy" "$(id -u)"
); then
    pass "matching root-custodied Operations policy accepted"
else
    fail "matching root-custodied Operations policy accepted"
fi
printf '\n' >> "$installed_policy"
if (
    ods_sudo() { "$@"; }
    _ods_pixel_verify_operations_policy_custody "$owner" "$home" "$operations_policy" \
        "$installed_policy" "$(id -u)"
) >/dev/null 2>&1; then
    fail "stale root-custodied Operations policy rejected"
else
    pass "stale root-custodied Operations policy rejected"
fi
rm -f "$installed_policy"
ln -s "$operations_policy" "$installed_policy"
if (
    ods_sudo() { "$@"; }
    _ods_pixel_verify_operations_policy_custody "$owner" "$home" "$operations_policy" \
        "$installed_policy" "$(id -u)"
) >/dev/null 2>&1; then
    fail "symlink root-custodied Operations policy rejected"
else
    pass "symlink root-custodied Operations policy rejected"
fi
rm -f "$installed_policy"
check python3 -c '
import json,sys
v=json.load(open(sys.argv[1]))
assert v["capabilityProfile"] == "engineering-operator"
assert v["modelProvider"] == "ods-gateway"
assert v["modelBaseUrl"] == "http://127.0.0.1:4000/v1"
assert v["modelApiKey"] == "test-litellm-secret"
assert v["modelId"] == "ods/current"
assert v["modelName"] == "ODS Current (qwen-test)"
assert v["modelContextWindow"] == 32768
assert v["modelMaxTokens"] == 8192
assert v["modelReasoning"] is False
assert v["frontierBudgetProfile"] == "starter"
assert v["operationsPolicyFile"] == sys.argv[2]
assert v["gatewayExtensions"] == [{"id":"pixel-ods","path":"/opt/ods/pixel-plugin","sha256":"a"*64,"tools":["pixel_ods_status","pixel_ods_apps_list","pixel_ods_host_observe","pixel_ods_host_command_propose","pixel_ods_evidence_report","pixel_ods_evidence_readback","pixel_ods_web_extract","pixel_ods_download_promote","pixel_ods_workspace_preview"]}]
assert v["operationsLimbEnabled"] is True
assert all(v[name] is False for name in ("emailLimbEnabled","calendarLimbEnabled","socialLimbEnabled","webLimbEnabled","frontierLimbEnabled"))
' "$answers" "$operations_policy"
check test "$(stat -c '%a' "$answers")" = 600
check test -z "$(find "${answers%/*}" -maxdepth 1 -name '.pixel-gateway-key.*' -print -quit)"

ODS_MODEL_SWITCHBOARD=enabled
_ods_pixel_write_onboarding "$owner" "$home" "$TEST_ROOT/switchboard-onboarding.json" \
    /usr/bin/openclaw /opt/ods/pixel-plugin "$digest"
check python3 -c 'import json,sys; v=json.load(open(sys.argv[1])); assert v["modelProvider"] == "ods-gateway" and v["modelId"] == "ods/current" and v["modelName"] == "ODS Current (qwen-test)" and v["modelBaseUrl"] == "http://127.0.0.1:4000/v1"' \
    "$TEST_ROOT/switchboard-onboarding.json"
ODS_MODEL_SWITCHBOARD=observe
if LITELLM_KEY='' _ods_pixel_write_onboarding "$owner" "$home" \
    "$TEST_ROOT/missing-gateway-key-onboarding.json" /usr/bin/openclaw \
    /opt/ods/pixel-plugin "$digest" >/dev/null 2>&1; then
    fail "Pixel onboarding without a LiteLLM key rejected"
else
    pass "Pixel onboarding without a LiteLLM key rejected"
fi
if (
    curl() {
        [[ "$*" != *"test-litellm-secret"* && "$*" == *"@/dev/fd/"* ]] || return 1
        printf '%s\n' '{"data":[{"id":"ods/current"}]}'
    }
    sleep() { :; }
    _ods_pixel_wait_model_gateway "test gateway" 4000 test-litellm-secret ods/current 1
); then
    pass "authenticated Pixel model-gateway alias preflight accepted"
else
    fail "authenticated Pixel model-gateway alias preflight accepted"
fi
if _ods_pixel_wait_model_gateway "test gateway" 4000 "" default 1 >/dev/null 2>&1; then
    fail "Pixel model-gateway preflight without authentication rejected"
else
    pass "Pixel model-gateway preflight without authentication rejected"
fi

cp "$answers" "$TEST_ROOT/gateway-name-control.json"
python3 - "$TEST_ROOT/gateway-name-control.json" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text())
value["modelName"] = "ODS Current (qwen-test)\nforged"
path.write_text(json.dumps(value) + "\n")
PY
chmod 0600 "$TEST_ROOT/gateway-name-control.json"
if _ods_pixel_update_onboarding_model "$owner" "$home" \
    "$TEST_ROOT/gateway-name-control.json" qwen-next >/dev/null 2>&1; then
    fail "forged Pixel gateway model display name rejected"
else
    pass "forged Pixel gateway model display name rejected"
fi

legacy_gateway_answers="$TEST_ROOT/legacy-gateway-onboarding.json"
cp "$answers" "$legacy_gateway_answers"
python3 - "$legacy_gateway_answers" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text())
value["modelId"] = "default"
value["modelName"] = "ODS Default (qwen-test)"
path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
PY
chmod 0600 "$legacy_gateway_answers"
_ods_pixel_update_onboarding_model "$owner" "$home" \
    "$legacy_gateway_answers" 'org/qwen+tools:remote' 131072 8192 true
check python3 -c 'import json,sys; v=json.load(open(sys.argv[1])); assert v["modelId"] == "ods/current" and v["modelName"] == "ODS Current (org/qwen+tools:remote)"' \
    "$legacy_gateway_answers"

MAX_CONTEXT=16384
LLM_MODEL=NVIDIA-Nemotron3-Nano-4B
_ods_pixel_write_onboarding "$owner" "$home" "$TEST_ROOT/nemotron-onboarding.json" \
    /usr/bin/openclaw /opt/ods/pixel-plugin "$digest"
check python3 -c 'import json,sys; v=json.load(open(sys.argv[1])); assert v["modelContextWindow"] == 16384 and v["modelMaxTokens"] == 4096 and v["modelReasoning"] is False' \
    "$TEST_ROOT/nemotron-onboarding.json"
LLAMA_REASONING=on
LLM_MODEL=qwen-reasoning-enabled
_ods_pixel_write_onboarding "$owner" "$home" "$TEST_ROOT/qwen-reasoning-onboarding.json" \
    /usr/bin/openclaw /opt/ods/pixel-plugin "$digest"
check python3 -c 'import json,sys; v=json.load(open(sys.argv[1])); assert v["modelReasoning"] is True' \
    "$TEST_ROOT/qwen-reasoning-onboarding.json"
LLAMA_REASONING=off
MAX_CONTEXT=4096
if _ods_pixel_write_onboarding "$owner" "$home" "$TEST_ROOT/undersized-onboarding.json" \
    /usr/bin/openclaw /opt/ods/pixel-plugin "$digest" >/dev/null 2>&1; then
    pass "Pixel onboarding accepts the minimum 4K adaptive context"
else
    fail "Pixel onboarding accepts the minimum 4K adaptive context"
fi
check python3 -c 'import json,sys; v=json.load(open(sys.argv[1])); assert v["modelContextWindow"] == 4096 and v["modelMaxTokens"] == 1024' \
    "$TEST_ROOT/undersized-onboarding.json"
MAX_CONTEXT=2048
if _ods_pixel_write_onboarding "$owner" "$home" "$TEST_ROOT/below-openclaw-minimum.json" \
    /usr/bin/openclaw /opt/ods/pixel-plugin "$digest" >/dev/null 2>&1; then
    fail "Pixel onboarding below the OpenClaw 4K minimum rejected"
else
    pass "Pixel onboarding below the OpenClaw 4K minimum rejected"
fi
MAX_CONTEXT=32768
LLM_MODEL=qwen-test

runtime_home="$TEST_ROOT/runtime-home"
runtime_config="$runtime_home/.openclaw/openclaw.json"
runtime_validator="$TEST_ROOT/openclaw-validator"
mkdir -p "$runtime_home/.openclaw"
chmod 0700 "$runtime_home/.openclaw"
cat > "$runtime_config" <<'JSON'
{
  "agents": {
    "defaults": {"bootstrapMaxChars": 32000},
    "list": [{
      "id": "pixel",
      "model": "ods-local/qwen-test",
      "tools": {"deny": ["web_fetch", "web_search", "pixel_web_extract"]}
    }]
  },
  "models": {
    "providers": {
      "ods-local": {
        "api": "openai-completions",
        "apiKey": "local-no-auth",
        "baseUrl": "http://127.0.0.1:11434/v1",
        "models": [{
          "id": "qwen-test",
          "name": "ODS Local qwen-test",
          "contextWindow": 32768,
          "maxTokens": 4096
        }]
      }
    }
  },
  "plugins": {
    "entries": {
      "searxng": {
        "enabled": true,
        "config": {"webSearch": {"baseUrl": "http://127.0.0.1:8888"}}
      }
    }
  },
  "session": {"dmScope": "per-account-channel-peer"},
  "tools": {
    "profile": "coding",
    "alsoAllow": ["pixel_web_extract"],
    "sandbox": {"tools": {"allow": ["exec", "pixel_ods_status", "pixel_web_extract"]}},
    "web": {"search": {"provider": "searxng"}}
  }
}
JSON
chmod 0600 "$runtime_config"
cat > "$runtime_validator" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
[[ "$1 $2" == "config validate" ]]
python3 - "$OPENCLAW_CONFIG_PATH" <<'PY'
import json, pathlib, re, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
assert value["agents"]["defaults"]["timeoutSeconds"] == 1800
assert value["agents"]["defaults"]["bootstrapMaxChars"] == 32000
assert value["agents"]["defaults"]["bootstrapTotalMaxChars"] == 96000
assert value["agents"]["defaults"]["contextInjection"] == "continuation-skip"
agent = value["agents"]["list"][0]
provider_id = agent["model"].split("/", 1)[0]
provider = value["models"]["providers"][provider_id]
assert provider["timeoutSeconds"] == 1800
model = provider["models"][0]
assert agent["experimental"] == {"localModelLean": True}
compact_context = model["contextWindow"] < 32768
model_label = "{} {}".format(model.get("id", ""), model.get("name", "")).casefold()
small_model = any(
    float(marker) <= 4
    for marker in re.findall(r"(?<![a-z0-9.])(\d+(?:\.\d+)?)\s*b(?![a-z0-9])", model_label)
)
lean_prompt = compact_context or small_model
assert agent["bootstrapMaxChars"] == (2000 if lean_prompt else 14000)
assert agent["bootstrapTotalMaxChars"] == (6000 if lean_prompt else 36000)
assert agent["contextInjection"] == ("never" if lean_prompt else "continuation-skip")
assert agent["contextLimits"] == {
    "toolResultMaxChars": max(4000, min(16000, model["contextWindow"] // 4)),
}
assert value["tools"]["toolSearch"] == {"enabled": True, "mode": "tools", "searchDefaultLimit": 5, "maxSearchLimit": 10}
assert agent["tools"]["deny"] == []
assert {"pixel_ods_status", "pixel_ods_apps_list", "pixel_ods_host_observe", "pixel_ods_host_command_propose", "pixel_ods_evidence_report", "pixel_ods_evidence_readback", "pixel_ods_web_extract", "pixel_ods_download_promote", "pixel_ods_workspace_preview"}.issubset(value["tools"]["alsoAllow"])
assert {"web_search", "web_fetch", "pixel_ods_status", "pixel_ods_apps_list", "pixel_ods_host_observe", "pixel_ods_host_command_propose", "pixel_ods_evidence_report", "pixel_ods_evidence_readback", "pixel_ods_web_extract", "pixel_ods_download_promote", "pixel_ods_workspace_preview"}.issubset(value["tools"]["sandbox"]["tools"]["allow"])
assert value["plugins"]["entries"]["pixel-ods"]["hooks"]["allowConversationAccess"] is True
assert value["plugins"]["entries"]["pixel-ods"]["config"] == {
    "modelContextWindow": model["contextWindow"],
    "leanPrompt": lean_prompt,
}
assert "pixel_web_extract" not in value["tools"]["alsoAllow"]
assert "pixel_web_extract" not in value["tools"]["sandbox"]["tools"]["allow"]
assert value["tools"]["web"]["fetch"] == {
    "enabled": True,
    "maxChars": 12000,
    "maxCharsCap": 20000,
    "maxResponseBytes": 1000000,
    "timeoutSeconds": 20,
    "cacheTtlMinutes": 15,
    "maxRedirects": 3,
    "readability": True,
    "useTrustedEnvProxy": False,
    "ssrfPolicy": {
        "allowRfc2544BenchmarkRange": False,
        "allowIpv6UniqueLocalRange": False,
    },
}
assert value["tools"]["loopDetection"] == {
    "enabled": True,
    "historySize": 12,
    "warningThreshold": 2,
    "unknownToolThreshold": 2,
    "criticalThreshold": 4,
    "globalCircuitBreakerThreshold": 6,
    "detectors": {
        "genericRepeat": True,
        "knownPollNoProgress": True,
        "pingPong": True,
    },
}
context_window = model["contextWindow"]
assert value["agents"]["defaults"]["compaction"] == {
    "reserveTokens": (context_window + 4 * model["maxTokens"] + 4) // 5,
    "reserveTokensFloor": context_window // 2 if 8192 <= context_window < 32768 else 0,
    "keepRecentTokens": max(512, min(20000, context_window // 16)),
}
if "qwen" in model["id"].lower() and model["reasoning"] is True:
    assert agent["thinkingDefault"] == "low"
    assert model["compat"] == {"thinkingFormat": "qwen-chat-template"}
    assert agent["params"]["chat_template_kwargs"]["enable_thinking"] is True
else:
    assert "thinkingDefault" not in agent
    assert model["reasoning"] is False
    assert "compat" not in model
    if "qwen" in model["id"].lower():
        assert agent["params"]["chat_template_kwargs"]["enable_thinking"] is False
if lean_prompt:
    params = agent["params"]
    assert params["temperature"] == 0.7
    assert params["topP"] == 0.8
    assert params["frequencyPenalty"] == 0.6
    assert params["presencePenalty"] == 0.2
else:
    assert all(name not in agent.get("params", {}) for name in (
        "temperature", "topP", "frequencyPenalty", "presencePenalty"
    ))
assert value["diagnostics"]["stuckSessionAbortMs"] == 1860000
assert value["session"]["writeLock"] == {"maxHoldMs": 1920000, "staleMs": 3600000}
assert value["agents"]["defaults"]["sandbox"]["docker"]["binds"] == [
    "{}:/run/pixel-ods-control:ro".format(
        pathlib.Path.home() / ".openclaw" / ".ods-exec-control"
    )
]
assert value["agents"]["defaults"]["sandbox"]["docker"]["dangerouslyAllowExternalBindSources"] is True
PY
SH
chmod 0755 "$runtime_validator"
runtime_recovery_candidate="$runtime_home/.openclaw/recovery-candidate.json"
cp "$runtime_config" "$runtime_recovery_candidate"
chmod 0600 "$runtime_recovery_candidate"
check test "$(_ods_pixel_apply_runtime_budget "$owner" "$runtime_home" "$runtime_config" "$runtime_validator")" = changed
runtime_sha256="$(sha256sum "$runtime_config" | awk '{print $1}')"
check python3 -c 'import json,sys; v=json.load(open(sys.argv[1])); d=v["agents"]["defaults"]; assert d["timeoutSeconds"] == 1800 and d["bootstrapMaxChars"] == 32000 and d["bootstrapTotalMaxChars"] == 96000 and d["contextInjection"] == "continuation-skip"; assert d["compaction"] == {"reserveTokens":9831,"reserveTokensFloor":0,"keepRecentTokens":2048}; assert d["sandbox"]["docker"]["binds"] == [sys.argv[2] + "/.openclaw/.ods-exec-control:/run/pixel-ods-control:ro"] and d["sandbox"]["docker"]["dangerouslyAllowExternalBindSources"] is True; a=v["agents"]["list"][0]; assert "thinkingDefault" not in a and a["tools"]["deny"] == [] and a["experimental"] == {"localModelLean":True} and a["bootstrapMaxChars"] == 14000 and a["bootstrapTotalMaxChars"] == 36000 and a["contextInjection"] == "continuation-skip" and a["contextLimits"] == {"toolResultMaxChars":8192} and a["params"]["chat_template_kwargs"]["enable_thinking"] is False; assert v["models"]["providers"]["ods-local"]["timeoutSeconds"] == 1800; m=v["models"]["providers"]["ods-local"]["models"][0]; assert m["reasoning"] is False and "compat" not in m; assert v["diagnostics"]["stuckSessionAbortMs"] == 1860000; assert v["session"]["writeLock"] == {"maxHoldMs":1920000,"staleMs":3600000}; assert {"pixel_ods_status","pixel_ods_apps_list","pixel_ods_host_observe","pixel_ods_host_command_propose","pixel_ods_evidence_report","pixel_ods_evidence_readback","pixel_ods_web_extract","pixel_ods_download_promote","pixel_ods_workspace_preview"}.issubset(v["tools"]["alsoAllow"]); assert v["tools"]["toolSearch"] == {"enabled":True,"mode":"tools","searchDefaultLimit":5,"maxSearchLimit":10}; assert {"web_search","web_fetch","pixel_ods_status","pixel_ods_apps_list","pixel_ods_host_observe","pixel_ods_host_command_propose","pixel_ods_evidence_report","pixel_ods_evidence_readback","pixel_ods_web_extract","pixel_ods_download_promote","pixel_ods_workspace_preview"}.issubset(v["tools"]["sandbox"]["tools"]["allow"]) and v["tools"]["loopDetection"]["globalCircuitBreakerThreshold"] == 6; assert v["plugins"]["entries"]["pixel-ods"]["hooks"]["allowConversationAccess"] is True and v["plugins"]["entries"]["pixel-ods"]["config"] == {"modelContextWindow":32768,"leanPrompt":False}; assert v["tools"]["web"]["fetch"]["enabled"] is True and v["tools"]["web"]["fetch"]["maxChars"] == 12000 and v["tools"]["web"]["fetch"]["timeoutSeconds"] == 20 and v["tools"]["web"]["fetch"]["ssrfPolicy"] == {"allowRfc2544BenchmarkRange":False,"allowIpv6UniqueLocalRange":False}' "$runtime_config" "$runtime_home"
check test "$(_ods_pixel_apply_runtime_budget "$owner" "$runtime_home" "$runtime_config" "$runtime_validator")" = unchanged
check test "$(sha256sum "$runtime_config" | awk '{print $1}')" = "$runtime_sha256"
check test "$(_ods_pixel_apply_runtime_budget "$owner" "$runtime_home" "$runtime_recovery_candidate" "$runtime_validator")" = changed
check _ods_pixel_candidate_config_matches_live "$owner" "$runtime_home" "$runtime_recovery_candidate"
check test -z "$(find "$runtime_home/.openclaw" -maxdepth 1 -name '.ods-pixel-runtime-budget.*' -print -quit)"

runtime_compact_config="$runtime_home/.openclaw/compact-context.json"
python3 - "$runtime_config" "$runtime_compact_config" <<'PY'
import json, pathlib, sys

source, destination = map(pathlib.Path, sys.argv[1:])
value = json.loads(source.read_text(encoding="utf-8"))
model = value["models"]["providers"]["ods-local"]["models"][0]
model["contextWindow"] = 8192
model["maxTokens"] = 1024
destination.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
destination.chmod(0o600)
PY
check test "$(_ods_pixel_apply_runtime_budget "$owner" "$runtime_home" "$runtime_compact_config" "$runtime_validator")" = changed
check python3 -c 'import json,sys; v=json.load(open(sys.argv[1])); a=v["agents"]["list"][0]; assert a["bootstrapMaxChars"] == 2000 and a["bootstrapTotalMaxChars"] == 6000 and a["contextInjection"] == "never" and a["contextLimits"] == {"toolResultMaxChars":4000}; assert {k:a["params"][k] for k in ("temperature","topP","frequencyPenalty","presencePenalty")} == {"temperature":0.7,"topP":0.8,"frequencyPenalty":0.6,"presencePenalty":0.2}; assert v["agents"]["defaults"]["compaction"] == {"reserveTokens":2458,"reserveTokensFloor":4096,"keepRecentTokens":512}; assert v["plugins"]["entries"]["pixel-ods"]["config"] == {"modelContextWindow":8192,"leanPrompt":True}' "$runtime_compact_config"

runtime_small_large_config="$runtime_home/.openclaw/small-model-large-context.json"
python3 - "$runtime_config" "$runtime_small_large_config" <<'PY'
import json, pathlib, sys

source, destination = map(pathlib.Path, sys.argv[1:])
value = json.loads(source.read_text(encoding="utf-8"))
model = value["models"]["providers"]["ods-local"]["models"][0]
model.update({
    "id": "qwen3.5-2b-q4",
    "name": "ODS Local qwen3.5-2b-q4",
    "contextWindow": 65536,
    "maxTokens": 4096,
})
value["agents"]["list"][0]["model"] = "ods-local/qwen3.5-2b-q4"
destination.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
destination.chmod(0o600)
PY
check test "$(_ods_pixel_apply_runtime_budget "$owner" "$runtime_home" "$runtime_small_large_config" "$runtime_validator")" = changed
check python3 -c 'import json,sys; v=json.load(open(sys.argv[1])); a=v["agents"]["list"][0]; m=v["models"]["providers"]["ods-local"]["models"][0]; assert m["contextWindow"] == 65536; assert a["bootstrapMaxChars"] == 2000 and a["bootstrapTotalMaxChars"] == 6000 and a["contextInjection"] == "never" and a["contextLimits"] == {"toolResultMaxChars":16000}; assert {k:a["params"][k] for k in ("temperature","topP","frequencyPenalty","presencePenalty")} == {"temperature":0.7,"topP":0.8,"frequencyPenalty":0.6,"presencePenalty":0.2}; assert v["plugins"]["entries"]["pixel-ods"]["config"] == {"modelContextWindow":65536,"leanPrompt":True}' "$runtime_small_large_config"
runtime_full_candidate="$TEST_ROOT/runtime-full-candidate.json"
runtime_transition_answers="$TEST_ROOT/runtime-transition-onboarding.json"
cp "$runtime_config" "$runtime_full_candidate"
cp "$runtime_compact_config" "$runtime_config"
python3 - "$answers" "$runtime_transition_answers" <<'PY'
import json, pathlib, sys
source, target = map(pathlib.Path, sys.argv[1:])
value = json.loads(source.read_text())
value.update({
    "modelProvider": "ods-local",
    "modelId": "qwen-test",
    "modelName": "ODS Local qwen-test",
    "modelBaseUrl": "http://127.0.0.1:11434/v1",
    "modelApiKey": "local-no-auth",
    "modelContextWindow": 32768,
    "modelMaxTokens": 4096,
    "modelReasoning": False,
})
target.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
PY
chmod 0600 "$runtime_transition_answers"
check _ods_pixel_candidate_is_managed_runtime_update "$owner" "$runtime_home" \
    "$runtime_full_candidate" "$runtime_transition_answers"
cp "$runtime_full_candidate" "$runtime_config"
check python3 -c 'import json,sys; v=json.load(open(sys.argv[1])); a=v["agents"]["list"][0]; assert all(k not in a.get("params", {}) for k in ("temperature","topP","frequencyPenalty","presencePenalty"))' "$runtime_config"

runtime_unsafe_bind="$runtime_home/.openclaw/unsafe-bind.json"
python3 - "$runtime_config" "$runtime_unsafe_bind" <<'PY'
import json, pathlib, sys
source, target = map(pathlib.Path, sys.argv[1:])
value = json.loads(source.read_text())
value["agents"]["defaults"]["sandbox"]["docker"]["binds"] = [
    "/:/host:rw"
]
target.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
PY
chmod 0600 "$runtime_unsafe_bind"
if _ods_pixel_apply_runtime_budget "$owner" "$runtime_home" "$runtime_unsafe_bind" \
    "$runtime_validator" >/dev/null 2>&1; then
    fail "unmanaged Pixel sandbox bind rejected"
else
    pass "unmanaged Pixel sandbox bind rejected"
fi

runtime_target="$TEST_ROOT/runtime-target.json"
runtime_link="$TEST_ROOT/runtime-link.json"
cp "$runtime_config" "$runtime_target"
ln -s "$runtime_target" "$runtime_link"
if _ods_pixel_apply_runtime_budget "$owner" "$runtime_home" "$runtime_link" "$runtime_validator" >/dev/null 2>&1; then
    fail "symlink ODS Pixel runtime config rejected"
else
    pass "symlink ODS Pixel runtime config rejected"
fi
chmod 0644 "$runtime_config"
if _ods_pixel_apply_runtime_budget "$owner" "$runtime_home" "$runtime_config" "$runtime_validator" >/dev/null 2>&1; then
    fail "unsafe ODS Pixel runtime config mode rejected"
else
    pass "unsafe ODS Pixel runtime config mode rejected"
fi
chmod 0600 "$runtime_config"

runtime_unvalidated="$runtime_home/.openclaw/unvalidated.json"
python3 - "$runtime_config" "$runtime_unvalidated" <<'PY'
import json, pathlib, sys
source, target = map(pathlib.Path, sys.argv[1:])
value = json.loads(source.read_text())
value["agents"]["defaults"].pop("timeoutSeconds")
value["models"]["providers"]["ods-local"].pop("timeoutSeconds")
value.pop("diagnostics")
value["session"].pop("writeLock")
target.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
PY
chmod 0600 "$runtime_unvalidated"
runtime_unvalidated_sha256="$(sha256sum "$runtime_unvalidated" | awk '{print $1}')"
if _ods_pixel_apply_runtime_budget "$owner" "$runtime_home" "$runtime_unvalidated" /bin/false >/dev/null 2>&1; then
    fail "invalid OpenClaw runtime budget candidate rejected"
else
    pass "invalid OpenClaw runtime budget candidate rejected"
fi
check test "$(sha256sum "$runtime_unvalidated" | awk '{print $1}')" = "$runtime_unvalidated_sha256"
check test -z "$(find "$runtime_home/.openclaw" -maxdepth 1 -name '.ods-pixel-runtime-budget.*' -print -quit)"

reconcile_home="$TEST_ROOT/reconcile-home"
reconcile_answers="$TEST_ROOT/reconcile-onboarding.json"
reconcile_candidate="$TEST_ROOT/reconcile-candidate.json"
reconcile_marker="$reconcile_home/.config/ods/pixel-managed.json"
reconcile_config="$reconcile_home/.openclaw/openclaw.json"
reconcile_ref="$(printf '9%.0s' {1..40})"
mkdir -p "$reconcile_home/.config/ods" "$reconcile_home/.openclaw/backups" \
    "$reconcile_home/.local/share/pixel"
chmod 0700 "$reconcile_home/.openclaw" "$reconcile_home/.openclaw/backups"
chmod 0700 "$reconcile_home/.config/ods"
cp "$answers" "$reconcile_answers"
python3 - "$reconcile_answers" "$reconcile_config" "$reconcile_candidate" <<'PY'
import copy, json, pathlib, sys

answers_path, live_path, candidate_path = map(pathlib.Path, sys.argv[1:])
answers = json.loads(answers_path.read_text())
answers["modelProvider"] = "ods-local"
answers["modelId"] = "qwen-old"
answers["modelName"] = "ODS Local qwen-old"
answers["modelBaseUrl"] = "http://127.0.0.1:11434/v1"
answers["modelApiKey"] = "local-no-auth"
answers_path.write_text(json.dumps(answers, indent=2, sort_keys=True) + "\n")
base = {
    "agents": {
        "defaults": {"bootstrapMaxChars": 32000},
        "list": [{
            "id": "pixel",
            "model": "ods-local/qwen-old",
            "preserve": 7,
            "tools": {"deny": ["web_fetch", "web_search"]},
        }],
    },
    "gateway": {"bind": "loopback"},
    "models": {"providers": {"ods-local": {
        "api": "openai-completions",
        "apiKey": "local-no-auth",
        "baseUrl": "http://127.0.0.1:11434/v1",
        "models": [{
            "id": "qwen-old",
            "name": "ODS Local qwen-old",
            "contextWindow": answers["modelContextWindow"],
            "maxTokens": answers["modelMaxTokens"],
            "reasoning": answers["modelReasoning"],
            "input": ["text"],
        }],
    }}},
    "plugins": {"entries": {"searxng": {
        "enabled": True,
        "config": {"webSearch": {"baseUrl": "http://127.0.0.1:8888"}},
    }}},
    "session": {"dmScope": "per-account-channel-peer"},
    "tools": {
        "profile": "coding",
        "sandbox": {"tools": {"allow": ["exec", "pixel_ods_status"]}},
        "web": {"search": {"provider": "searxng"}},
    },
}
candidate = copy.deepcopy(base)
candidate["agents"]["list"][0]["model"] = "ods-local/qwen-new"
candidate_model = candidate["models"]["providers"]["ods-local"]["models"][0]
candidate_model["id"] = "qwen-new"
candidate_model["name"] = "ODS Local qwen-new"
candidate_model["contextWindow"] = 65536
candidate_model["maxTokens"] = 2048
candidate_model["reasoning"] = True
live_path.write_text(json.dumps(base, indent=2, sort_keys=True) + "\n")
candidate_path.write_text(json.dumps(candidate, indent=2, sort_keys=True) + "\n")
PY
chmod 0600 "$reconcile_answers" "$reconcile_config" "$reconcile_candidate"
check test "$(_ods_pixel_apply_runtime_budget "$owner" "$reconcile_home" "$reconcile_config" "$runtime_validator")" = changed
python3 - "$reconcile_config" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text())
agent = value["agents"]["list"][0]
model = value["models"]["providers"]["ods-local"]["models"][0]
agent.pop("thinkingDefault", None)
model.pop("compat", None)
model["reasoning"] = False
path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
PY
printf '%s\n' '{"kind":"pixel-runtime-attestation"}' > "$reconcile_home/.local/share/pixel/runtime-attestation.json"
chmod 0600 "$reconcile_home/.local/share/pixel/runtime-attestation.json"
python3 - "$reconcile_marker" "$reconcile_config" "$INSTALL_DIR" "$reconcile_ref" <<'PY'
import hashlib, json, pathlib, sys

marker, config, install_dir, source_ref = sys.argv[1:]
value = json.loads(pathlib.Path(config).read_text())
canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
payload = {
    "schema_version": 2,
    "manager": "ods",
    "state": "ready",
    "initial_active_state": "absent",
    "install_dir": install_dir,
    "pixel_source_ref": source_ref,
    "contract_sha256": "a" * 64,
    "configuration_sha256": hashlib.sha256(b"ods-pixel-openclaw-v1\0" + canonical).hexdigest(),
    "active_release_version": "4.3.14",
    "release_identity_sha256": "b" * 64,
    "install_manifest_sha256": "c" * 64,
    "sandbox_image": "openclaw-sandbox:test",
    "sandbox_image_id": "sha256:" + "d" * 64,
}
pathlib.Path(marker).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
PY
chmod 0600 "$reconcile_marker"
check test "$(_ods_pixel_managed_source_ref "$owner" "$reconcile_home")" = "$reconcile_ref"
mkdir -p "$reconcile_home/.config/pixel-deployment"
chmod 0700 "$reconcile_home/.config/pixel-deployment"
cp "$reconcile_answers" "$reconcile_home/.config/pixel-deployment/onboarding.json"
chmod 0600 "$reconcile_home/.config/pixel-deployment/onboarding.json"
reconcile_backup="$(_ods_pixel_model_reconciliation_snapshot "$owner" "$reconcile_home" "$reconcile_answers")"
check cmp -s "$reconcile_backup/installed-onboarding.json" "$reconcile_answers"
check python3 -c 'import json,sys; v=json.load(open(sys.argv[1])); assert v["modelId"] == "qwen-old" and v["modelName"] == "ODS Local qwen-old"' "$reconcile_backup/rollback-onboarding.json"
rm -f "$reconcile_home/.local/share/pixel/runtime-attestation.json"
missing_attestation_backup="$(_ods_pixel_model_reconciliation_snapshot \
    "$owner" "$reconcile_home" "$reconcile_answers")"
check test ! -e "$missing_attestation_backup/runtime-attestation.json"
ln -s "$reconcile_config" "$reconcile_home/.local/share/pixel/runtime-attestation.json"
if _ods_pixel_model_reconciliation_snapshot "$owner" "$reconcile_home" \
    "$reconcile_answers" >/dev/null 2>&1; then
    fail "symlink Pixel runtime attestation rejected during model snapshot"
else
    pass "symlink Pixel runtime attestation rejected during model snapshot"
fi
rm -f "$reconcile_home/.local/share/pixel/runtime-attestation.json"
printf '%s\n' '{"kind":"pixel-runtime-attestation"}' \
    > "$reconcile_home/.local/share/pixel/runtime-attestation.json"
chmod 0600 "$reconcile_home/.local/share/pixel/runtime-attestation.json"
_ods_pixel_update_onboarding_model "$owner" "$reconcile_home" "$reconcile_answers" \
    qwen-new 65536 2048 true
check python3 -c 'import json,sys; v=json.load(open(sys.argv[1])); assert v["modelId"] == "qwen-new" and v["modelName"] == "ODS Local qwen-new" and v["modelContextWindow"] == 65536 and v["modelMaxTokens"] == 2048 and v["modelReasoning"] is True' "$reconcile_answers"
check _ods_pixel_install_onboarding_mirror "$owner" "$reconcile_home" "$reconcile_answers"
check cmp -s "$reconcile_answers" "$reconcile_home/.config/pixel-deployment/onboarding.json"
check test "$(stat -c '%a' "$reconcile_home/.config/pixel-deployment/onboarding.json")" = 600
mv "$reconcile_home/.config/pixel-deployment/onboarding.json" "$TEST_ROOT/mirror-before-symlink"
ln -s "$reconcile_config" "$reconcile_home/.config/pixel-deployment/onboarding.json"
if _ods_pixel_install_onboarding_mirror "$owner" "$reconcile_home" "$reconcile_answers" >/dev/null 2>&1; then
    fail "symlink installed onboarding mirror rejected"
else
    pass "symlink installed onboarding mirror rejected"
fi
rm "$reconcile_home/.config/pixel-deployment/onboarding.json"
mv "$TEST_ROOT/mirror-before-symlink" "$reconcile_home/.config/pixel-deployment/onboarding.json"
if _ods_pixel_update_onboarding_model "$owner" "$reconcile_home" "$reconcile_answers" \
    qwen-invalid 2048 1024 false >/dev/null 2>&1; then
    fail "undersized Pixel model context rejected"
else
    pass "undersized Pixel model context rejected"
fi
check test "$(_ods_pixel_apply_runtime_budget "$owner" "$reconcile_home" "$reconcile_candidate" "$runtime_validator")" = changed
check python3 -c 'import json,sys; v=json.load(open(sys.argv[1])); a=v["agents"]["list"][0]; m=v["models"]["providers"]["ods-local"]["models"][0]; assert m["reasoning"] is True and m["compat"] == {"thinkingFormat":"qwen-chat-template"} and a["thinkingDefault"] == "low" and a["params"]["chat_template_kwargs"]["enable_thinking"] is True' "$reconcile_candidate"
check _ods_pixel_candidate_is_managed_runtime_update "$owner" "$reconcile_home" "$reconcile_candidate" "$reconcile_answers"
cp "$reconcile_config" "$TEST_ROOT/reconcile-config-with-control-bind.json"
python3 - "$reconcile_config" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text())
value["agents"]["defaults"].pop("sandbox", None)
path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
PY
chmod 0600 "$reconcile_config"
check _ods_pixel_candidate_is_managed_runtime_update "$owner" "$reconcile_home" "$reconcile_candidate" "$reconcile_answers"
cp "$TEST_ROOT/reconcile-config-with-control-bind.json" "$reconcile_config"
chmod 0600 "$reconcile_config"
python3 - "$reconcile_candidate" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text())
value["gateway"]["bind"] = "lan"
path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
PY
if _ods_pixel_candidate_is_managed_runtime_update "$owner" "$reconcile_home" "$reconcile_candidate" "$reconcile_answers" >/dev/null 2>&1; then
    fail "unmanaged Pixel candidate change rejected"
else
    pass "unmanaged Pixel candidate change rejected"
fi
python3 - "$reconcile_candidate" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text())
value["gateway"]["bind"] = "loopback"
path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
PY
chmod 0600 "$reconcile_candidate"
check _ods_pixel_atomic_replace_managed_file "$owner" "$reconcile_home" "$reconcile_candidate" "$reconcile_config"
check _ods_pixel_candidate_config_matches_live "$owner" "$reconcile_home" "$reconcile_candidate"
linked_reconcile_candidate="$TEST_ROOT/reconcile-candidate-link.json"
ln -s "$reconcile_candidate" "$linked_reconcile_candidate"
if _ods_pixel_atomic_replace_managed_file "$owner" "$reconcile_home" "$linked_reconcile_candidate" "$reconcile_config" >/dev/null 2>&1; then
    fail "symlink model reconciliation source rejected"
else
    pass "symlink model reconciliation source rejected"
fi

# A model-family change is part of the supported ODS swap contract, not only a
# Qwen-to-Qwen rename. The exact model limits may change and Qwen-only runtime
# policy must disappear, while every unrelated Pixel field stays identical.
non_qwen_answers="$TEST_ROOT/non-qwen-onboarding.json"
non_qwen_candidate="$TEST_ROOT/non-qwen-candidate.json"
cp "$reconcile_answers" "$non_qwen_answers"
cp "$reconcile_config" "$non_qwen_candidate"
_ods_pixel_update_onboarding_model "$owner" "$reconcile_home" "$non_qwen_answers" \
    phi-4-mini 128000 4096 false
python3 - "$non_qwen_candidate" <<'PY'
import json, pathlib, sys

path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text())
agent = next(item for item in value["agents"]["list"] if item.get("id") == "pixel")
model = value["models"]["providers"]["ods-local"]["models"][0]
agent["model"] = "ods-local/phi-4-mini"
agent.pop("thinkingDefault", None)
model.update({
    "id": "phi-4-mini",
    "name": "ODS Local phi-4-mini",
    "contextWindow": 128000,
    "maxTokens": 4096,
    "reasoning": False,
})
model.pop("compat", None)
path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
PY
chmod 0600 "$non_qwen_answers" "$non_qwen_candidate"
check test "$(_ods_pixel_apply_runtime_budget "$owner" "$reconcile_home" "$non_qwen_candidate" "$runtime_validator")" = changed
check _ods_pixel_candidate_is_managed_runtime_update "$owner" "$reconcile_home" \
    "$non_qwen_candidate" "$non_qwen_answers"
check _ods_pixel_atomic_replace_managed_file "$owner" "$reconcile_home" \
    "$non_qwen_candidate" "$reconcile_config"
check python3 -c 'import json,sys; v=json.load(open(sys.argv[1])); a=v["agents"]["list"][0]; m=v["models"]["providers"]["ods-local"]["models"][0]; assert m["id"] == "phi-4-mini" and m["contextWindow"] == 128000 and m["maxTokens"] == 4096 and m["reasoning"] is False and "compat" not in m and "thinkingDefault" not in a and "params" not in a' "$reconcile_config"

# Migrate an already verified direct llama.cpp route to the authenticated ODS
# model gateway. Only the provider route, stable alias, concrete-model metadata,
# and deterministic runtime policy may change; every unrelated Pixel field must
# remain byte-equivalent after normalization. The rollback contract must still
# describe the exact prior direct route.
gateway_answers="$TEST_ROOT/gateway-reconcile-onboarding.json"
gateway_candidate="$TEST_ROOT/gateway-reconcile-candidate.json"
cp "$answers" "$gateway_answers"
_ods_pixel_update_onboarding_model "$owner" "$reconcile_home" "$gateway_answers" \
    qwen-gateway 65536 4096 false
python3 - "$reconcile_config" "$gateway_candidate" <<'PY'
import json, pathlib, sys

source, target = map(pathlib.Path, sys.argv[1:])
value = json.loads(source.read_text())
providers = value["models"]["providers"]
provider = providers.pop("ods-local")
providers["ods-gateway"] = provider
provider["apiKey"] = "test-litellm-secret"
provider["baseUrl"] = "http://127.0.0.1:4000/v1"
model = provider["models"][0]
model.update({
    "id": "ods/current",
    "name": "ODS Current (qwen-gateway)",
    "contextWindow": 65536,
    "maxTokens": 4096,
    "reasoning": False,
})
model.pop("compat", None)
agent = next(item for item in value["agents"]["list"] if item.get("id") == "pixel")
agent["model"] = "ods-gateway/ods/current"
agent.pop("thinkingDefault", None)
agent["params"] = {"chat_template_kwargs": {"enable_thinking": False}}
value["agents"]["defaults"]["compaction"] = {
    "reserveTokens": (model["contextWindow"] + 4 * model["maxTokens"] + 4) // 5,
    "reserveTokensFloor": 0,
    "keepRecentTokens": 4096,
}
target.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
PY
chmod 0600 "$gateway_answers" "$gateway_candidate"
gateway_budget_status="$(_ods_pixel_apply_runtime_budget "$owner" "$reconcile_home" \
    "$gateway_candidate" "$runtime_validator")"
check test "$gateway_budget_status" = changed
check _ods_pixel_candidate_is_managed_runtime_update "$owner" "$reconcile_home" \
    "$gateway_candidate" "$gateway_answers"
cp "$reconcile_config" "$TEST_ROOT/pre-gateway-alias-config.json"
cp "$gateway_candidate" "$reconcile_config"
chmod 0600 "$reconcile_config"
check _ods_pixel_uses_stable_model_alias "$owner" "$reconcile_home" "$gateway_answers"
check _ods_pixel_stable_alias_matches_promoted_model "$owner" "$reconcile_home" \
    "$gateway_answers" qwen-gateway 65536 4096 false
stale_alias_answers="$TEST_ROOT/stale-alias-onboarding.json"
cp "$gateway_answers" "$stale_alias_answers"
_ods_pixel_update_onboarding_model "$owner" "$reconcile_home" "$stale_alias_answers" \
    qwen-gateway-next 32768 4096 false
staged_alias_candidate="$(_ods_pixel_stage_stable_alias_candidate \
    "$owner" "$reconcile_home" "$stale_alias_answers")"
check test "$(_ods_pixel_apply_runtime_budget "$owner" "$reconcile_home" \
    "$staged_alias_candidate" "$runtime_validator")" = changed
check _ods_pixel_candidate_is_managed_runtime_update "$owner" "$reconcile_home" \
    "$staged_alias_candidate" "$stale_alias_answers"
check python3 -c 'import json,sys; v=json.load(open(sys.argv[1])); m=v["models"]["providers"]["ods-gateway"]["models"][0]; assert m["id"] == "ods/current" and m["name"] == "ODS Current (qwen-gateway-next)" and m["contextWindow"] == 32768 and m["maxTokens"] == 4096 and m["reasoning"] is False; assert v["agents"]["defaults"]["compaction"] == {"reserveTokens":9831,"reserveTokensFloor":0,"keepRecentTokens":2048}; assert v["agents"]["list"][0]["contextLimits"] == {"toolResultMaxChars":8192}' \
    "$staged_alias_candidate"
rm -f -- "$staged_alias_candidate"
if _ods_pixel_stable_alias_matches_promoted_model "$owner" "$reconcile_home" \
    "$gateway_answers" qwen-gateway-next 32768 4096 false >/dev/null 2>&1; then
    fail "stable Pixel alias rejected stale concrete-model limits"
else
    pass "stable Pixel alias detects stale concrete-model limits"
fi
cp "$TEST_ROOT/pre-gateway-alias-config.json" "$reconcile_config"
chmod 0600 "$reconcile_config"
if _ods_pixel_uses_stable_model_alias "$owner" "$reconcile_home" "$gateway_answers" >/dev/null 2>&1; then
    fail "stable Pixel model alias rejected a direct-model live route"
else
    pass "stable Pixel model alias rejects a direct-model live route"
fi
python3 - "$reconcile_marker" "$reconcile_config" <<'PY'
import hashlib, json, pathlib, sys

marker_path, config_path = map(pathlib.Path, sys.argv[1:])
marker = json.loads(marker_path.read_text())
config = json.loads(config_path.read_text())
canonical = json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
marker["configuration_sha256"] = hashlib.sha256(b"ods-pixel-openclaw-v1\0" + canonical).hexdigest()
marker_path.write_text(json.dumps(marker, indent=2, sort_keys=True) + "\n")
PY
chmod 0600 "$reconcile_marker"
gateway_migration_backup="$(_ods_pixel_model_reconciliation_snapshot \
    "$owner" "$reconcile_home" "$gateway_answers")"
check python3 -c 'import json,sys; v=json.load(open(sys.argv[1])); assert v["modelProvider"] == "ods-local" and v["modelId"] == "phi-4-mini" and v["modelName"] == "ODS Local phi-4-mini" and v["modelBaseUrl"] == "http://127.0.0.1:11434/v1" and v["modelApiKey"] == "local-no-auth" and v["modelContextWindow"] == 128000 and v["modelMaxTokens"] == 4096 and v["modelReasoning"] is False' \
    "$gateway_migration_backup/rollback-onboarding.json"
cp "$gateway_candidate" "$TEST_ROOT/gateway-candidate-unsafe-route.json"
python3 - "$TEST_ROOT/gateway-candidate-unsafe-route.json" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text())
value["models"]["providers"]["ods-gateway"]["baseUrl"] = "http://example.invalid/v1"
path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
PY
chmod 0600 "$TEST_ROOT/gateway-candidate-unsafe-route.json"
if _ods_pixel_candidate_is_managed_runtime_update "$owner" "$reconcile_home" \
    "$TEST_ROOT/gateway-candidate-unsafe-route.json" "$gateway_answers" >/dev/null 2>&1; then
    fail "non-loopback Pixel gateway migration rejected"
else
    pass "non-loopback Pixel gateway migration rejected"
fi
_ods_pixel_update_onboarding_model "$owner" "$reconcile_home" "$gateway_answers" \
    qwen-gateway-next 131072 8192 true
check python3 -c 'import json,sys; v=json.load(open(sys.argv[1])); assert v["modelProvider"] == "ods-gateway" and v["modelId"] == "ods/current" and v["modelName"] == "ODS Current (qwen-gateway-next)" and v["modelContextWindow"] == 131072 and v["modelMaxTokens"] == 8192 and v["modelReasoning"] is True' \
    "$gateway_answers"
_ods_pixel_update_onboarding_model "$owner" "$reconcile_home" "$gateway_answers" \
    'org/qwen+tools:remote' 131072 8192 true
check python3 -c 'import json,sys; v=json.load(open(sys.argv[1])); assert v["modelId"] == "ods/current" and v["modelName"] == "ODS Current (org/qwen+tools:remote)"' \
    "$gateway_answers"
_ods_pixel_update_onboarding_model "$owner" "$reconcile_home" "$gateway_answers" \
    'My Custom Model (Q4_K_M).gguf' 32768 4096 false
check python3 -c 'import json,sys; v=json.load(open(sys.argv[1])); assert v["modelId"] == "ods/current" and v["modelName"] == "ODS Current (My Custom Model (Q4_K_M).gguf)" and v["modelContextWindow"] == 32768' \
    "$gateway_answers"
check test "$(GGUF_FILE='My Custom Model (Q4_K_M).gguf' \
    LLM_MODEL='friendly-library-alias' GPU_BACKEND=nvidia \
    _ods_pixel_runtime_model_identity)" = 'My Custom Model (Q4_K_M).gguf'
check test "$(GGUF_FILE='My Custom Model (Q4_K_M).gguf' \
    LLM_MODEL='friendly-library-alias' GPU_BACKEND=amd LLM_BACKEND=lemonade \
    AMD_INFERENCE_RUNTIME=lemonade \
    LEMONADE_MODEL='extra.My Custom Model (Q4_K_M).gguf' \
    _ods_pixel_runtime_model_identity)" = 'extra.My Custom Model (Q4_K_M).gguf'
check test "$(EXTERNAL_LLM_URL='http://10.0.2.2:18080' \
    EXTERNAL_LLM_MODEL='org/qwen+tools:remote' GGUF_FILE='stale-local.gguf' \
    _ods_pixel_runtime_model_identity)" = 'org/qwen+tools:remote'
if EXTERNAL_LLM_URL='http://10.0.2.2:18080' EXTERNAL_LLM_MODEL='' \
    _ods_pixel_runtime_model_identity >/dev/null 2>&1; then
    fail "external runtime identity requires the selected upstream model"
else
    pass "external runtime identity requires the selected upstream model"
fi

linked_answers="$TEST_ROOT/onboarding-linked.json"
printf '%s\n' 'sentinel' > "$TEST_ROOT/onboarding-link-target"
ln -s "$TEST_ROOT/onboarding-link-target" "$linked_answers"
if _ods_pixel_write_onboarding "$owner" "$home" "$linked_answers" /usr/bin/openclaw /opt/ods/pixel-plugin "$digest" >/dev/null 2>&1; then
    fail "symlink Pixel onboarding contract rejected"
else
    pass "symlink Pixel onboarding contract rejected"
fi
check test "$(cat "$TEST_ROOT/onboarding-link-target")" = sentinel

original_run_as_owner="$(declare -f ods_pixel_run_as_owner)"
mock_ingress_attempts="$TEST_ROOT/ingress-attempts"
ods_pixel_run_as_owner() {
    local count=0
    [[ -f "$mock_ingress_attempts" ]] && read -r count < "$mock_ingress_attempts"
    count=$((count + 1))
    printf '%s\n' "$count" > "$mock_ingress_attempts"
    (( count >= 3 )) || return 7
    printf '%s\n' '{"status":"ok"}'
}
check _ods_pixel_wait_ingress "$owner" "$home" 3 0
check test "$(cat "$mock_ingress_attempts")" = 3
ods_pixel_run_as_owner() { printf '%s\n' '{"status":"starting"}'; }
if _ods_pixel_wait_ingress "$owner" "$home" 2 0; then
    fail "non-ready Pixel ingress status rejected"
else
    pass "non-ready Pixel ingress status rejected"
fi
eval "$original_run_as_owner"

# The long-lived host agent normally lacks an active sudo credential. Prove
# its fallback can only terminate the same-owner PID of the exact hardened
# Restart=always unit, then waits for systemd to replace it before verification.
gateway_mock_root="$TEST_ROOT/gateway-restart-mock"
mkdir -p "$gateway_mock_root"
printf '0\n' > "$gateway_mock_root/mainpid-calls"
systemctl() {
    case "$*" in
        'show openclaw-gateway.service -p MainPID --value')
            local count
            read -r count < "$gateway_mock_root/mainpid-calls"
            count=$((count + 1))
            printf '%s\n' "$count" > "$gateway_mock_root/mainpid-calls"
            if (( count <= 2 )); then printf '111\n'; else printf '222\n'; fi
            ;;
        'show openclaw-gateway.service -p User --value') printf '%s\n' "$owner" ;;
        'show openclaw-gateway.service -p Restart --value') printf 'always\n' ;;
        'is-active --quiet openclaw-gateway.service') return 0 ;;
        *) return 1 ;;
    esac
}
id() {
    case "$1" in
        -u) printf '1000\n' ;;
        -un) printf '%s\n' "$owner" ;;
        *) return 1 ;;
    esac
}
awk() {
    [[ "$*" == *'/proc/111/status'* ]] || return 1
    printf '1000\n'
}
kill() {
    printf '%s\n' "$*" >> "$gateway_mock_root/kills"
}
curl() {
    printf '%s\n' '{"ok":true,"status":"live"}'
}
jq() {
    command jq "$@"
}
sleep() { :; }
ods_sudo_available() { return 1; }
ods_pixel_run_as_owner() {
    printf '%s\n' "$*" >> "$gateway_mock_root/owner-runs"
}
check _ods_pixel_restart_gateway_and_verify "$owner" "$reconcile_home" /verified/pixel
check test "$(cat "$gateway_mock_root/kills")" = '-TERM 111'
check grep -F '/verified/pixel/pixel verify' "$gateway_mock_root/owner-runs"

# A mismatched systemd User must fail before any signal is sent.
printf '0\n' > "$gateway_mock_root/mainpid-calls"
: > "$gateway_mock_root/kills"
systemctl() {
    case "$*" in
        'show openclaw-gateway.service -p MainPID --value') printf '111\n' ;;
        'show openclaw-gateway.service -p User --value') printf 'someone-else\n' ;;
        'show openclaw-gateway.service -p Restart --value') printf 'always\n' ;;
        *) return 1 ;;
    esac
}
if _ods_pixel_restart_gateway_and_verify "$owner" "$reconcile_home" /verified/pixel >/dev/null 2>&1; then
    fail "mismatched Pixel gateway unit owner rejected"
else
    pass "mismatched Pixel gateway unit owner rejected"
fi
check test ! -s "$gateway_mock_root/kills"
unset -f systemctl id awk kill curl jq sleep ods_sudo_available
eval "$original_run_as_owner"

plugin="$ROOT/extensions/services/pixel-agent/plugin"
check node --check "$plugin/index.js"
check node --check "$plugin/projection.mjs"
check node --check "$plugin/prompt-contract.mjs"
check node --check "$plugin/tool-content.mjs"
check node --check "$plugin/tool-loop-guard.mjs"
check node --check "$plugin/web-extract.mjs"
check node --check "$plugin/workspace-preview.mjs"
check node --check "$plugin/download-promote.mjs"
check node --check "$plugin/host-observe.mjs"
check python3 -m py_compile "$ROOT/extensions/services/pixel-agent/host/artifact_promoter.py"
check sh -n "$ROOT/extensions/services/pixel-agent/host/cancellable-exec.sh"
check bash -n "$ROOT/extensions/services/pixel-agent/host/noninteractive-sudo.sh"
check python3 -c '
import pathlib,sys
lines=[line for line in pathlib.Path(sys.argv[1]).read_text().splitlines() if line and not line.startswith("#")]
assert lines[:5] == ["[Service]","RestrictAddressFamilies=","RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK AF_VSOCK","PrivateDevices=false","DevicePolicy=closed"]
assert "DeviceAllow=/dev/dxg rw" in lines
assert "DeviceAllow=/dev/nvidiactl rw" in lines
assert "DeviceAllow=/dev/nvidia0 rw" in lines
assert all(line.startswith("DeviceAllow=/dev/") for line in lines[5:])
' "$ROOT/extensions/services/pixel-agent/host/pixel-ops-broker-ods.conf"
check python3 -c '
import pathlib,sys
text=pathlib.Path(sys.argv[1]).read_text()
assert "trusted_sudo=/usr/bin/sudo" in text
assert "if [[ $# == 1 && \"$1\" == -v ]]" in text
assert "exec \"$trusted_sudo\" -n \"$@\"" in text
' "$ROOT/extensions/services/pixel-agent/host/noninteractive-sudo.sh"
check python3 -c '
import json,sys
p=json.load(open(sys.argv[1])); m=json.load(open(sys.argv[2]))
assert p["type"] == "module" and p["openclaw"]["extensions"] == ["./index.js"]
assert "dependencies" not in p
assert sorted(m["contracts"]["tools"]) == ["pixel_ods_apps_list","pixel_ods_download_promote","pixel_ods_evidence_readback","pixel_ods_evidence_report","pixel_ods_host_command_propose","pixel_ods_host_observe","pixel_ods_status","pixel_ods_web_extract","pixel_ods_workspace_preview"]
import re
reserved = re.compile(r"^pixel_(?:gmail|calendar|social|web|ops|frontier)_")
assert all(name != "pixel_limb_status" and not reserved.match(name) for name in m["contracts"]["tools"])
assert m["toolMetadata"] == {
    "pixel_ods_status": {"replaySafe": True},
    "pixel_ods_apps_list": {"replaySafe": True},
    "pixel_ods_host_observe": {"replaySafe": True},
}
' "$plugin/package.json" "$plugin/openclaw.plugin.json"
check python3 -c '
import pathlib,sys
text=pathlib.Path(sys.argv[1]).read_text()
assert "api.on(\"before_prompt_build\"" in text
assert "api.on(\"model_call_started\"" in text
assert "api.on(\"before_agent_finalize\"" in text
assert "api.on(\"tool_result_persist\"" in text
assert "promptContractForAgent(context, AGENT_ID, event, {" in text
assert "verificationStatus: toolLoopGuard.verificationStatus(context?.runId)" in text
' "$plugin/index.js"
# Dollar expressions below are literal source-code assertions.
# shellcheck disable=SC2016
check python3 -c '
import pathlib,sys
text=pathlib.Path(sys.argv[1]).read_text()
installer=text[text.index("ods_pixel_install_default_agent() {"):]
assert "ods_pixel_run_as_owner \"$owner\" \"$home\" curl" in text
assert "_ods_pixel_wait_ingress \"$owner\" \"$home\"" in installer
assert installer.index("_ods_pixel_wait_ingress \"$owner\" \"$home\"") < installer.index("_ods_pixel_mark_ready \"$owner\" \"$home\"")
assert "pixel\" configure --answers \"$answers\" --force" in text
assert "pixel\" plan" in text
assert "Pixel configure failed. See $pixel_log" in text
assert "Pixel plan failed. See $pixel_log" in text
assert "Pixel configure or plan failed" not in text
assert text.count("pixel\" ops-broker --confirm") == 2
assert "Pixel could not install and verify the isolated Operations Broker" in text
assert text.count("_ods_pixel_verify_operations_policy_custody \"$owner\" \"$home\" \"$operations_policy\"") == 2
assert "PATH=\"$home/.openclaw/.ods-exec-control:$PATH\"" in text
assert "pixel\" apply --confirm </dev/null &&" in text
assert "_ods_pixel_write_operations_policy" in text
assert "Could not write the owner-private ODS Pixel Operations policy" in text
assert "_ods_pixel_write_extension_catalog" in text
assert "secret-free ODS extension catalog" in text
assert "_ods_pixel_write_extension_manager_unit" in text
assert "owner-private ODS Pixel extension manager service" in text
assert "_ods_pixel_write_artifact_promoter_unit" in text
assert "owner-private ODS Pixel artifact promoter service" in text
assert "ods-pixel-contract-v9" in text
assert "ods-pixel-system-observe.py" in text
assert "pixel-ops-broker-ods.conf" in text
for family in ("AF_UNIX", "AF_INET", "AF_INET6", "AF_NETLINK", "AF_VSOCK"):
    assert family in text
assert "CapabilityBoundingSet --value" in text
assert "bin/ods-pixel-approve" in text
assert "ods_sudo install -o root -g pixel-ops -m 0640 \"$extension_catalog\" \"$installed_extension_catalog\"" in text
assert "ods-extension-manager.py" in text
assert "pixel-extension-manager.service" in text
assert "manage-extensions" in text
assert "ods_sudo -u pixel-ops-broker /usr/bin/python3" in text
assert "_ods_pixel_wait_extension_manager_probe" in text
assert "_ods_pixel_wait_artifact_promoter_probe" in text
assert "_ods_pixel_wait_workspace_preview_probe" in text
assert "_ods_pixel_managed_contract_matches" in text
assert "_ods_pixel_verified_source_matches" in text
assert "_ods_pixel_candidate_config_matches_live" in text
assert "_ods_pixel_apply_runtime_budget" in text
assert "_ods_pixel_install_exec_control" in text
assert "_ods_pixel_recreate_agent_sandbox" in text
assert "_ods_pixel_refresh_plugin_registry" in text
assert "plugins registry --refresh --json" in text
assert "ods_pixel_reconcile_promoted_model" in text
stable_alias = text.index("if _ods_pixel_uses_stable_model_alias")
reconcile_snapshot = text.index("_ods_pixel_model_reconciliation_snapshot", stable_alias)
reconcile_restart = text.index("_ods_pixel_restart_gateway_and_verify", reconcile_snapshot)
assert stable_alias < reconcile_snapshot < reconcile_restart
stable_branch = text[stable_alias:reconcile_snapshot]
assert "Pixel stable model alias remains active" in stable_branch
assert "_ods_pixel_restart_gateway_and_verify" not in stable_branch
assert "\"$pixel_root/pixel\" verify" not in stable_branch
assert "_ods_pixel_wait_ingress \"$owner\" \"$home\" 6 1" in stable_branch
assert "_ods_pixel_verify_plugin_loaded \"$owner\" \"$home\" \"$openclaw_bin\"" in stable_branch
assert "if _ods_pixel_managed_contract_matches \"$owner\" \"$home\" \"$contract_sha256\"; then" in stable_branch
assert "_ods_pixel_managed_contract_matches \"$owner\" \"$home\" \"$contract_sha256\" || return 1" not in stable_branch
assert stable_branch.index("_ods_pixel_managed_contract_matches") < stable_branch.index("_ods_pixel_wait_ingress")
assert stable_branch.index("_ods_pixel_wait_ingress") < stable_branch.index("_ods_pixel_verify_plugin_loaded")
assert stable_branch.index("_ods_pixel_verify_plugin_loaded") < stable_branch.index("_ods_pixel_mark_ready")
assert stable_branch.index("Pixel stable model alias remains active") < stable_branch.index("stable_alias=true")
assert "failure_phase=\"onboarding-update\"" in text
assert "failure_phase=\"onboarding-mirror-install\"" in text
mirror_install = text.index("&& ! _ods_pixel_install_onboarding_mirror", reconcile_snapshot)
assert reconcile_snapshot < mirror_install < reconcile_restart
assert "installed-onboarding.json" in text
assert "failure_phase=\"pixel-configure\"" in text
assert "failure_phase=\"pixel-plan\"" in text
assert "failure_phase=\"stable-alias-candidate\"" in text
assert "failure_phase=\"runtime-budget\"" in text
assert "failure_phase=\"managed-update-validation\"" in text
assert "failure_phase=\"config-install\"" in text
assert "failure_phase=\"gateway-restart-verify\"" in text
assert "failure_phase=\"ingress-runtime-refresh\"" in text
assert "failure_phase=\"sandbox-recreate\"" in text
assert "failure_phase=\"contract-hash\"" in text
assert "failure_phase=\"ready-marker\"" in text
assert "failure_phase=\"installing-marker\"" in text
assert "rollback=verified" in text
assert "rollback=failed" in text
assert installer.index("if _ods_pixel_verified_source_matches") < installer.index("_ods_pixel_mark_installing")
assert "The exact ODS-managed Pixel contract is already active" in text
assert "refreshing the verified ODS extension without reapplying the release" in text
assert "repairing the interrupted ownership checkpoint" in text
candidate_recovery = installer.index("Could not validate the exact-source Pixel runtime candidate")
model_reconcile = installer.index("ods_pixel_reconcile_promoted_model", candidate_recovery)
assert installer.index("_ods_pixel_apply_runtime_budget", candidate_recovery - 1000) < candidate_recovery < model_reconcile
resume_start = installer.index("if [[ \"$same_source_resume\" == true ]]")
resume_end = installer.index("ai \"The exact Pixel release is active with an older ODS route", resume_start)
resume = installer[resume_start:resume_end]
stop = resume.index("systemctl stop openclaw-gateway.service")
retire = resume.index("_ods_pixel_recreate_agent_sandbox \"$owner\" \"$home\" \"$openclaw_bin\"")
start = resume.index("systemctl start openclaw-gateway.service", retire)
health = resume.index("_ods_pixel_wait_http \"Pixel gateway\"", start)
verify = resume.index("\"$pixel_root/pixel\" verify", health)
assert stop < retire < start < health < verify
helper_start = text.index("_ods_pixel_recreate_agent_sandbox()")
helper_end = text.index("_ods_pixel_apply_runtime_budget()", helper_start)
helper = text[helper_start:helper_end]
assert helper.index("sandbox recreate --agent pixel --force") < helper.index("docker ps --all --quiet")
for diagnostic in (
    "could not enter maintenance mode",
    "could not retire its stale agent sandbox",
    "could not restart after sandbox recovery",
    "did not become healthy after sandbox recovery",
    "failed verification",
):
    assert diagnostic in resume
assert "pixel\" verify >>\"$pixel_log\"" in text
assert "if ! _ods_pixel_install_ingress" in text
assert "systemctl restart pixel-ingress.service" in text
assert "if ! _ods_pixel_mark_verified_installing" in text
assert text.index("_ods_pixel_mark_verified_installing \"$owner\"") < text.index("_ods_pixel_install_ingress \"$owner\"")
assert "if ! _ods_pixel_mark_ready" in text
runtime_overlay = installer.index("runtime_budget_status=\"")
runtime_checkpoint = installer.index(
    "Could not bind the verified Pixel ODS managed-runtime configuration.",
    runtime_overlay,
)
registry_refresh = installer.index("_ods_pixel_refresh_plugin_registry", runtime_overlay)
assert runtime_overlay < runtime_checkpoint < registry_refresh
assert installer.index("_ods_pixel_refresh_plugin_registry") < installer.index("_ods_pixel_mark_ready")
assert "ods_linux_node_tools_available" in text
assert "runtime_token_file=\"/run/ods-pixel/openclaw.json\"" in text
assert "PIXEL_GATEWAY_TOKEN_FILE=$runtime_token_file" in text
assert "PIXEL_ODS_VERSION=$ods_version" in text
assert "PIXEL_ODS_N8N_PORT=${N8N_PORT:-5678}" in text
assert "PIXEL_ODS_WHISPER_PORT=${WHISPER_PORT:-9000}" in text
prerequisites = installer.index("litellm searxng dashboard-api")
control_health = installer.index("_ods_pixel_wait_http \"ODS control API\"", prerequisites)
bootstrap = installer.index("ai \"Bootstrapping the exact Pixel source", control_health)
assert prerequisites < control_health < bootstrap
assert "exact ODS prerequisite services" in installer
' "$ROOT/installers/lib/pixel-host-install.sh"
check python3 -c '
import pathlib,sys
phase=pathlib.Path(sys.argv[1]).read_text()
handoff = (
    "ods_pixel_activate_source_contract \\\n"
    "            \"$PIXEL_SOURCE_URL_VALUE\" \"$PIXEL_SOURCE_REF_VALUE\" \"$PIXEL_SOURCE_DIR_VALUE\""
)
assert handoff in phase
assert phase.index(handoff) < phase.index("PIXEL_SOURCE_URL=$(dotenv_quote")
preflight = phase.index("_phase06_step \"preflight-pixel-source\"")
checkout = phase.index("if ! _ods_pixel_source_checkout", preflight)
assert phase.index(handoff) < preflight < checkout < phase.index("PIXEL_SOURCE_URL=$(dotenv_quote")
assert "Pixel source is unavailable. Configure authorized Git access" in phase
assert "PIXEL_SOURCE_REF \"70f44c90ac40b8409ebc965becc5b085a053e270\"" in phase
' "$ROOT/installers/phases/06-directories.sh"
check python3 -c '
import pathlib,sys
text=pathlib.Path(sys.argv[1]).read_text()
assert "ProtectHome=true" in text
assert "RestrictNamespaces=true" in text
assert "RuntimeDirectoryPreserve=restart" in text
assert "BindReadOnlyPaths=__PIXEL_GATEWAY_TOKEN_SOURCE__:__PIXEL_GATEWAY_TOKEN_FILE__" in text
' "$ROOT/extensions/services/pixel-agent/host/pixel-ingress.service"
check python3 -c '
import pathlib,sys
text=pathlib.Path(sys.argv[1]).read_text()
assert "ProtectHome=tmpfs" in text
assert "BindReadOnlyPaths=\"__ODS_INSTALL_DIR__\"" in text
assert "IPAddressDeny=any" in text and "IPAddressAllow=localhost" in text
assert "RestrictAddressFamilies=AF_UNIX AF_INET" in text
' "$ROOT/extensions/services/pixel-agent/host/pixel-extension-manager.service"
check python3 -c '
import pathlib,sys
text=pathlib.Path(sys.argv[1]).read_text()
assert "User=root" in text
assert "RestrictAddressFamilies=AF_UNIX" in text
assert "CapabilityBoundingSet=CAP_CHOWN CAP_DAC_OVERRIDE" in text
assert "BindPaths=\"__PIXEL_WORKSPACE__\"" in text
assert "ReadOnlyPaths=/var/lib/pixel-ops-broker/results /var/lib/pixel-ops-broker/artifacts" in text
assert "IPAddressAllow" not in text
' "$ROOT/extensions/services/pixel-agent/host/pixel-artifact-promoter.service"
check python3 -c '
import pathlib,sys
text=pathlib.Path(sys.argv[1]).read_text()
assert "User=__PIXEL_SERVICE_USER__" in text
assert "Group=__PIXEL_SERVICE_USER__" not in text
assert "BindReadOnlyPaths=\"__PIXEL_WORKSPACE__\"" in text
assert "IPAddressDeny=any" in text and "IPAddressAllow=localhost" in text
assert "RestrictAddressFamilies=AF_UNIX AF_INET" in text
assert "CapabilityBoundingSet=" in text
' "$ROOT/extensions/services/pixel-agent/host/pixel-workspace-preview.service"
check python3 -c '
import pathlib,sys
text=pathlib.Path(sys.argv[1]).read_text()
reconcile=text.index("if ! reconcile_ods_managed_pixel_model")
discard=text.index("discard_active_model_config_snapshot", reconcile)
cleanup=text.index("# ── Phase 5b: Remove bootstrap model", reconcile)
assert reconcile < discard < cleanup
' "$ROOT/scripts/bootstrap-upgrade.sh"

printf '\nResults: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
