#!/usr/bin/env bash
# Remove only a Pixel deployment whose private management marker binds it to
# the ODS install being uninstalled. Importing this file has no side effects.

# The standalone uninstaller provides these loggers, while install-core uses a
# different logging API. Keep this library safe in either caller without
# overriding a logger that the caller already supplied.
if ! declare -F log_info >/dev/null 2>&1; then
    log_info() { printf '[INFO] %s\n' "$*"; }
fi
if ! declare -F log_ok >/dev/null 2>&1; then
    log_ok() { printf '[OK] %s\n' "$*"; }
fi
if ! declare -F log_error >/dev/null 2>&1; then
    log_error() { printf '[ERROR] %s\n' "$*" >&2; }
fi

ods_pixel_uninstall_managed() {
    local install_dir="$1" owner_home="$2"
    local marker="$owner_home/.config/ods/pixel-managed.json"
    local systemd_dir="${ODS_PIXEL_UNINSTALL_SYSTEMD_DIR:-/etc/systemd/system}"
    local etc_dir="${ODS_PIXEL_UNINSTALL_ETC_DIR:-/etc/ods}"
    local libexec_dir="${ODS_PIXEL_UNINSTALL_LIBEXEC_DIR:-/usr/local/libexec}"
    local root_uid="${ODS_PIXEL_UNINSTALL_ROOT_UID:-0}"
    local root_gid="${ODS_PIXEL_UNINSTALL_ROOT_GID:-0}"
    local gateway_unit="$systemd_dir/openclaw-gateway.service"
    local ingress_unit="$systemd_dir/pixel-ingress.service"
    local ingress_env="$etc_dir/pixel-agent.env"
    local ingress_program="$libexec_dir/ods-pixel-ingress.mjs"
    local source_program="$install_dir/extensions/services/pixel-agent/host/pixel_ingress.mjs"
    local extension_manager_unit="$systemd_dir/pixel-extension-manager.service"
    local extension_manager_program="$libexec_dir/ods-pixel-extension-manager.py"
    local extension_manager_source="$install_dir/extensions/services/pixel-agent/host/extension_manager.py"
    local extension_manager_owner_unit="$install_dir/data/pixel/extension-manager.service"
    local approval_source="$install_dir/bin/ods-pixel-approve"
    local artifact_promoter_unit="$systemd_dir/pixel-artifact-promoter.service"
    local artifact_promoter_program="$libexec_dir/ods-pixel-artifact-promoter.py"
    local artifact_promoter_source="$install_dir/extensions/services/pixel-agent/host/artifact_promoter.py"
    local artifact_promoter_owner_unit="$install_dir/data/pixel/artifact-promoter.service"
    local workspace_preview_unit="$systemd_dir/pixel-workspace-preview.service"
    local workspace_preview_program="$libexec_dir/ods-pixel-workspace-preview.py"
    local workspace_preview_source="$install_dir/extensions/services/pixel-agent/host/workspace_preview.py"
    local workspace_preview_owner_unit="$install_dir/data/pixel/workspace-preview.service"
    local workspace_preview_state="${ODS_PIXEL_UNINSTALL_PREVIEW_STATE_DIR:-/var/lib/ods-pixel-preview}"
    local system_observer_program="$libexec_dir/ods-pixel-system-observe.py"
    local system_observer_source="$install_dir/extensions/services/pixel-agent/host/system_observe.py"
    local ops_user="pixel-ops-broker"
    local ops_group="pixel-ops"
    local ops_unit="$systemd_dir/pixel-ops-broker.service"
    local ops_dropin_dir="$systemd_dir/pixel-ops-broker.service.d"
    local ops_dropin="$ops_dropin_dir/10-ods-host-observation.conf"
    local ops_dropin_source="$install_dir/extensions/services/pixel-agent/host/pixel-ops-broker-ods.conf"
    local ops_env="${ODS_PIXEL_UNINSTALL_OPS_ENV:-/etc/pixel-ops-broker.env}"
    local ops_policy="${ODS_PIXEL_UNINSTALL_OPS_POLICY:-/etc/pixel-ops-broker/policy.json}"
    local ops_policy_dir="${ops_policy%/*}"
    local ops_install="${ODS_PIXEL_UNINSTALL_OPS_INSTALL_DIR:-/opt/pixel-ops-broker}"
    local ops_program="$ops_install/broker.py"
    local ops_extension_program="$ops_install/ods-extension-search.py"
    local ops_extension_catalog="$ops_install/ods-extension-catalog.json"
    local ops_extension_manager="$ops_install/ods-extension-manager.py"
    local ops_state="${ODS_PIXEL_UNINSTALL_OPS_STATE_DIR:-/var/lib/pixel-ops-broker}"
    local ops_owner_policy="$install_dir/data/pixel/operations-policy.json"
    local ops_owner_extension_catalog="$install_dir/data/pixel/extension-catalog.json"
    local ops_extension_source_program="$install_dir/extensions/services/pixel-agent/host/extension_search.py"
    local openclaw_config="$owner_home/.openclaw/openclaw.json"
    local exec_control="$owner_home/.openclaw/.ods-exec-control"
    local gateway_env="$owner_home/.config/pixel-agent/gateway.env"
    local onboarding="$owner_home/.config/pixel-deployment/onboarding.json"
    local pixel_install="$owner_home/.local/share/pixel"
    local current="$pixel_install/current"
    local runtime_attestation="$pixel_install/runtime-attestation.json"
    local staged_current="$pixel_install/.ods-uninstall-current"
    local staged_attestation="$pixel_install/.ods-uninstall-runtime-attestation"
    local deployment_lock="$pixel_install/.deployment.lock"
    local retired_releases="$pixel_install/retired-ods-releases"
    local cleanup_plan cleanup_state release_version sandbox_image sandbox_image_id release_path marker_state pixel_source_ref
    local release_identity_sha256 install_manifest_sha256 retired_release_path
    local ops_plan="absent||||" ops_state_status ops_uid ops_gid ops_user_present ops_group_present
    local ops_passwd_entry="" ops_group_entry="" ops_user_group_ids="" ops_user_group_names="" ops_artifacts_present=false
    local pixel_lock_fd="" owner_uid
    local root_artifacts_present=false owner_gid

    [[ "$install_dir" == /* && "$install_dir" != / && -d "$install_dir" && ! -L "$install_dir" ]] || {
        log_error "Refusing Pixel cleanup for an invalid ODS install directory"
        return 1
    }
    [[ "$owner_home" == /* && "$owner_home" != / && -d "$owner_home" && ! -L "$owner_home" ]] || {
        log_error "Refusing Pixel cleanup for an invalid owner home"
        return 1
    }
    [[ "$root_uid" =~ ^[0-9]+$ && "$root_gid" =~ ^[0-9]+$ ]] || return 1
    for path in "$ops_unit" "$ops_dropin_dir" "$ops_dropin" "$ops_dropin_source" \
        "$ops_env" "$ops_policy" "$ops_install" "$ops_program" \
        "$ops_extension_program" "$ops_extension_catalog" "$ops_extension_manager" "$ops_state" \
        "$extension_manager_unit" "$extension_manager_program" \
        "$artifact_promoter_unit" "$artifact_promoter_program" \
        "$workspace_preview_unit" "$workspace_preview_program" "$workspace_preview_state" \
        "$system_observer_program"; do
        [[ "$path" == /* && "$path" != / ]] || {
            log_error "Refusing Pixel Operations cleanup for an invalid absolute target"
            return 1
        }
    done
    [[ "$ops_program" == "$ops_install/broker.py" \
        && "$ops_unit" == "$systemd_dir/pixel-ops-broker.service" \
        && "$ops_dropin" == "$ops_dropin_dir/10-ods-host-observation.conf" \
        && "$ops_dropin_dir" == "$systemd_dir/pixel-ops-broker.service.d" \
        && "$ops_policy" == "$ops_policy_dir/policy.json" \
        && "${ops_policy_dir##*/}" == pixel-ops-broker \
        && "${ops_install##*/}" == pixel-ops-broker \
        && "${ops_state##*/}" == pixel-ops-broker \
        && "$ops_policy" != "$ops_state"/* \
        && "$ops_install" != "$ops_state"/* ]] || {
        log_error "Refusing Pixel Operations cleanup for overlapping targets"
        return 1
    }
    [[ "${workspace_preview_state##*/}" == ods-pixel-preview ]] || {
        log_error "Refusing Pixel workspace preview cleanup for an unexpected state root"
        return 1
    }

    if [[ ! -e "$marker" && ! -L "$marker" ]]; then
        return 0
    fi

    # Validate every deletion target before stopping services or removing any
    # file. The marker must be private, owner-controlled, and bind this exact
    # install. Root artifacts must still match the ODS/Pixel contract; drift
    # fails closed instead of deleting an ambient or operator-modified service.
    owner_uid="$(id -u)"
    owner_gid="$(id -g)"
    if ! cleanup_plan="$(python3 - \
        "$marker" "$install_dir" "$owner_home" "$(id -u)" "$root_uid" \
        "$gateway_unit" "$ingress_unit" "$ingress_env" "$ingress_program" "$source_program" \
        "$extension_manager_unit" "$extension_manager_program" "$extension_manager_source" \
        "$extension_manager_owner_unit" "$approval_source" \
        "$artifact_promoter_unit" "$artifact_promoter_program" "$artifact_promoter_source" \
        "$artifact_promoter_owner_unit" "$workspace_preview_unit" "$workspace_preview_program" \
        "$workspace_preview_source" "$workspace_preview_owner_unit" "$workspace_preview_state" \
        "$system_observer_program" "$system_observer_source" \
        "$openclaw_config" "$gateway_env" "$onboarding" "$exec_control" "$ops_owner_policy" \
        "$ops_owner_extension_catalog" "$ops_extension_source_program" "$ops_dropin_source" \
        "$current" "$runtime_attestation" "$staged_current" "$staged_attestation" "$deployment_lock" \
        "$retired_releases" <<'PY'
import hashlib
import json
import os
import pathlib
import re
import stat
import sys

(
    marker_raw,
    install_raw,
    home_raw,
    owner_uid_raw,
    root_uid_raw,
    gateway_unit_raw,
    ingress_unit_raw,
    ingress_env_raw,
    ingress_program_raw,
    source_program_raw,
    extension_manager_unit_raw,
    extension_manager_program_raw,
    extension_manager_source_raw,
    extension_manager_owner_unit_raw,
    approval_source_raw,
    artifact_promoter_unit_raw,
    artifact_promoter_program_raw,
    artifact_promoter_source_raw,
    artifact_promoter_owner_unit_raw,
    workspace_preview_unit_raw,
    workspace_preview_program_raw,
    workspace_preview_source_raw,
    workspace_preview_owner_unit_raw,
    workspace_preview_state_raw,
    system_observer_program_raw,
    system_observer_source_raw,
    openclaw_config_raw,
    gateway_env_raw,
    onboarding_raw,
    exec_control_raw,
    ops_owner_policy_raw,
    ops_owner_extension_catalog_raw,
    ops_extension_source_program_raw,
    ops_dropin_source_raw,
    current_raw,
    runtime_attestation_raw,
    staged_current_raw,
    staged_attestation_raw,
    deployment_lock_raw,
    retired_releases_raw,
) = sys.argv[1:]

marker = pathlib.Path(marker_raw)
install_dir = pathlib.Path(install_raw)
owner_home = pathlib.Path(home_raw)
owner_uid = int(owner_uid_raw)
root_uid = int(root_uid_raw)


def regular(path: pathlib.Path, uid: int, maximum: int, private: bool = False) -> os.stat_result:
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != uid
        or info.st_size > maximum
        or info.st_mode & 0o022
        or (private and info.st_mode & 0o077)
    ):
        raise SystemExit(f"unsafe managed Pixel artifact: {path}")
    return info


regular(marker, owner_uid, 65536, private=True)
value = json.loads(marker.read_text(encoding="utf-8"))
if not isinstance(value, dict):
    raise SystemExit("Pixel management marker does not bind this ODS install")
state = value.get("state")
if (
    value.get("schema_version") not in {1, 2}
    or value.get("manager") != "ods"
    or value.get("install_dir") != str(install_dir)
    or state not in {"installing", "ready", "deactivating"}
):
    raise SystemExit("Pixel management marker does not bind this ODS install")
source_ref = value.get("pixel_source_ref")
if not isinstance(source_ref, str) or len(source_ref) != 40 or any(c not in "0123456789abcdef" for c in source_ref):
    raise SystemExit("Pixel management marker has an invalid source binding")

current = pathlib.Path(current_raw)
runtime_attestation = pathlib.Path(runtime_attestation_raw)
staged_current = pathlib.Path(staged_current_raw)
staged_attestation = pathlib.Path(staged_attestation_raw)
deployment_lock = pathlib.Path(deployment_lock_raw)
retired_releases = pathlib.Path(retired_releases_raw)
if retired_releases.exists() or retired_releases.is_symlink():
    retired_root_info = retired_releases.lstat()
    if (not stat.S_ISDIR(retired_root_info.st_mode) or stat.S_ISLNK(retired_root_info.st_mode)
            or retired_root_info.st_uid != owner_uid or retired_root_info.st_mode & 0o077):
        raise SystemExit("unsafe ODS-managed Pixel retired release root")
current_present = current.exists() or current.is_symlink()
attestation_present = runtime_attestation.exists() or runtime_attestation.is_symlink()
staged_current_present = staged_current.exists() or staged_current.is_symlink()
staged_attestation_present = staged_attestation.exists() or staged_attestation.is_symlink()
if current_present and not current.is_symlink():
    raise SystemExit("Pixel current active-release object is not a symlink")
if staged_current_present and not staged_current.is_symlink():
    raise SystemExit("Pixel staged active-release object is not a symlink")
retired_release_raw = value.get("retired_release_path")
if state == "deactivating":
    # The marker is changed before the release move, so both the pre-move and
    # post-move layouts are valid resumable states. The release must exist in
    # exactly one of those locations.
    if value.get("schema_version") != 2 or value.get("initial_active_state") != "absent":
        raise SystemExit("Pixel deactivation state lacks an ODS pre-install absence proof")
    if current_present or attestation_present or staged_current_present != staged_attestation_present:
        raise SystemExit("Pixel deactivation state is partial or still active")
    if not isinstance(retired_release_raw, str) or "|" in retired_release_raw:
        raise SystemExit("Pixel deactivation marker has an invalid retired release path")
    retired_release = pathlib.Path(retired_release_raw)
    try:
        if retired_release.name != "release" or retired_release.parent.parent != retired_releases:
            raise ValueError
        if not re.fullmatch(r"[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}-[0-9a-f]{12}\.[A-Za-z0-9]{8}", retired_release.parent.name):
            raise ValueError
    except ValueError:
        raise SystemExit("Pixel retired release path is outside its bounded archive root")
    retired_root_info = retired_releases.lstat()
    retired_container_info = retired_release.parent.lstat()
    if (not stat.S_ISDIR(retired_root_info.st_mode) or stat.S_ISLNK(retired_root_info.st_mode)
            or retired_root_info.st_uid != owner_uid or retired_root_info.st_mode & 0o077
            or not stat.S_ISDIR(retired_container_info.st_mode) or stat.S_ISLNK(retired_container_info.st_mode)
            or retired_container_info.st_uid != owner_uid or retired_container_info.st_mode & 0o077):
        raise SystemExit("unsafe ODS-managed Pixel retired release archive")
    version = value.get("active_release_version")
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}", version):
        raise SystemExit("Pixel deactivation marker has an invalid release version")
    identity_prefix = value.get("release_identity_sha256")
    if (not isinstance(identity_prefix, str) or not re.fullmatch(r"[0-9a-f]{64}", identity_prefix)
            or not retired_release.parent.name.startswith(f"{version}-{identity_prefix[:12]}.")):
        raise SystemExit("Pixel retired release archive is not bound to its release identity")
    expected_release = pathlib.Path(home_raw) / ".local/share/pixel/releases" / version
    live_release_present = expected_release.exists() or expected_release.is_symlink()
    retired_release_present = retired_release.exists() or retired_release.is_symlink()
    if int(live_release_present) + int(retired_release_present) != 1:
        raise SystemExit("Pixel deactivation release is missing or duplicated")
    if staged_current_present:
        link_info = staged_current.lstat()
        if not stat.S_ISLNK(link_info.st_mode) or link_info.st_uid != owner_uid:
            raise SystemExit("unsafe ODS-managed Pixel staged active-release link")
        if staged_current.resolve(strict=False) != expected_release.resolve(strict=False):
            raise SystemExit("Pixel staged active-release link has an unexpected target")
    release = expected_release if live_release_present else retired_release
    cleanup = (
        "retiring" if live_release_present else "retired",
        None, None, None, None, None, None, str(retired_release),
    )
elif not any((current_present, attestation_present, staged_current_present, staged_attestation_present)):
    cleanup = ("none", "", "", "", "", "", "", "")
else:
    if ((int(current_present) + int(staged_current_present) != 1)
            or (int(attestation_present) + int(staged_attestation_present) != 1)):
        raise SystemExit("Pixel active-release state is partial, duplicated, or has an unsafe type")
    if current_present and attestation_present:
        cleanup = ("active", None, None, None, None, None, None, "")
    elif current_present and staged_attestation_present:
        cleanup = ("staging-attestation", None, None, None, None, None, None, "")
    elif staged_current_present and attestation_present:
        cleanup = ("staging-link", None, None, None, None, None, None, "")
    else:
        cleanup = ("staged", None, None, None, None, None, None, "")

if cleanup[0] != "none":
    if value.get("schema_version") != 2 or value.get("initial_active_state") != "absent":
        raise SystemExit("Pixel active state lacks an ODS pre-install absence proof")
    receipt = None
    if cleanup[0] not in {"retiring", "retired"}:
        link = current if cleanup[0] in {"active", "staging-attestation"} else staged_current
        receipt = runtime_attestation if cleanup[0] in {"active", "staging-link"} else staged_attestation
        link_info = link.lstat()
        if not stat.S_ISLNK(link_info.st_mode) or link_info.st_uid != owner_uid:
            raise SystemExit("unsafe ODS-managed Pixel active-release link")
        release = link.resolve(strict=True)
    elif staged_attestation_present:
        receipt = staged_attestation
    releases_root = pathlib.Path(home_raw) / ".local/share/pixel/releases"
    releases_info = releases_root.lstat()
    release_info = release.lstat()
    if (not stat.S_ISDIR(releases_info.st_mode) or stat.S_ISLNK(releases_info.st_mode)
            or releases_info.st_uid != owner_uid or releases_info.st_mode & 0o022
            or not stat.S_ISDIR(release_info.st_mode) or stat.S_ISLNK(release_info.st_mode)
            or release_info.st_uid != owner_uid or release_info.st_mode & 0o022):
        raise SystemExit("ODS-managed Pixel release is outside its owner-controlled release root")
    if cleanup[0] in {"retiring", "active", "staging-attestation", "staging-link", "staged"}:
        if release.parent.resolve(strict=True) != releases_root.resolve(strict=True):
            raise SystemExit("ODS-managed Pixel release is outside its owner-controlled release root")
    elif release != pathlib.Path(retired_release_raw):
        raise SystemExit("ODS-managed Pixel retired release path changed")
    identity_path = release / "release-identity.json"
    manifest_path = release / "install-manifest.sha256"
    regular(identity_path, owner_uid, 65536)
    regular(manifest_path, owner_uid, 2 * 1024 * 1024)
    if receipt is not None:
        regular(receipt, owner_uid, 2 * 1024 * 1024, private=True)
    regular(deployment_lock, owner_uid, 65536, private=True)
    identity_bytes = identity_path.read_bytes()
    manifest_bytes = manifest_path.read_bytes()
    attestation_bytes = receipt.read_bytes() if receipt is not None else None
    identity = json.loads(identity_bytes)
    attestation = json.loads(attestation_bytes) if attestation_bytes is not None else None
    version = value.get("active_release_version")
    identity_sha256 = hashlib.sha256(identity_bytes).hexdigest()
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    if (not isinstance(version, str) or not re.fullmatch(r"[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}", version)
            or (cleanup[0] != "retired" and release.name != version) or identity.get("pixel") != version
            or not isinstance(identity.get("source"), dict)
            or identity["source"].get("state") != "git-clean"
            or identity["source"].get("commit") != source_ref
            or value.get("release_identity_sha256") != identity_sha256
            or value.get("install_manifest_sha256") != manifest_sha256):
        raise SystemExit("ODS marker does not bind the active Pixel release identity")
    if receipt is not None and (not isinstance(attestation, dict) or attestation.get("kind") != "pixel-runtime-attestation"
            or attestation.get("status") not in {"verified", "limited"}
            or attestation.get("pixel") != version or attestation.get("source") != identity.get("source")
            or not isinstance(attestation.get("release"), dict)
            or attestation["release"].get("sourceIdentitySha256") != identity_sha256
            or attestation["release"].get("installManifestSha256") != manifest_sha256):
        raise SystemExit("Pixel runtime attestation does not bind the ODS-managed active release")
    sandbox_image = value.get("sandbox_image")
    sandbox_image_id = value.get("sandbox_image_id")
    if (not isinstance(sandbox_image, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,255}:[A-Za-z0-9][A-Za-z0-9._-]{0,127}", sandbox_image)
            or not isinstance(sandbox_image_id, str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", sandbox_image_id)
            or "|" in str(release)):
        raise SystemExit("ODS marker has an invalid Pixel sandbox binding")
    cleanup = (
        cleanup[0], version, sandbox_image, sandbox_image_id, str(release),
        identity_sha256, manifest_sha256, cleanup[7],
    )

openclaw_config = pathlib.Path(openclaw_config_raw)
gateway_env = pathlib.Path(gateway_env_raw)
onboarding = pathlib.Path(onboarding_raw)
ops_owner_policy = pathlib.Path(ops_owner_policy_raw)
ops_owner_extension_catalog = pathlib.Path(ops_owner_extension_catalog_raw)
ops_extension_source_program = pathlib.Path(ops_extension_source_program_raw)
for path in (openclaw_config, gateway_env, onboarding):
    if path.exists() or path.is_symlink():
        regular(path, owner_uid, 2 * 1024 * 1024, private=True)

exec_control = pathlib.Path(exec_control_raw)
if exec_control.exists() or exec_control.is_symlink():
    control_info = exec_control.lstat()
    if (not stat.S_ISDIR(control_info.st_mode) or stat.S_ISLNK(control_info.st_mode)
            or control_info.st_uid != owner_uid
            or (control_info.st_mode & 0o777) != 0o700):
        raise SystemExit("unsafe ODS-managed Pixel execution control root")
    for item in exec_control.iterdir():
        item_info = item.lstat()
        is_wrapper = item.name in {"cancellable-exec.sh", "sudo"}
        is_marker = bool(re.fullmatch(r"[0-9a-f]{64}\.cancel", item.name))
        is_temporary = bool(re.fullmatch(r"\.[0-9a-f]{64}\.[0-9]+\.[0-9a-f]{16}\.tmp", item.name))
        if (not stat.S_ISREG(item_info.st_mode) or stat.S_ISLNK(item_info.st_mode)
                or item_info.st_nlink != 1 or item_info.st_uid != owner_uid
                or item_info.st_mode & 0o077 or not (is_wrapper or is_marker or is_temporary)):
            raise SystemExit("unsafe ODS-managed Pixel execution control artifact")
        if is_wrapper and (item_info.st_mode & 0o777) != 0o500:
            raise SystemExit("unsafe ODS-managed Pixel execution wrapper")
        if not is_wrapper and item_info.st_size != 0:
            raise SystemExit("unsafe ODS-managed Pixel execution marker")
    wrapper = exec_control / "cancellable-exec.sh"
    sudo_adapter = exec_control / "sudo"
    if not wrapper.exists() or not sudo_adapter.exists():
        raise SystemExit("ODS-managed Pixel execution control is incomplete")

if openclaw_config.exists():
    config = json.loads(openclaw_config.read_text(encoding="utf-8"))
    serialized_config = json.dumps(config, sort_keys=True, separators=(",", ":"))
    bootstrap_config = {
        "gateway": {
            "http": {
                "endpoints": {
                    "chatCompletions": {"enabled": True},
                },
            },
        },
    }
    bootstrap_marker = {
        "schema_version": 2,
        "manager": "ods",
        "state": "installing",
        "initial_active_state": "absent",
        "install_dir": str(install_dir),
        "pixel_source_ref": source_ref,
    }
    # ODS enables the loopback chat endpoint immediately before Pixel apply.
    # A fail-closed apply can therefore leave this exact bootstrap-only config
    # with no active release. Accept only that byte-semantic shape and the
    # original minimal marker; arbitrary ambient OpenClaw config still fails.
    if str(install_dir) not in serialized_config and not (
        cleanup[0] == "none" and config == bootstrap_config and value == bootstrap_marker
    ):
        raise SystemExit("OpenClaw configuration is not bound to this ODS install")
    if cleanup[0] != "none":
        canonical = json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
        observed = hashlib.sha256(b"ods-pixel-openclaw-v1\0" + canonical).hexdigest()
        if value.get("configuration_sha256") != observed:
            raise SystemExit("OpenClaw configuration drifted from its ODS marker")
elif cleanup[0] != "none" and state != "deactivating":
    raise SystemExit("ODS-managed active Pixel configuration is missing")

ops_policy_present = ops_owner_policy.exists() or ops_owner_policy.is_symlink()
if ops_policy_present:
    regular(ops_owner_policy, owner_uid, 2 * 1024 * 1024, private=True)
    policy_value = json.loads(ops_owner_policy.read_text(encoding="utf-8"))
    if (not isinstance(policy_value, dict) or policy_value.get("schemaVersion") != 2
            or policy_value.get("deployment") != "ods-default"):
        raise SystemExit("ODS Pixel Operations policy is not an ODS-managed policy")

extension_catalog_present = (
    ops_owner_extension_catalog.exists() or ops_owner_extension_catalog.is_symlink()
)
extension_program_present = (
    ops_extension_source_program.exists() or ops_extension_source_program.is_symlink()
)
extension_manager_source = pathlib.Path(extension_manager_source_raw)
extension_manager_owner_unit = pathlib.Path(extension_manager_owner_unit_raw)
approval_source = pathlib.Path(approval_source_raw)
artifact_promoter_source = pathlib.Path(artifact_promoter_source_raw)
artifact_promoter_owner_unit = pathlib.Path(artifact_promoter_owner_unit_raw)
workspace_preview_source = pathlib.Path(workspace_preview_source_raw)
workspace_preview_owner_unit = pathlib.Path(workspace_preview_owner_unit_raw)
system_observer_source = pathlib.Path(system_observer_source_raw)
ops_dropin_source = pathlib.Path(ops_dropin_source_raw)
extension_manager_present = (
    extension_manager_source.exists() or extension_manager_source.is_symlink()
)
extension_manager_unit_present = (
    extension_manager_owner_unit.exists() or extension_manager_owner_unit.is_symlink()
)
if extension_catalog_present and not extension_program_present:
    raise SystemExit("ODS Pixel extension projection source is incomplete")
if extension_manager_present != extension_manager_unit_present:
    raise SystemExit("ODS Pixel extension lifecycle source is incomplete")
if extension_manager_present:
    regular(extension_manager_source, owner_uid, 2 * 1024 * 1024)
    regular(extension_manager_owner_unit, owner_uid, 2 * 1024 * 1024, private=True)
approval_present = approval_source.exists() or approval_source.is_symlink()
if approval_present:
    approval_info = regular(approval_source, owner_uid, 256 * 1024)
    if not approval_info.st_mode & 0o111:
        raise SystemExit("ODS Pixel approval helper is not executable")
artifact_promoter_contract_present = (
    artifact_promoter_owner_unit.exists() or artifact_promoter_owner_unit.is_symlink()
)
if artifact_promoter_contract_present:
    regular(artifact_promoter_source, owner_uid, 2 * 1024 * 1024)
    regular(artifact_promoter_owner_unit, owner_uid, 2 * 1024 * 1024, private=True)
workspace_preview_source_present = workspace_preview_source.exists() or workspace_preview_source.is_symlink()
workspace_preview_contract_present = (
    workspace_preview_owner_unit.exists() or workspace_preview_owner_unit.is_symlink()
)
if workspace_preview_source_present != workspace_preview_contract_present:
    raise SystemExit("ODS Pixel workspace preview source is incomplete")
if workspace_preview_contract_present:
    regular(workspace_preview_source, owner_uid, 2 * 1024 * 1024)
    regular(workspace_preview_owner_unit, owner_uid, 2 * 1024 * 1024, private=True)
system_observer_source_present = system_observer_source.exists() or system_observer_source.is_symlink()
system_observer_contract_present = False
if system_observer_source_present:
    observer_info = regular(system_observer_source, owner_uid, 2 * 1024 * 1024)
    if observer_info.st_mode & 0o022:
        raise SystemExit("ODS Pixel system observer source is writable by another identity")
ops_dropin_contract_present = ops_dropin_source.exists() or ops_dropin_source.is_symlink()
if ops_dropin_contract_present:
    regular(ops_dropin_source, owner_uid, 2 * 1024 * 1024)
if extension_catalog_present:
    regular(ops_owner_extension_catalog, owner_uid, 2 * 1024 * 1024, private=True)
    regular(ops_extension_source_program, owner_uid, 2 * 1024 * 1024)
    catalog_value = json.loads(ops_owner_extension_catalog.read_text(encoding="utf-8"))
    if (not isinstance(catalog_value, dict) or catalog_value.get("schemaVersion") != 1
            or catalog_value.get("kind") != "ods-pixel-extension-catalog"
            or not isinstance(catalog_value.get("extensions"), list)
            or not catalog_value.get("extensions")):
        raise SystemExit("ODS Pixel extension catalog is not ODS-managed")

if onboarding.exists():
    answers = json.loads(onboarding.read_text(encoding="utf-8"))
    extensions = answers.get("gatewayExtensions") if isinstance(answers, dict) else None
    expected_plugin = str(install_dir / "extensions/services/pixel-agent/plugin")
    if not isinstance(extensions, list) or not any(
        isinstance(item, dict) and item.get("id") == "pixel-ods" and item.get("path") == expected_plugin
        for item in extensions
    ):
        raise SystemExit("Pixel onboarding is not bound to this ODS install")
    operations_enabled = answers.get("operationsLimbEnabled") is True
    if operations_enabled:
        if answers.get("operationsPolicyFile") != str(ops_owner_policy) or not ops_policy_present:
            raise SystemExit("Pixel onboarding is not bound to the ODS Operations policy")
    elif ops_policy_present and state != "installing":
        raise SystemExit("ODS Operations policy exists without an enabled onboarding contract")
    if cleanup[0] != "none":
        onboarding_payload = onboarding.read_bytes()
        accepted_contracts = {
            hashlib.sha256(b"ods-pixel-contract-v1\0" + onboarding_payload).hexdigest(),
        }
        if ops_policy_present:
            policy_payload = ops_owner_policy.read_bytes()
            v2 = hashlib.sha256()
            v2.update(b"ods-pixel-contract-v2\0")
            for payload in (onboarding_payload, policy_payload):
                v2.update(len(payload).to_bytes(8, "big"))
                v2.update(payload)
            accepted_contracts.add(v2.hexdigest())
            if extension_catalog_present:
                v3 = hashlib.sha256()
                v3.update(b"ods-pixel-contract-v3\0")
                for payload in (
                    onboarding_payload,
                    policy_payload,
                    ops_owner_extension_catalog.read_bytes(),
                    ops_extension_source_program.read_bytes(),
                ):
                    v3.update(len(payload).to_bytes(8, "big"))
                    v3.update(payload)
                accepted_contracts.add(v3.hexdigest())
                if extension_manager_present:
                    v4 = hashlib.sha256()
                    v4.update(b"ods-pixel-contract-v4\0")
                    for payload in (
                        onboarding_payload,
                        policy_payload,
                        ops_owner_extension_catalog.read_bytes(),
                        ops_extension_source_program.read_bytes(),
                        extension_manager_source.read_bytes(),
                        extension_manager_owner_unit.read_bytes(),
                    ):
                        v4.update(len(payload).to_bytes(8, "big"))
                        v4.update(payload)
                    accepted_contracts.add(v4.hexdigest())
                    if approval_present:
                        v5 = hashlib.sha256()
                        v5.update(b"ods-pixel-contract-v5\0")
                        for payload in (
                            onboarding_payload,
                            policy_payload,
                            ops_owner_extension_catalog.read_bytes(),
                            ops_extension_source_program.read_bytes(),
                            extension_manager_source.read_bytes(),
                            extension_manager_owner_unit.read_bytes(),
                            approval_source.read_bytes(),
                        ):
                            v5.update(len(payload).to_bytes(8, "big"))
                            v5.update(payload)
                        accepted_contracts.add(v5.hexdigest())
                        if artifact_promoter_contract_present:
                            v6 = hashlib.sha256()
                            v6.update(b"ods-pixel-contract-v6\0")
                            for payload in (
                                onboarding_payload,
                                policy_payload,
                                ops_owner_extension_catalog.read_bytes(),
                                ops_extension_source_program.read_bytes(),
                                extension_manager_source.read_bytes(),
                                extension_manager_owner_unit.read_bytes(),
                                approval_source.read_bytes(),
                                artifact_promoter_source.read_bytes(),
                                artifact_promoter_owner_unit.read_bytes(),
                            ):
                                v6.update(len(payload).to_bytes(8, "big"))
                                v6.update(payload)
                            accepted_contracts.add(v6.hexdigest())
                            if ops_dropin_contract_present:
                                v7 = hashlib.sha256()
                                v7.update(b"ods-pixel-contract-v7\0")
                                for payload in (
                                    onboarding_payload,
                                    policy_payload,
                                    ops_owner_extension_catalog.read_bytes(),
                                    ops_extension_source_program.read_bytes(),
                                    extension_manager_source.read_bytes(),
                                    extension_manager_owner_unit.read_bytes(),
                                    approval_source.read_bytes(),
                                    artifact_promoter_source.read_bytes(),
                                    artifact_promoter_owner_unit.read_bytes(),
                                    ops_dropin_source.read_bytes(),
                                ):
                                    v7.update(len(payload).to_bytes(8, "big"))
                                    v7.update(payload)
                                accepted_contracts.add(v7.hexdigest())
                                if workspace_preview_contract_present:
                                    v8 = hashlib.sha256()
                                    v8.update(b"ods-pixel-contract-v8\0")
                                    for payload in (
                                        onboarding_payload,
                                        policy_payload,
                                        ops_owner_extension_catalog.read_bytes(),
                                        ops_extension_source_program.read_bytes(),
                                        extension_manager_source.read_bytes(),
                                        extension_manager_owner_unit.read_bytes(),
                                        approval_source.read_bytes(),
                                        artifact_promoter_source.read_bytes(),
                                        artifact_promoter_owner_unit.read_bytes(),
                                        ops_dropin_source.read_bytes(),
                                        workspace_preview_source.read_bytes(),
                                        workspace_preview_owner_unit.read_bytes(),
                                    ):
                                        v8.update(len(payload).to_bytes(8, "big"))
                                        v8.update(payload)
                                    accepted_contracts.add(v8.hexdigest())
                                    if system_observer_source_present:
                                        v9 = hashlib.sha256()
                                        v9.update(b"ods-pixel-contract-v9\0")
                                        for payload in (
                                            onboarding_payload,
                                            policy_payload,
                                            ops_owner_extension_catalog.read_bytes(),
                                            ops_extension_source_program.read_bytes(),
                                            extension_manager_source.read_bytes(),
                                            extension_manager_owner_unit.read_bytes(),
                                            approval_source.read_bytes(),
                                            artifact_promoter_source.read_bytes(),
                                            artifact_promoter_owner_unit.read_bytes(),
                                            ops_dropin_source.read_bytes(),
                                            workspace_preview_source.read_bytes(),
                                            workspace_preview_owner_unit.read_bytes(),
                                            system_observer_source.read_bytes(),
                                        ):
                                            v9.update(len(payload).to_bytes(8, "big"))
                                            v9.update(payload)
                                        v9_digest = v9.hexdigest()
                                        accepted_contracts.add(v9_digest)
                                        system_observer_contract_present = value.get("contract_sha256") == v9_digest
        if value.get("contract_sha256") not in accepted_contracts:
            raise SystemExit("Pixel onboarding drifted from its ODS marker")
elif cleanup[0] != "none" and state != "deactivating":
    raise SystemExit("ODS-managed active Pixel onboarding contract is missing")

gateway_unit = pathlib.Path(gateway_unit_raw)
ingress_unit = pathlib.Path(ingress_unit_raw)
ingress_env = pathlib.Path(ingress_env_raw)
ingress_program = pathlib.Path(ingress_program_raw)
source_program = pathlib.Path(source_program_raw)
extension_manager_unit = pathlib.Path(extension_manager_unit_raw)
extension_manager_program = pathlib.Path(extension_manager_program_raw)
artifact_promoter_unit = pathlib.Path(artifact_promoter_unit_raw)
artifact_promoter_program = pathlib.Path(artifact_promoter_program_raw)
workspace_preview_unit = pathlib.Path(workspace_preview_unit_raw)
workspace_preview_program = pathlib.Path(workspace_preview_program_raw)
workspace_preview_state = pathlib.Path(workspace_preview_state_raw)
system_observer_program = pathlib.Path(system_observer_program_raw)
for path, maximum in (
    (gateway_unit, 256 * 1024),
    (ingress_unit, 256 * 1024),
    (ingress_env, 64 * 1024),
    (ingress_program, 2 * 1024 * 1024),
    (extension_manager_unit, 256 * 1024),
    (extension_manager_program, 2 * 1024 * 1024),
    (artifact_promoter_unit, 256 * 1024),
    (artifact_promoter_program, 2 * 1024 * 1024),
    (workspace_preview_unit, 256 * 1024),
    (workspace_preview_program, 2 * 1024 * 1024),
    (system_observer_program, 2 * 1024 * 1024),
):
    if path.exists() or path.is_symlink():
        regular(path, root_uid, maximum)

manager_root_present = extension_manager_unit.exists() or extension_manager_unit.is_symlink()
manager_program_present = extension_manager_program.exists() or extension_manager_program.is_symlink()
if manager_root_present != manager_program_present:
    raise SystemExit("ODS-managed Pixel extension manager system artifacts are partial")
if state == "ready" and extension_manager_present != manager_root_present:
    raise SystemExit("ready ODS-managed Pixel extension lifecycle deployment is partial")
if manager_root_present and not extension_manager_present:
    raise SystemExit("Pixel extension manager system artifacts lack an ODS contract")

promoter_unit_present = artifact_promoter_unit.exists() or artifact_promoter_unit.is_symlink()
promoter_program_present = artifact_promoter_program.exists() or artifact_promoter_program.is_symlink()
if promoter_unit_present != promoter_program_present:
    raise SystemExit("ODS-managed Pixel artifact promoter system artifacts are partial")
if state == "ready" and artifact_promoter_contract_present != promoter_unit_present:
    raise SystemExit("ready ODS-managed Pixel artifact promotion deployment is partial")
if promoter_unit_present and not artifact_promoter_contract_present:
    raise SystemExit("Pixel artifact promoter system artifacts lack an ODS contract")

preview_unit_present = workspace_preview_unit.exists() or workspace_preview_unit.is_symlink()
preview_program_present = workspace_preview_program.exists() or workspace_preview_program.is_symlink()
if preview_unit_present != preview_program_present:
    raise SystemExit("ODS-managed Pixel workspace preview system artifacts are partial")
if state == "ready" and workspace_preview_contract_present != preview_unit_present:
    raise SystemExit("ready ODS-managed Pixel workspace preview deployment is partial")
if preview_unit_present and not workspace_preview_contract_present:
    raise SystemExit("Pixel workspace preview system artifacts lack an ODS contract")

system_observer_program_present = system_observer_program.exists() or system_observer_program.is_symlink()
if state == "ready" and system_observer_contract_present != system_observer_program_present:
    raise SystemExit("ready ODS-managed Pixel system observer deployment is partial")
if system_observer_program_present and not system_observer_contract_present:
    raise SystemExit("Pixel system observer artifact lacks an ODS contract")

if workspace_preview_state.exists() or workspace_preview_state.is_symlink():
    root_info = workspace_preview_state.lstat()
    if (not stat.S_ISDIR(root_info.st_mode) or stat.S_ISLNK(root_info.st_mode)
            or root_info.st_uid != owner_uid or stat.S_IMODE(root_info.st_mode) != 0o700):
        raise SystemExit("unsafe Pixel workspace preview state")
    for root, directories, names in os.walk(workspace_preview_state, topdown=True, followlinks=False):
        root_path = pathlib.Path(root)
        info = root_path.lstat()
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or info.st_uid != owner_uid or stat.S_IMODE(info.st_mode) != 0o700):
            raise SystemExit("unsafe Pixel workspace preview state")
        for directory in directories:
            child = (root_path / directory).lstat()
            if (not stat.S_ISDIR(child.st_mode) or stat.S_ISLNK(child.st_mode)
                    or child.st_uid != owner_uid or stat.S_IMODE(child.st_mode) != 0o700):
                raise SystemExit("unsafe Pixel workspace preview state")
        for name in names:
            child = (root_path / name).lstat()
            if (not stat.S_ISREG(child.st_mode) or stat.S_ISLNK(child.st_mode)
                    or child.st_nlink != 1 or child.st_uid != owner_uid
                    or stat.S_IMODE(child.st_mode) != 0o400):
                raise SystemExit("unsafe Pixel workspace preview state")

if gateway_unit.exists():
    text = gateway_unit.read_text(encoding="utf-8")
    if "Description=OpenClaw Gateway - Pixel" not in text or str(install_dir) not in text:
        raise SystemExit("gateway unit is not the ODS-managed Pixel unit")

if ingress_unit.exists():
    text = ingress_unit.read_text(encoding="utf-8")
    if (
        f"ExecStart=/usr/bin/env node {ingress_program}" not in text
        or f"EnvironmentFile={ingress_env}" not in text
        or "Description=Pixel Agent host ingress" not in text
    ):
        raise SystemExit("ingress unit is not the ODS-managed Pixel unit")

if extension_manager_unit.exists():
    if not extension_manager_owner_unit.exists():
        raise SystemExit("ODS-managed Pixel extension manager unit source is missing")
    if extension_manager_unit.read_bytes() != extension_manager_owner_unit.read_bytes():
        raise SystemExit("installed Pixel extension manager unit drifted from this ODS install")

if extension_manager_program.exists():
    if not extension_manager_source.exists():
        raise SystemExit("ODS-managed Pixel extension manager source is missing")
    if extension_manager_program.read_bytes() != extension_manager_source.read_bytes():
        raise SystemExit("installed Pixel extension manager program drifted from this ODS install")

if artifact_promoter_unit.exists():
    if artifact_promoter_unit.read_bytes() != artifact_promoter_owner_unit.read_bytes():
        raise SystemExit("installed Pixel artifact promoter unit drifted from this ODS install")

if artifact_promoter_program.exists():
    if artifact_promoter_program.read_bytes() != artifact_promoter_source.read_bytes():
        raise SystemExit("installed Pixel artifact promoter program drifted from this ODS install")

if workspace_preview_unit.exists():
    if workspace_preview_unit.read_bytes() != workspace_preview_owner_unit.read_bytes():
        raise SystemExit("installed Pixel workspace preview unit drifted from this ODS install")

if workspace_preview_program.exists():
    if workspace_preview_program.read_bytes() != workspace_preview_source.read_bytes():
        raise SystemExit("installed Pixel workspace preview program drifted from this ODS install")

if system_observer_program.exists():
    if system_observer_program.read_bytes() != system_observer_source.read_bytes():
        raise SystemExit("installed Pixel system observer drifted from this ODS install")

if ingress_env.exists():
    entries = {}
    for line in ingress_env.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        key, separator, item = line.partition("=")
        if not separator or key in entries:
            raise SystemExit("invalid Pixel ingress environment")
        entries[key] = item
    if (
        entries.get("PIXEL_INGRESS_SOCKET") != "/run/ods-pixel/pixel-ingress.sock"
        or entries.get("PIXEL_GATEWAY_TOKEN_FILE") != "/run/ods-pixel/openclaw.json"
        or entries.get("PIXEL_STATUS_FILE") != "/run/ods-pixel/ods-status.json"
    ):
        raise SystemExit("Pixel ingress environment is not ODS-managed")

if ingress_program.exists():
    if not source_program.exists() or source_program.is_symlink():
        raise SystemExit("ODS Pixel ingress source is unavailable for cleanup verification")
    if ingress_program.read_bytes() != source_program.read_bytes():
        raise SystemExit("installed Pixel ingress program drifted from this ODS install")
print("|".join((*cleanup, state, source_ref)))
PY
    )"; then
        log_error "ODS-managed Pixel validation failed; leaving every Pixel artifact untouched"
        return 1
    fi
    IFS='|' read -r cleanup_state release_version sandbox_image sandbox_image_id release_path \
        release_identity_sha256 install_manifest_sha256 retired_release_path marker_state pixel_source_ref <<<"$cleanup_plan"
    [[ "$cleanup_state" == none || "$cleanup_state" == active || "$cleanup_state" == staged \
        || "$cleanup_state" == staging-attestation || "$cleanup_state" == staging-link \
        || "$cleanup_state" == retiring || "$cleanup_state" == retired ]] || {
        log_error "ODS-managed Pixel cleanup plan is invalid"
        return 1
    }
    [[ "$marker_state" == installing || "$marker_state" == ready || "$marker_state" == deactivating ]] || {
        log_error "ODS-managed Pixel marker state is invalid"
        return 1
    }
    [[ "$pixel_source_ref" =~ ^[0-9a-f]{40}$ ]] || {
        log_error "ODS-managed Pixel source binding is invalid"
        return 1
    }

    # Pixel's Operations Broker deliberately crosses the owner/root boundary.
    # Inspect its protected bytes as root, but bind them to this exact ODS
    # install's generated Pixel source and private policy before stopping any
    # service. A ready deployment must be complete; only an installing or
    # deactivating marker may describe a resumable partial broker lifecycle.
    if command -v getent >/dev/null 2>&1; then
        ops_passwd_entry="$(getent passwd "$ops_user" 2>/dev/null || true)"
        ops_group_entry="$(getent group "$ops_group" 2>/dev/null || true)"
    fi
    if [[ -n "$ops_passwd_entry" ]]; then
        ops_user_group_ids="$(id -G "$ops_user" 2>/dev/null || true)"
        ops_user_group_names="$(id -nG "$ops_user" 2>/dev/null || true)"
    fi
    if [[ -n "$ops_passwd_entry" || -n "$ops_group_entry" ]]; then
        ops_artifacts_present=true
    fi
    for path in "$ops_unit" "$ops_dropin" "$ops_env" "$ops_policy" "$ops_install" "$ops_state" \
        "$ops_owner_policy" "$ops_owner_extension_catalog" "$ops_extension_manager" \
        "$extension_manager_owner_unit" "$artifact_promoter_owner_unit"; do
        if [[ -e "$path" || -L "$path" ]]; then
            ops_artifacts_present=true
        fi
    done
    if [[ "$ops_artifacts_present" == true ]]; then
        command -v sudo >/dev/null 2>&1 || {
            log_error "sudo is required to validate ODS-managed Pixel Operations artifacts"
            return 1
        }
        if ! ops_plan="$(sudo python3 - \
            "$root_uid" "$root_gid" "$owner_uid" "$owner_gid" "$marker_state" "$ops_user" "$ops_group" \
            "$ops_passwd_entry" "$ops_group_entry" "$ops_user_group_ids" "$ops_user_group_names" \
            "${ODS_PIXEL_UNINSTALL_OPS_UID:-}" "${ODS_PIXEL_UNINSTALL_OPS_GID:-}" \
            "$ops_unit" "$ops_env" "$ops_policy" "$ops_install" "$ops_program" "$ops_state" \
            "$ops_owner_policy" "$ops_extension_program" "$ops_extension_catalog" \
            "$ops_extension_manager" "$ops_extension_source_program" \
            "$ops_owner_extension_catalog" "$extension_manager_source" \
            "$extension_manager_owner_unit" \
            "$ops_dropin" "$ops_dropin_source" \
            "$install_dir/data/pixel/source-$pixel_source_ref/.generated/pixel-ops-broker.service" \
            "$install_dir/data/pixel/source-$pixel_source_ref/.generated/ops-broker.env" \
            "$install_dir/data/pixel/source-$pixel_source_ref/deploy/ops-broker/broker.py" <<'PY'
import os
import pathlib
import stat
import sys

(
    root_uid_raw,
    root_gid_raw,
    owner_uid_raw,
    owner_gid_raw,
    marker_state,
    ops_user,
    ops_group,
    passwd_entry,
    group_entry,
    user_group_ids,
    user_group_names,
    uid_override,
    gid_override,
    unit_raw,
    env_raw,
    policy_raw,
    install_raw,
    program_raw,
    state_raw,
    owner_policy_raw,
    extension_program_raw,
    extension_catalog_raw,
    extension_manager_raw,
    expected_extension_program_raw,
    owner_extension_catalog_raw,
    expected_extension_manager_raw,
    owner_extension_manager_unit_raw,
    dropin_raw,
    expected_dropin_raw,
    expected_unit_raw,
    expected_env_raw,
    expected_program_raw,
) = sys.argv[1:]

root_uid = int(root_uid_raw)
root_gid = int(root_gid_raw)
owner_uid = int(owner_uid_raw)
owner_gid = int(owner_gid_raw)
unit = pathlib.Path(unit_raw)
environment = pathlib.Path(env_raw)
policy = pathlib.Path(policy_raw)
install_dir = pathlib.Path(install_raw)
program = pathlib.Path(program_raw)
state_dir = pathlib.Path(state_raw)
owner_policy = pathlib.Path(owner_policy_raw)
extension_program = pathlib.Path(extension_program_raw)
extension_catalog = pathlib.Path(extension_catalog_raw)
extension_manager = pathlib.Path(extension_manager_raw)
expected_extension_program = pathlib.Path(expected_extension_program_raw)
owner_extension_catalog = pathlib.Path(owner_extension_catalog_raw)
expected_extension_manager = pathlib.Path(expected_extension_manager_raw)
owner_extension_manager_unit = pathlib.Path(owner_extension_manager_unit_raw)
dropin = pathlib.Path(dropin_raw)
expected_dropin = pathlib.Path(expected_dropin_raw)
expected_unit = pathlib.Path(expected_unit_raw)
expected_env = pathlib.Path(expected_env_raw)
expected_program = pathlib.Path(expected_program_raw)


def exists(path: pathlib.Path) -> bool:
    return path.exists() or path.is_symlink()


def parse_passwd(value: str):
    if not value:
        return None
    fields = value.split(":")
    if len(fields) != 7 or fields[0] != ops_user or not fields[2].isdigit() or not fields[3].isdigit():
        raise SystemExit("unsafe Pixel Operations Broker user identity")
    return fields


def parse_group(value: str):
    if not value:
        return None
    fields = value.split(":")
    if len(fields) != 4 or fields[0] != ops_group or not fields[2].isdigit() or fields[3]:
        raise SystemExit("unsafe Pixel Operations Broker group identity")
    return fields


passwd = parse_passwd(passwd_entry)
group = parse_group(group_entry)
if bool(passwd) != bool(group) and marker_state not in {"installing", "deactivating"}:
    raise SystemExit("partial Pixel Operations Broker identity")

broker_uid = int(uid_override) if uid_override else (int(passwd[2]) if passwd else 0)
broker_gid = int(gid_override) if gid_override else (int(group[2]) if group else 0)
if passwd:
    observed_uid = int(passwd[2])
    observed_gid = int(passwd[3])
    if uid_override:
        if observed_uid != broker_uid:
            raise SystemExit("Pixel Operations Broker user UID drifted")
    elif observed_uid <= 0 or observed_uid == owner_uid or observed_uid >= 65536:
        raise SystemExit("unsafe Pixel Operations Broker system UID")
    if (not group or observed_gid != int(group[2]) or observed_gid != broker_gid
            or passwd[5] != str(state_dir) or passwd[6] != "/usr/sbin/nologin"):
        raise SystemExit("Pixel Operations Broker user identity drifted")
    if not uid_override and ({int(item) for item in user_group_ids.split()} != {broker_gid}
            or set(user_group_names.split()) != {ops_group}):
        raise SystemExit("Pixel Operations Broker user has unexpected supplementary groups")
if group:
    observed_gid = int(group[2])
    if gid_override:
        if observed_gid != broker_gid:
            raise SystemExit("Pixel Operations Broker group GID drifted")
    elif observed_gid <= 0 or observed_gid >= 65536:
        raise SystemExit("unsafe Pixel Operations Broker system GID")

projection_source_present = exists(owner_extension_catalog)
if projection_source_present and not exists(expected_extension_program):
    raise SystemExit("Pixel extension projection source is partial")
lifecycle_source_present = exists(owner_extension_manager_unit)
if lifecycle_source_present and not exists(expected_extension_manager):
    raise SystemExit("Pixel extension lifecycle source is partial")
dropin_source_present = exists(expected_dropin)

paths = (unit, dropin, environment, policy, install_dir, state_dir)
present = [exists(path) for path in paths]
if marker_state == "ready" and (not all(present) or not passwd or not group):
    raise SystemExit("ready Pixel Operations Broker deployment is partial")
if any(present) and (not passwd or not group):
    raise SystemExit("Pixel Operations Broker files lack their exact isolated identity")


def exact_file(path: pathlib.Path, uid: int, gid: int, mode: int, maximum: int) -> None:
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_nlink != 1 or info.st_uid != uid or info.st_gid != gid
            or stat.S_IMODE(info.st_mode) != mode or info.st_size > maximum):
        raise SystemExit(f"unsafe Pixel Operations Broker artifact: {path}")


def owner_source(
    path: pathlib.Path,
    maximum: int,
    private: bool = False,
    allow_owner_group_write: bool = False,
) -> None:
    info = path.lstat()
    write_mask = 0o002 if allow_owner_group_write else 0o022
    if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_nlink != 1 or info.st_uid != owner_uid
            or info.st_size > maximum or info.st_mode & write_mask
            or (allow_owner_group_write and info.st_gid != owner_gid)
            or (private and info.st_mode & 0o077)):
        raise SystemExit(f"unsafe ODS Pixel Operations source: {path}")


if exists(unit):
    exact_file(unit, root_uid, root_gid, 0o644, 256 * 1024)
    owner_source(expected_unit, 256 * 1024)
    if unit.read_bytes() != expected_unit.read_bytes():
        raise SystemExit("Pixel Operations Broker unit drifted from the exact generated source")
if exists(dropin):
    exact_file(dropin, root_uid, root_gid, 0o644, 64 * 1024)
    owner_source(expected_dropin, 64 * 1024)
    if dropin.read_bytes() != expected_dropin.read_bytes():
        raise SystemExit("Pixel Operations Broker ODS drop-in drifted from the exact source")
dropin_parent = dropin.parent
if exists(dropin_parent):
    info = dropin_parent.lstat()
    contents = {item.name for item in dropin_parent.iterdir()}
    expected_contents = {dropin.name} if exists(dropin) else set()
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_uid != root_uid or info.st_gid != root_gid
            or stat.S_IMODE(info.st_mode) != 0o755 or contents != expected_contents):
        raise SystemExit("unsafe Pixel Operations Broker ODS drop-in directory")
elif dropin_source_present and marker_state == "ready":
    raise SystemExit("ready Pixel Operations Broker ODS drop-in is missing")
if exists(environment):
    exact_file(environment, root_uid, broker_gid, 0o640, 64 * 1024)
    owner_source(expected_env, 64 * 1024)
    if environment.read_bytes() != expected_env.read_bytes():
        raise SystemExit("Pixel Operations Broker environment drifted from the exact generated source")
if exists(policy):
    exact_file(policy, root_uid, broker_gid, 0o640, 2 * 1024 * 1024)
    owner_source(owner_policy, 2 * 1024 * 1024, private=True)
    if policy.read_bytes() != owner_policy.read_bytes():
        raise SystemExit("Pixel Operations Broker policy drifted from the ODS private policy")
if exists(install_dir):
    info = install_dir.lstat()
    contents = {item.name for item in install_dir.iterdir()}
    expected_contents = {"broker.py"}
    if projection_source_present:
        expected_contents.update({"ods-extension-search.py", "ods-extension-catalog.json"})
    if lifecycle_source_present:
        expected_contents.add("ods-extension-manager.py")
    contents_valid = (
        contents.issubset(expected_contents)
        if marker_state in {"installing", "deactivating"}
        else contents == expected_contents
    )
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_uid != root_uid or stat.S_IMODE(info.st_mode) != 0o755
            or info.st_gid != root_gid
            or not contents_valid):
        raise SystemExit("unsafe Pixel Operations Broker install directory")
    if exists(program):
        exact_file(program, root_uid, root_gid, 0o755, 2 * 1024 * 1024)
        # Early ODS checkouts inherited an owner-private group's cooperative
        # umask and can therefore carry 0664 here. That does not authorize the
        # source as a mutable deletion oracle: it must still be a regular,
        # single-link, owner/primary-group file whose bytes exactly equal the
        # root-owned 0755 executable below. New checkouts are created at 0644.
        owner_source(expected_program, 2 * 1024 * 1024, allow_owner_group_write=True)
        if program.read_bytes() != expected_program.read_bytes():
            raise SystemExit("Pixel Operations Broker program drifted from the exact Pixel source")
    if exists(extension_program):
        exact_file(extension_program, root_uid, root_gid, 0o755, 2 * 1024 * 1024)
        owner_source(expected_extension_program, 2 * 1024 * 1024)
        if extension_program.read_bytes() != expected_extension_program.read_bytes():
            raise SystemExit("Pixel extension search program drifted from the exact ODS source")
    if exists(extension_catalog):
        exact_file(extension_catalog, root_uid, broker_gid, 0o640, 2 * 1024 * 1024)
        owner_source(owner_extension_catalog, 2 * 1024 * 1024, private=True)
        if extension_catalog.read_bytes() != owner_extension_catalog.read_bytes():
            raise SystemExit("Pixel extension catalog drifted from the ODS private projection")
    if exists(extension_manager):
        exact_file(extension_manager, root_uid, root_gid, 0o755, 2 * 1024 * 1024)
        owner_source(expected_extension_manager, 2 * 1024 * 1024)
        if extension_manager.read_bytes() != expected_extension_manager.read_bytes():
            raise SystemExit("Pixel extension manager client drifted from the exact ODS source")
elif exists(program) or exists(extension_program) or exists(extension_catalog) or exists(extension_manager):
    raise SystemExit("Pixel Operations Broker program escaped its install directory")

policy_parent = policy.parent
if exists(policy_parent):
    info = policy_parent.lstat()
    contents = {item.name for item in policy_parent.iterdir()}
    expected_contents = {"policy.json"} if exists(policy) else set()
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_uid != root_uid or stat.S_IMODE(info.st_mode) != 0o755
            or info.st_gid != root_gid
            or contents != expected_contents):
        raise SystemExit("unsafe Pixel Operations Broker policy directory")

if exists(state_dir):
    root = state_dir.lstat()
    if (not stat.S_ISDIR(root.st_mode) or stat.S_ISLNK(root.st_mode)
            or root.st_uid != broker_uid or root.st_gid != broker_gid
            or stat.S_IMODE(root.st_mode) != 0o750):
        raise SystemExit("unsafe Pixel Operations Broker state root")
    state_absolute = pathlib.Path(os.path.abspath(state_dir))
    try:
        mount_lines = pathlib.Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise SystemExit("cannot inspect mounts before Pixel Operations cleanup") from error
    for line in mount_lines:
        fields = line.split()
        if len(fields) < 5:
            raise SystemExit("invalid mount table while validating Pixel Operations state")
        mount_text = fields[4]
        for encoded, decoded in ((r"\040", " "), (r"\011", "\t"), (r"\012", "\n"), (r"\134", "\\")):
            mount_text = mount_text.replace(encoded, decoded)
        mount_path = pathlib.Path(os.path.abspath(mount_text))
        if mount_path == state_absolute or state_absolute in mount_path.parents:
            raise SystemExit(f"mount inside Pixel Operations Broker state: {mount_path}")
    root_device = root.st_dev
    bounded_service_profiles = {
        state_dir / ".bash_logout",
        state_dir / ".bashrc",
        state_dir / ".profile",
    }
    for current, directories, files in os.walk(state_dir, topdown=True, followlinks=False):
        for name in (*directories, *files):
            path = pathlib.Path(current) / name
            info = path.lstat()
            if (stat.S_ISLNK(info.st_mode) or info.st_dev != root_device
                    or info.st_uid not in {broker_uid, owner_uid}
                    or info.st_gid != broker_gid):
                raise SystemExit(f"unsafe Pixel Operations Broker state entry: {path}")
            if path in bounded_service_profiles:
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != broker_uid
                        or info.st_nlink != 1
                        or stat.S_IMODE(info.st_mode) not in {0o600, 0o640, 0o644}
                        or info.st_size > 64 * 1024):
                    raise SystemExit(f"unsafe Pixel Operations service profile: {path}")
                continue
            if info.st_mode & 0o007:
                raise SystemExit(f"unsafe Pixel Operations Broker state entry: {path}")
            if stat.S_ISDIR(info.st_mode):
                if info.st_mode & (stat.S_ISUID | stat.S_ISVTX):
                    raise SystemExit(f"unsafe Pixel Operations Broker state directory: {path}")
            elif stat.S_ISREG(info.st_mode):
                if info.st_nlink != 1 or info.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
                    raise SystemExit(f"unsafe Pixel Operations Broker state file: {path}")
            else:
                raise SystemExit(f"special file in Pixel Operations Broker state: {path}")

print("present|{}|{}|{}|{}".format(
    broker_uid,
    broker_gid,
    "true" if passwd else "false",
    "true" if group else "false",
))
PY
        )"; then
            log_error "ODS-managed Pixel Operations validation failed; leaving every Pixel artifact untouched"
            return 1
        fi
        IFS='|' read -r ops_state_status ops_uid ops_gid ops_user_present ops_group_present <<<"$ops_plan"
        [[ "$ops_state_status" == present && "$ops_uid" =~ ^[0-9]+$ && "$ops_gid" =~ ^[0-9]+$ \
            && ( "$ops_user_present" == true || "$ops_user_present" == false ) \
            && ( "$ops_group_present" == true || "$ops_group_present" == false ) ]] || {
            log_error "ODS-managed Pixel Operations cleanup plan is invalid"
            return 1
        }
    fi

    (
    local candidate_image observed_image shared_image_present=true sandbox_container_list
    local retired_container
    local -a sandbox_containers=()
    if [[ "$cleanup_state" != none ]]; then
        for required_command in docker flock sha256sum timeout mktemp; do
            command -v "$required_command" >/dev/null 2>&1 || {
                log_error "Cannot safely deactivate ODS-managed Pixel without $required_command"
                return 1
            }
        done
        exec {pixel_lock_fd}<>"$deployment_lock" || {
            log_error "Could not open the Pixel deployment lock safely"
            return 1
        }
        flock -n "$pixel_lock_fd" || {
            log_error "Another Pixel deployment operation is active"
            return 1
        }
        local active_link="$current"
        [[ "$cleanup_state" == staged || "$cleanup_state" == staging-link \
            || "$cleanup_state" == retiring ]] && active_link="$staged_current"
        [[ ( "$cleanup_state" == retired || "$(readlink -f -- "$active_link")" == "$release_path" ) \
            && "$(sha256sum "$release_path/release-identity.json" | awk '{print $1}')" == "$release_identity_sha256" \
            && "$(sha256sum "$release_path/install-manifest.sha256" | awk '{print $1}')" == "$install_manifest_sha256" ]] || {
            log_error "ODS-managed Pixel release changed while acquiring its deployment lock"
            return 1
        }
        (cd "$release_path" && timeout 60s sha256sum -c install-manifest.sha256 >/dev/null) || {
            log_error "ODS-managed Pixel release bytes no longer match their install manifest"
            return 1
        }

        candidate_image="pixel-sandbox-candidate:${release_version}-uid-${owner_uid}"
        observed_image="$(timeout 30s docker image inspect --format \
            '{{.Id}}|{{index .Config.Labels "org.osmantic.pixel.sandbox-version"}}|{{index .Config.Labels "org.osmantic.pixel.sandbox-uid"}}|{{.Config.User}}' \
            "$candidate_image" 2>/dev/null)" || {
            log_error "The ODS-managed Pixel sandbox preservation tag is missing"
            return 1
        }
        [[ "$observed_image" == "$sandbox_image_id|$release_version|$owner_uid|sandbox" ]] || {
            log_error "The ODS-managed Pixel sandbox preservation tag drifted"
            return 1
        }
        if observed_image="$(timeout 30s docker image inspect --format \
            '{{.Id}}|{{index .Config.Labels "org.osmantic.pixel.sandbox-version"}}|{{index .Config.Labels "org.osmantic.pixel.sandbox-uid"}}|{{.Config.User}}' \
            "$sandbox_image" 2>/dev/null)"; then
            [[ "$observed_image" == "$sandbox_image_id|$release_version|$owner_uid|sandbox" ]] || {
                log_error "The live Pixel sandbox image drifted"
                return 1
            }
        else
            shared_image_present=false
            [[ "$cleanup_state" == staged || "$cleanup_state" == retiring \
                || "$cleanup_state" == retired ]] || {
                log_error "The active ODS-managed Pixel sandbox image is missing"
                return 1
            }
        fi
        sandbox_container_list="$(timeout 30s docker ps -aq --filter 'name=^/pixel-sbx-agent-pixel-')" || {
            log_error "Could not enumerate ODS-managed Pixel sandbox containers"
            return 1
        }
        if [[ -n "$sandbox_container_list" ]]; then
            mapfile -t sandbox_containers <<<"$sandbox_container_list"
        fi
        local container container_identity
        for container in "${sandbox_containers[@]}"; do
            [[ "$container" =~ ^[0-9a-f]{12,64}$ ]] || {
                log_error "Docker returned an unsafe Pixel sandbox container ID"
                return 1
            }
            container_identity="$(timeout 30s docker inspect --format \
                '{{.Name}}|{{index .Config.Labels "openclaw.sandbox"}}|{{index .Config.Labels "openclaw.sessionKey"}}|{{.Image}}' \
                "$container" 2>/dev/null)" || return 1
            [[ "$container_identity" == /pixel-sbx-agent-pixel-*"|1|agent:pixel|$sandbox_image_id" ]] || {
                log_error "A container in Pixel's reserved sandbox namespace has unsafe identity"
                return 1
            }
        done
    fi

    log_info "Removing the ODS-managed Pixel host deployment..."
    if [[ -e "$gateway_unit" || -L "$gateway_unit" \
        || -e "$ingress_unit" || -L "$ingress_unit" \
        || -e "$ingress_env" || -L "$ingress_env" \
        || -e "$ingress_program" || -L "$ingress_program" \
        || -e "$extension_manager_unit" || -L "$extension_manager_unit" \
        || -e "$extension_manager_program" || -L "$extension_manager_program" \
        || -e "$artifact_promoter_unit" || -L "$artifact_promoter_unit" \
        || -e "$artifact_promoter_program" || -L "$artifact_promoter_program" \
        || -e "$workspace_preview_unit" || -L "$workspace_preview_unit" \
        || -e "$workspace_preview_program" || -L "$workspace_preview_program" \
        || -e "$system_observer_program" || -L "$system_observer_program" \
        || -e "$workspace_preview_state" || -L "$workspace_preview_state" \
        || "$ops_artifacts_present" == true ]]; then
        root_artifacts_present=true
        command -v sudo >/dev/null 2>&1 || {
            log_error "sudo is required to remove ODS-managed Pixel system artifacts"
            return 1
        }
    fi

    if [[ -e "$gateway_unit" || -e "$ingress_unit" || -e "$extension_manager_unit" \
        || -e "$artifact_promoter_unit" || -e "$workspace_preview_unit" \
        || -e "$ops_unit" ]]; then
        # Stop the ingress before the gateway it proxies to. Keep these as
        # separate calls so the shutdown order is an enforced contract rather
        # than an argument-order hint to systemctl. An interrupted first install
        # can have created the gateway before it creates ingress, so only ask
        # systemd to disable unit files whose exact reviewed artifacts exist.
        if [[ -e "$ingress_unit" ]] \
            && ! timeout 30s sudo systemctl disable --now pixel-ingress.service; then
            log_error "Could not stop ODS-managed Pixel system services; no Pixel files were removed"
            return 1
        fi
        if [[ -e "$extension_manager_unit" ]] \
            && ! timeout 30s sudo systemctl disable --now pixel-extension-manager.service; then
            log_error "Could not stop ODS-managed Pixel system services; no Pixel files were removed"
            return 1
        fi
        if [[ -e "$artifact_promoter_unit" ]] \
            && ! timeout 30s sudo systemctl disable --now pixel-artifact-promoter.service; then
            log_error "Could not stop ODS-managed Pixel system services; no Pixel files were removed"
            return 1
        fi
        if [[ -e "$workspace_preview_unit" ]] \
            && ! timeout 30s sudo systemctl disable --now pixel-workspace-preview.service; then
            log_error "Could not stop ODS-managed Pixel system services; no Pixel files were removed"
            return 1
        fi
        if [[ -e "$gateway_unit" ]] \
            && ! timeout 30s sudo systemctl disable --now openclaw-gateway.service; then
            log_error "Could not stop ODS-managed Pixel system services; no Pixel files were removed"
            return 1
        fi
        if [[ -e "$ops_unit" ]] \
            && ! timeout 30s sudo systemctl disable --now pixel-ops-broker.service; then
            log_error "Could not stop ODS-managed Pixel system services; no Pixel files were removed"
            return 1
        fi
        if systemctl is-active --quiet openclaw-gateway.service \
            || systemctl is-active --quiet pixel-ingress.service \
            || systemctl is-active --quiet pixel-extension-manager.service \
            || systemctl is-active --quiet pixel-artifact-promoter.service \
            || systemctl is-active --quiet pixel-workspace-preview.service \
            || systemctl is-active --quiet pixel-ops-broker.service; then
            log_error "ODS-managed Pixel system services are still active; no Pixel files were removed"
            return 1
        fi
    fi

    if [[ "$cleanup_state" != none ]]; then
        if (( ${#sandbox_containers[@]} > 0 )) \
            && ! timeout 30s docker rm -f -- "${sandbox_containers[@]}" >/dev/null; then
            log_error "Could not retire the ODS-managed Pixel sandbox containers"
            return 1
        fi
        case "$cleanup_state" in
            active)
                mv -T -- "$runtime_attestation" "$staged_attestation" || {
                    log_error "Could not stage the ODS-managed Pixel runtime attestation"
                    return 1
                }
                if ! mv -T -- "$current" "$staged_current"; then
                    mv -T -- "$staged_attestation" "$runtime_attestation" || true
                    log_error "Could not stage the ODS-managed Pixel active-release link"
                    return 1
                fi
                ;;
            staging-attestation)
                mv -T -- "$current" "$staged_current" || {
                    log_error "Could not resume the ODS-managed Pixel active-release staging"
                    return 1
                }
                ;;
            staging-link)
                mv -T -- "$runtime_attestation" "$staged_attestation" || {
                    log_error "Could not resume the ODS-managed Pixel runtime-attestation staging"
                    return 1
                }
                ;;
        esac
        if [[ "$shared_image_present" == true ]] \
            && ! timeout 30s docker image rm -- "$sandbox_image" >/dev/null; then
            mv -T -- "$staged_current" "$current" || true
            mv -T -- "$staged_attestation" "$runtime_attestation" || true
            log_error "Could not remove the exact ODS-managed Pixel live sandbox tag"
            return 1
        fi
        # Pixel release plans contain ODS-path-bound generated config. Preserve
        # the verified bytes for recovery, but move them out of the active
        # version namespace so another ODS path can install the same version.
        # Record the destination first so either side of the move is resumable.
        if [[ "$cleanup_state" != retiring && "$cleanup_state" != retired ]]; then
            if [[ ! -e "$retired_releases" && ! -L "$retired_releases" ]]; then
                mkdir -m 0700 -- "$retired_releases" || {
                    log_error "Could not create the private ODS-managed Pixel release archive"
                    return 1
                }
            fi
            retired_container="$(mktemp -d \
                "$retired_releases/${release_version}-${release_identity_sha256:0:12}.XXXXXXXX")" || {
                log_error "Could not reserve the ODS-managed Pixel release archive"
                return 1
            }
            retired_release_path="$retired_container/release"
            if ! python3 - "$marker" "$retired_release_path" "$release_version" \
                "$release_identity_sha256" "$install_manifest_sha256" <<'PY'
import json
import os
import pathlib
import stat
import sys
import tempfile

marker = pathlib.Path(sys.argv[1])
retired_release = pathlib.Path(sys.argv[2])
version, identity_sha256, manifest_sha256 = sys.argv[3:]
value = json.loads(marker.read_text(encoding="utf-8"))
if (
    not isinstance(value, dict)
    or value.get("schema_version") != 2
    or value.get("state") not in {"installing", "ready"}
    or value.get("initial_active_state") != "absent"
    or value.get("active_release_version") != version
    or value.get("release_identity_sha256") != identity_sha256
    or value.get("install_manifest_sha256") != manifest_sha256
):
    raise SystemExit("Pixel marker changed before its deactivation transition")
container_info = retired_release.parent.lstat()
if (
    retired_release.name != "release"
    or not stat.S_ISDIR(container_info.st_mode)
    or stat.S_ISLNK(container_info.st_mode)
    or container_info.st_uid != os.getuid()
    or container_info.st_mode & 0o077
    or retired_release.exists()
    or retired_release.is_symlink()
):
    raise SystemExit("unsafe Pixel retired release reservation")
value["state"] = "deactivating"
value["retired_release_path"] = str(retired_release)
temporary = None
try:
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=marker.parent,
        prefix=".pixel-managed-deactivating-", delete=False,
    ) as output:
        temporary = pathlib.Path(output.name)
        os.fchmod(output.fileno(), 0o600)
        json.dump(value, output, sort_keys=True, separators=(",", ":"))
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, marker)
    directory_fd = os.open(marker.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
except BaseException:
    if temporary is not None:
        temporary.unlink(missing_ok=True)
    raise
PY
            then
                log_error "Could not record the resumable ODS-managed Pixel deactivation state"
                return 1
            fi
            cleanup_state=retiring
        fi
        if [[ "$cleanup_state" == retiring ]]; then
            [[ ! -e "$retired_release_path" && ! -L "$retired_release_path" ]] || {
                log_error "The ODS-managed Pixel release archive destination is not empty"
                return 1
            }
            mv -T -- "$release_path" "$retired_release_path" || {
                log_error "Could not retire the exact ODS-managed Pixel release"
                return 1
            }
            release_path="$retired_release_path"
            cleanup_state=retired
        fi
        (cd "$release_path" && timeout 60s sha256sum -c install-manifest.sha256 >/dev/null) || {
            log_error "The retired ODS-managed Pixel release failed its exact-byte verification"
            return 1
        }
        rm -f -- "$staged_current" "$staged_attestation"
        if [[ -e "$current" || -L "$current" || -e "$runtime_attestation" || -L "$runtime_attestation" \
            || -e "$staged_current" || -L "$staged_current" \
            || -e "$staged_attestation" || -L "$staged_attestation" \
            || -e "$pixel_install/releases/$release_version" \
            || -L "$pixel_install/releases/$release_version" \
            || ! -d "$retired_release_path" ]] \
            || timeout 30s docker image inspect "$sandbox_image" >/dev/null 2>&1; then
            log_error "ODS-managed Pixel active-state cleanup was incomplete"
            return 1
        fi
    fi

    if [[ "$ops_artifacts_present" == true ]]; then
        # Remove the broker's bounded state only after the service is inactive.
        # The privileged helper rechecks every entry immediately before the
        # recursive operation and rejects links, devices, mounts, hardlinks,
        # foreign identities, and world-accessible mutable state.
        if ! sudo python3 - "$ops_state" "$ops_uid" "$ops_gid" "$owner_uid" <<'PY'
import os
import pathlib
import shutil
import stat
import sys

root = pathlib.Path(sys.argv[1])
broker_uid = int(sys.argv[2])
broker_gid = int(sys.argv[3])
owner_uid = int(sys.argv[4])
if not root.is_absolute() or root == pathlib.Path("/"):
    raise SystemExit("unsafe Pixel Operations Broker cleanup root")
if not root.exists() and not root.is_symlink():
    raise SystemExit(0)
root_info = root.lstat()
if (not stat.S_ISDIR(root_info.st_mode) or stat.S_ISLNK(root_info.st_mode)
        or root_info.st_uid != broker_uid or root_info.st_gid != broker_gid
        or stat.S_IMODE(root_info.st_mode) != 0o750):
    raise SystemExit("unsafe Pixel Operations Broker cleanup state root")
root_absolute = pathlib.Path(os.path.abspath(root))
try:
    mount_lines = pathlib.Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
except OSError as error:
    raise SystemExit("cannot inspect mounts before Pixel Operations cleanup") from error
for line in mount_lines:
    fields = line.split()
    if len(fields) < 5:
        raise SystemExit("invalid mount table while cleaning Pixel Operations state")
    mount_text = fields[4]
    for encoded, decoded in ((r"\040", " "), (r"\011", "\t"), (r"\012", "\n"), (r"\134", "\\")):
        mount_text = mount_text.replace(encoded, decoded)
    mount_path = pathlib.Path(os.path.abspath(mount_text))
    if mount_path == root_absolute or root_absolute in mount_path.parents:
        raise SystemExit(f"mount inside Pixel Operations Broker cleanup state: {mount_path}")
root_device = root_info.st_dev
bounded_service_profiles = {
    root / ".bash_logout",
    root / ".bashrc",
    root / ".profile",
}
for current, directories, files in os.walk(root, topdown=True, followlinks=False):
    for name in (*directories, *files):
        path = pathlib.Path(current) / name
        info = path.lstat()
        if (stat.S_ISLNK(info.st_mode) or info.st_dev != root_device
                or info.st_uid not in {broker_uid, owner_uid}
                or info.st_gid != broker_gid):
            raise SystemExit(f"unsafe Pixel Operations Broker cleanup entry: {path}")
        if path in bounded_service_profiles:
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != broker_uid
                    or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) not in {0o600, 0o640, 0o644}
                    or info.st_size > 64 * 1024):
                raise SystemExit(f"unsafe Pixel Operations cleanup profile: {path}")
            continue
        if info.st_mode & 0o007:
            raise SystemExit(f"unsafe Pixel Operations Broker cleanup entry: {path}")
        if stat.S_ISDIR(info.st_mode):
            if info.st_mode & (stat.S_ISUID | stat.S_ISVTX):
                raise SystemExit(f"unsafe Pixel Operations Broker cleanup directory: {path}")
        elif stat.S_ISREG(info.st_mode):
            if info.st_nlink != 1 or info.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
                raise SystemExit(f"unsafe Pixel Operations Broker cleanup file: {path}")
        else:
            raise SystemExit(f"special file in Pixel Operations Broker cleanup state: {path}")
shutil.rmtree(root)
PY
        then
            log_error "Could not remove the verified Pixel Operations Broker state"
            return 1
        fi
        if ! sudo rm -f -- "$ops_unit" "$ops_dropin" "$ops_env" "$ops_policy" "$ops_program" \
            "$ops_extension_program" "$ops_extension_catalog" "$ops_extension_manager" \
            || ! { [[ ! -e "$ops_dropin_dir" && ! -L "$ops_dropin_dir" ]] || sudo rmdir -- "$ops_dropin_dir"; } \
            || ! { [[ ! -e "$ops_install" && ! -L "$ops_install" ]] || sudo rmdir -- "$ops_install"; } \
            || ! { [[ ! -e "$ops_policy_dir" && ! -L "$ops_policy_dir" ]] || sudo rmdir -- "$ops_policy_dir"; }; then
            log_error "Could not remove the verified Pixel Operations Broker artifacts"
            return 1
        fi
        if [[ "$ops_user_present" == true ]]; then
            [[ "$(getent passwd "$ops_user" 2>/dev/null || true)" == "$ops_passwd_entry" ]] || {
                log_error "Pixel Operations Broker user changed before removal"
                return 1
            }
            if ! timeout 30s sudo userdel "$ops_user"; then
                log_error "Could not remove the isolated Pixel Operations Broker user"
                return 1
            fi
        fi
        if [[ "$ops_group_present" == true ]]; then
            [[ "$(getent group "$ops_group" 2>/dev/null || true)" == "$ops_group_entry" ]] || {
                log_error "Pixel Operations Broker group changed before removal"
                return 1
            }
            if ! timeout 30s sudo groupdel "$ops_group"; then
                log_error "Could not remove the isolated Pixel Operations Broker group"
                return 1
            fi
        fi
        if [[ -e "$ops_unit" || -L "$ops_unit" || -e "$ops_dropin" || -L "$ops_dropin" \
            || -e "$ops_dropin_dir" || -L "$ops_dropin_dir" \
            || -e "$ops_env" || -L "$ops_env" \
            || -e "$ops_policy" || -L "$ops_policy" || -e "$ops_install" || -L "$ops_install" \
            || -e "$ops_state" || -L "$ops_state" \
            || -n "$(getent passwd "$ops_user" 2>/dev/null || true)" \
            || -n "$(getent group "$ops_group" 2>/dev/null || true)" ]]; then
            log_error "Pixel Operations Broker cleanup was incomplete"
            return 1
        fi
    fi

    if [[ "$root_artifacts_present" == "true" ]]; then
        if [[ -e "$workspace_preview_state" || -L "$workspace_preview_state" ]]; then
            if ! sudo python3 - "$workspace_preview_state" "$owner_uid" <<'PY'
import os, pathlib, shutil, stat, sys

root = pathlib.Path(sys.argv[1])
owner_uid = int(sys.argv[2])
if root.name != "ods-pixel-preview" or not root.is_absolute() or root == pathlib.Path("/"):
    raise SystemExit("unsafe Pixel workspace preview cleanup root")
for path in [root, *root.rglob("*")]:
    info = path.lstat()
    if stat.S_ISDIR(info.st_mode):
        if stat.S_ISLNK(info.st_mode) or info.st_uid != owner_uid or stat.S_IMODE(info.st_mode) != 0o700:
            raise SystemExit("unsafe Pixel workspace preview cleanup directory")
    elif stat.S_ISREG(info.st_mode):
        if (stat.S_ISLNK(info.st_mode) or info.st_nlink != 1
                or info.st_uid != owner_uid or stat.S_IMODE(info.st_mode) != 0o400):
            raise SystemExit("unsafe Pixel workspace preview cleanup file")
    else:
        raise SystemExit("unsafe Pixel workspace preview cleanup artifact")
shutil.rmtree(root)
PY
            then
                log_error "Could not remove verified Pixel workspace preview state"
                return 1
            fi
        fi
        if ! sudo rm -f -- "$gateway_unit" "$ingress_unit" "$ingress_env" "$ingress_program" \
            "$extension_manager_unit" "$extension_manager_program" \
            "$artifact_promoter_unit" "$artifact_promoter_program" \
            "$workspace_preview_unit" "$workspace_preview_program" \
            "$system_observer_program" \
            || ! sudo systemctl daemon-reload; then
            log_error "Could not remove ODS-managed Pixel system artifacts"
            return 1
        fi
        if [[ -e "$gateway_unit" || -e "$ingress_unit" || -e "$ingress_env" \
            || -e "$ingress_program" || -e "$extension_manager_unit" \
            || -e "$extension_manager_program" || -e "$artifact_promoter_unit" \
            || -e "$artifact_promoter_program" || -e "$workspace_preview_unit" \
            || -e "$workspace_preview_program" || -e "$system_observer_program" \
            || -e "$workspace_preview_state" ]]; then
            log_error "ODS-managed Pixel system artifact cleanup was incomplete"
            return 1
        fi
    fi

    if [[ -e "$exec_control" || -L "$exec_control" ]]; then
        python3 - "$exec_control" <<'PY'
import os, pathlib, re, stat, sys

root = pathlib.Path(sys.argv[1])
root_info = root.lstat()
if (not stat.S_ISDIR(root_info.st_mode) or stat.S_ISLNK(root_info.st_mode)
        or root_info.st_uid != os.getuid()
        or (root_info.st_mode & 0o777) != 0o700):
    raise SystemExit("unsafe ODS-managed Pixel execution control cleanup")
for item in root.iterdir():
    item_info = item.lstat()
    is_wrapper = item.name in {"cancellable-exec.sh", "sudo"}
    is_marker = bool(re.fullmatch(r"[0-9a-f]{64}\.cancel", item.name))
    is_temporary = bool(re.fullmatch(r"\.[0-9a-f]{64}\.[0-9]+\.[0-9a-f]{16}\.tmp", item.name))
    if (not (is_wrapper or is_marker or is_temporary)
            or not stat.S_ISREG(item_info.st_mode) or stat.S_ISLNK(item_info.st_mode)
            or item_info.st_nlink != 1 or item_info.st_uid != os.getuid()
            or item_info.st_mode & 0o077):
        raise SystemExit("unsafe ODS-managed Pixel execution control cleanup")
    if is_wrapper and (item_info.st_mode & 0o777) != 0o500:
        raise SystemExit("unsafe ODS-managed Pixel execution wrapper cleanup")
    if not is_wrapper and item_info.st_size != 0:
        raise SystemExit("unsafe ODS-managed Pixel execution marker cleanup")
    item.unlink()
root.rmdir()
PY
    fi
    rm -f -- "$openclaw_config" "$gateway_env" "$onboarding" "$ops_owner_policy" \
        "$ops_owner_extension_catalog" "$extension_manager_owner_unit" \
        "$artifact_promoter_owner_unit" "$workspace_preview_owner_unit"
    if [[ -e "$openclaw_config" || -e "$gateway_env" || -e "$onboarding" \
        || -e "$ops_owner_policy" || -L "$ops_owner_policy" \
        || -e "$ops_owner_extension_catalog" || -L "$ops_owner_extension_catalog" \
        || -e "$extension_manager_owner_unit" || -L "$extension_manager_owner_unit" \
        || -e "$artifact_promoter_owner_unit" || -L "$artifact_promoter_owner_unit" \
        || -e "$workspace_preview_owner_unit" || -L "$workspace_preview_owner_unit" \
        || -e "$exec_control" || -L "$exec_control" ]]; then
        log_error "ODS-managed Pixel owner artifact cleanup was incomplete"
        return 1
    fi
    rm -f -- "$marker"
    [[ ! -e "$marker" && ! -L "$marker" ]] || {
        log_error "ODS-managed Pixel marker cleanup was incomplete"
        return 1
    }
    log_ok "ODS-managed Pixel host deployment removed"
    )
}
