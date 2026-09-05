#!/usr/bin/env bash
# Install and verify Pixel's host-side ODS integration. Importing this file has
# no side effects. Callers must have already selected ENABLE_PIXEL_RUNTIME=true.

ods_pixel_install_owner() {
    local owner="${INSTALL_USER:-${SUDO_USER:-${USER:-}}}"
    [[ -n "$owner" && "$owner" != root && "$owner" =~ ^[A-Za-z_][A-Za-z0-9_.-]{0,63}$ ]] || {
        printf '%s\n' 'error: Pixel requires a non-root ODS install owner' >&2
        return 1
    }
    id "$owner" >/dev/null 2>&1 || return 1
    printf '%s\n' "$owner"
}

ods_pixel_owner_home() {
    local owner="$1" home
    home="$(getent passwd "$owner" 2>/dev/null | awk -F: 'NR == 1 { print $6 }')"
    [[ "$home" == /* && "$home" != / && "$home" != *[[:space:]\\]* && -d "$home" && ! -L "$home" ]] || return 1
    printf '%s\n' "$home"
}

ods_pixel_run_as_owner() {
    local owner="$1" home="$2"
    shift 2
    if ods_sudo_available && command -v sudo >/dev/null 2>&1; then
        ods_sudo -u "$owner" -- env HOME="$home" USER="$owner" LOGNAME="$owner" PATH="$PATH" "$@"
    elif [[ "$(id -un)" == "$owner" ]]; then
        env HOME="$home" USER="$owner" LOGNAME="$owner" PATH="$PATH" "$@"
    else
        printf '%s\n' 'error: cannot enter the Pixel install owner identity' >&2
        return 1
    fi
}

ods_pixel_run_as_owner_with_umask() {
    local owner="$1" home="$2" requested_umask="$3"
    shift 3
    [[ "$requested_umask" =~ ^0[0-7]{3}$ && "$#" -gt 0 ]] || return 1

    # Set the mask inside the target owner's process. sudo may apply its own
    # configured umask, so a caller-side subshell is not sufficient.
    ods_pixel_run_as_owner "$owner" "$home" sh -c '
        requested_umask=$1
        shift
        umask "$requested_umask"
        exec "$@"
    ' sh "$requested_umask" "$@"
}

_ods_pixel_prepare_attempt_log() {
    local owner="$1" home="$2" path="$3" parent temporary parent_kind parent_uid
    [[ "$path" == /* && "$path" != / && "$path" != *$'\n'* && "$path" != *$'\r'* ]] || return 1
    parent="${path%/*}"
    if [[ -e "$parent" || -L "$parent" ]]; then
        [[ -d "$parent" && ! -L "$parent" ]] || return 1
        parent_kind="$(stat -c '%F' -- "$parent")" || return 1
        parent_uid="$(stat -c '%u' -- "$parent")" || return 1
        [[ "$parent_kind" == directory && "$parent_uid" == "$(id -u "$owner")" ]] || return 1
    else
        ods_pixel_run_as_owner "$owner" "$home" install -d -m 0700 -- "$parent" || return 1
    fi
    temporary="$(ods_pixel_run_as_owner "$owner" "$home" mktemp "$parent/.pixel-install.XXXXXX")" || return 1
    [[ "$temporary" == "$parent"/.pixel-install.* && -f "$temporary" && ! -L "$temporary" ]] || {
        if [[ -n "$temporary" ]]; then
            ods_pixel_run_as_owner "$owner" "$home" rm -f -- "$temporary" >/dev/null 2>&1 || true
        fi
        return 1
    }
    if ! ods_pixel_run_as_owner "$owner" "$home" chmod 0600 -- "$temporary" \
        || ! ods_pixel_run_as_owner "$owner" "$home" mv -fT -- "$temporary" "$path"; then
        ods_pixel_run_as_owner "$owner" "$home" rm -f -- "$temporary" >/dev/null 2>&1 || true
        return 1
    fi
    printf '%s\n' "$path"
}

_ods_pixel_assert_managed_state() {
    local owner="$1" home="$2" marker marker_dir pixel_install
    marker="$home/.config/ods/pixel-managed.json"
    marker_dir="${marker%/*}"
    pixel_install="$home/.local/share/pixel"
    local gateway_unit="${ODS_PIXEL_GATEWAY_UNIT_PATH:-/etc/systemd/system/openclaw-gateway.service}"
    if [[ -e "$marker_dir" || -L "$marker_dir" ]]; then
        [[ -d "$marker_dir" && ! -L "$marker_dir" ]] || return 1
        [[ "$(stat -c '%u' -- "$marker_dir")" == "$(id -u "$owner")" ]] || return 1
        ods_pixel_run_as_owner "$owner" "$home" chmod 0700 -- "$marker_dir" || return 1
    else
        ods_pixel_run_as_owner "$owner" "$home" install -d -m 0700 -- "$marker_dir" || return 1
    fi
    if [[ -e "$marker" || -L "$marker" ]]; then
        [[ -f "$marker" && ! -L "$marker" ]] || return 1
        [[ "$(stat -c '%u' -- "$marker")" == "$(id -u "$owner")" ]] || return 1
        (( (8#$(stat -c '%a' -- "$marker") & 0077) == 0 )) || return 1
        ods_pixel_run_as_owner "$owner" "$home" python3 - "$marker" "${INSTALL_DIR:?}" <<'PY'
import json, pathlib, sys
value = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
if (value.get("schema_version") not in {1, 2} or value.get("manager") != "ods"
        or value.get("install_dir") != sys.argv[2]):
    raise SystemExit("Pixel management marker does not match this ODS install")
if value.get("schema_version") == 2 and value.get("initial_active_state") != "absent":
    raise SystemExit("Pixel management marker has no safe pre-install state")
PY
        return
    fi

    # Never adopt or rewrite an ambient user-managed Pixel/OpenClaw deployment.
    for existing in \
        "$home/.openclaw/openclaw.json" \
        "$home/.config/pixel-agent/gateway.env" \
        "$home/.config/pixel-deployment/onboarding.json" \
        "$pixel_install/current" \
        "$pixel_install/runtime-attestation.json" \
        "$gateway_unit"; do
        if [[ -e "$existing" || -L "$existing" ]]; then
            ai_bad "An existing non-ODS Pixel/OpenClaw deployment was found. ODS will not overwrite it."
            return 1
        fi
    done

    ods_pixel_run_as_owner "$owner" "$home" python3 - "$marker" "${INSTALL_DIR:?}" "${PIXEL_SOURCE_REF:?}" <<'PY'
import json, os, pathlib, sys, tempfile
path = pathlib.Path(sys.argv[1])
payload = json.dumps({
    "schema_version": 2,
    "manager": "ods",
    "state": "installing",
    "initial_active_state": "absent",
    "install_dir": sys.argv[2],
    "pixel_source_ref": sys.argv[3],
}, indent=2, sort_keys=True) + "\n"
fd, temporary = tempfile.mkstemp(prefix=".pixel-managed.", dir=path.parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
}

# Return 0 when an exact ODS-managed Pixel deployment must be retired before
# the installer copies newer source over the installed ownership evidence.
# Return 1 when no transition is needed, and 2 for unsafe or ambiguous state.
_ods_pixel_source_transition_required() {
    local owner="$1" home="$2" requested_ref="$3" marker
    marker="$home/.config/ods/pixel-managed.json"
    [[ "$requested_ref" =~ ^[0-9a-f]{40}$ ]] || return 2
    if [[ ! -e "$marker" && ! -L "$marker" ]]; then
        return 1
    fi
    ods_pixel_run_as_owner "$owner" "$home" python3 - \
        "$marker" "${INSTALL_DIR:?}" "$requested_ref" <<'PY'
import json, os, pathlib, re, stat, sys

path = pathlib.Path(sys.argv[1])
info = path.lstat()
if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
        or info.st_nlink != 1 or info.st_uid != os.getuid()
        or info.st_mode & 0o077 or info.st_size > 65536):
    raise SystemExit(2)
value = json.loads(path.read_text(encoding="utf-8"))
source_ref = value.get("pixel_source_ref")
requested_ref = sys.argv[3]
if (value.get("schema_version") != 2 or value.get("manager") != "ods"
        or value.get("initial_active_state") != "absent"
        or value.get("install_dir") != sys.argv[2]
        or value.get("state") not in {"ready", "installing", "deactivating"}
        or not isinstance(source_ref, str)
        or not re.fullmatch(r"[0-9a-f]{40}", source_ref)
        or value.get("requested_source_ref") not in {None, source_ref, requested_ref}):
    raise SystemExit(2)
raise SystemExit(0 if value.get("state") == "deactivating" or source_ref != requested_ref else 1)
PY
}

_ods_pixel_record_verified_state() {
    local owner="$1" home="$2" contract_sha256="$3" state="$4" pixel_root="$5"
    local marker config manifest sandbox_image sandbox_image_id
    [[ "$contract_sha256" =~ ^[0-9a-f]{64}$ ]] || return 1
    [[ "$state" == installing || "$state" == ready ]] || return 1
    marker="$home/.config/ods/pixel-managed.json"
    config="$home/.openclaw/openclaw.json"
    manifest="$pixel_root/RELEASE-MANIFEST.json"
    sandbox_image="$(ods_pixel_run_as_owner "$owner" "$home" python3 - "$manifest" <<'PY'
import json, pathlib, re, sys
path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text(encoding="utf-8"))
image = value.get("sandboxImage") if isinstance(value, dict) else None
if not isinstance(image, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,255}:[A-Za-z0-9][A-Za-z0-9._-]{0,127}", image):
    raise SystemExit("invalid Pixel sandbox image reference")
print(image)
PY
)" || return 1
    sandbox_image_id="$(ods_pixel_run_as_owner "$owner" "$home" timeout 30s docker image inspect \
        --format '{{.Id}}' "$sandbox_image")" || return 1
    [[ "$sandbox_image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || return 1
    ods_pixel_run_as_owner "$owner" "$home" python3 - \
        "$marker" "$config" "${INSTALL_DIR:?}" "${PIXEL_SOURCE_REF:?}" \
        "$contract_sha256" "$state" "$home" "$sandbox_image" "$sandbox_image_id" <<'PY'
import hashlib, json, os, pathlib, stat, sys, tempfile

path = pathlib.Path(sys.argv[1])
info = path.lstat()
if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_nlink != 1
        or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 65536):
    raise SystemExit("invalid Pixel management marker")
value = json.loads(path.read_text(encoding="utf-8"))
if (value.get("schema_version") != 2 or value.get("manager") != "ods"
        or value.get("install_dir") != sys.argv[3]
        or value.get("initial_active_state") != "absent"):
    raise SystemExit("Pixel management marker does not match this ODS install")
if value.get("requested_source_ref") not in {None, sys.argv[4]}:
    raise SystemExit("Pixel management marker requested source does not match the verified source")
config_path = pathlib.Path(sys.argv[2])
config_info = config_path.lstat()
if (not stat.S_ISREG(config_info.st_mode) or stat.S_ISLNK(config_info.st_mode)
        or config_info.st_uid != os.getuid() or config_info.st_mode & 0o077
        or config_info.st_size > 2 * 1024 * 1024):
    raise SystemExit("invalid ODS-managed OpenClaw configuration")
config = json.loads(config_path.read_text(encoding="utf-8"))
if not isinstance(config, dict):
    raise SystemExit("invalid ODS-managed OpenClaw configuration")
canonical_config = json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
if sys.argv[6] not in {"installing", "ready"}:
    raise SystemExit("invalid Pixel management state")

home = pathlib.Path(sys.argv[7])
install_root = home / ".local/share/pixel"
releases_root = install_root / "releases"
current = install_root / "current"
current_info = current.lstat()
if not stat.S_ISLNK(current_info.st_mode) or current_info.st_uid != os.getuid():
    raise SystemExit("invalid active Pixel release link")
release = current.resolve(strict=True)
releases_info = releases_root.lstat()
release_info = release.lstat()
if (not stat.S_ISDIR(releases_info.st_mode) or stat.S_ISLNK(releases_info.st_mode)
        or releases_info.st_uid != os.getuid() or releases_info.st_mode & 0o022
        or not stat.S_ISDIR(release_info.st_mode) or stat.S_ISLNK(release_info.st_mode)
        or release_info.st_uid != os.getuid() or release_info.st_mode & 0o022
        or release.parent.resolve(strict=True) != releases_root.resolve(strict=True)):
    raise SystemExit("active Pixel release is outside its release root")

def regular_file(item: pathlib.Path, maximum: int, private: bool = False) -> bytes:
    details = item.lstat()
    if (not stat.S_ISREG(details.st_mode) or stat.S_ISLNK(details.st_mode)
            or details.st_uid != os.getuid() or details.st_nlink != 1
            or details.st_size > maximum or details.st_mode & 0o022
            or (private and details.st_mode & 0o077)):
        raise SystemExit(f"unsafe verified Pixel artifact: {item}")
    return item.read_bytes()

identity_bytes = regular_file(release / "release-identity.json", 65536)
manifest_bytes = regular_file(release / "install-manifest.sha256", 2 * 1024 * 1024)
attestation_bytes = regular_file(install_root / "runtime-attestation.json", 2 * 1024 * 1024, private=True)
identity = json.loads(identity_bytes)
attestation = json.loads(attestation_bytes)
version = identity.get("pixel") if isinstance(identity, dict) else None
source = identity.get("source") if isinstance(identity, dict) else None
if (not isinstance(version, str) or release.name != version
        or not isinstance(source, dict) or source.get("state") != "git-clean"
        or source.get("commit") != sys.argv[4]):
    raise SystemExit("active Pixel release is not bound to the configured source")
identity_sha256 = hashlib.sha256(identity_bytes).hexdigest()
manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
if (not isinstance(attestation, dict) or attestation.get("kind") != "pixel-runtime-attestation"
        or attestation.get("status") not in {"verified", "limited"}
        or attestation.get("pixel") != version or attestation.get("source") != source
        or not isinstance(attestation.get("release"), dict)
        or attestation["release"].get("sourceIdentitySha256") != identity_sha256
        or attestation["release"].get("installManifestSha256") != manifest_sha256):
    raise SystemExit("Pixel runtime attestation does not bind the active release")
if not isinstance(sys.argv[8], str) or not isinstance(sys.argv[9], str):
    raise SystemExit("invalid Pixel sandbox binding")

value["state"] = sys.argv[6]
value["pixel_source_ref"] = sys.argv[4]
value.pop("requested_source_ref", None)
value["contract_sha256"] = sys.argv[5]
value["configuration_sha256"] = hashlib.sha256(b"ods-pixel-openclaw-v1\0" + canonical_config).hexdigest()
value["active_release_version"] = version
value["release_identity_sha256"] = identity_sha256
value["install_manifest_sha256"] = manifest_sha256
value["sandbox_image"] = sys.argv[8]
value["sandbox_image_id"] = sys.argv[9]
fd, temporary = tempfile.mkstemp(prefix=".pixel-managed.", dir=path.parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
}

_ods_pixel_mark_verified_installing() {
    _ods_pixel_record_verified_state "$1" "$2" "$3" installing "$4"
}

_ods_pixel_mark_ready() {
    _ods_pixel_record_verified_state "$1" "$2" "$3" ready "$4"
}

_ods_pixel_contract_sha256() {
    local owner="$1" home="$2" answers="$3"
    local extension_catalog="${INSTALL_DIR:?}/data/pixel/extension-catalog.json"
    local extension_helper="${INSTALL_DIR:?}/extensions/services/pixel-agent/host/extension_search.py"
    local extension_manager="${INSTALL_DIR:?}/extensions/services/pixel-agent/host/extension_manager.py"
    local extension_manager_unit="${INSTALL_DIR:?}/data/pixel/extension-manager.service"
    local artifact_promoter="${INSTALL_DIR:?}/extensions/services/pixel-agent/host/artifact_promoter.py"
    local artifact_promoter_unit="${INSTALL_DIR:?}/data/pixel/artifact-promoter.service"
    local workspace_preview="${INSTALL_DIR:?}/extensions/services/pixel-agent/host/workspace_preview.py"
    local workspace_preview_unit="${INSTALL_DIR:?}/data/pixel/workspace-preview.service"
    local system_observer="${INSTALL_DIR:?}/extensions/services/pixel-agent/host/system_observe.py"
    local operations_service_dropin="${INSTALL_DIR:?}/extensions/services/pixel-agent/host/pixel-ops-broker-ods.conf"
    local approval_helper="${INSTALL_DIR:?}/bin/ods-pixel-approve"
    ods_pixel_run_as_owner "$owner" "$home" python3 - "$answers" "$extension_catalog" \
        "$extension_helper" "$extension_manager" "$extension_manager_unit" "$approval_helper" \
        "$artifact_promoter" "$artifact_promoter_unit" "$operations_service_dropin" \
        "$workspace_preview" "$workspace_preview_unit" "$system_observer" <<'PY'
import hashlib, json, os, pathlib, stat, sys

path, catalog_path, helper_path, manager_path, manager_unit_path, approval_path, promoter_path, promoter_unit_path, operations_service_dropin_path, preview_path, preview_unit_path, system_observer_path = map(pathlib.Path, sys.argv[1:13])

def read_private_regular(candidate, label):
    info = candidate.lstat()
    if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.getuid() or info.st_mode & 0o077
            or info.st_size > 2 * 1024 * 1024):
        raise SystemExit(f"invalid ODS Pixel {label}")
    return candidate.read_bytes()

answers_payload = read_private_regular(path, "onboarding contract")
try:
    answers_value = json.loads(answers_payload)
except (UnicodeDecodeError, json.JSONDecodeError) as exc:
    raise SystemExit("invalid ODS Pixel onboarding contract") from exc
policy_value = answers_value.get("operationsPolicyFile") if isinstance(answers_value, dict) else None
expected_policy = path.parent / "operations-policy.json"
if not isinstance(policy_value, str) or pathlib.Path(policy_value) != expected_policy:
    raise SystemExit("ODS Pixel Operations policy is outside the managed contract")
policy_payload = read_private_regular(expected_policy, "Operations policy")
catalog_payload = read_private_regular(catalog_path, "extension catalog")
helper_payloads = []
for helper in (helper_path, manager_path, manager_unit_path, approval_path, promoter_path, promoter_unit_path, operations_service_dropin_path, preview_path, preview_unit_path, system_observer_path):
    helper_info = helper.lstat()
    if (not stat.S_ISREG(helper_info.st_mode) or stat.S_ISLNK(helper_info.st_mode)
            or helper_info.st_nlink != 1 or helper_info.st_uid != os.getuid()
            or helper_info.st_mode & 0o022 or helper_info.st_size > 2 * 1024 * 1024):
        raise SystemExit("invalid ODS Pixel extension helper")
    helper_payloads.append(helper.read_bytes())
digest = hashlib.sha256()
digest.update(b"ods-pixel-contract-v9\0")
for payload in (answers_payload, policy_payload, catalog_payload, *helper_payloads):
    digest.update(len(payload).to_bytes(8, "big"))
    digest.update(payload)
print(digest.hexdigest())
PY
}

_ods_pixel_verify_operations_policy_custody() {
    local owner="$1" home="$2" source_policy="$3"
    local installed_policy="${4:-/etc/pixel-ops-broker/policy.json}" expected_uid="${5:-0}"
    local metadata kind uid mode size
    [[ "$source_policy" == /* && "$installed_policy" == /* && "$expected_uid" =~ ^[0-9]+$ ]] || return 1
    ods_pixel_run_as_owner "$owner" "$home" test -f "$source_policy" || return 1
    ods_pixel_run_as_owner "$owner" "$home" test ! -L "$source_policy" || return 1
    metadata="$(ods_sudo stat -c '%F|%u|%a|%s' -- "$installed_policy")" || return 1
    IFS='|' read -r kind uid mode size <<<"$metadata"
    [[ "$kind" == "regular file" && "$uid" == "$expected_uid" && "$mode" == 640
        && "$size" =~ ^[0-9]+$ && "$size" -le 2097152 ]] || return 1
    ods_sudo cmp -s -- "$source_policy" "$installed_policy"
}

_ods_pixel_harden_operations_state_profiles() {
    local state_root="${1:-/var/lib/pixel-ops-broker}"
    local broker_user="${2:-pixel-ops-broker}" broker_uid broker_gid
    [[ "$state_root" == /* && "$state_root" != / \
        && "$broker_user" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || return 1
    broker_uid="$(ods_sudo id -u "$broker_user")" || return 1
    broker_gid="$(ods_sudo id -g "$broker_user")" || return 1
    [[ "$broker_uid" =~ ^[0-9]+$ && "$broker_gid" =~ ^[0-9]+$ ]] || return 1
    ods_sudo python3 - "$state_root" "$broker_uid" "$broker_gid" <<'PY'
import os
import pathlib
import stat
import sys

root = pathlib.Path(sys.argv[1])
uid = int(sys.argv[2])
gid = int(sys.argv[3])
root_info = root.lstat()
if (not stat.S_ISDIR(root_info.st_mode) or stat.S_ISLNK(root_info.st_mode)
        or root_info.st_uid != uid or root_info.st_gid != gid
        or stat.S_IMODE(root_info.st_mode) != 0o750):
    raise SystemExit("unsafe Pixel Operations state root")

profiles = [root / name for name in (".bash_logout", ".bashrc", ".profile")]
present = []
for path in profiles:
    if not path.exists() and not path.is_symlink():
        continue
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_nlink != 1 or info.st_uid != uid or info.st_gid != gid
            or stat.S_IMODE(info.st_mode) not in {0o600, 0o640, 0o644}
            or info.st_size > 64 * 1024):
        raise SystemExit(f"unsafe Pixel Operations service profile: {path}")
    present.append(path)

# Validate the complete bounded set before changing any mode.
for path in present:
    os.chmod(path, 0o600, follow_symlinks=False)
for path in present:
    if stat.S_IMODE(path.lstat().st_mode) != 0o600:
        raise SystemExit(f"could not harden Pixel Operations service profile: {path}")
PY
}

_ods_pixel_managed_contract_matches() {
    local owner="$1" home="$2" contract_sha256="$3" marker config
    [[ "$contract_sha256" =~ ^[0-9a-f]{64}$ ]] || return 1
    marker="$home/.config/ods/pixel-managed.json"
    config="$home/.openclaw/openclaw.json"
    ods_pixel_run_as_owner "$owner" "$home" python3 - "$marker" "$config" "${INSTALL_DIR:?}" "${PIXEL_SOURCE_REF:?}" "$contract_sha256" <<'PY'
import hashlib, json, os, pathlib, stat, sys

path = pathlib.Path(sys.argv[1])
info = path.lstat()
if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != os.getuid()
        or info.st_size > 65536 or info.st_mode & 0o077):
    raise SystemExit(1)
value = json.loads(path.read_text(encoding="utf-8"))
expected = {
    "schema_version": 2,
    "manager": "ods",
    "initial_active_state": "absent",
    "install_dir": sys.argv[3],
    "pixel_source_ref": sys.argv[4],
    "contract_sha256": sys.argv[5],
}
if value.get("state") not in {"ready", "installing"} or any(value.get(key) != item for key, item in expected.items()):
    raise SystemExit(1)
config_path = pathlib.Path(sys.argv[2])
config_info = config_path.lstat()
if (not stat.S_ISREG(config_info.st_mode) or stat.S_ISLNK(config_info.st_mode)
        or config_info.st_uid != os.getuid() or config_info.st_mode & 0o077
        or config_info.st_size > 2 * 1024 * 1024):
    raise SystemExit(1)
config = json.loads(config_path.read_text(encoding="utf-8"))
if not isinstance(config, dict):
    raise SystemExit(1)
canonical_config = json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
observed = hashlib.sha256(b"ods-pixel-openclaw-v1\0" + canonical_config).hexdigest()
if value.get("configuration_sha256") != observed:
    raise SystemExit(1)
for key in ("release_identity_sha256", "install_manifest_sha256"):
    item = value.get(key)
    if not isinstance(item, str) or len(item) != 64 or any(ch not in "0123456789abcdef" for ch in item):
        raise SystemExit(1)
if (not isinstance(value.get("active_release_version"), str)
        or not isinstance(value.get("sandbox_image"), str)
        or not isinstance(value.get("sandbox_image_id"), str)
        or len(value["sandbox_image_id"]) != 71 or not value["sandbox_image_id"].startswith("sha256:")
        or any(ch not in "0123456789abcdef" for ch in value["sandbox_image_id"][7:])):
    raise SystemExit(1)
PY
}

_ods_pixel_verified_source_matches() {
    local owner="$1" home="$2" marker
    marker="$home/.config/ods/pixel-managed.json"
    ods_pixel_run_as_owner "$owner" "$home" python3 - "$marker" "${INSTALL_DIR:?}" "${PIXEL_SOURCE_REF:?}" <<'PY'
import json, os, pathlib, stat, sys

path = pathlib.Path(sys.argv[1])
info = path.lstat()
if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 65536):
    raise SystemExit(1)
value = json.loads(path.read_text(encoding="utf-8"))
if (value.get("schema_version") != 2 or value.get("manager") != "ods"
        or value.get("initial_active_state") != "absent"
        or value.get("install_dir") != sys.argv[2]
        or value.get("pixel_source_ref") != sys.argv[3]
        or value.get("state") not in {"ready", "installing"}):
    raise SystemExit(1)
for key in ("contract_sha256", "configuration_sha256", "release_identity_sha256", "install_manifest_sha256"):
    item = value.get(key)
    if not isinstance(item, str) or len(item) != 64 or any(ch not in "0123456789abcdef" for ch in item):
        raise SystemExit(1)
PY
}

_ods_pixel_candidate_config_matches_live() {
    local owner="$1" home="$2" candidate="$3" live
    live="$home/.openclaw/openclaw.json"
    ods_pixel_run_as_owner "$owner" "$home" python3 - "$live" "$candidate" <<'PY'
import json, os, pathlib, stat, sys

values = []
for raw in sys.argv[1:]:
    path = pathlib.Path(raw)
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.getuid() or info.st_mode & 0o022
            or info.st_size > 2 * 1024 * 1024):
        raise SystemExit(1)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(1)
    values.append(json.dumps(value, sort_keys=True, separators=(",", ":")))
if values[0] != values[1]:
    raise SystemExit(1)
PY
}

_ods_pixel_uses_stable_model_alias() {
    local owner="$1" home="$2" answers="$3" config
    config="$home/.openclaw/openclaw.json"
    ods_pixel_run_as_owner "$owner" "$home" python3 - "$answers" "$config" <<'PY'
import json, os, pathlib, re, stat, sys

documents = []
for raw in sys.argv[1:]:
    path = pathlib.Path(raw)
    info = path.lstat()
    parent = path.parent.lstat()
    if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_nlink != 1 or info.st_uid != os.getuid()
            or info.st_mode & 0o077 or info.st_size > 2 * 1024 * 1024
            or not stat.S_ISDIR(parent.st_mode) or stat.S_ISLNK(parent.st_mode)
            or parent.st_uid != os.getuid() or parent.st_mode & 0o022):
        raise SystemExit(1)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(1)
    documents.append(value)

answers, config = documents
if answers.get("modelProvider") != "ods-gateway" or answers.get("modelId") != "ods/current":
    raise SystemExit(1)
base_url = answers.get("modelBaseUrl")
api_key = answers.get("modelApiKey")
if (not isinstance(base_url, str)
        or not re.fullmatch(r"http://127\.0\.0\.1:[1-9][0-9]{0,4}/v1", base_url)
        or int(base_url.rsplit(":", 1)[1].split("/", 1)[0]) > 65535
        or not isinstance(api_key, str) or not api_key or len(api_key) > 4096
        or any(ord(character) < 32 or ord(character) == 127 for character in api_key)):
    raise SystemExit(1)

providers = config.get("models", {}).get("providers", {})
agents = config.get("agents", {}).get("list", [])
selected = [item for item in agents if isinstance(item, dict) and item.get("id") == "pixel"]
if not isinstance(providers, dict) or set(providers) != {"ods-gateway"} or len(selected) != 1:
    raise SystemExit(1)
provider = providers["ods-gateway"]
models = provider.get("models") if isinstance(provider, dict) else None
if (not isinstance(models, list) or len(models) != 1 or not isinstance(models[0], dict)
        or provider.get("api") != "openai-completions"
        or provider.get("baseUrl") != base_url or provider.get("apiKey") != api_key
        or models[0].get("id") != "ods/current"
        or selected[0].get("model") != "ods-gateway/ods/current"):
    raise SystemExit(1)
PY
}

_ods_pixel_stable_alias_matches_promoted_model() {
    local owner="$1" home="$2" answers="$3" promoted_model="$4"
    local promoted_context="${5:-}" promoted_max_tokens="${6:-}" promoted_reasoning="${7:-}" config
    config="$home/.openclaw/openclaw.json"
    ods_pixel_run_as_owner "$owner" "$home" python3 - \
        "$answers" "$config" "$promoted_model" "$promoted_context" \
        "$promoted_max_tokens" "$promoted_reasoning" <<'PY'
import json, os, pathlib, re, stat, sys

answers_path, config_path = map(pathlib.Path, sys.argv[1:3])
promoted_model, context_raw, max_tokens_raw, reasoning_raw = sys.argv[3:7]
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+:/ @(),=-]{0,255}", promoted_model):
    raise SystemExit(1)
documents = []
for path in (answers_path, config_path):
    info = path.lstat()
    parent = path.parent.lstat()
    if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_nlink != 1 or info.st_uid != os.getuid()
            or info.st_mode & 0o077 or info.st_size > 2 * 1024 * 1024
            or not stat.S_ISDIR(parent.st_mode) or stat.S_ISLNK(parent.st_mode)
            or parent.st_uid != os.getuid() or parent.st_mode & 0o022):
        raise SystemExit(1)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(1)
    documents.append(value)

answers, config = documents
if answers.get("modelProvider") != "ods-gateway" or answers.get("modelId") != "ods/current":
    raise SystemExit(1)
expected_name = f"ODS Current ({promoted_model})"
context = answers.get("modelContextWindow")
max_tokens = answers.get("modelMaxTokens")
reasoning = answers.get("modelReasoning")
if context_raw:
    if not context_raw.isdigit():
        raise SystemExit(1)
    context = int(context_raw)
if max_tokens_raw:
    if not max_tokens_raw.isdigit():
        raise SystemExit(1)
    max_tokens = int(max_tokens_raw)
if reasoning_raw:
    if reasoning_raw not in {"true", "false"}:
        raise SystemExit(1)
    reasoning = reasoning_raw == "true"
if (answers.get("modelName") != expected_name
        or type(context) is not int or not 4096 <= context <= 10_000_000
        or type(max_tokens) is not int or not 1 <= max_tokens <= context
        or type(reasoning) is not bool
        or answers.get("modelContextWindow") != context
        or answers.get("modelMaxTokens") != max_tokens
        or answers.get("modelReasoning") is not reasoning):
    raise SystemExit(1)

providers = config.get("models", {}).get("providers", {})
if not isinstance(providers, dict) or set(providers) != {"ods-gateway"}:
    raise SystemExit(1)
provider = providers["ods-gateway"]
models = provider.get("models") if isinstance(provider, dict) else None
if not isinstance(models, list) or len(models) != 1 or not isinstance(models[0], dict):
    raise SystemExit(1)
model = models[0]
if (model.get("id") != "ods/current" or model.get("name") != expected_name
        or model.get("contextWindow") != context or model.get("maxTokens") != max_tokens
        or model.get("reasoning") is not reasoning):
    raise SystemExit(1)
PY
}

_ods_pixel_stage_stable_alias_candidate() {
    local owner="$1" home="$2" answers="$3" live
    live="$home/.openclaw/openclaw.json"
    ods_pixel_run_as_owner "$owner" "$home" python3 - "$live" "$answers" <<'PY'
import copy, json, os, pathlib, re, stat, sys, tempfile

live_path, answers_path = map(pathlib.Path, sys.argv[1:])
documents = []
for path in (live_path, answers_path):
    info = path.lstat()
    parent = path.parent.lstat()
    if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_nlink != 1 or info.st_uid != os.getuid()
            or info.st_mode & 0o077 or info.st_size > 2 * 1024 * 1024
            or not stat.S_ISDIR(parent.st_mode) or stat.S_ISLNK(parent.st_mode)
            or parent.st_uid != os.getuid() or parent.st_mode & 0o022):
        raise SystemExit("unsafe stable-alias reconciliation input")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit("invalid stable-alias reconciliation input")
    documents.append(value)

live, contract = documents
if contract.get("modelProvider") != "ods-gateway" or contract.get("modelId") != "ods/current":
    raise SystemExit("stable-alias onboarding contract is invalid")
name = contract.get("modelName")
context = contract.get("modelContextWindow")
max_tokens = contract.get("modelMaxTokens")
reasoning = contract.get("modelReasoning")
if (not isinstance(name, str)
        or not re.fullmatch(r"ODS Current \([A-Za-z0-9][A-Za-z0-9._+:/ @(),=-]{0,255}\)", name)
        or type(context) is not int or not 4096 <= context <= 10_000_000
        or type(max_tokens) is not int or not 1 <= max_tokens <= context
        or type(reasoning) is not bool):
    raise SystemExit("stable-alias model limits are invalid")
providers = live.get("models", {}).get("providers", {})
agents = live.get("agents", {}).get("list", [])
selected = [item for item in agents if isinstance(item, dict) and item.get("id") == "pixel"]
if not isinstance(providers, dict) or set(providers) != {"ods-gateway"} or len(selected) != 1:
    raise SystemExit("live stable-alias route is invalid")
provider = providers["ods-gateway"]
models = provider.get("models") if isinstance(provider, dict) else None
if (not isinstance(models, list) or len(models) != 1 or not isinstance(models[0], dict)
        or models[0].get("id") != "ods/current"
        or selected[0].get("model") != "ods-gateway/ods/current"):
    raise SystemExit("live stable-alias model binding is invalid")
candidate = copy.deepcopy(live)
model = candidate["models"]["providers"]["ods-gateway"]["models"][0]
model["name"] = name
model["contextWindow"] = context
model["maxTokens"] = max_tokens
model["reasoning"] = reasoning
agent = next(
    item for item in candidate["agents"]["list"]
    if isinstance(item, dict) and item.get("id") == "pixel"
)
context_limits = agent.setdefault("contextLimits", {})
if not isinstance(context_limits, dict):
    raise SystemExit("live stable-alias context limits are invalid")
context_limits["toolResultMaxChars"] = max(4000, min(16000, context // 4))
defaults = candidate.get("agents", {}).get("defaults", {})
compaction = defaults.setdefault("compaction", {}) if isinstance(defaults, dict) else None
if not isinstance(compaction, dict):
    raise SystemExit("live stable-alias compaction policy is invalid")
compaction["reserveTokens"] = (context + 4 * max_tokens + 4) // 5
compaction["reserveTokensFloor"] = context // 2 if 8192 <= context < 32768 else 0
compaction["keepRecentTokens"] = max(512, min(20000, context // 16))
descriptor, temporary = tempfile.mkstemp(prefix=".ods-model-reconcile-", dir=live_path.parent)
try:
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(candidate, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    print(temporary)
except BaseException:
    try:
        os.unlink(temporary)
    except FileNotFoundError:
        pass
    raise
PY
}

_ods_pixel_managed_source_ref() {
    local owner="$1" home="$2" marker config
    marker="$home/.config/ods/pixel-managed.json"
    config="$home/.openclaw/openclaw.json"
    ods_pixel_run_as_owner "$owner" "$home" python3 - "$marker" "$config" "${INSTALL_DIR:?}" <<'PY'
import hashlib, json, os, pathlib, re, stat, sys

marker_path = pathlib.Path(sys.argv[1])
config_path = pathlib.Path(sys.argv[2])
for path, maximum in ((marker_path, 65536), (config_path, 2 * 1024 * 1024)):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_nlink != 1
            or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > maximum):
        raise SystemExit("unsafe ODS-managed Pixel state")
marker = json.loads(marker_path.read_text(encoding="utf-8"))
source_ref = marker.get("pixel_source_ref")
if (marker.get("schema_version") != 2 or marker.get("manager") != "ods"
        or marker.get("initial_active_state") != "absent"
        or marker.get("install_dir") != sys.argv[3]
        or marker.get("state") not in {"ready", "installing"}
        or not isinstance(source_ref, str) or not re.fullmatch(r"[0-9a-f]{40}", source_ref)
        or marker.get("requested_source_ref") not in {None, source_ref}):
    raise SystemExit("ODS-managed Pixel marker is not eligible for reconciliation")
config = json.loads(config_path.read_text(encoding="utf-8"))
if not isinstance(config, dict):
    raise SystemExit("invalid ODS-managed OpenClaw configuration")
canonical = json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
observed = hashlib.sha256(b"ods-pixel-openclaw-v1\0" + canonical).hexdigest()
if marker.get("configuration_sha256") != observed:
    raise SystemExit("ODS-managed OpenClaw configuration drifted")
print(source_ref)
PY
}

_ods_pixel_model_reconciliation_snapshot() {
    local owner="$1" home="$2" answers="$3" marker config attestation backup_root installed_answers
    marker="$home/.config/ods/pixel-managed.json"
    config="$home/.openclaw/openclaw.json"
    attestation="$home/.local/share/pixel/runtime-attestation.json"
    backup_root="$home/.openclaw/backups"
    installed_answers="$home/.config/pixel-deployment/onboarding.json"
    ods_pixel_run_as_owner "$owner" "$home" python3 - \
        "$marker" "$config" "$answers" "$attestation" "$backup_root" "$installed_answers" <<'PY'
import json, os, pathlib, re, shutil, stat, sys, tempfile

marker, config, answers, attestation, backup_root, installed_answers = map(pathlib.Path, sys.argv[1:])
required_sources = (
    (marker, 65536),
    (config, 2 * 1024 * 1024),
    (answers, 2 * 1024 * 1024),
    (installed_answers, 2 * 1024 * 1024),
)
for path, maximum in required_sources:
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_nlink != 1
            or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > maximum):
        raise SystemExit(f"unsafe Pixel reconciliation source: {path}")
attestation_present = False
try:
    attestation_info = attestation.lstat()
except FileNotFoundError:
    pass
else:
    if (not stat.S_ISREG(attestation_info.st_mode) or stat.S_ISLNK(attestation_info.st_mode)
            or attestation_info.st_nlink != 1 or attestation_info.st_uid != os.getuid()
            or attestation_info.st_mode & 0o077 or attestation_info.st_size > 2 * 1024 * 1024):
        raise SystemExit(f"unsafe Pixel reconciliation source: {attestation}")
    attestation_present = True
backup_root.mkdir(mode=0o700, parents=True, exist_ok=True)
root_info = backup_root.lstat()
if (not stat.S_ISDIR(root_info.st_mode) or stat.S_ISLNK(root_info.st_mode)
        or root_info.st_uid != os.getuid() or root_info.st_mode & 0o077):
    raise SystemExit("unsafe Pixel backup root")
backup = pathlib.Path(tempfile.mkdtemp(prefix="ods-model-reconcile-", dir=backup_root))
os.chmod(backup, 0o700)
backup_sources = [
    (marker, "pixel-managed.json"),
    (config, "openclaw.json"),
    (answers, "onboarding.json"),
    (installed_answers, "installed-onboarding.json"),
]
if attestation_present:
    backup_sources.append((attestation, "runtime-attestation.json"))
for source, name in backup_sources:
    target = backup / name
    with source.open("rb") as source_handle, target.open("xb") as target_handle:
        shutil.copyfileobj(source_handle, target_handle)
        target_handle.flush()
        os.fsync(target_handle.fileno())
    os.chmod(target, 0o600)

live = json.loads(config.read_text(encoding="utf-8"))
contract = json.loads(answers.read_text(encoding="utf-8"))
agent_id = contract.get("agentId")
providers = live.get("models", {}).get("providers", {})
if (not isinstance(providers, dict) or len(providers) != 1
        or next(iter(providers), None) not in {"ods-local", "ods-gateway"}):
    raise SystemExit("live Pixel provider is outside the ODS model-only contract")
provider = next(iter(providers))
provider_value = providers[provider]
models = provider_value.get("models", []) if isinstance(provider_value, dict) else []
agents = live.get("agents", {}).get("list", [])
agent = [item for item in agents if isinstance(item, dict) and item.get("id") == agent_id]
extensions = contract.get("gatewayExtensions")
if (contract.get("deploymentName") != "ods-default" or agent_id != "pixel"
        or not isinstance(extensions, list) or len(extensions) != 1
        or extensions[0].get("id") != "pixel-ods"
        or len(models) != 1 or len(agent) != 1
        or not isinstance(models[0].get("id"), str) or not isinstance(models[0].get("name"), str)
        or provider_value.get("api") != "openai-completions"
        or agent[0].get("model") != f"{provider}/{models[0]['id']}"):
    raise SystemExit("live Pixel configuration is outside the ODS model-only contract")
model_id = models[0]["id"]
model_name = models[0]["name"]
if provider == "ods-local":
    if (provider_value.get("apiKey") != "local-no-auth"
            or provider_value.get("baseUrl") != "http://127.0.0.1:11434/v1"
            or model_name != f"ODS Local {model_id}"):
        raise SystemExit("live Pixel local route is outside the ODS model-only contract")
else:
    gateway_key = provider_value.get("apiKey")
    gateway_url = provider_value.get("baseUrl")
    alias_label = "Current" if model_id == "ods/current" else "Default"
    if (model_id not in {"default", "ods/current"}
            or not isinstance(gateway_key, str) or not gateway_key or len(gateway_key) > 4096
            or any(ord(character) < 32 or ord(character) == 127 for character in gateway_key)
            or not isinstance(gateway_url, str)
            or not re.fullmatch(r"http://127\.0\.0\.1:[1-9][0-9]{0,4}/v1", gateway_url)
            or int(gateway_url.rsplit(":", 1)[1].split("/", 1)[0]) > 65535
            or not re.fullmatch(
                rf"ODS {alias_label} \([A-Za-z0-9][A-Za-z0-9._+:/ @(),=-]{{0,255}}\)",
                model_name,
            )):
        raise SystemExit("live Pixel gateway route is outside the ODS model-only contract")
context_window = models[0].get("contextWindow")
max_tokens = models[0].get("maxTokens")
reasoning = models[0].get("reasoning")
if (type(context_window) is not int or type(max_tokens) is not int or type(reasoning) is not bool
        or not 4096 <= context_window <= 10_000_000
        or not 1 <= max_tokens <= context_window):
    raise SystemExit("live Pixel model limits are outside the ODS model-only contract")
contract["modelProvider"] = provider
contract["modelId"] = models[0]["id"]
contract["modelName"] = models[0]["name"]
contract["modelBaseUrl"] = provider_value.get("baseUrl")
contract["modelApiKey"] = provider_value.get("apiKey")
contract["modelContextWindow"] = context_window
contract["modelMaxTokens"] = max_tokens
contract["modelReasoning"] = reasoning
rollback = backup / "rollback-onboarding.json"
payload = json.dumps(contract, indent=2, sort_keys=True) + "\n"
with rollback.open("x", encoding="utf-8", newline="\n") as handle:
    handle.write(payload)
    handle.flush()
    os.fsync(handle.fileno())
os.chmod(rollback, 0o600)
print(backup)
PY
}

_ods_pixel_update_onboarding_model() {
    local owner="$1" home="$2" answers="$3" model="$4"
    local context="${5:-}" max_tokens="${6:-}" reasoning="${7:-}"
    ods_pixel_run_as_owner "$owner" "$home" python3 - \
        "$answers" "$model" "$context" "$max_tokens" "$reasoning" <<'PY'
import json, os, pathlib, re, stat, sys, tempfile

path = pathlib.Path(sys.argv[1])
model = sys.argv[2]
context_raw, max_tokens_raw, reasoning_raw = sys.argv[3:6]
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+:/ @(),=-]{0,255}", model):
    raise SystemExit("invalid promoted Pixel model id")
info = path.lstat()
if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_nlink != 1
        or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 2 * 1024 * 1024):
    raise SystemExit("unsafe ODS Pixel onboarding contract")
parent_info = path.parent.lstat()
if (not stat.S_ISDIR(parent_info.st_mode) or stat.S_ISLNK(parent_info.st_mode)
        or parent_info.st_uid != os.getuid() or parent_info.st_mode & 0o022):
    raise SystemExit("unsafe ODS Pixel onboarding directory")
value = json.loads(path.read_text(encoding="utf-8"))
extensions = value.get("gatewayExtensions")
provider = value.get("modelProvider")
model_id = value.get("modelId")
model_name = value.get("modelName")
base_url = value.get("modelBaseUrl")
api_key = value.get("modelApiKey")
if (value.get("deploymentName") != "ods-default" or value.get("agentId") != "pixel"
        or not isinstance(extensions, list) or len(extensions) != 1
        or extensions[0].get("id") != "pixel-ods"):
    raise SystemExit("onboarding contract is outside the ODS-managed Pixel boundary")
if provider == "ods-local":
    if (api_key != "local-no-auth" or base_url != "http://127.0.0.1:11434/v1"
            or model_name != f"ODS Local {model_id}"):
        raise SystemExit("onboarding local route is outside the ODS-managed Pixel boundary")
elif provider == "ods-gateway":
    alias_label = "Current" if model_id == "ods/current" else "Default"
    if (model_id not in {"default", "ods/current"}
            or not isinstance(model_name, str)
            or not re.fullmatch(
                rf"ODS {alias_label} \([A-Za-z0-9][A-Za-z0-9._+:/ @(),=-]{{0,255}}\)",
                model_name,
            )
            or not isinstance(api_key, str) or not api_key or len(api_key) > 4096
            or any(ord(character) < 32 or ord(character) == 127 for character in api_key)
            or not isinstance(base_url, str)
            or not re.fullmatch(r"http://127\.0\.0\.1:[1-9][0-9]{0,4}/v1", base_url)
            or int(base_url.rsplit(":", 1)[1].split("/", 1)[0]) > 65535):
        raise SystemExit("onboarding gateway route is outside the ODS-managed Pixel boundary")
else:
    raise SystemExit("onboarding provider is outside the ODS-managed Pixel boundary")
if (type(value.get("modelContextWindow")) is not int
        or type(value.get("modelMaxTokens")) is not int
        or type(value.get("modelReasoning")) is not bool
        or not 4096 <= value["modelContextWindow"] <= 10_000_000
        or not 1 <= value["modelMaxTokens"] <= value["modelContextWindow"]):
    raise SystemExit("onboarding model limits are outside the ODS-managed Pixel boundary")
if context_raw:
    if not context_raw.isdigit() or not 4096 <= int(context_raw) <= 10_000_000:
        raise SystemExit("invalid promoted Pixel context window")
    value["modelContextWindow"] = int(context_raw)
if max_tokens_raw:
    if not max_tokens_raw.isdigit() or not 1 <= int(max_tokens_raw) <= value.get("modelContextWindow", 0):
        raise SystemExit("invalid promoted Pixel maximum tokens")
    value["modelMaxTokens"] = int(max_tokens_raw)
elif context_raw and value.get("modelMaxTokens", 0) > value["modelContextWindow"]:
    value["modelMaxTokens"] = value["modelContextWindow"]
if reasoning_raw:
    if reasoning_raw not in {"true", "false"}:
        raise SystemExit("invalid promoted Pixel reasoning capability")
    value["modelReasoning"] = reasoning_raw == "true"
if provider == "ods-local":
    value["modelId"] = model
    value["modelName"] = f"ODS Local {model}"
else:
    # Reconciliation is the safe upgrade boundary for pre-stable-alias ODS
    # installs. Keep accepting the legacy default contract above so it can be
    # rolled forward, then persist only the canonical alias.
    value["modelId"] = "ods/current"
    value["modelName"] = f"ODS Current ({model})"
payload = json.dumps(value, indent=2, sort_keys=True) + "\n"
fd, temporary = tempfile.mkstemp(prefix=".pixel-onboarding.", dir=path.parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
}

_ods_pixel_candidate_is_managed_runtime_update() {
    local owner="$1" home="$2" candidate="$3" answers="$4" live
    live="$home/.openclaw/openclaw.json"
    ods_pixel_run_as_owner "$owner" "$home" python3 - "$live" "$candidate" "$answers" <<'PY'
import copy, json, os, pathlib, re, stat, sys

values = []
for raw in sys.argv[1:]:
    path = pathlib.Path(raw)
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_nlink != 1
            or info.st_uid != os.getuid() or info.st_mode & 0o077
            or info.st_size > 2 * 1024 * 1024):
        raise SystemExit("unsafe Pixel model-reconciliation input")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit("Pixel model-reconciliation input is not an object")
    values.append(value)
live, candidate, contract = values
provider = contract.get("modelProvider")
model_id = contract.get("modelId")
model_name = contract.get("modelName")
agent_id = contract.get("agentId")
if provider not in {"ods-local", "ods-gateway"} or agent_id != "pixel":
    raise SystemExit("candidate is outside the ODS model contract")
if provider == "ods-local":
    if (model_name != f"ODS Local {model_id}"
            or contract.get("modelApiKey") != "local-no-auth"
            or contract.get("modelBaseUrl") != "http://127.0.0.1:11434/v1"):
        raise SystemExit("candidate local model is outside the ODS model contract")
else:
    gateway_key = contract.get("modelApiKey")
    gateway_url = contract.get("modelBaseUrl")
    alias_label = "Current" if model_id == "ods/current" else "Default"
    if (model_id not in {"default", "ods/current"}
            or not isinstance(model_name, str)
            or not re.fullmatch(
                rf"ODS {alias_label} \([A-Za-z0-9][A-Za-z0-9._+:/ @(),=-]{{0,255}}\)",
                model_name,
            )
            or not isinstance(gateway_key, str) or not gateway_key or len(gateway_key) > 4096
            or any(ord(character) < 32 or ord(character) == 127 for character in gateway_key)
            or not isinstance(gateway_url, str)
            or not re.fullmatch(r"http://127\.0\.0\.1:[1-9][0-9]{0,4}/v1", gateway_url)
            or int(gateway_url.rsplit(":", 1)[1].split("/", 1)[0]) > 65535):
        raise SystemExit("candidate gateway alias is outside the ODS model contract")

def binding(document, expected_provider=None):
    providers = document.get("models", {}).get("providers", {})
    if (not isinstance(providers, dict) or len(providers) != 1
            or next(iter(providers), None) not in {"ods-local", "ods-gateway"}):
        raise SystemExit("unexpected Pixel model providers")
    provider_id = next(iter(providers))
    if expected_provider is not None and provider_id != expected_provider:
        raise SystemExit("unexpected Pixel model provider")
    provider_value = providers[provider_id]
    models = provider_value.get("models") if isinstance(provider_value, dict) else None
    agents = document.get("agents", {}).get("list", [])
    selected = [item for item in agents if isinstance(item, dict) and item.get("id") == agent_id]
    if not isinstance(models, list) or len(models) != 1 or len(selected) != 1:
        raise SystemExit("unexpected Pixel model or agent cardinality")
    return provider_id, provider_value, models[0], selected[0]

def validate_live_route(provider_id, provider_value, model, agent):
    live_id = model.get("id")
    if (provider_value.get("api") != "openai-completions" or not isinstance(live_id, str)
            or agent.get("model") != f"{provider_id}/{live_id}"):
        raise SystemExit("live provider route is outside the ODS model contract")
    if provider_id == "ods-local":
        if (provider_value.get("apiKey") != "local-no-auth"
                or provider_value.get("baseUrl") != "http://127.0.0.1:11434/v1"
                or model.get("name") != f"ODS Local {live_id}"):
            raise SystemExit("live local provider route is outside the ODS model contract")
        return
    live_key = provider_value.get("apiKey")
    live_url = provider_value.get("baseUrl")
    live_label = "Current" if live_id == "ods/current" else "Default"
    live_name = model.get("name")
    if (live_id not in {"default", "ods/current"}
            or not isinstance(live_name, str)
            or not re.fullmatch(
                rf"ODS {live_label} \([A-Za-z0-9][A-Za-z0-9._+:/ @(),=-]{{0,255}}\)",
                live_name,
            )
            or not isinstance(live_key, str) or not live_key or len(live_key) > 4096
            or any(ord(character) < 32 or ord(character) == 127 for character in live_key)
            or not isinstance(live_url, str)
            or not re.fullmatch(r"http://127\.0\.0\.1:[1-9][0-9]{0,4}/v1", live_url)
            or int(live_url.rsplit(":", 1)[1].split("/", 1)[0]) > 65535):
        raise SystemExit("live gateway provider route is outside the ODS model contract")

live_provider_id, live_provider, live_model, live_agent = binding(live)
candidate_provider_id, candidate_provider, candidate_model, candidate_agent = binding(candidate, provider)
validate_live_route(live_provider_id, live_provider, live_model, live_agent)
expected_model = {
    "id": model_id,
    "name": model_name,
    "contextWindow": contract.get("modelContextWindow"),
    "maxTokens": contract.get("modelMaxTokens"),
    "reasoning": contract.get("modelReasoning"),
}
for key, expected in expected_model.items():
    if candidate_model.get(key) != expected:
        raise SystemExit(f"candidate model field does not match onboarding: {key}")
if (candidate_provider.get("api") != "openai-completions"
        or candidate_provider.get("apiKey") != contract.get("modelApiKey")
        or candidate_provider.get("baseUrl") != contract.get("modelBaseUrl")
        or candidate_agent.get("model") != f"{provider}/{model_id}"):
    raise SystemExit("candidate provider route does not match onboarding")
normalized = copy.deepcopy(live)
normalized_providers = normalized.get("models", {}).get("providers", {})
if not isinstance(normalized_providers, dict) or set(normalized_providers) != {live_provider_id}:
    raise SystemExit("live provider collection is outside the ODS model contract")
normalized_provider = normalized_providers.pop(live_provider_id)
normalized_providers[provider] = normalized_provider
normalized_provider["api"] = candidate_provider.get("api")
normalized_provider["apiKey"] = candidate_provider.get("apiKey")
normalized_provider["baseUrl"] = candidate_provider.get("baseUrl")
normalized_models = normalized_provider.get("models")
if not isinstance(normalized_models, list) or len(normalized_models) != 1:
    raise SystemExit("live provider models are outside the ODS model contract")
normalized_model = normalized_models[0]
normalized_agents = normalized.get("agents")
normalized_agent_list = normalized_agents.get("list", []) if isinstance(normalized_agents, dict) else []
normalized_selected = [item for item in normalized_agent_list if isinstance(item, dict) and item.get("id") == agent_id]
if len(normalized_selected) != 1:
    raise SystemExit("live Pixel agent is outside the ODS model contract")
normalized_agent = normalized_selected[0]
normalized_agent_experimental = normalized_agent.setdefault("experimental", {})
normalized_model["id"] = model_id
normalized_model["name"] = model_name
normalized_model["contextWindow"] = contract.get("modelContextWindow")
normalized_model["maxTokens"] = contract.get("modelMaxTokens")
normalized_model["reasoning"] = contract.get("modelReasoning")
normalized_agent["model"] = f"{provider}/{model_id}"
normalized_defaults = normalized_agents.get("defaults") if isinstance(normalized_agents, dict) else None
normalized_session = normalized.get("session")
if not isinstance(normalized_defaults, dict) or not isinstance(normalized_session, dict):
    raise SystemExit("live Pixel runtime policy is outside the ODS contract")

# A promoted route may also carry the current deterministic ODS runtime policy.
# Normalize only those exact fields before the whole-document comparison; any
# other candidate change still fails closed below.
normalized_provider["timeoutSeconds"] = 1800
normalized_defaults["timeoutSeconds"] = 1800
# Preserve Pixel's complete workspace operating and tool contracts. The
# shipped AGENTS.md and TOOLS.md files are both larger than 4,000 characters;
# a smaller ODS override silently removes most of their instructions.
normalized_defaults["bootstrapMaxChars"] = 32000
normalized_defaults["bootstrapTotalMaxChars"] = 96000
normalized_defaults["contextInjection"] = "continuation-skip"
# Full upstream bootstrap documents remain available at 32K and above. Below
# that capability floor they cannot fit beside OpenClaw's core prompt and even
# its compact Tool Search surface, so the ODS plugin supplies a compact safety
# core plus request-specific contracts instead. This changes prompt shape only:
# tools, sandboxing, approvals, and broker authority remain identical.
normalized_context_window = contract.get("modelContextWindow")
normalized_compact_context = normalized_context_window < 32768
model_label = f"{model_id} {model_name}".casefold()
normalized_parameter_markers = re.findall(
    r"(?<![a-z0-9.])(\d+(?:\.\d+)?)\s*b(?![a-z0-9])",
    model_label,
)
normalized_small_model = any(float(marker) <= 4 for marker in normalized_parameter_markers)
normalized_lean_prompt = normalized_compact_context or normalized_small_model
normalized_agent["bootstrapMaxChars"] = 2000 if normalized_lean_prompt else 14000
normalized_agent["bootstrapTotalMaxChars"] = 6000 if normalized_lean_prompt else 36000
normalized_agent["contextInjection"] = "never" if normalized_lean_prompt else "continuation-skip"
normalized_agent_context_limits = normalized_agent.setdefault("contextLimits", {})
normalized_agent_context_limits["toolResultMaxChars"] = max(
    4000,
    min(16000, contract.get("modelContextWindow") // 4),
)
normalized_compaction = normalized_defaults.setdefault("compaction", {})
normalized_diagnostics = normalized.setdefault("diagnostics", {})
normalized_write_lock = normalized_session.setdefault("writeLock", {})
normalized_tools = normalized.setdefault("tools", {})
normalized_also_allow = normalized_tools.setdefault("alsoAllow", [])
normalized_web = normalized_tools.setdefault("web", {})
normalized_fetch = normalized_web.setdefault("fetch", {})
normalized_plugins = normalized.setdefault("plugins", {})
normalized_plugin_entries = normalized_plugins.setdefault("entries", {}) if isinstance(normalized_plugins, dict) else None
normalized_pixel_plugin = normalized_plugin_entries.setdefault("pixel-ods", {}) if isinstance(normalized_plugin_entries, dict) else None
normalized_pixel_config = normalized_pixel_plugin.setdefault("config", {}) if isinstance(normalized_pixel_plugin, dict) else None
normalized_agent_tools = normalized_agent.setdefault("tools", {})
normalized_agent_deny = normalized_agent_tools.setdefault("deny", [])
normalized_sandbox_tools = normalized_tools.setdefault("sandbox", {}).setdefault("tools", {})
normalized_sandbox_allow = normalized_sandbox_tools.setdefault("allow", [])
normalized_agent_sandbox = normalized_defaults.setdefault("sandbox", {})
normalized_sandbox_docker = normalized_agent_sandbox.setdefault("docker", {})
if (not isinstance(normalized_compaction, dict)
        or not isinstance(normalized_diagnostics, dict)
        or not isinstance(normalized_write_lock, dict)
        or not isinstance(normalized_tools, dict)
        or not isinstance(normalized_also_allow, list)
        or not all(isinstance(item, str) for item in normalized_also_allow)
        or not isinstance(normalized_web, dict)
        or not isinstance(normalized_fetch, dict)
        or not isinstance(normalized_plugins, dict)
        or not isinstance(normalized_plugin_entries, dict)
        or not isinstance(normalized_pixel_plugin, dict)
        or not isinstance(normalized_pixel_config, dict)
        or not isinstance(normalized_agent_tools, dict)
        or not isinstance(normalized_agent_experimental, dict)
        or not isinstance(normalized_agent_deny, list)
        or not all(isinstance(item, str) for item in normalized_agent_deny)
        or not isinstance(normalized_sandbox_tools, dict)
        or not isinstance(normalized_sandbox_allow, list)
        or not all(isinstance(item, str) for item in normalized_sandbox_allow)
        or not isinstance(normalized_agent_sandbox, dict)
        or not isinstance(normalized_sandbox_docker, dict)):
    raise SystemExit("live Pixel runtime policy is outside the ODS contract")
normalized_agent_experimental["localModelLean"] = True
normalized_pixel_config["modelContextWindow"] = normalized_context_window
normalized_pixel_config["leanPrompt"] = normalized_lean_prompt
# Structured Tool Search keeps the catalog compact for every model. Exact ODS
# routes are injected by the guard when required; otherwise models can search,
# describe, and call the same complete authorized catalog without an allowlist.
normalized_tools["toolSearch"] = {
    "enabled": True,
    "mode": "tools",
    "searchDefaultLimit": 5,
    "maxSearchLimit": 10,
}
exec_control_bind = "{}:/run/pixel-ods-control:ro".format(
    pathlib.Path.home() / ".openclaw" / ".ods-exec-control"
)
existing_binds = normalized_sandbox_docker.get("binds", [])
if existing_binds not in ([], [exec_control_bind]):
    raise SystemExit("live Pixel sandbox binds are outside the ODS contract")
normalized_sandbox_docker["binds"] = [exec_control_bind]
normalized_sandbox_docker["dangerouslyAllowExternalBindSources"] = True
# OpenClaw's OpenAI-compatible transport adds a 1.25 character-based input
# safety margin after its independent pre-prompt compaction estimate. Reserve
# enough headroom that the precheck runs before that transport can silently
# clamp a useful model continuation down to one token. Algebraically this is
# context - ((context - output) / 1.25), rounded up.
normalized_compaction["reserveTokens"] = (
    contract.get("modelContextWindow") + 4 * contract.get("modelMaxTokens") + 4
) // 5
normalized_compaction["reserveTokensFloor"] = (
    normalized_context_window // 2
    if 8192 <= normalized_context_window < 32768 else 0
)
# OpenClaw otherwise retains its fixed 20K recent-token default. On compact
# contexts that can select nothing to summarize, create a no-op compaction,
# and then reject the useful retry as already compacted. Keep a context-scaled
# recent tail so every admitted context can actually recover while preserving
# the newest tool evidence and continuation state.
normalized_compaction["keepRecentTokens"] = max(
    512,
    min(20000, normalized_context_window // 16),
)
normalized_diagnostics["stuckSessionAbortMs"] = 1860000
normalized_write_lock["maxHoldMs"] = 1920000
normalized_write_lock["staleMs"] = 3600000
normalized_tools["loopDetection"] = {
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
normalized_fetch.update({
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
})
normalized_agent_tools["deny"] = [
    item for item in normalized_agent_deny
    if item not in {
        "web_search", "web_fetch", "pixel_ods_status", "pixel_ods_apps_list", "pixel_ods_host_observe", "pixel_ods_host_command_propose",
        "pixel_ods_evidence_report", "pixel_ods_evidence_readback",
        "pixel_ods_web_extract", "pixel_ods_download_promote", "pixel_ods_workspace_preview",
        "pixel_web_extract"
    }
]
normalized_also_allow = [item for item in normalized_also_allow if item != "pixel_web_extract"]
normalized_sandbox_allow = [item for item in normalized_sandbox_allow if item != "pixel_web_extract"]
for extension_tool in (
    "pixel_ods_status", "pixel_ods_apps_list", "pixel_ods_host_observe", "pixel_ods_host_command_propose",
    "pixel_ods_evidence_report", "pixel_ods_evidence_readback",
    "pixel_ods_web_extract", "pixel_ods_download_promote", "pixel_ods_workspace_preview"
):
    if extension_tool not in normalized_also_allow:
        normalized_also_allow.append(extension_tool)
for permitted_tool in (
    "web_search", "web_fetch", "pixel_ods_status", "pixel_ods_apps_list", "pixel_ods_host_observe", "pixel_ods_host_command_propose",
    "pixel_ods_evidence_report", "pixel_ods_evidence_readback",
    "pixel_ods_web_extract", "pixel_ods_download_promote", "pixel_ods_workspace_preview"
):
    if permitted_tool not in normalized_sandbox_allow:
        normalized_sandbox_allow.append(permitted_tool)
normalized_tools["alsoAllow"] = sorted(set(normalized_also_allow))
normalized_sandbox_tools["allow"] = sorted(set(normalized_sandbox_allow))
if "qwen" in model_label and contract.get("modelReasoning") is True:
    normalized_model["compat"] = {"thinkingFormat": "qwen-chat-template"}
    normalized_agent["thinkingDefault"] = "low"
else:
    normalized_model.pop("compat", None)
    normalized_agent.pop("thinkingDefault", None)
normalized_agent_params = normalized_agent.setdefault("params", {})
if not isinstance(normalized_agent_params, dict):
    raise SystemExit("live Pixel agent parameters are outside the ODS contract")
if "qwen" in model_label:
    template_kwargs = normalized_agent_params.setdefault("chat_template_kwargs", {})
    if not isinstance(template_kwargs, dict):
        raise SystemExit("live Pixel chat-template parameters are outside the ODS contract")
    template_kwargs["enable_thinking"] = contract.get("modelReasoning") is True
else:
    template_kwargs = normalized_agent_params.get("chat_template_kwargs")
    if isinstance(template_kwargs, dict):
        template_kwargs.pop("enable_thinking", None)
        if not template_kwargs:
            normalized_agent_params.pop("chat_template_kwargs", None)
    if not normalized_agent_params:
        normalized_agent.pop("params", None)
normalized_compact_sampling = {
    "temperature": 0.7,
    "topP": 0.8,
    "frequencyPenalty": 0.6,
    "presencePenalty": 0.2,
}
if normalized_lean_prompt:
    normalized_agent["params"] = normalized_agent_params
    normalized_agent_params.update(normalized_compact_sampling)
else:
    for key in normalized_compact_sampling:
        normalized_agent_params.pop(key, None)
if not normalized_agent_params:
    normalized_agent.pop("params", None)
if normalized != candidate:
    raise SystemExit("candidate changes more than the ODS managed model/runtime fields")
PY
}

_ods_pixel_atomic_replace_managed_file() {
    local owner="$1" home="$2" source="$3" target="$4"
    ods_pixel_run_as_owner "$owner" "$home" python3 - "$source" "$target" <<'PY'
import os, pathlib, stat, sys, tempfile

source = pathlib.Path(sys.argv[1])
target = pathlib.Path(sys.argv[2])
for path in (source,):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_nlink != 1
            or info.st_uid != os.getuid() or info.st_mode & 0o077
            or info.st_size > 2 * 1024 * 1024):
        raise SystemExit(f"unsafe managed file: {path}")
if target.exists() or target.is_symlink():
    info = target.lstat()
    if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_nlink != 1
            or info.st_uid != os.getuid() or info.st_mode & 0o077
            or info.st_size > 2 * 1024 * 1024):
        raise SystemExit(f"unsafe managed file: {target}")
parent_info = target.parent.lstat()
if (not stat.S_ISDIR(parent_info.st_mode) or stat.S_ISLNK(parent_info.st_mode)
        or parent_info.st_uid != os.getuid() or parent_info.st_mode & 0o022):
    raise SystemExit(f"unsafe managed directory: {target.parent}")
payload = source.read_bytes()
fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
try:
    with os.fdopen(fd, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, target)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
}

_ods_pixel_install_onboarding_mirror() {
    local owner="$1" home="$2" answers="$3"
    local installed_answers="$home/.config/pixel-deployment/onboarding.json"
    # Pixel configure normally maintains this copy. Stable-alias model changes
    # deliberately skip configure, so they must publish it in the same model
    # transaction. Uninstall verifies these exact bytes against the ready marker.
    _ods_pixel_atomic_replace_managed_file "$owner" "$home" "$answers" "$installed_answers" || return 1
    ods_pixel_run_as_owner "$owner" "$home" cmp -s -- "$answers" "$installed_answers"
}

_ods_pixel_openclaw_bin() {
    local owner="$1" home="$2"
    # Expansion is intentionally performed in the owner shell, not here.
    # shellcheck disable=SC2016
    ods_pixel_run_as_owner "$owner" "$home" bash -c \
        'if [[ -x "$HOME/.npm-global/bin/openclaw" ]]; then printf "%s\\n" "$HOME/.npm-global/bin/openclaw"; else command -v openclaw; fi'
}

_ods_pixel_secure_plugin_tree() {
    local owner="$1" home="$2" plugin_root="$3" expected path
    expected="${INSTALL_DIR:?}/extensions/services/pixel-agent/plugin"
    [[ "$plugin_root" == "$expected" ]] || return 1

    # A source copied from a Windows mount can arrive as mode 0777 even when
    # the Git blob is ordinary read-only code. OpenClaw correctly blocks any
    # plugin below a group/world-writable ancestor, so normalize only this
    # fixed ODS-owned path and fail closed on links, special files, or foreign
    # ownership before calculating the approved extension digest.
    for path in \
        "$INSTALL_DIR" \
        "$INSTALL_DIR/extensions" \
        "$INSTALL_DIR/extensions/services" \
        "$INSTALL_DIR/extensions/services/pixel-agent" \
        "$plugin_root"; do
        [[ -d "$path" && ! -L "$path" ]] || return 1
        [[ "$(stat -c '%u' -- "$path")" == "$(id -u "$owner")" ]] || return 1
        ods_pixel_run_as_owner "$owner" "$home" chmod 0755 -- "$path" || return 1
    done
    if find -P "$plugin_root" -mindepth 1 \( -type l -o ! -user "$owner" \) -print -quit | grep -q .; then
        return 1
    fi
    if find -P "$plugin_root" -mindepth 1 ! -type d ! -type f -print -quit | grep -q .; then
        return 1
    fi
    ods_pixel_run_as_owner "$owner" "$home" find -P "$plugin_root" -type d -exec chmod 0755 -- '{}' + || return 1
    ods_pixel_run_as_owner "$owner" "$home" find -P "$plugin_root" -type f -exec chmod 0644 -- '{}' + || return 1
    if find -P "$plugin_root" -perm /022 -print -quit | grep -q .; then
        return 1
    fi
}

_ods_pixel_refresh_plugin_registry() {
    local owner="$1" home="$2" openclaw_bin="$3" plugin_root="$4" registry
    registry="$(ods_pixel_run_as_owner "$owner" "$home" "$openclaw_bin" \
        plugins registry --refresh --json 2>/dev/null)" || return 1
    jq -e --arg root "$plugin_root" '
        (["pixel_ods_apps_list", "pixel_ods_download_promote", "pixel_ods_evidence_readback", "pixel_ods_evidence_report", "pixel_ods_host_command_propose", "pixel_ods_host_observe", "pixel_ods_status", "pixel_ods_web_extract", "pixel_ods_workspace_preview"] | sort) as $tools
        | .refreshed == true
        and .registry.version == 1
        and .registry.refreshReason == "manual"
        and ([
            .registry.plugins[]?
            | select(
                .pluginId == "pixel-ods"
                and .enabled == true
                and .rootDir == $root
                and ((.contributions.contracts.tools // []) | sort) == $tools
            )
        ] | length == 1)
    ' <<<"$registry" >/dev/null
}

_ods_pixel_verify_plugin_loaded() {
    local owner="$1" home="$2" openclaw_bin="$3" plugin_root="$4"
    ods_pixel_run_as_owner "$owner" "$home" "$openclaw_bin" plugins list --json 2>/dev/null \
        | jq -e --arg root "$plugin_root" '
            ["pixel_ods_apps_list", "pixel_ods_download_promote", "pixel_ods_evidence_readback", "pixel_ods_evidence_report", "pixel_ods_host_command_propose", "pixel_ods_host_observe", "pixel_ods_status", "pixel_ods_web_extract", "pixel_ods_workspace_preview"] as $tools
            | [
                .plugins[]?
                | select(
                    .id == "pixel-ods"
                    and .status == "loaded"
                    and .rootDir == $root
                    and ((.contracts.tools // []) | sort) == ($tools | sort)
                )
            ] | length == 1
        ' \
            >/dev/null
}

_ods_pixel_install_exec_control() {
    local owner="$1" home="$2" source="$3" sudo_source="$4"
    local parent="$home/.openclaw" root="$home/.openclaw/.ods-exec-control"
    local candidate
    for candidate in "$source" "$sudo_source"; do
        [[ -f "$candidate" && ! -L "$candidate" \
            && "$(stat -c '%U' -- "$candidate")" == "$owner" \
            && "$(stat -c '%h' -- "$candidate")" == 1 ]] || return 1
        (( (8#$(stat -c '%a' -- "$candidate") & 0022) == 0 )) || return 1
    done
    # A clean Pixel install has not run OpenClaw bootstrap yet, so its private
    # state directory legitimately does not exist. Create only that exact
    # owner path, then apply the same ownership/symlink/mode checks used for an
    # existing installation. Never follow or replace an ambient path.
    if [[ ! -e "$parent" && ! -L "$parent" ]]; then
        ods_pixel_run_as_owner "$owner" "$home" install -d -m 0700 -- "$parent" || return 1
    fi
    [[ -d "$parent" && ! -L "$parent" \
        && "$(stat -c '%U' -- "$parent")" == "$owner" ]] || return 1
    (( (8#$(stat -c '%a' -- "$parent") & 0022) == 0 )) || return 1
    if [[ -e "$root" || -L "$root" ]]; then
        [[ -d "$root" && ! -L "$root" && "$(stat -c '%U' -- "$root")" == "$owner" \
            && "$(stat -c '%a' -- "$root")" == 700 ]] || return 1
    fi
    for candidate in "$root/cancellable-exec.sh" "$root/sudo"; do
        if [[ -e "$candidate" || -L "$candidate" ]]; then
            [[ -f "$candidate" && ! -L "$candidate" \
                && "$(stat -c '%U' -- "$candidate")" == "$owner" \
                && "$(stat -c '%h' -- "$candidate")" == 1 ]] || return 1
        fi
    done
    ods_pixel_run_as_owner "$owner" "$home" install -d -m 0700 -- "$root" || return 1
    ods_pixel_run_as_owner "$owner" "$home" install -m 0500 -- \
        "$source" "$root/cancellable-exec.sh" || return 1
    ods_pixel_run_as_owner "$owner" "$home" install -m 0500 -- \
        "$sudo_source" "$root/sudo" || return 1
    [[ -d "$root" && ! -L "$root" \
        && -f "$root/cancellable-exec.sh" && ! -L "$root/cancellable-exec.sh" \
        && -f "$root/sudo" && ! -L "$root/sudo" \
        && "$(stat -c '%U' -- "$root")" == "$owner" \
        && "$(stat -c '%U' -- "$root/cancellable-exec.sh")" == "$owner" \
        && "$(stat -c '%U' -- "$root/sudo")" == "$owner" \
        && "$(stat -c '%a' -- "$root")" == 700 \
        && "$(stat -c '%h' -- "$root/cancellable-exec.sh")" == 1 \
        && "$(stat -c '%a' -- "$root/cancellable-exec.sh")" == 500 \
        && "$(stat -c '%h' -- "$root/sudo")" == 1 \
        && "$(stat -c '%a' -- "$root/sudo")" == 500 ]]
}

_ods_pixel_recreate_agent_sandbox() {
    local owner="$1" home="$2" openclaw_bin="$3" remaining
    [[ "$openclaw_bin" == /* && -x "$openclaw_bin" ]] || return 1
    ods_pixel_run_as_owner "$owner" "$home" "$openclaw_bin" \
        sandbox recreate --agent pixel --force || return 1
    # OpenClaw retires only sandboxes present in its registry. Independently
    # prove that Docker has no stale agent-scoped container left behind before
    # a gateway restart can accept another tool turn. This catches registry
    # drift as well as a failed runtime removal without broadening the scope to
    # any non-Pixel container.
    remaining="$(ods_pixel_run_as_owner "$owner" "$home" docker ps --all --quiet \
        --filter 'name=^/pixel-sbx-agent-pixel-')" || return 1
    [[ -z "${remaining//[[:space:]]/}" ]]
}

_ods_pixel_apply_runtime_budget() {
    local owner="$1" home="$2" config="$3" openclaw_bin="$4" staged
    # ODS qualifies Pixel on CPU-only hosts. The first local 9B turn can spend
    # more than five minutes loading and prefilling its managed context, while
    # OpenClaw's default session watchdogs assume a responsive remote model.
    # Keep the larger CPU-only budgets deterministic and confined to this
    # ODS-owned Pixel route.
    # macOS Bash 3.2 reparses this command-substitution heredoc; keep the
    # embedded Python body free of literal apostrophe characters.
    staged="$(ods_pixel_run_as_owner "$owner" "$home" python3 - "$config" <<'PY'
import copy, json, os, pathlib, re, stat, sys, tempfile

path = pathlib.Path(sys.argv[1])
info = path.lstat()
if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_nlink != 1
        or info.st_uid != os.getuid() or info.st_mode & 0o077
        or info.st_size > 2 * 1024 * 1024):
    raise SystemExit("unsafe ODS-managed OpenClaw configuration")
parent_info = path.parent.lstat()
if (not stat.S_ISDIR(parent_info.st_mode) or stat.S_ISLNK(parent_info.st_mode)
        or parent_info.st_uid != os.getuid() or parent_info.st_mode & 0o022):
    raise SystemExit("unsafe ODS-managed OpenClaw configuration directory")
value = json.loads(path.read_text(encoding="utf-8"))
if not isinstance(value, dict):
    raise SystemExit("ODS-managed OpenClaw configuration must be an object")

providers = value.get("models", {}).get("providers", {})
agents = value.get("agents", {})
agent_list = agents.get("list", []) if isinstance(agents, dict) else []
selected = [item for item in agent_list if isinstance(item, dict) and item.get("id") == "pixel"]
if (not isinstance(providers, dict) or len(providers) != 1
        or next(iter(providers), None) not in {"ods-local", "ods-gateway"}
        or len(selected) != 1):
    raise SystemExit("OpenClaw configuration is outside the ODS Pixel runtime boundary")
provider_id = next(iter(providers))
provider = providers[provider_id]
models = provider.get("models") if isinstance(provider, dict) else None
defaults = agents.get("defaults") if isinstance(agents, dict) else None
session = value.get("session")
model_id = models[0].get("id") if isinstance(models, list) and len(models) == 1 and isinstance(models[0], dict) else None
if (not isinstance(models, list) or len(models) != 1 or not isinstance(models[0], dict)
        or not isinstance(defaults, dict) or not isinstance(session, dict)
        or provider.get("api") != "openai-completions"
        or selected[0].get("model") != f"{provider_id}/{model_id}"):
    raise SystemExit("OpenClaw configuration is outside the ODS Pixel runtime contract")
if provider_id == "ods-local":
    if (provider.get("apiKey") != "local-no-auth"
            or provider.get("baseUrl") != "http://127.0.0.1:11434/v1"
            or not isinstance(model_id, str)
            or models[0].get("name") != f"ODS Local {model_id}"):
        raise SystemExit("OpenClaw local route is outside the ODS Pixel runtime contract")
else:
    gateway_key = provider.get("apiKey")
    gateway_url = provider.get("baseUrl")
    alias_label = "Current" if model_id == "ods/current" else "Default"
    if (model_id not in {"default", "ods/current"}
            or not isinstance(gateway_key, str) or not gateway_key or len(gateway_key) > 4096
            or any(ord(character) < 32 or ord(character) == 127 for character in gateway_key)
            or not isinstance(gateway_url, str)
            or not re.fullmatch(r"http://127\.0\.0\.1:[1-9][0-9]{0,4}/v1", gateway_url)
            or int(gateway_url.rsplit(":", 1)[1].split("/", 1)[0]) > 65535
            or not isinstance(models[0].get("name"), str)
            or not re.fullmatch(
                rf"ODS {alias_label} \([A-Za-z0-9][A-Za-z0-9._+:/ @(),=-]{{0,255}}\)",
                models[0]["name"],
            )):
        raise SystemExit("OpenClaw gateway route is outside the ODS Pixel runtime contract")

updated = copy.deepcopy(value)
updated_provider = updated["models"]["providers"][provider_id]
updated_defaults = updated["agents"]["defaults"]
updated_agent = next(item for item in updated["agents"]["list"] if isinstance(item, dict) and item.get("id") == "pixel")
updated_agent_experimental = updated_agent.setdefault("experimental", {})
updated_model = updated_provider["models"][0]
updated_session = updated["session"]
updated_agent_sandbox = updated_defaults.setdefault("sandbox", {})
updated_sandbox_docker = updated_agent_sandbox.setdefault("docker", {})
updated_diagnostics = updated.setdefault("diagnostics", {})
updated_compaction = updated_defaults.setdefault("compaction", {})
write_lock = updated_session.setdefault("writeLock", {})
updated_tools = updated.setdefault("tools", {})
updated_also_allow = updated_tools.setdefault("alsoAllow", [])
updated_web = updated_tools.setdefault("web", {})
updated_fetch = updated_web.setdefault("fetch", {})
updated_agent_tools = updated_agent.setdefault("tools", {})
updated_agent_deny = updated_agent_tools.setdefault("deny", [])
updated_sandbox = updated_tools.setdefault("sandbox", {})
updated_sandbox_tools = updated_sandbox.setdefault("tools", {})
updated_sandbox_allow = updated_sandbox_tools.setdefault("allow", [])
updated_plugins = updated.setdefault("plugins", {})
updated_plugin_entries = updated_plugins.setdefault("entries", {}) if isinstance(updated_plugins, dict) else None
updated_pixel_plugin = updated_plugin_entries.setdefault("pixel-ods", {}) if isinstance(updated_plugin_entries, dict) else None
updated_pixel_hooks = updated_pixel_plugin.setdefault("hooks", {}) if isinstance(updated_pixel_plugin, dict) else None
updated_pixel_config = updated_pixel_plugin.setdefault("config", {}) if isinstance(updated_pixel_plugin, dict) else None
if not isinstance(updated_compaction, dict):
    raise SystemExit("OpenClaw compaction configuration must be an object")
if not isinstance(write_lock, dict):
    raise SystemExit("OpenClaw session write-lock configuration must be an object")
if not isinstance(updated_diagnostics, dict):
    raise SystemExit("OpenClaw diagnostics configuration must be an object")
if not isinstance(updated_agent_sandbox, dict) or not isinstance(updated_sandbox_docker, dict):
    raise SystemExit("OpenClaw sandbox configuration must be an object")
if (not isinstance(updated_tools, dict)
        or not isinstance(updated_also_allow, list)
        or not all(isinstance(item, str) for item in updated_also_allow)
        or not isinstance(updated_agent_tools, dict)
        or not isinstance(updated_agent_experimental, dict)
        or not isinstance(updated_agent_deny, list)
        or not all(isinstance(item, str) for item in updated_agent_deny)
        or not isinstance(updated_sandbox, dict)
        or not isinstance(updated_sandbox_tools, dict)
        or not isinstance(updated_sandbox_allow, list)
        or not all(isinstance(item, str) for item in updated_sandbox_allow)
        or not isinstance(updated_plugins, dict)
        or not isinstance(updated_plugin_entries, dict)
        or not isinstance(updated_pixel_plugin, dict)
        or not isinstance(updated_pixel_hooks, dict)
        or not isinstance(updated_pixel_config, dict)):
    raise SystemExit("OpenClaw tool policy is outside the ODS Pixel runtime contract")
if not isinstance(updated_web, dict) or not isinstance(updated_fetch, dict):
    raise SystemExit("OpenClaw web tool policy is outside the ODS Pixel runtime contract")
exec_control_bind = "{}:/run/pixel-ods-control:ro".format(
    pathlib.Path.home() / ".openclaw" / ".ods-exec-control"
)
existing_binds = updated_sandbox_docker.get("binds", [])
if existing_binds not in ([], [exec_control_bind]):
    raise SystemExit("OpenClaw sandbox binds are outside the ODS Pixel runtime contract")
# OpenClaw accepts this owner-private source only with its explicit external
# bind opt-in. ODS still pins the sole source and destination above, validates
# the host tree owner/mode, and exposes it read-only inside the sandbox.
updated_sandbox_docker["binds"] = [exec_control_bind]
updated_sandbox_docker["dangerouslyAllowExternalBindSources"] = True
search = updated_web.get("search", {})
searxng = updated.get("plugins", {}).get("entries", {}).get("searxng", {})
search_url = searxng.get("config", {}).get("webSearch", {}).get("baseUrl")
if (not isinstance(search, dict) or search.get("provider") != "searxng"
        or searxng.get("enabled") is not True
        or not isinstance(search_url, str)
        or not re.fullmatch(r"http://127\.0\.0\.1:[1-9][0-9]{0,4}", search_url)
        or int(search_url.rsplit(":", 1)[1]) > 65535):
    raise SystemExit("ODS Pixel private web search is not bound to local SearXNG")
updated_provider["timeoutSeconds"] = 1800
updated_defaults["timeoutSeconds"] = 1800
updated_defaults["bootstrapMaxChars"] = 32000
updated_defaults["bootstrapTotalMaxChars"] = 96000
updated_defaults["contextInjection"] = "continuation-skip"
updated_agent_context_limits = updated_agent.setdefault("contextLimits", {})
# ODS always presents Pixel through a private local gateway, including when the
# gateway's current route terminates at an owner-configured remote provider.
# OpenClaw cannot infer that fact from the stable ``ods/current`` alias. Enable
# its model-agnostic lean tool surface explicitly so small local models do not
# receive every direct tool schema at once. Capabilities remain available
# through structured Tool Search; this changes prompt shape, not authority.
updated_agent_experimental["localModelLean"] = True
updated_tools["toolSearch"] = {
    "enabled": True,
    "mode": "tools",
    "searchDefaultLimit": 5,
    "maxSearchLimit": 10,
}
# The finalization hook consumes only the guard's per-run structured state and
# does not inspect or persist conversation text. OpenClaw nevertheless requires
# this explicit trust bit before any installed plugin may register the hook.
updated_pixel_hooks["allowConversationAccess"] = True
context_window = updated_model.get("contextWindow")
model_max_tokens = updated_model.get("maxTokens")
if (type(context_window) is not int or type(model_max_tokens) is not int
        or context_window < 4096 or not 1 <= model_max_tokens <= context_window):
    raise SystemExit("OpenClaw model limits are outside the ODS Pixel runtime contract")
# Preserve complete upstream workspace contracts when the selected route can
# carry and follow them. Small checkpoints and compact contexts receive the
# plugin's equivalent concise core and on-demand capability contracts. This
# generic size/context profile never rejects a model or hides a callable tool.
compact_context = context_window < 32768
model_label = "{} {}".format(updated_model.get("id", ""), updated_model.get("name", "")).casefold()
parameter_markers = re.findall(
    r"(?<![a-z0-9.])(\d+(?:\.\d+)?)\s*b(?![a-z0-9])",
    model_label,
)
small_model = any(float(marker) <= 4 for marker in parameter_markers)
lean_prompt = compact_context or small_model
updated_pixel_config["modelContextWindow"] = context_window
updated_pixel_config["leanPrompt"] = lean_prompt
updated_agent["bootstrapMaxChars"] = 2000 if lean_prompt else 14000
updated_agent["bootstrapTotalMaxChars"] = 6000 if lean_prompt else 36000
updated_agent["contextInjection"] = "never" if lean_prompt else "continuation-skip"
# Bound each live tool result by the selected model's real context capacity.
# This is capability-based prompt shaping, never a model allowlist: failures
# retain OpenClaw's diagnostic head/tail projection and every tool remains
# callable, while one large read or verbose suite cannot crowd out the next
# model continuation on compact local contexts.
updated_agent_context_limits["toolResultMaxChars"] = max(
    4000,
    min(16000, context_window // 4),
)
# OpenClaw's OpenAI-compatible transport applies a 1.25 input estimate after
# the pre-prompt compaction check. Leave enough precheck headroom for the real
# model output ceiling before that later transport clamp can reduce a
# continuation to one token. This remains context-derived for every model and
# does not change its tools or authority.
updated_compaction["reserveTokens"] = (
    context_window + 4 * model_max_tokens + 4
) // 5
# OpenClaw's LLM-boundary estimate is character based and can undercount dense
# tool transcripts. Its context-aware floor is intentionally capped to half of
# a compact window; enable that same bound for the 8K-31K adaptive profiles so
# compaction begins before a provider can end the continuation at length. A 4K
# best-effort profile keeps the formula above because half the window may be
# smaller than its first useful prompt.
updated_compaction["reserveTokensFloor"] = (
    context_window // 2 if 8192 <= context_window < 32768 else 0
)
# OpenClaw's fixed 20K keep-recent default is larger than every compact ODS
# profile. Scale it for all contexts so compaction always drops real history
# instead of writing an empty no-op summary and blocking the recovery retry.
updated_compaction["keepRecentTokens"] = max(
    512,
    min(20000, context_window // 16),
)
# The legacy OpenAI-completions transport in OpenClaw 2026.6.33 coerces the literal
# reasoning effort "off" with Boolean("off"), which wrongly sends
# chat_template_kwargs.enable_thinking=true. With the llama.cpp Qwen template
# that can spend the complete output budget in hidden reasoning after a tool
# call and leave no user-visible answer. When ODS reasoning is disabled, keep
# the model non-reasoning and omit the Qwen compatibility knob so the llama.cpp
# independently pinned no-think default remains authoritative. When the owner
# explicitly enables reasoning, advertise the capability and use a real
# non-off effort so both affected and corrected OpenClaw transports agree.
model_reasoning = updated_model.get("reasoning", False)
if type(model_reasoning) is not bool:
    raise SystemExit("OpenClaw model reasoning configuration must be boolean")
updated_model["reasoning"] = model_reasoning
if "qwen" in model_label and model_reasoning:
    model_compat = updated_model.setdefault("compat", {})
    if not isinstance(model_compat, dict):
        raise SystemExit("OpenClaw Qwen compatibility configuration must be an object")
    model_compat["thinkingFormat"] = "qwen-chat-template"
    updated_agent["thinkingDefault"] = "low"
else:
    updated_model.pop("compat", None)
    updated_agent.pop("thinkingDefault", None)
updated_agent_params = updated_agent.setdefault("params", {})
if not isinstance(updated_agent_params, dict):
    raise SystemExit("OpenClaw Pixel agent parameters must be an object")
if "qwen" in model_label:
    template_kwargs = updated_agent_params.setdefault("chat_template_kwargs", {})
    if not isinstance(template_kwargs, dict):
        raise SystemExit("OpenClaw Pixel chat-template parameters must be an object")
    template_kwargs["enable_thinking"] = model_reasoning
else:
    template_kwargs = updated_agent_params.get("chat_template_kwargs")
    if isinstance(template_kwargs, dict):
        template_kwargs.pop("enable_thinking", None)
        if not template_kwargs:
            updated_agent_params.pop("chat_template_kwargs", None)
    if not updated_agent_params:
        updated_agent.pop("params", None)
# Small or compact local checkpoints can get trapped repeating a valid prefix inside a
# JSON tool argument even though their plain-text generation is healthy. The
# standard OpenAI sampling controls below made the same 2B route terminate its
# minimal write call in 81 tokens instead of exhausting 1024. Apply the profile
# by generic size/context capability rather than an allowlist or readiness
# gate, and remove it transactionally when a larger route is promoted.
compact_sampling = {
    "temperature": 0.7,
    "topP": 0.8,
    "frequencyPenalty": 0.6,
    "presencePenalty": 0.2,
}
if lean_prompt:
    updated_agent["params"] = updated_agent_params
    updated_agent_params.update(compact_sampling)
else:
    for key in compact_sampling:
        updated_agent_params.pop(key, None)
if not updated_agent_params:
    updated_agent.pop("params", None)
# A CPU-only model call can emit no progress while evaluating a long prompt.
# Let the 30-minute provider own its terminal timeout, then retain one minute
# for the OpenClaw stalled-session recovery before the 32-minute host ingress.
updated_diagnostics["stuckSessionAbortMs"] = 1860000
write_lock["maxHoldMs"] = 1920000
write_lock["staleMs"] = 3600000
updated_tools["loopDetection"] = {
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
# Search stays private through loopback-only SearXNG. Page retrieval uses
# OpenClaw public-network SSRF guard with deliberately tighter ODS bounds;
# private/link-local targets and trusted environment proxies remain disabled.
updated_fetch.update({
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
})
updated_agent_tools["deny"] = [
    item for item in updated_agent_deny
    if item not in {
        "web_search", "web_fetch", "pixel_ods_status", "pixel_ods_apps_list", "pixel_ods_host_observe", "pixel_ods_host_command_propose",
        "pixel_ods_evidence_report", "pixel_ods_evidence_readback",
        "pixel_ods_web_extract", "pixel_ods_download_promote", "pixel_ods_workspace_preview",
        "pixel_web_extract"
    }
]
updated_also_allow = [item for item in updated_also_allow if item != "pixel_web_extract"]
updated_sandbox_allow = [item for item in updated_sandbox_allow if item != "pixel_web_extract"]
for extension_tool in (
    "pixel_ods_status", "pixel_ods_apps_list", "pixel_ods_host_observe", "pixel_ods_host_command_propose",
    "pixel_ods_evidence_report", "pixel_ods_evidence_readback",
    "pixel_ods_web_extract", "pixel_ods_download_promote", "pixel_ods_workspace_preview"
):
    if extension_tool not in updated_also_allow:
        updated_also_allow.append(extension_tool)
for permitted_tool in (
    "web_search", "web_fetch", "pixel_ods_status", "pixel_ods_apps_list", "pixel_ods_host_observe", "pixel_ods_host_command_propose",
    "pixel_ods_evidence_report", "pixel_ods_evidence_readback",
    "pixel_ods_web_extract", "pixel_ods_download_promote", "pixel_ods_workspace_preview"
):
    if permitted_tool not in updated_sandbox_allow:
        updated_sandbox_allow.append(permitted_tool)
updated_tools["alsoAllow"] = sorted(set(updated_also_allow))
updated_sandbox_tools["allow"] = sorted(set(updated_sandbox_allow))
if updated == value:
    print("unchanged")
    raise SystemExit(0)

fd, temporary = tempfile.mkstemp(prefix=".ods-pixel-runtime-budget.", dir=path.parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(updated, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    print(temporary)
except BaseException:
    if os.path.exists(temporary):
        os.unlink(temporary)
    raise
PY
)" || return 1
    if [[ "$staged" == unchanged ]]; then
        printf '%s\n' unchanged
        return 0
    fi
    [[ "$staged" == "${config%/*}/.ods-pixel-runtime-budget."* && -f "$staged" && ! -L "$staged" ]] || return 1
    if ! ods_pixel_run_as_owner "$owner" "$home" env OPENCLAW_CONFIG_PATH="$staged" \
        "$openclaw_bin" config validate >/dev/null 2>&1; then
        ods_pixel_run_as_owner "$owner" "$home" rm -f -- "$staged"
        return 1
    fi
    if ! _ods_pixel_atomic_replace_managed_file "$owner" "$home" "$staged" "$config"; then
        ods_pixel_run_as_owner "$owner" "$home" rm -f -- "$staged"
        return 1
    fi
    ods_pixel_run_as_owner "$owner" "$home" rm -f -- "$staged" || return 1
    printf '%s\n' changed
}

_ods_pixel_restart_gateway_and_verify() {
    local owner="$1" home="$2" pixel_root="$3" attempt ready=false previous_pid current_pid
    previous_pid="$(systemctl show openclaw-gateway.service -p MainPID --value 2>/dev/null || true)"
    if ods_sudo_available; then
        # Writing the final ODS runtime overlay can make OpenClaw begin its own
        # supervised config restart before this helper samples MainPID. A
        # transient MainPID=0 is safe on the privileged systemd path because
        # `systemctl restart` establishes the desired service state directly.
        # Keep the stricter live-PID proof below for the unprivileged signal
        # fallback, where ODS must prove exactly which owner process it kills.
        ods_sudo systemctl restart openclaw-gateway.service || return 1
    else
        # The ODS host agent runs as the same unprivileged install owner. Its
        # non-interactive sudo credential may expire long after installation,
        # so allow one narrow restart path without granting general sudo: the
        # verified system unit must run as this owner with Restart=always, and
        # /proc must prove the current MainPID has that owner's UID. SIGTERM is
        # then enough for systemd to replace the process under the same unit.
        local unit_user restart_policy owner_uid process_uid
        [[ "$previous_pid" =~ ^[1-9][0-9]*$ ]] || return 1
        unit_user="$(systemctl show openclaw-gateway.service -p User --value 2>/dev/null || true)"
        restart_policy="$(systemctl show openclaw-gateway.service -p Restart --value 2>/dev/null || true)"
        owner_uid="$(id -u "$owner" 2>/dev/null || true)"
        process_uid="$(awk '/^Uid:/ { print $2; exit }' "/proc/${previous_pid}/status" 2>/dev/null || true)"
        [[ "$(id -un)" == "$owner" && "$unit_user" == "$owner" \
            && "$restart_policy" == "always" && "$owner_uid" =~ ^[0-9]+$ \
            && "$process_uid" == "$owner_uid" ]] || return 1
        current_pid="$(systemctl show openclaw-gateway.service -p MainPID --value 2>/dev/null || true)"
        [[ "$current_pid" == "$previous_pid" ]] || return 1
        kill -TERM "$previous_pid" || return 1
    fi
    current_pid=""
    for attempt in {1..60}; do
        current_pid="$(systemctl show openclaw-gateway.service -p MainPID --value 2>/dev/null || true)"
        if [[ "$current_pid" =~ ^[1-9][0-9]*$ \
            && ( ! "$previous_pid" =~ ^[1-9][0-9]*$ || "$current_pid" != "$previous_pid" ) ]] \
            && systemctl is-active --quiet openclaw-gateway.service; then
            break
        fi
        sleep 1
    done
    [[ "$current_pid" =~ ^[1-9][0-9]*$ \
        && ( ! "$previous_pid" =~ ^[1-9][0-9]*$ || "$current_pid" != "$previous_pid" ) ]] || return 1
    for attempt in {1..60}; do
        if curl --fail --silent --show-error --max-time 5 http://127.0.0.1:18789/health 2>/dev/null \
            | jq -e '.ok == true and .status == "live"' >/dev/null 2>&1; then
            ready=true
            break
        fi
        (( attempt < 60 )) && sleep 2
    done
    [[ "$ready" == true ]] || return 1
    ods_pixel_run_as_owner "$owner" "$home" "$pixel_root/pixel" verify
}

_ods_pixel_restore_model_reconciliation() {
    local owner="$1" home="$2" pixel_root="$3" answers="$4" backup="$5"
    local old_contract openclaw_bin
    openclaw_bin="$(_ods_pixel_openclaw_bin "$owner" "$home")" || return 1
    _ods_pixel_atomic_replace_managed_file "$owner" "$home" "$backup/openclaw.json" "$home/.openclaw/openclaw.json" || return 1
    _ods_pixel_atomic_replace_managed_file "$owner" "$home" "$backup/rollback-onboarding.json" "$answers" || return 1
    _ods_pixel_atomic_replace_managed_file "$owner" "$home" "$backup/pixel-managed.json" "$home/.config/ods/pixel-managed.json" || return 1
    if ! ods_pixel_run_as_owner "$owner" "$home" "$pixel_root/pixel" configure --answers "$answers" --force \
        || ! _ods_pixel_install_onboarding_mirror "$owner" "$home" "$answers" \
        || ! ods_pixel_run_as_owner "$owner" "$home" "$pixel_root/pixel" plan \
        || ! _ods_pixel_recreate_agent_sandbox "$owner" "$home" "$openclaw_bin" \
        || ! _ods_pixel_restart_gateway_and_verify "$owner" "$home" "$pixel_root" \
        || ! _ods_pixel_restart_ingress_and_verify "$owner" "$home" "$answers"; then
        if [[ -f "$backup/runtime-attestation.json" && ! -L "$backup/runtime-attestation.json" ]]; then
            _ods_pixel_atomic_replace_managed_file "$owner" "$home" "$backup/runtime-attestation.json" \
                "$home/.local/share/pixel/runtime-attestation.json" || true
        else
            ods_pixel_run_as_owner "$owner" "$home" rm -f -- \
                "$home/.local/share/pixel/runtime-attestation.json" || true
        fi
        return 1
    fi
    old_contract="$(_ods_pixel_contract_sha256 "$owner" "$home" "$answers")" || return 1
    _ods_pixel_mark_ready "$owner" "$home" "$old_contract" "$pixel_root"
}

ods_pixel_reconcile_promoted_model() {
    local owner="$1" home="$2" promoted_model="$3" final_state="${4:-ready}"
    local promoted_context="${5:-}" promoted_max_tokens="${6:-}" promoted_reasoning="${7:-}"
    local source_ref source_root pixel_root answers candidate backup contract_sha256 openclaw_bin failed=false
    local stable_alias=false staged_alias_candidate=""
    local failure_phase="unknown"
    [[ "$final_state" == ready || "$final_state" == installing ]] || return 1
    source_ref="$(_ods_pixel_managed_source_ref "$owner" "$home")" || return 1
    local PIXEL_SOURCE_REF="$source_ref"
    local PIXEL_SOURCE_URL="${PIXEL_SOURCE_URL:-https://github.com/Osmantic/Pixel.git}"
    source_root="${INSTALL_DIR:?}/data/pixel/source-$source_ref"
    pixel_root="$(_ods_pixel_source_checkout "$owner" "$home" "$source_root")" || return 1
    answers="$INSTALL_DIR/data/pixel/onboarding.json"
    candidate="$pixel_root/dist/openclaw.json"
    openclaw_bin="$(_ods_pixel_openclaw_bin "$owner" "$home")" || return 1
    [[ "$openclaw_bin" == /* && -x "$openclaw_bin" ]] || return 1

    # A stable ODS gateway alias separates Pixel from the concrete endpoint but
    # not from the active model limits. Reuse the live gateway only when its
    # concrete display identity, context, output budget, and reasoning contract
    # already match the promoted route. A model or context change must continue
    # through the transactional path below so OpenClaw compacts against the
    # model that LiteLLM actually serves. Model activation already refuses new
    # work and waits for Pixel streams to drain before reaching this boundary.
    if _ods_pixel_uses_stable_model_alias "$owner" "$home" "$answers"; then
        if _ods_pixel_stable_alias_matches_promoted_model "$owner" "$home" "$answers" \
            "$promoted_model" "$promoted_context" "$promoted_max_tokens" \
            "$promoted_reasoning"; then
            contract_sha256="$(_ods_pixel_contract_sha256 "$owner" "$home" "$answers")" || return 1
            # This is a no-op model reconciliation only when the complete
            # ODS-managed contract is already active. A same-model installer
            # upgrade can legitimately change the extension or host-service
            # contract first; continue through the transactional path below
            # so that change is installed instead of requiring its new hash
            # to already exist in the old ready marker.
            if _ods_pixel_managed_contract_matches "$owner" "$home" "$contract_sha256"; then
                # A stale installed mirror needs the transactional path below,
                # even if the active model and the ODS-side answers match.
                if ods_pixel_run_as_owner "$owner" "$home" cmp -s -- \
                    "$answers" "$home/.config/pixel-deployment/onboarding.json"; then
                    _ods_pixel_wait_ingress "$owner" "$home" 6 1 || return 1
                    _ods_pixel_verify_plugin_loaded "$owner" "$home" "$openclaw_bin" \
                        "${INSTALL_DIR:?}/extensions/services/pixel-agent/plugin" || return 1
                    if [[ "$final_state" == ready ]]; then
                        _ods_pixel_mark_ready "$owner" "$home" "$contract_sha256" "$pixel_root" || return 1
                    else
                        _ods_pixel_mark_verified_installing "$owner" "$home" "$contract_sha256" "$pixel_root" || return 1
                    fi
                    printf '%s\n' "Pixel stable model alias remains active for $promoted_model"
                    return 0
                fi
            fi
        fi
        stable_alias=true
    fi

    backup="$(_ods_pixel_model_reconciliation_snapshot "$owner" "$home" "$answers")" || return 1

    if ! _ods_pixel_update_onboarding_model "$owner" "$home" "$answers" "$promoted_model" \
        "$promoted_context" "$promoted_max_tokens" "$promoted_reasoning"; then
        failed=true
        failure_phase="onboarding-update"
    fi
    if [[ "$failed" == false && "$stable_alias" == true ]]; then
        if ! staged_alias_candidate="$(_ods_pixel_stage_stable_alias_candidate \
            "$owner" "$home" "$answers")"; then
            failed=true
            failure_phase="stable-alias-candidate"
        else
            candidate="$staged_alias_candidate"
        fi
    else
        if [[ "$failed" == false ]] \
            && ! ods_pixel_run_as_owner "$owner" "$home" "$pixel_root/pixel" configure --answers "$answers" --force; then
            failed=true
            failure_phase="pixel-configure"
        fi
        if [[ "$failed" == false ]] \
            && ! ods_pixel_run_as_owner "$owner" "$home" "$pixel_root/pixel" plan; then
            failed=true
            failure_phase="pixel-plan"
        fi
    fi
    if [[ "$failed" == false ]] \
        && ! _ods_pixel_apply_runtime_budget "$owner" "$home" "$candidate" "$openclaw_bin" >/dev/null; then
        failed=true
        failure_phase="runtime-budget"
    fi
    if [[ "$failed" == false ]] \
        && ! _ods_pixel_candidate_is_managed_runtime_update "$owner" "$home" "$candidate" "$answers"; then
        failed=true
        failure_phase="managed-update-validation"
    fi
    if [[ "$failed" == false ]] && ! _ods_pixel_candidate_config_matches_live "$owner" "$home" "$candidate"; then
        if ! _ods_pixel_atomic_replace_managed_file "$owner" "$home" "$candidate" "$home/.openclaw/openclaw.json"; then
            failed=true
            failure_phase="config-install"
        fi
    fi
    if [[ "$failed" == false ]] \
        && ! _ods_pixel_install_onboarding_mirror "$owner" "$home" "$answers"; then
        failed=true
        failure_phase="onboarding-mirror-install"
    fi
    if [[ "$failed" == false ]] \
        && ! _ods_pixel_recreate_agent_sandbox "$owner" "$home" "$openclaw_bin"; then
        failed=true
        failure_phase="sandbox-recreate"
    fi
    if [[ "$failed" == false ]] \
        && ! _ods_pixel_restart_gateway_and_verify "$owner" "$home" "$pixel_root"; then
        failed=true
        failure_phase="gateway-restart-verify"
    fi
    if [[ "$failed" == false ]] \
        && ! _ods_pixel_restart_ingress_and_verify "$owner" "$home" "$answers"; then
        failed=true
        failure_phase="ingress-runtime-refresh"
    fi
    if [[ "$failed" == false ]]; then
        if ! contract_sha256="$(_ods_pixel_contract_sha256 "$owner" "$home" "$answers")"; then
            failed=true
            failure_phase="contract-hash"
        fi
    fi
    if [[ "$failed" == false ]]; then
        if [[ "$final_state" == ready ]]; then
            if ! _ods_pixel_mark_ready "$owner" "$home" "$contract_sha256" "$pixel_root"; then
                failed=true
                failure_phase="ready-marker"
            fi
        else
            if ! _ods_pixel_mark_verified_installing "$owner" "$home" "$contract_sha256" "$pixel_root"; then
                failed=true
                failure_phase="installing-marker"
            fi
        fi
    fi
    if [[ "$failed" == false ]]; then
        if [[ -n "$staged_alias_candidate" ]]; then
            ods_pixel_run_as_owner "$owner" "$home" rm -f -- "$staged_alias_candidate" || true
        fi
        printf '%s\n' "Pixel model route reconciled to $promoted_model"
        return 0
    fi

    if [[ -n "$staged_alias_candidate" ]]; then
        ods_pixel_run_as_owner "$owner" "$home" rm -f -- "$staged_alias_candidate" || true
    fi
    printf 'warning: Pixel model reconciliation failed during phase=%s; restoring the previous verified route\n' \
        "$failure_phase" >&2
    if _ods_pixel_restore_model_reconciliation "$owner" "$home" "$pixel_root" "$answers" "$backup"; then
        printf '%s\n' 'warning: previous Pixel model route restored and verified; rollback=verified' >&2
    else
        printf '%s\n' "error: Pixel model reconciliation and verified rollback both failed; rollback=failed evidence=$backup" >&2
    fi
    return 1
}

_ods_pixel_mark_installing() {
    local owner="$1" home="$2" marker
    marker="$home/.config/ods/pixel-managed.json"
    ods_pixel_run_as_owner "$owner" "$home" python3 - "$marker" "${INSTALL_DIR:?}" "${PIXEL_SOURCE_REF:?}" <<'PY'
import json, os, pathlib, stat, sys, tempfile

path = pathlib.Path(sys.argv[1])
info = path.lstat()
if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_nlink != 1
        or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 65536):
    raise SystemExit("invalid Pixel management marker")
value = json.loads(path.read_text(encoding="utf-8"))
if (value.get("schema_version") != 2 or value.get("manager") != "ods"
        or value.get("initial_active_state") != "absent" or value.get("install_dir") != sys.argv[2]):
    raise SystemExit("Pixel management marker does not match this ODS install")
value["state"] = "installing"
if all(key in value for key in (
        "active_release_version", "release_identity_sha256", "install_manifest_sha256",
        "sandbox_image", "sandbox_image_id")):
    value["requested_source_ref"] = sys.argv[3]
else:
    value["pixel_source_ref"] = sys.argv[3]
fd, temporary = tempfile.mkstemp(prefix=".pixel-managed.", dir=path.parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
}

ods_pixel_prepare_runtime_identity() {
    [[ "${ENABLE_PIXEL_RUNTIME:-false}" == true ]] || return 0
    ods_sudo_available || {
        ai_bad "Pixel requires privileged systemd and group setup on this host."
        return 1
    }

    local owner gid
    owner="$(ods_pixel_install_owner)" || return 1
    if ! getent group ods-pixel >/dev/null 2>&1; then
        ods_sudo groupadd --system ods-pixel
    fi
    ods_sudo usermod -aG ods-pixel "$owner"
    gid="$(getent group ods-pixel | awk -F: 'NR == 1 { print $3 }')"
    [[ "$gid" =~ ^[1-9][0-9]*$ ]] || {
        ai_bad "Could not resolve the ods-pixel group GID."
        return 1
    }
    PIXEL_SERVICE_USER="$owner"
    PIXEL_INGRESS_GID="$gid"
    export PIXEL_SERVICE_USER PIXEL_INGRESS_GID
    if declare -f _phase11_env_set >/dev/null 2>&1; then
        _phase11_env_set PIXEL_INGRESS_GID "$gid"
    fi
    ai_ok "Prepared the unprivileged Pixel runtime identity"
}

_ods_pixel_source_checkout() {
    local owner="$1" home="$2" source_root="$3"
    local source="${PIXEL_SOURCE_URL:?}" ref="${PIXEL_SOURCE_REF:?}"
    local source_timeout="${ODS_PIXEL_SOURCE_TIMEOUT_SECONDS:-180}"
    [[ "$source_root" == /* && "$source_root" != / && ! -L "$source_root" ]] || return 1
    [[ "$source_timeout" =~ ^[0-9]+$ && "$source_timeout" -ge 1 && "$source_timeout" -le 900 ]] || return 1

    if [[ ! -e "$source_root" ]]; then
        local parent="${source_root%/*}" stage checkout
        ods_pixel_run_as_owner "$owner" "$home" mkdir -p -- "$parent"
        stage="$(ods_pixel_run_as_owner "$owner" "$home" mktemp -d "$parent/.pixel-source.XXXXXX")" || return 1
        checkout="$stage/checkout"
        if [[ "$source" == https://github.com/Osmantic/Pixel.git ]]; then
            if ! ods_pixel_run_as_owner_with_umask "$owner" "$home" 0022 timeout "${source_timeout}s" \
                env GIT_TERMINAL_PROMPT=0 git -c credential.interactive=never \
                clone --filter=blob:none --no-checkout -- "$source" "$checkout" >/dev/null; then
                ods_pixel_run_as_owner "$owner" "$home" rm -rf -- "$stage"
                printf '%s\n' 'error: Pixel source clone failed or timed out; configure authorized Git access or use the documented local checkout' >&2
                return 1
            fi
        else
            if ! ods_pixel_run_as_owner_with_umask "$owner" "$home" 0022 timeout "${source_timeout}s" \
                env GIT_TERMINAL_PROMPT=0 git -c credential.interactive=never \
                clone --no-local --no-checkout -- "$source" "$checkout" >/dev/null; then
                ods_pixel_run_as_owner "$owner" "$home" rm -rf -- "$stage"
                return 1
            fi
        fi
        if ! ods_pixel_run_as_owner_with_umask "$owner" "$home" 0022 timeout 60s \
            env GIT_TERMINAL_PROMPT=0 git -C "$checkout" -c advice.detachedHead=false checkout --detach "$ref" >/dev/null \
            || ! ods_pixel_run_as_owner "$owner" "$home" mv -T -- "$checkout" "$source_root"; then
            ods_pixel_run_as_owner "$owner" "$home" rm -rf -- "$stage"
            return 1
        fi
        ods_pixel_run_as_owner "$owner" "$home" rmdir -- "$stage" || return 1
    fi

    # Re-check the destination after the atomic move. The initial guard runs
    # before cloning; this closes the narrow replacement window between mv and
    # the exact-commit/clean-tree verification below.
    [[ ! -L "$source_root" && -d "$source_root/.git" && ! -L "$source_root/.git" ]] || return 1
    [[ "$(ods_pixel_run_as_owner "$owner" "$home" git -C "$source_root" rev-parse HEAD)" == "$ref" ]] || return 1
    ods_pixel_run_as_owner "$owner" "$home" git -C "$source_root" diff --quiet --ignore-submodules --
    ods_pixel_run_as_owner "$owner" "$home" git -C "$source_root" diff --cached --quiet --ignore-submodules --
    printf '%s\n' "$source_root"
}

_ods_pixel_wait_http() {
    local label="$1" url="$2" attempts="${3:-120}" jq_filter="${4:-}"
    local body attempt
    for ((attempt = 1; attempt <= attempts; attempt++)); do
        if body="$(curl --fail --silent --show-error --max-time 8 "$url" 2>/dev/null)"; then
            if [[ -z "$jq_filter" ]] || jq -e "$jq_filter" >/dev/null 2>&1 <<<"$body"; then
                return 0
            fi
        fi
        sleep 2
    done
    ai_bad "$label did not become ready at its loopback endpoint."
    return 1
}

_ods_pixel_gateway_model_alias() {
    case "${ODS_MODEL_SWITCHBOARD:-enabled}" in
        legacy|observe|enabled|"") printf '%s\n' 'ods/current' ;;
        *) return 1 ;;
    esac
}

_ods_pixel_runtime_model_identity() {
    # Pixel must bind to the concrete identity served behind ods/current, not
    # the friendlier catalog alias. Otherwise ingress truth and the OpenClaw
    # contract diverge as soon as a model has a distinct GGUF/runtime name.
    local model=""
    if [[ -n "${EXTERNAL_LLM_URL:-}" ]]; then
        model="${EXTERNAL_LLM_MODEL:-}"
        [[ -n "$model" ]] || return 1
    elif [[ "${GPU_BACKEND:-}" == amd \
        && "${LLM_BACKEND:-}" == lemonade \
        && "${AMD_INFERENCE_RUNTIME:-}" == lemonade ]]; then
        model="${LEMONADE_MODEL:-}"
    fi
    [[ -n "$model" ]] || model="${GGUF_FILE:-${LLM_MODEL:-default}}"
    printf '%s\n' "$model"
}

_ods_pixel_wait_model_gateway() {
    local label="$1" port="$2" api_key="$3" model="$4" attempts="${5:-120}"
    local body attempt
    [[ "$port" =~ ^[0-9]+$ ]] && (( port >= 1 && port <= 65535 )) || return 1
    [[ -n "$api_key" && ${#api_key} -le 4096 && "$api_key" != *[[:cntrl:]]* \
        && "$model" =~ ^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$ ]] || return 1
    for ((attempt = 1; attempt <= attempts; attempt++)); do
        if body="$(curl --fail --silent --show-error --max-time 8 \
            -H @<(printf 'Authorization: Bearer %s\n' "$api_key") \
            "http://127.0.0.1:${port}/v1/models" 2>/dev/null)" \
            && jq -e --arg model "$model" \
                '.data | type == "array" and any(.[]?; .id == $model)' \
                >/dev/null 2>&1 <<<"$body"; then
            return 0
        fi
        sleep 2
    done
    ai_bad "$label did not publish the required model alias at its authenticated loopback endpoint."
    return 1
}

_ods_pixel_enable_chat_endpoint() {
    local owner="$1" home="$2" config
    config="$home/.openclaw/openclaw.json"
    ods_pixel_run_as_owner "$owner" "$home" mkdir -p -- "$home/.openclaw"
    ods_pixel_run_as_owner "$owner" "$home" python3 - "$config" <<'PY'
import json, os, pathlib, stat, sys, tempfile

path = pathlib.Path(sys.argv[1])
if path.is_symlink():
    raise SystemExit("OpenClaw config cannot be a symlink")
value = {}
if path.exists():
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 2 * 1024 * 1024:
        raise SystemExit("OpenClaw config is not a bounded regular file")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit("OpenClaw config must be an object")
gateway = value.setdefault("gateway", {})
http = gateway.setdefault("http", {})
endpoints = http.setdefault("endpoints", {})
endpoints["chatCompletions"] = {"enabled": True}
fd, temporary = tempfile.mkstemp(prefix=".openclaw.", dir=path.parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
}

_ods_pixel_write_extension_catalog() {
    local owner="$1" home="$2" output="$3" install_root="${INSTALL_DIR:?}"
    local source_catalog="$install_root/config/extensions-catalog.json"
    local services_root="$install_root/extensions/library/services"

    ods_pixel_run_as_owner "$owner" "$home" install -d -m 0700 -- "${output%/*}" || return 1
    ods_pixel_run_as_owner "$owner" "$home" python3 - \
        "$source_catalog" "$services_root" "$output" <<'PY'
import hashlib, json, os, pathlib, re, stat, sys, tempfile

source_path, services_path, output_path = map(pathlib.Path, sys.argv[1:4])
owner_uid = os.getuid()


def owned_regular(path, label, maximum):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_nlink != 1 or info.st_uid != owner_uid
            or info.st_mode & 0o022 or info.st_size > maximum):
        raise SystemExit(f"unsafe ODS {label}")
    return path.read_bytes()


def clean_text(value, label, maximum):
    if not isinstance(value, str):
        raise SystemExit(f"invalid ODS extension {label}")
    result = " ".join(value.split())
    if not result or len(result) > maximum or any(ord(character) < 32 for character in result):
        raise SystemExit(f"invalid ODS extension {label}")
    return result


def token_list(value, label, pattern, maximum_items=64, maximum_length=128):
    if not isinstance(value, list) or len(value) > maximum_items:
        raise SystemExit(f"invalid ODS extension {label}")
    result = []
    for item in value:
        if not isinstance(item, str) or len(item) > maximum_length or re.fullmatch(pattern, item) is None:
            raise SystemExit(f"invalid ODS extension {label}")
        if item not in result:
            result.append(item)
    return sorted(result)


source_payload = owned_regular(source_path, "extension source catalog", 8 * 1024 * 1024)
services_info = services_path.lstat()
if (not stat.S_ISDIR(services_info.st_mode) or stat.S_ISLNK(services_info.st_mode)
        or services_info.st_uid != owner_uid or services_info.st_mode & 0o022):
    raise SystemExit("unsafe ODS extension services directory")
try:
    source = json.loads(source_payload)
except (UnicodeDecodeError, json.JSONDecodeError) as exc:
    raise SystemExit("invalid ODS extension source catalog") from exc
raw_extensions = source.get("extensions") if isinstance(source, dict) else None
if not isinstance(raw_extensions, list) or not 1 <= len(raw_extensions) <= 256:
    raise SystemExit("invalid ODS extension source catalog")

extensions = []
seen = set()
for item in raw_extensions:
    if not isinstance(item, dict):
        raise SystemExit("invalid ODS extension catalog entry")
    extension_id = item.get("id")
    if (not isinstance(extension_id, str)
            or re.fullmatch(r"[a-z0-9](?:[a-z0-9_-]|\.(?=[a-z0-9])){0,63}", extension_id) is None
            or extension_id in seen):
        raise SystemExit("invalid or duplicate ODS extension id")
    seen.add(extension_id)
    compose_name = item.get("compose_file")
    if compose_name in {None, ""}:
        continue
    if not isinstance(compose_name, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", compose_name) is None:
        raise SystemExit(f"invalid compose file for ODS extension {extension_id}")
    compose_path = services_path / extension_id / compose_name
    try:
        owned_regular(compose_path, f"extension compose file {extension_id}", 2 * 1024 * 1024)
    except FileNotFoundError:
        # Catalog-only entries are useful references but are not installable by
        # the current ODS lifecycle and must not be advertised to Pixel as such.
        continue

    env_vars = item.get("env_vars", [])
    if not isinstance(env_vars, list) or len(env_vars) > 128:
        raise SystemExit(f"invalid environment metadata for ODS extension {extension_id}")
    required_configuration = []
    optional_configuration = []
    for env_item in env_vars:
        if not isinstance(env_item, dict) or not isinstance(env_item.get("required", False), bool):
            raise SystemExit(f"invalid environment metadata for ODS extension {extension_id}")
        key = env_item.get("key")
        if not isinstance(key, str) or re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", key) is None:
            raise SystemExit(f"invalid configuration key for ODS extension {extension_id}")
        destination = required_configuration if env_item.get("required", False) else optional_configuration
        if key not in destination:
            destination.append(key)

    feature_names = []
    features = item.get("features", [])
    if not isinstance(features, list) or len(features) > 64:
        raise SystemExit(f"invalid feature metadata for ODS extension {extension_id}")
    for feature in features:
        if not isinstance(feature, dict):
            raise SystemExit(f"invalid feature metadata for ODS extension {extension_id}")
        name = clean_text(feature.get("name"), "feature name", 256)
        if name not in feature_names:
            feature_names.append(name)

    extensions.append({
        "id": extension_id,
        "name": clean_text(item.get("name"), "name", 128),
        "description": clean_text(item.get("description"), "description", 1000),
        "category": clean_text(item.get("category"), "category", 64),
        "gpuBackends": token_list(item.get("gpu_backends", []), "GPU backends", r"[a-z0-9][a-z0-9._-]{0,31}", 16, 32),
        "dependsOn": token_list(item.get("depends_on", []), "dependencies", r"[a-z0-9][a-z0-9._-]{0,63}"),
        "requiredConfiguration": sorted(required_configuration),
        "optionalConfiguration": sorted(optional_configuration),
        "tags": token_list(item.get("tags", []), "tags", r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}"),
        "featureNames": sorted(feature_names),
    })

if not extensions:
    raise SystemExit("ODS extension catalog has no installable entries")
extensions.sort(key=lambda entry: entry["id"])
payload = {
    "schemaVersion": 1,
    "kind": "ods-pixel-extension-catalog",
    "sourceSha256": hashlib.sha256(source_payload).hexdigest(),
    "extensions": extensions,
}
serialized = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
parent_info = output_path.parent.lstat()
if (not stat.S_ISDIR(parent_info.st_mode) or stat.S_ISLNK(parent_info.st_mode)
        or parent_info.st_uid != owner_uid or parent_info.st_mode & 0o077):
    raise SystemExit("unsafe ODS Pixel extension catalog directory")
if output_path.is_symlink():
    raise SystemExit("ODS Pixel extension catalog cannot be a symlink")
if output_path.exists():
    existing = output_path.lstat()
    if (not stat.S_ISREG(existing.st_mode) or existing.st_nlink != 1
            or existing.st_uid != owner_uid or existing.st_mode & 0o077
            or existing.st_size > 2 * 1024 * 1024):
        raise SystemExit("unsafe existing ODS Pixel extension catalog")

descriptor, temporary = tempfile.mkstemp(prefix=".extension-catalog.", dir=output_path.parent)
try:
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(serialized)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, output_path)
    directory = os.open(output_path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
}

_ods_pixel_write_extension_manager_unit() {
    local owner="$1" home="$2" output="$3"
    local source="${INSTALL_DIR:?}/extensions/services/pixel-agent/host/pixel-extension-manager.service"
    local dashboard_port="${DASHBOARD_API_PORT:-3002}"

    ods_pixel_run_as_owner "$owner" "$home" install -d -m 0700 -- "${output%/*}" || return 1
    ods_pixel_run_as_owner "$owner" "$home" python3 - "$source" "$output" \
        "$owner" "${INSTALL_DIR:?}" "$dashboard_port" <<'PY'
import os, pathlib, re, stat, sys, tempfile

source_path, output_path = map(pathlib.Path, sys.argv[1:3])
owner, install_dir, port = sys.argv[3:6]
if re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", owner) is None:
    raise SystemExit("unsafe Pixel manager service user")
if not port.isdigit() or not 1 <= int(port) <= 65535:
    raise SystemExit("unsafe ODS dashboard port")
if (not install_dir.startswith("/") or len(install_dir) > 1024
        or any(character in install_dir for character in '\n\r\0"\\%')):
    raise SystemExit("unsafe ODS install directory for systemd")
source_info = source_path.lstat()
if (not stat.S_ISREG(source_info.st_mode) or stat.S_ISLNK(source_info.st_mode)
        or source_info.st_nlink != 1 or source_info.st_uid != os.getuid()
        or source_info.st_mode & 0o022 or source_info.st_size > 1024 * 1024):
    raise SystemExit("unsafe Pixel manager service template")
parent_info = output_path.parent.lstat()
if (not stat.S_ISDIR(parent_info.st_mode) or stat.S_ISLNK(parent_info.st_mode)
        or parent_info.st_uid != os.getuid() or parent_info.st_mode & 0o077):
    raise SystemExit("unsafe Pixel manager service output directory")
if output_path.is_symlink():
    raise SystemExit("Pixel manager service output cannot be a symlink")
if output_path.exists():
    output_info = output_path.lstat()
    if (not stat.S_ISREG(output_info.st_mode) or output_info.st_nlink != 1
            or output_info.st_uid != os.getuid() or output_info.st_mode & 0o077
            or output_info.st_size > 1024 * 1024):
        raise SystemExit("unsafe existing Pixel manager service output")
text = source_path.read_text(encoding="utf-8")
text = (text.replace("__PIXEL_SERVICE_USER__", owner)
            .replace("__ODS_INSTALL_DIR__", install_dir)
            .replace("__ODS_DASHBOARD_PORT__", port))
if "__PIXEL_" in text or "__ODS_" in text:
    raise SystemExit("unresolved Pixel manager systemd placeholder")
descriptor, temporary = tempfile.mkstemp(prefix=".extension-manager.", dir=output_path.parent)
try:
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, output_path)
    directory = os.open(output_path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
}

_ods_pixel_write_artifact_promoter_unit() {
    local owner="$1" home="$2" output="$3"
    local source="${INSTALL_DIR:?}/extensions/services/pixel-agent/host/pixel-artifact-promoter.service"
    local workspace="$home/.openclaw/workspace-pixel"

    ods_pixel_run_as_owner "$owner" "$home" install -d -m 0700 -- "${output%/*}" || return 1
    ods_pixel_run_as_owner "$owner" "$home" python3 - "$source" "$output" \
        "$owner" "$workspace" <<'PY'
import os, pathlib, re, stat, sys, tempfile

source_path, output_path = map(pathlib.Path, sys.argv[1:3])
owner, workspace = sys.argv[3:5]
if re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", owner) is None:
    raise SystemExit("unsafe Pixel artifact promoter owner")
if (not workspace.startswith("/") or workspace == "/" or len(workspace) > 1024
        or any(character in workspace for character in '\n\r\0"\\%')):
    raise SystemExit("unsafe Pixel workspace for artifact promotion")
source_info = source_path.lstat()
if (not stat.S_ISREG(source_info.st_mode) or stat.S_ISLNK(source_info.st_mode)
        or source_info.st_nlink != 1 or source_info.st_uid != os.getuid()
        or source_info.st_mode & 0o022 or source_info.st_size > 1024 * 1024):
    raise SystemExit("unsafe Pixel artifact promoter service template")
parent_info = output_path.parent.lstat()
if (not stat.S_ISDIR(parent_info.st_mode) or stat.S_ISLNK(parent_info.st_mode)
        or parent_info.st_uid != os.getuid() or parent_info.st_mode & 0o077):
    raise SystemExit("unsafe Pixel artifact promoter output directory")
if output_path.is_symlink():
    raise SystemExit("Pixel artifact promoter output cannot be a symlink")
if output_path.exists():
    output_info = output_path.lstat()
    if (not stat.S_ISREG(output_info.st_mode) or output_info.st_nlink != 1
            or output_info.st_uid != os.getuid() or output_info.st_mode & 0o077
            or output_info.st_size > 1024 * 1024):
        raise SystemExit("unsafe existing Pixel artifact promoter output")
text = (source_path.read_text(encoding="utf-8")
        .replace("__PIXEL_SERVICE_USER__", owner)
        .replace("__PIXEL_WORKSPACE__", workspace))
if "__PIXEL_" in text:
    raise SystemExit("unresolved Pixel artifact promoter systemd placeholder")
descriptor, temporary = tempfile.mkstemp(prefix=".artifact-promoter.", dir=output_path.parent)
try:
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, output_path)
    directory = os.open(output_path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
}

_ods_pixel_write_workspace_preview_unit() {
    local owner="$1" home="$2" output="$3" port="${4:-9437}"
    local source="${INSTALL_DIR:?}/extensions/services/pixel-agent/host/pixel-workspace-preview.service"
    local workspace="$home/.openclaw/workspace-pixel"
    [[ "$port" =~ ^[0-9]+$ ]] && (( port >= 1 && port <= 65535 )) || return 1

    ods_pixel_run_as_owner "$owner" "$home" install -d -m 0700 -- "${output%/*}" || return 1
    ods_pixel_run_as_owner "$owner" "$home" python3 - "$source" "$output" \
        "$owner" "$workspace" "$port" <<'PY'
import os, pathlib, re, stat, sys, tempfile

source_path, output_path = map(pathlib.Path, sys.argv[1:3])
owner, workspace, port = sys.argv[3:6]
if re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", owner) is None:
    raise SystemExit("unsafe Pixel workspace preview owner")
if (not workspace.startswith("/") or workspace == "/" or len(workspace) > 1024
        or any(character in workspace for character in '\n\r\0"\\%')):
    raise SystemExit("unsafe Pixel workspace for preview")
if not port.isdigit() or not 1 <= int(port) <= 65535:
    raise SystemExit("unsafe Pixel workspace preview port")
source_info = source_path.lstat()
if (not stat.S_ISREG(source_info.st_mode) or stat.S_ISLNK(source_info.st_mode)
        or source_info.st_nlink != 1 or source_info.st_uid != os.getuid()
        or source_info.st_mode & 0o022 or source_info.st_size > 1024 * 1024):
    raise SystemExit("unsafe Pixel workspace preview service template")
parent_info = output_path.parent.lstat()
if (not stat.S_ISDIR(parent_info.st_mode) or stat.S_ISLNK(parent_info.st_mode)
        or parent_info.st_uid != os.getuid() or parent_info.st_mode & 0o077):
    raise SystemExit("unsafe Pixel workspace preview output directory")
if output_path.is_symlink():
    raise SystemExit("Pixel workspace preview output cannot be a symlink")
if output_path.exists():
    output_info = output_path.lstat()
    if (not stat.S_ISREG(output_info.st_mode) or output_info.st_nlink != 1
            or output_info.st_uid != os.getuid() or output_info.st_mode & 0o077
            or output_info.st_size > 1024 * 1024):
        raise SystemExit("unsafe existing Pixel workspace preview output")
text = (source_path.read_text(encoding="utf-8")
        .replace("__PIXEL_SERVICE_USER__", owner)
        .replace("__PIXEL_WORKSPACE__", workspace)
        .replace("__PIXEL_PREVIEW_PORT__", port))
if "__PIXEL_" in text:
    raise SystemExit("unresolved Pixel workspace preview systemd placeholder")
descriptor, temporary = tempfile.mkstemp(prefix=".workspace-preview.", dir=output_path.parent)
try:
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, output_path)
    directory = os.open(output_path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
}

_ods_pixel_write_operations_policy() {
    local owner="$1" home="$2" policy="$3" install_root="${INSTALL_DIR:?}"
    local workspace="$home/.openclaw/workspace-pixel"
    local system_observer_source="$install_root/extensions/services/pixel-agent/host/system_observe.py"

    ods_pixel_run_as_owner "$owner" "$home" install -d -m 0700 -- "${policy%/*}" || return 1
    ods_pixel_run_as_owner "$owner" "$home" python3 - "$policy" "$install_root" "$workspace" \
        "$system_observer_source" <<'PY'
import json, os, pathlib, re, shutil, socket, stat, sys, tempfile

out, install_root, workspace, system_observer_source_raw = sys.argv[1:]
path = pathlib.Path(out)
if not path.is_absolute() or path == pathlib.Path("/"):
    raise SystemExit("ODS Pixel Operations policy path must be absolute and non-root")
parent_info = path.parent.lstat()
if (not stat.S_ISDIR(parent_info.st_mode) or stat.S_ISLNK(parent_info.st_mode)
        or parent_info.st_uid != os.getuid() or parent_info.st_mode & 0o077):
    raise SystemExit("unsafe ODS Pixel Operations policy directory")
if path.is_symlink():
    raise SystemExit("ODS Pixel Operations policy cannot be a symlink")
if path.exists():
    info = path.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid != os.getuid() or info.st_mode & 0o077
            or info.st_size > 2 * 1024 * 1024):
        raise SystemExit("unsafe existing ODS Pixel Operations policy")

hostname = socket.gethostname()
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}", hostname):
    raise SystemExit("unsafe ODS host name for Pixel Operations policy")

def normalized_root(value, label):
    candidate = pathlib.Path(value)
    if not candidate.is_absolute() or candidate == pathlib.Path("/"):
        raise SystemExit(f"{label} must be an absolute non-root path")
    return os.path.normpath(str(candidate))

install_root = normalized_root(install_root, "ODS install root")
workspace = normalized_root(workspace, "Pixel workspace")
manager_socket_root = "/run/ods-pixel-manager"
manager_socket = manager_socket_root + "/extension-manager.sock"
manager_program = "/opt/pixel-ops-broker/ods-extension-manager.py"
system_observer = "/usr/local/libexec/ods-pixel-system-observe.py"
system_observer_source = pathlib.Path(system_observer_source_raw)
python_binary = str(pathlib.Path("/usr/bin/python3").resolve(strict=True))
hostname_binary = "/usr/bin/hostname"
uname_binary = "/usr/bin/uname"
cat_binary = "/usr/bin/cat"
uptime_binary = "/usr/bin/uptime"

def required_binary(name):
    candidate = shutil.which(name)
    if not candidate:
        raise SystemExit(f"required Pixel Operations executable is unavailable: {name}")
    return str(pathlib.Path(candidate).resolve(strict=True))

ps_binary = required_binary("ps")
systemctl_binary = required_binary("systemctl")
lscpu_binary = required_binary("lscpu")
free_binary = required_binary("free")
df_binary = required_binary("df")
ip_binary = required_binary("ip")
ss_binary = required_binary("ss")
for binary in (
    python_binary, hostname_binary, uname_binary, cat_binary, uptime_binary, ps_binary,
    systemctl_binary, lscpu_binary, free_binary, df_binary, ip_binary, ss_binary,
):
    info = pathlib.Path(binary).lstat()
    if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_uid != 0 or info.st_mode & 0o022
            or not os.access(binary, os.X_OK)):
        raise SystemExit(f"unsafe Pixel Operations executable: {binary}")
observer_info = system_observer_source.lstat()
if (not stat.S_ISREG(observer_info.st_mode) or stat.S_ISLNK(observer_info.st_mode)
        or observer_info.st_nlink != 1 or observer_info.st_uid != os.getuid()
        or observer_info.st_mode & 0o022 or observer_info.st_size > 2 * 1024 * 1024):
    raise SystemExit("unsafe ODS Pixel system observer source")

payload = {
    "schemaVersion": 2,
    "deployment": "ods-default",
    "maxWorkers": 4,
    "workflowWorkers": 4,
    "maxWorkflowSteps": 32,
    "defaultTimeoutSeconds": 60,
    "maxTimeoutSeconds": 3600,
    "maxOutputBytes": 262144,
    "planTtlMinutes": 30,
    "identityCacheSeconds": 30,
    "sshBinary": "/usr/bin/ssh",
    "download": {
        "stagingRoot": "/var/lib/pixel-ops-broker/artifacts",
        "maxBytes": 536870912,
        "maxRedirects": 5,
        "allowedDomains": [
            "example.com",
            "github.com",
            "githubusercontent.com",
            "hf.co",
            "huggingface.co",
            "nodejs.org",
            "npmjs.org",
            "pypi.org",
            "pythonhosted.org",
        ],
    },
    "targets": {
        "ods-host": {
            "enabled": True,
            "backend": "local",
            "environment": "unclassified",
            "expectedHostname": hostname,
            # The broker service intentionally uses ProtectHome=true. Fixed
            # host observations do not need an owner-home cwd, so start them
            # inside the broker's root-custodied state tree instead of making
            # /home visible to the privileged execution service.
            "defaultCwd": "/var/lib/pixel-ops-broker",
            "allowedRoots": [
                install_root, workspace, "/var/lib/pixel-ops-broker", manager_socket_root,
            ],
            "writableRoots": [workspace],
            "shell": "/bin/bash",
            # Raw host commands remain a proposal-only break-glass path in the
            # external broker: every immutable plan requires a separate owner
            # approval, and the broker service itself is unprivileged and
            # systemd-confined. This enables useful host work for every model
            # without granting any model ambient execution authority.
            "allowRaw": True,
            "labels": ["ods-host"],
            "capabilities": ["inspect", "manage-extensions", "stage-download", "approved-host-command"],
        },
        "broker": {
            "enabled": True,
            "backend": "local",
            "environment": "lab",
            "expectedHostname": hostname,
            "defaultCwd": "/var/lib/pixel-ops-broker",
            "allowedRoots": ["/var/lib/pixel-ops-broker"],
            "writableRoots": ["/var/lib/pixel-ops-broker/artifacts"],
            "shell": "/bin/sh",
            "allowRaw": False,
            "labels": ["broker-quarantine"],
            "capabilities": ["stage-download"],
        },
    },
    "actions": {
        "host.identity": {
            "description": "Verify and report the ODS host name.",
            "tier": "read",
            "effect": "observe",
            "defaultAuthority": "observe",
            "idempotent": True,
            "reversible": False,
            "targets": ["ods-host"],
            "argv": [hostname_binary],
            "timeoutSeconds": 10,
            "exclusiveTarget": False,
        },
        "host.platform": {
            "description": "Report the ODS host kernel and architecture.",
            "tier": "read",
            "effect": "observe",
            "defaultAuthority": "observe",
            "idempotent": True,
            "reversible": False,
            "targets": ["ods-host"],
            "argv": [uname_binary, "-a"],
            "timeoutSeconds": 10,
            "exclusiveTarget": False,
        },
        "host.kernel": {
            "description": "Report the ODS host kernel name and release.",
            "tier": "read",
            "effect": "observe",
            "defaultAuthority": "observe",
            "idempotent": True,
            "reversible": False,
            "targets": ["ods-host"],
            "argv": [uname_binary, "-sr"],
            "timeoutSeconds": 10,
            "exclusiveTarget": False,
        },
        "host.architecture": {
            "description": "Report the ODS host machine architecture.",
            "tier": "read",
            "effect": "observe",
            "defaultAuthority": "observe",
            "idempotent": True,
            "reversible": False,
            "targets": ["ods-host"],
            "argv": [uname_binary, "-m"],
            "timeoutSeconds": 10,
            "exclusiveTarget": False,
        },
        "host.os-release": {
            "description": "Report the ODS host operating-system release metadata.",
            "tier": "read",
            "effect": "observe",
            "defaultAuthority": "observe",
            "idempotent": True,
            "reversible": False,
            "targets": ["ods-host"],
            "argv": [cat_binary, "/etc/os-release"],
            "timeoutSeconds": 10,
            "exclusiveTarget": False,
        },
        "host.uptime": {
            "description": "Report host uptime and the one, five, and fifteen minute load averages.",
            "tier": "read",
            "effect": "observe",
            "defaultAuthority": "observe",
            "idempotent": True,
            "reversible": False,
            "targets": ["ods-host"],
            "argv": [uptime_binary],
            "timeoutSeconds": 10,
            "exclusiveTarget": False,
        },
        "host.processes": {
            "description": "List bounded process identity and resource fields without command arguments or environment values.",
            "tier": "read",
            "effect": "observe",
            "defaultAuthority": "observe",
            "idempotent": True,
            "reversible": False,
            "targets": ["ods-host"],
            "argv": [
                ps_binary, "-eo", "pid=,ppid=,user=,stat=,%cpu=,%mem=,comm=", "--sort=-%cpu",
            ],
            "timeoutSeconds": 10,
            "exclusiveTarget": False,
        },
        "host.services": {
            "description": "List running and failed system services without reading service environments or credentials.",
            "tier": "read",
            "effect": "observe",
            "defaultAuthority": "observe",
            "idempotent": True,
            "reversible": False,
            "targets": ["ods-host"],
            "argv": [
                systemctl_binary, "--no-pager", "--plain", "--legend=no", "list-units",
                "--type=service", "--state=running,failed",
            ],
            "timeoutSeconds": 15,
            "exclusiveTarget": False,
        },
        "host.cpu": {
            "description": "Report bounded CPU and virtualization inventory as JSON.",
            "tier": "read",
            "effect": "observe",
            "defaultAuthority": "observe",
            "idempotent": True,
            "reversible": False,
            "targets": ["ods-host"],
            "argv": [lscpu_binary, "--json"],
            "timeoutSeconds": 10,
            "exclusiveTarget": False,
        },
        "host.gpu": {
            "description": "Report a bounded GPU name, memory capacity, and driver projection without UUIDs or serial numbers.",
            "tier": "read",
            "effect": "observe",
            "defaultAuthority": "observe",
            "idempotent": True,
            "reversible": False,
            "targets": ["ods-host"],
            "argv": [python_binary, system_observer, "gpu"],
            "timeoutSeconds": 15,
            "exclusiveTarget": False,
        },
        "host.memory": {
            "description": "Report host memory and swap capacity in bytes.",
            "tier": "read",
            "effect": "observe",
            "defaultAuthority": "observe",
            "idempotent": True,
            "reversible": False,
            "targets": ["ods-host"],
            "argv": [free_binary, "--bytes"],
            "timeoutSeconds": 10,
            "exclusiveTarget": False,
        },
        "host.storage": {
            "description": "Report mounted filesystem capacity without reading filesystem contents.",
            "tier": "read",
            "effect": "observe",
            "defaultAuthority": "observe",
            "idempotent": True,
            "reversible": False,
            "targets": ["ods-host"],
            "argv": [
                df_binary, "--block-size=1", "--output=fstype,size,used,avail,pcent,target",
            ],
            "timeoutSeconds": 10,
            "exclusiveTarget": False,
        },
        "host.network-addresses": {
            "description": "Report host network interfaces and assigned addresses as JSON.",
            "tier": "read",
            "effect": "observe",
            "defaultAuthority": "observe",
            "idempotent": True,
            "reversible": False,
            "targets": ["ods-host"],
            "argv": [ip_binary, "-j", "address", "show"],
            "timeoutSeconds": 10,
            "exclusiveTarget": False,
        },
        "host.network-routes": {
            "description": "Report the host routing table as bounded JSON.",
            "tier": "read",
            "effect": "observe",
            "defaultAuthority": "observe",
            "idempotent": True,
            "reversible": False,
            "targets": ["ods-host"],
            "argv": [ip_binary, "-j", "route", "show"],
            "timeoutSeconds": 10,
            "exclusiveTarget": False,
        },
        "host.listening-ports": {
            "description": "List listening TCP and UDP endpoints without process arguments or credentials.",
            "tier": "read",
            "effect": "observe",
            "defaultAuthority": "observe",
            "idempotent": True,
            "reversible": False,
            "targets": ["ods-host"],
            "argv": [ss_binary, "-H", "-lntu"],
            "timeoutSeconds": 10,
            "exclusiveTarget": False,
        },
        "host.tailscale": {
            "description": "Report only whether Tailscale is installed and running, without addresses, peers, accounts, or routes.",
            "tier": "read",
            "effect": "observe",
            "defaultAuthority": "observe",
            "idempotent": True,
            "reversible": False,
            "targets": ["ods-host"],
            "argv": [python_binary, system_observer, "tailscale"],
            "timeoutSeconds": 15,
            "exclusiveTarget": False,
        },
        "host.network-peer": {
            "description": "Resolve and perform bounded ICMP and TCP reachability checks against one owner-named private LAN or Tailscale peer without authentication or mutation.",
            "tier": "read",
            "effect": "observe",
            "defaultAuthority": "observe",
            "idempotent": True,
            "reversible": False,
            "targets": ["ods-host"],
            "parameters": {
                "peer": {
                    "pattern": "^[A-Za-z0-9.:-]{1,253}$",
                    "maxLength": 253,
                },
                "ports": {
                    "pattern": "^[0-9,]{1,47}$",
                    "maxLength": 47,
                },
            },
            "argv": [
                python_binary, system_observer, "network-peer", "{peer}", "{ports}",
            ],
            "timeoutSeconds": 30,
            "exclusiveTarget": False,
        },
        "ods.extensions.search": {
            "description": "Search the installable ODS extension catalog. Use query 'all' to list the bounded first page. This read-only action does not install or configure anything.",
            "tier": "read",
            "effect": "observe",
            "defaultAuthority": "observe",
            "idempotent": True,
            "reversible": False,
            "targets": ["ods-host"],
            "parameters": {
                "query": {
                    "pattern": "^[A-Za-z0-9 _/+:#.-]{1,80}$",
                    "maxLength": 80,
                },
            },
            "argv": [
                python_binary,
                "/opt/pixel-ops-broker/ods-extension-search.py",
                "/opt/pixel-ops-broker/ods-extension-catalog.json",
                "{query}",
            ],
            "timeoutSeconds": 10,
            "exclusiveTarget": False,
        },
        "ods.extensions.list": {
            "description": "List the live installed, enabled, disabled, unhealthy, and available ODS extension states through the scoped lifecycle proxy.",
            "tier": "read",
            "effect": "observe",
            "defaultAuthority": "observe",
            "idempotent": True,
            "reversible": False,
            "targets": ["ods-host"],
            "argv": [
                python_binary, manager_program, "client", manager_socket, "list", "all",
            ],
            "timeoutSeconds": 30,
            "exclusiveTarget": False,
        },
        "ods.extensions.inspect": {
            "description": "Inspect one ODS extension's installed state and configuration prerequisites through the scoped lifecycle proxy.",
            "tier": "read",
            "effect": "observe",
            "defaultAuthority": "observe",
            "idempotent": True,
            "reversible": False,
            "targets": ["ods-host"],
            "parameters": {
                "serviceId": {
                    "pattern": "^([a-z0-9]|[a-z0-9][a-z0-9._-]{0,62}[a-z0-9])$",
                    "maxLength": 64,
                },
            },
            "argv": [
                python_binary, manager_program, "client", manager_socket, "inspect", "{serviceId}",
            ],
            "timeoutSeconds": 30,
            "exclusiveTarget": False,
        },
        "ods.extensions.install": {
            "description": "Install and verify one cataloged ODS extension through the scoped lifecycle proxy. Exact-plan owner approval is required.",
            "tier": "managed",
            "effect": "manage",
            "defaultAuthority": "propose",
            "idempotent": True,
            "reversible": True,
            "rollbackAction": "ods.extensions.remove",
            "verificationAction": "ods.extensions.inspect",
            "targets": ["ods-host"],
            "parameters": {
                "serviceId": {
                    "pattern": "^([a-z0-9]|[a-z0-9][a-z0-9._-]{0,62}[a-z0-9])$",
                    "maxLength": 64,
                },
            },
            "argv": [
                python_binary, manager_program, "client", manager_socket, "install", "{serviceId}",
            ],
            "timeoutSeconds": 900,
            "exclusiveTarget": True,
        },
        "ods.extensions.enable": {
            "description": "Enable and verify one installed ODS extension through the scoped lifecycle proxy. Exact-plan owner approval is required.",
            "tier": "managed",
            "effect": "manage",
            "defaultAuthority": "propose",
            "idempotent": True,
            "reversible": True,
            "rollbackAction": "ods.extensions.disable",
            "verificationAction": "ods.extensions.inspect",
            "targets": ["ods-host"],
            "parameters": {
                "serviceId": {
                    "pattern": "^([a-z0-9]|[a-z0-9][a-z0-9._-]{0,62}[a-z0-9])$",
                    "maxLength": 64,
                },
            },
            "argv": [
                python_binary, manager_program, "client", manager_socket, "enable", "{serviceId}",
            ],
            "timeoutSeconds": 300,
            "exclusiveTarget": True,
        },
        "ods.extensions.disable": {
            "description": "Disable and verify one ODS extension while preserving its data. Exact-plan owner approval is required.",
            "tier": "managed",
            "effect": "manage",
            "defaultAuthority": "propose",
            "idempotent": True,
            "reversible": True,
            "rollbackAction": "ods.extensions.enable",
            "verificationAction": "ods.extensions.inspect",
            "targets": ["ods-host"],
            "parameters": {
                "serviceId": {
                    "pattern": "^([a-z0-9]|[a-z0-9][a-z0-9._-]{0,62}[a-z0-9])$",
                    "maxLength": 64,
                },
            },
            "argv": [
                python_binary, manager_program, "client", manager_socket, "disable", "{serviceId}",
            ],
            "timeoutSeconds": 300,
            "exclusiveTarget": True,
        },
        "ods.extensions.remove": {
            "description": "Remove one disabled user-installed ODS extension definition while preserving its data. Exact-plan owner approval is required.",
            "tier": "change",
            "effect": "change",
            "defaultAuthority": "propose",
            "idempotent": True,
            "reversible": False,
            "verificationAction": "ods.extensions.inspect",
            "targets": ["ods-host"],
            "parameters": {
                "serviceId": {
                    "pattern": "^([a-z0-9]|[a-z0-9][a-z0-9._-]{0,62}[a-z0-9])$",
                    "maxLength": 64,
                },
            },
            "argv": [
                python_binary, manager_program, "client", manager_socket, "remove", "{serviceId}",
            ],
            "timeoutSeconds": 300,
            "exclusiveTarget": True,
        },
    },
    "authority": {
        "defaultLevel": "propose",
        "grants": [{
            "id": "ods-approved-downloads",
            "level": "bounded-auto",
            "actions": ["download.stage"],
            "targets": ["broker"],
            "tiers": ["staging"],
            "environments": ["lab"],
            "maxExecutions": 100,
            "windowSeconds": 86400,
            "maxConcurrent": 2,
            "maxRuntimeSeconds": 600,
            "maxFailures": 10,
            "maxArtifactBytes": 536870912,
        }],
    },
}
content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
descriptor, temporary = tempfile.mkstemp(prefix=".pixel-ops-policy.", dir=path.parent)
try:
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
}

_ods_pixel_write_onboarding() {
    local owner="$1" home="$2" answers="$3" openclaw_bin="$4" plugin_path="$5" plugin_digest="$6"
    local context="${MAX_CONTEXT:-16384}" max_tokens reasoning=false
    local gateway_alias gateway_label runtime_model gateway_port="${LITELLM_PORT:-4000}" gateway_key="${LITELLM_KEY:-}"
    local gateway_key_file write_status=0
    if [[ "$context" =~ ^[0-9]+$ && "$context" -ge 4096 ]]; then
        :
    else
        ai_bad "Pixel requires a model context of at least 4096 tokens."
        return 1
    fi
    # Models need enough output room to emit a complete structured tool call
    # containing an original file. Live 64K Qwen qualification proved that a
    # fixed 4096-token ceiling can truncate a single-file SVG before the first
    # write. Keep the ceiling model-agnostic and capability-derived: reserve
    # three quarters of compact contexts for input, with an 8192-token output
    # ceiling for larger contexts (matching ODS's Windows/macOS agent budget).
    max_tokens="$((context / 4))"
    (( max_tokens > 8192 )) && max_tokens=8192
    # This field controls the active OpenClaw reasoning path, not merely the
    # model family's theoretical capability. Keep the default no-think setting
    # false even for reasoning-capable models; an explicit operator setting
    # enables it and is reconciled transactionally on model swaps.
    if [[ ! "${LLAMA_REASONING:-off}" =~ ^(off|none|false|0)$ ]]; then
        reasoning=true
    fi
    gateway_alias="$(_ods_pixel_gateway_model_alias)" || {
        ai_bad "Pixel received an unsupported ODS model Switchboard mode."
        return 1
    }
    gateway_label="Default"
    [[ "$gateway_alias" == "ods/current" ]] && gateway_label="Current"
    runtime_model="$(_ods_pixel_runtime_model_identity)" || return 1
    if [[ ! "$gateway_port" =~ ^[0-9]+$ ]] || (( gateway_port < 1 || gateway_port > 65535 )); then
        ai_bad "Pixel requires a valid loopback LiteLLM port."
        return 1
    fi
    [[ -n "$gateway_key" && ${#gateway_key} -le 4096 ]] || {
        ai_bad "Pixel requires the generated LiteLLM gateway key."
        return 1
    }

    ods_pixel_run_as_owner "$owner" "$home" install -d -m 0700 -- "${answers%/*}" || return 1
    gateway_key_file="$(ods_pixel_run_as_owner "$owner" "$home" \
        mktemp "${answers%/*}/.pixel-gateway-key.XXXXXX")" || return 1
    if ! printf '%s' "$gateway_key" \
        | ods_pixel_run_as_owner "$owner" "$home" tee -- "$gateway_key_file" >/dev/null \
        || ! ods_pixel_run_as_owner "$owner" "$home" chmod 0600 -- "$gateway_key_file"; then
        ods_pixel_run_as_owner "$owner" "$home" rm -f -- "$gateway_key_file" || true
        return 1
    fi
    ods_pixel_run_as_owner "$owner" "$home" python3 - "$answers" \
        "$openclaw_bin" "$home" "$runtime_model" "$context" "$max_tokens" "$reasoning" \
        "$gateway_alias" "$gateway_label" "$gateway_port" "$gateway_key_file" \
        "${SEARXNG_PORT:-8888}" "$plugin_path" "$plugin_digest" <<'PY' || write_status=$?
import json, os, pathlib, re, stat, sys, tempfile

(out, openclaw_bin, home, model, context, max_tokens, reasoning,
 gateway_alias, gateway_label, gateway_port, gateway_key_path,
 search_port, plugin_path, plugin_digest) = sys.argv[1:]
gateway_key_path = pathlib.Path(gateway_key_path)
gateway_key_info = gateway_key_path.lstat()
if (not stat.S_ISREG(gateway_key_info.st_mode) or stat.S_ISLNK(gateway_key_info.st_mode)
        or gateway_key_info.st_nlink != 1 or gateway_key_info.st_uid != os.getuid()
        or gateway_key_info.st_mode & 0o077 or gateway_key_info.st_size > 4096):
    raise SystemExit("unsafe ODS Pixel gateway credential")
gateway_key = gateway_key_path.read_text(encoding="utf-8")
if gateway_alias not in {"default", "ods/current"}:
    raise SystemExit("invalid ODS Pixel gateway alias")
if gateway_label not in {"Default", "Current"}:
    raise SystemExit("invalid ODS Pixel gateway label")
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+:/ @(),=-]{0,255}", model):
    raise SystemExit("invalid ODS Pixel model id")
if (not gateway_port.isdigit() or not 1 <= int(gateway_port) <= 65535
        or not gateway_key or len(gateway_key) > 4096
        or any(ord(character) < 32 or ord(character) == 127 for character in gateway_key)):
    raise SystemExit("invalid ODS Pixel gateway route")
home = pathlib.Path(home)
path = pathlib.Path(out)
payload = {
    "deploymentProfile": "prepared",
    "capabilityProfile": "engineering-operator",
    "ownerName": "ODS Owner",
    "organization": "Local ODS",
    "deploymentName": "ods-default",
    "timeZone": "UTC",
    "agentId": "pixel",
    "agentName": "Pixel",
    "openclawBin": openclaw_bin,
    "openclawHome": str(home / ".openclaw"),
    "installDir": str(home / ".local" / "share" / "pixel"),
    "workspace": str(home / ".openclaw" / "workspace-pixel"),
    "modelProvider": "ods-gateway",
    "modelId": gateway_alias,
    "modelName": f"ODS {gateway_label} ({model})",
    "modelBaseUrl": f"http://127.0.0.1:{gateway_port}/v1",
    "modelApiKey": gateway_key,
    "modelReasoning": reasoning == "true",
    "modelContextWindow": int(context),
    "modelMaxTokens": int(max_tokens),
    "modelPrivateHosts": [],
    "searxngBaseUrl": f"http://127.0.0.1:{search_port}",
    "embeddingModel": "embeddinggemma-300m-qat-Q8_0.gguf",
    "embeddingCache": str(home / ".cache" / "openclaw" / "embeddings"),
    "googleAccount": "ods@localhost.local",
    "calendarId": "primary",
    "gatewayPort": 18789,
    "gatewayExtensions": [{
        "id": "pixel-ods",
        "path": plugin_path,
        "sha256": plugin_digest,
        "tools": ["pixel_ods_status", "pixel_ods_apps_list", "pixel_ods_host_observe", "pixel_ods_host_command_propose", "pixel_ods_evidence_report", "pixel_ods_evidence_readback", "pixel_ods_web_extract", "pixel_ods_download_promote", "pixel_ods_workspace_preview"],
    }],
    "localCapabilityPacks": [],
    "agentSkills": [],
    "emailLimbEnabled": False,
    "calendarLimbEnabled": False,
    "calendarDirectEnabled": False,
    "socialLimbEnabled": False,
    "webLimbEnabled": False,
    "operationsLimbEnabled": True,
    "operationsPolicyFile": str(path.parent / "operations-policy.json"),
    "frontierLimbEnabled": False,
    "frontierAuthMode": "api-key",
    # Pixel still validates the managed Frontier policy while the limb is
    # disabled. Use its smallest built-in budget rather than "custom", which
    # is reserved for a separate private policy and otherwise renders an empty
    # budget object during configure.
    "frontierBudgetProfile": "starter",
    "frontierTaskPacks": [],
    "operationsActionPacks": [],
}
path.parent.mkdir(parents=True, exist_ok=True)
if path.is_symlink():
    raise SystemExit("ODS Pixel onboarding contract cannot be a symlink")
if path.exists():
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > 2 * 1024 * 1024:
        raise SystemExit("invalid existing ODS Pixel onboarding contract")
content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
fd, temporary = tempfile.mkstemp(prefix=".pixel-onboarding.", dir=path.parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
    ods_pixel_run_as_owner "$owner" "$home" rm -f -- "$gateway_key_file" || write_status=1
    return "$write_status"
}

_ods_pixel_wait_extension_manager_probe() {
    local program="$1" extension_id="$2" attempts="${3:-30}" delay="${4:-1}"
    local manager_probe attempt
    [[ "$program" == /* && -f "$program" && ! -L "$program" \
        && "$extension_id" =~ ^[a-z0-9][a-z0-9._-]{0,63}$ \
        && "$extension_id" != *. \
        && "$attempts" =~ ^[0-9]+$ && "$attempts" -ge 1 && "$attempts" -le 60 \
        && "$delay" =~ ^[0-9]+$ && "$delay" -le 5 ]] || return 1
    for ((attempt = 1; attempt <= attempts; attempt++)); do
        if manager_probe="$(ods_sudo -u pixel-ops-broker /usr/bin/python3 \
            "$program" client /run/ods-pixel-manager/extension-manager.sock \
            inspect "$extension_id" 2>/dev/null)" \
            && jq -e --arg id "$extension_id" '.schemaVersion == 1
                and .kind == "ods-pixel-extension-lifecycle"
                and .action == "inspect" and .extensionId == $id
                and (.outcome == "ready" or .outcome == "blocked")
                and .changed == false and .externalEffectOccurred == false
                and (.requiredConfiguration | type == "array")
                and (.optionalConfiguration | type == "array")
                and (.missingConfiguration | type == "array")
                and .rollback == {"attempted": false, "succeeded": null}
                and .boundary == "Scoped ODS extension lifecycle proxy; it grants no Docker, shell, credential, arbitrary HTTP, or data-purge authority."' \
                <<<"$manager_probe" >/dev/null; then
            return 0
        fi
        if (( attempt < attempts && delay > 0 )); then
            sleep "$delay"
        fi
    done
    return 1
}

_ods_pixel_wait_artifact_promoter_probe() {
    local owner="$1" home="$2" program="$3" attempts="${4:-30}" delay="${5:-1}"
    local response attempt
    [[ "$program" == /* && -f "$program" && ! -L "$program" \
        && "$attempts" =~ ^[0-9]+$ && "$attempts" -ge 1 && "$attempts" -le 60 \
        && "$delay" =~ ^[0-9]+$ && "$delay" -le 5 ]] || return 1
    for ((attempt = 1; attempt <= attempts; attempt++)); do
        if response="$(ods_pixel_run_as_owner "$owner" "$home" /usr/bin/python3 \
            "$program" health /run/ods-pixel-artifact-promoter/promoter.sock 2>/dev/null)" \
            && jq -e '.schemaVersion == 1
                and .kind == "ods-pixel-download-promotion"
                and .status == "ok"
                and .boundary == "Verified create-only promotion from Pixel Operations quarantine into the configured owner workspace; no arbitrary source, overwrite, execution, or path traversal authority."' \
                <<<"$response" >/dev/null; then
            return 0
        fi
        if (( attempt < attempts && delay > 0 )); then
            sleep "$delay"
        fi
    done
    return 1
}

_ods_pixel_wait_workspace_preview_probe() {
    local owner="$1" home="$2" program="$3" port="${4:-9437}" attempts="${5:-30}" delay="${6:-1}"
    local response attempt
    [[ "$program" == /* && -f "$program" && ! -L "$program" \
        && "$port" =~ ^[0-9]+$ && "$port" -ge 1 && "$port" -le 65535 \
        && "$attempts" =~ ^[0-9]+$ && "$attempts" -ge 1 && "$attempts" -le 60 \
        && "$delay" =~ ^[0-9]+$ && "$delay" -le 5 ]] || return 1
    for ((attempt = 1; attempt <= attempts; attempt++)); do
        if response="$(ods_pixel_run_as_owner "$owner" "$home" /usr/bin/python3 \
            "$program" health /run/ods-pixel-preview/control.sock 2>/dev/null)" \
            && jq -e --argjson port "$port" '.schemaVersion == 1
                and .kind == "ods-pixel-workspace-preview"
                and .status == "ok" and .port == $port
                and .boundary == "Create-only static-site snapshot from the configured Pixel workspace to a dedicated loopback preview origin; no arbitrary host path, network destination, server process, overwrite, or execution authority."' \
                <<<"$response" >/dev/null; then
            return 0
        fi
        if (( attempt < attempts && delay > 0 )); then
            sleep "$delay"
        fi
    done
    return 1
}

_ods_pixel_install_ingress() {
    local owner="$1" home="$2" plugin_root="$3" extension_catalog="$4"
    local rendered_extension_manager_unit="$5" rendered_artifact_promoter_unit="$6"
    local rendered_workspace_preview_unit="$7" preview_port="${PIXEL_PREVIEW_PORT:-9437}"
    local token_file="$home/.openclaw/openclaw.json"
    local runtime_token_file="/run/ods-pixel/openclaw.json"
    local extension_helper="$plugin_root/host/extension_search.py"
    local installed_extension_helper="/opt/pixel-ops-broker/ods-extension-search.py"
    local installed_extension_catalog="/opt/pixel-ops-broker/ods-extension-catalog.json"
    local extension_manager="$plugin_root/host/extension_manager.py"
    local installed_extension_manager="/opt/pixel-ops-broker/ods-extension-manager.py"
    local system_extension_manager="/usr/local/libexec/ods-pixel-extension-manager.py"
    local artifact_promoter="$plugin_root/host/artifact_promoter.py"
    local system_artifact_promoter="/usr/local/libexec/ods-pixel-artifact-promoter.py"
    local workspace_preview="$plugin_root/host/workspace_preview.py"
    local system_workspace_preview="/usr/local/libexec/ods-pixel-workspace-preview.py"
    local system_observer="$plugin_root/host/system_observe.py"
    local installed_system_observer="/usr/local/libexec/ods-pixel-system-observe.py"
    local operations_service_dropin="$plugin_root/host/pixel-ops-broker-ods.conf"
    local operations_service_dropin_dir="/etc/systemd/system/pixel-ops-broker.service.d"
    local installed_operations_service_dropin="$operations_service_dropin_dir/10-ods-host-observation.conf"
    local ods_version="${VERSION:-2.6.0}"
    [[ "$ods_version" =~ ^[0-9]+(\.[0-9]+){1,3}([-+][A-Za-z0-9.-]+)?$ ]] || return 1
    [[ -f "$token_file" && ! -L "$token_file" ]] || return 1
    [[ "$(stat -c '%u' -- "$token_file")" == "$(id -u "$owner")" ]] || return 1
    (( (8#$(stat -c '%a' -- "$token_file") & 0077) == 0 )) || return 1
    local projection_source kind uid mode size
    for projection_source in "$extension_helper" "$extension_catalog" \
        "$extension_manager" "$rendered_extension_manager_unit" \
        "$artifact_promoter" "$rendered_artifact_promoter_unit" \
        "$workspace_preview" "$rendered_workspace_preview_unit" \
        "$system_observer" \
        "$operations_service_dropin"; do
        [[ -f "$projection_source" && ! -L "$projection_source" ]] || return 1
        IFS='|' read -r kind uid mode size < <(stat -c '%F|%u|%a|%s' -- "$projection_source")
        [[ "$kind" == "regular file" && "$uid" == "$(id -u "$owner")" \
            && "$size" =~ ^[0-9]+$ && "$size" -le 2097152 ]] || return 1
        (( (8#$mode & 0022) == 0 )) || return 1
    done
    (( (8#$(stat -c '%a' -- "$extension_catalog") & 0077) == 0 )) || return 1

    [[ "$preview_port" =~ ^[0-9]+$ ]] || return 1
    (( preview_port >= 1 && preview_port <= 65535 )) || return 1
    local app_port
    for app_port in \
        "${DASHBOARD_PORT:-3001}" "${WEBUI_PORT:-3000}" "${SEARXNG_PORT:-8888}" \
        "${PERPLEXICA_PORT:-3004}" "${WHISPER_PORT:-9000}" "${TTS_PORT:-8880}" \
        "${N8N_PORT:-5678}" "${QDRANT_PORT:-6333}" "${EMBEDDINGS_PORT:-8090}" \
        "${LITELLM_PORT:-4000}" "${OLLAMA_PORT:-11434}" "${SHIELD_PORT:-8085}" \
        "${TOKEN_SPY_PORT:-3005}" "${APE_PORT:-7890}" "${HERMES_PROXY_PORT:-9120}"; do
        [[ "$app_port" =~ ^[0-9]+$ ]] || return 1
        (( app_port >= 1 && app_port <= 65535 )) || return 1
        (( preview_port != app_port )) || return 1
    done

    local stage extension_probe
    stage="$(mktemp -d)" || return 1
    python3 - "$plugin_root/host/pixel-ingress.service" "$stage/pixel-ingress.service" "$owner" "$token_file" "$runtime_token_file" <<'PY'
import pathlib, sys

source, target, owner, token_source, token_file = sys.argv[1:6]
text = pathlib.Path(source).read_text(encoding="utf-8")
if any(c in owner + token_source + token_file for c in "\n\r\0"):
    raise SystemExit("unsafe systemd substitution")
text = (text.replace("__PIXEL_SERVICE_USER__", owner)
            .replace("__PIXEL_GATEWAY_TOKEN_SOURCE__", token_source)
            .replace("__PIXEL_GATEWAY_TOKEN_FILE__", token_file))
if "__PIXEL_" in text:
    raise SystemExit("unresolved Pixel systemd placeholder")
pathlib.Path(target).write_text(text, encoding="utf-8", newline="\n")
PY
    cat > "$stage/pixel-agent.env" <<EOF
PIXEL_INGRESS_SOCKET=/run/ods-pixel/pixel-ingress.sock
PIXEL_INGRESS_GID=${PIXEL_INGRESS_GID:?}
PIXEL_GATEWAY_TOKEN_FILE=$runtime_token_file
PIXEL_GATEWAY_PORT=18789
PIXEL_STATUS_FILE=/run/ods-pixel/ods-status.json
PIXEL_STATUS_INTERVAL_MS=30000
PIXEL_ODS_VERSION=$ods_version
PIXEL_ODS_DASHBOARD_PORT=${DASHBOARD_PORT:-3001}
PIXEL_ODS_WEBUI_PORT=${WEBUI_PORT:-3000}
PIXEL_ODS_SEARXNG_PORT=${SEARXNG_PORT:-8888}
PIXEL_ODS_PERPLEXICA_PORT=${PERPLEXICA_PORT:-3004}
PIXEL_ODS_WHISPER_PORT=${WHISPER_PORT:-9000}
PIXEL_ODS_TTS_PORT=${TTS_PORT:-8880}
PIXEL_ODS_N8N_PORT=${N8N_PORT:-5678}
PIXEL_ODS_QDRANT_PORT=${QDRANT_PORT:-6333}
PIXEL_ODS_EMBEDDINGS_PORT=${EMBEDDINGS_PORT:-8090}
PIXEL_ODS_LITELLM_PORT=${LITELLM_PORT:-4000}
PIXEL_ODS_LLAMA_PORT=${OLLAMA_PORT:-11434}
PIXEL_ODS_PRIVACY_SHIELD_PORT=${SHIELD_PORT:-8085}
PIXEL_ODS_TOKEN_SPY_PORT=${TOKEN_SPY_PORT:-3005}
PIXEL_ODS_APE_PORT=${APE_PORT:-7890}
PIXEL_ODS_HERMES_PROXY_PORT=${HERMES_PROXY_PORT:-9120}
EOF
    chmod 0640 "$stage/pixel-agent.env"
    ods_sudo install -d -m 0755 /usr/local/libexec /etc/ods
    ods_sudo test -d /opt/pixel-ops-broker
    ods_sudo test ! -L /opt/pixel-ops-broker
    ods_sudo install -o root -g root -m 0755 "$extension_helper" "$installed_extension_helper"
    ods_sudo install -o root -g pixel-ops -m 0640 "$extension_catalog" "$installed_extension_catalog"
    ods_sudo install -o root -g root -m 0755 "$extension_manager" "$installed_extension_manager"
    ods_sudo install -o root -g root -m 0755 "$extension_manager" "$system_extension_manager"
    ods_sudo install -o root -g root -m 0755 "$artifact_promoter" "$system_artifact_promoter"
    ods_sudo install -o root -g root -m 0755 "$workspace_preview" "$system_workspace_preview"
    ods_sudo install -o root -g root -m 0755 "$system_observer" "$installed_system_observer"
    if ods_sudo test -e "$operations_service_dropin_dir" \
        || ods_sudo test -L "$operations_service_dropin_dir"; then
        ods_sudo test -d "$operations_service_dropin_dir" || return 1
        ods_sudo test ! -L "$operations_service_dropin_dir" || return 1
    else
        ods_sudo install -d -o root -g root -m 0755 "$operations_service_dropin_dir" || return 1
    fi
    [[ "$(ods_sudo stat -c '%U:%G:%a' -- "$operations_service_dropin_dir")" == "root:root:755" ]] \
        || return 1
    ods_sudo install -o root -g root -m 0644 "$operations_service_dropin" \
        "$installed_operations_service_dropin" || return 1
    ods_sudo cmp -s -- "$extension_helper" "$installed_extension_helper"
    ods_sudo cmp -s -- "$extension_catalog" "$installed_extension_catalog"
    ods_sudo cmp -s -- "$extension_manager" "$installed_extension_manager"
    ods_sudo cmp -s -- "$extension_manager" "$system_extension_manager"
    ods_sudo cmp -s -- "$artifact_promoter" "$system_artifact_promoter"
    ods_sudo cmp -s -- "$workspace_preview" "$system_workspace_preview"
    ods_sudo cmp -s -- "$system_observer" "$installed_system_observer"
    ods_sudo cmp -s -- "$operations_service_dropin" "$installed_operations_service_dropin" \
        || return 1
    extension_probe="$(ods_sudo -u pixel-ops-broker /usr/bin/python3 \
        "$installed_extension_helper" "$installed_extension_catalog" all)" || return 1
    jq -e '.schemaVersion == 1 and .kind == "ods-pixel-extension-search"
        and .query == "all" and (.totalCatalog | type == "number") and .totalCatalog > 0
        and (.matches | type == "array") and (.matches | length) <= 10
        and (.boundary | type == "string")' <<<"$extension_probe" >/dev/null || return 1
    ods_sudo install -o root -g root -m 0755 "$plugin_root/host/pixel_ingress.mjs" /usr/local/libexec/ods-pixel-ingress.mjs
    ods_sudo install -o root -g ods-pixel -m 0640 "$stage/pixel-agent.env" /etc/ods/pixel-agent.env
    ods_sudo install -o root -g root -m 0644 "$stage/pixel-ingress.service" /etc/systemd/system/pixel-ingress.service
    ods_sudo install -o root -g root -m 0644 "$rendered_extension_manager_unit" \
        /etc/systemd/system/pixel-extension-manager.service
    ods_sudo cmp -s -- "$rendered_extension_manager_unit" \
        /etc/systemd/system/pixel-extension-manager.service
    ods_sudo install -o root -g root -m 0644 "$rendered_artifact_promoter_unit" \
        /etc/systemd/system/pixel-artifact-promoter.service
    ods_sudo cmp -s -- "$rendered_artifact_promoter_unit" \
        /etc/systemd/system/pixel-artifact-promoter.service
    ods_sudo install -o root -g root -m 0644 "$rendered_workspace_preview_unit" \
        /etc/systemd/system/pixel-workspace-preview.service
    ods_sudo cmp -s -- "$rendered_workspace_preview_unit" \
        /etc/systemd/system/pixel-workspace-preview.service
    rm -f -- "$stage/pixel-agent.env" "$stage/pixel-ingress.service"
    rmdir -- "$stage"
    ods_sudo systemctl daemon-reload || return 1
    ods_sudo systemctl restart pixel-ops-broker.service || return 1
    local operations_address_families operations_capabilities operations_device_policy
    local operations_private_devices
    operations_address_families="$(ods_sudo systemctl show pixel-ops-broker.service \
        --property=RestrictAddressFamilies --value)" || return 1
    operations_capabilities="$(ods_sudo systemctl show pixel-ops-broker.service \
        --property=CapabilityBoundingSet --value)" || return 1
    operations_private_devices="$(ods_sudo systemctl show pixel-ops-broker.service \
        --property=PrivateDevices --value)" || return 1
    operations_device_policy="$(ods_sudo systemctl show pixel-ops-broker.service \
        --property=DevicePolicy --value)" || return 1
    python3 - "$operations_address_families" "$operations_capabilities" \
        "$operations_private_devices" "$operations_device_policy" <<'PY'
import sys

if set(sys.argv[1].split()) != {"AF_UNIX", "AF_INET", "AF_INET6", "AF_NETLINK", "AF_VSOCK"}:
    raise SystemExit("unexpected Pixel Operations address-family boundary")
if sys.argv[2]:
    raise SystemExit("Pixel Operations capability boundary is not empty")
if sys.argv[3] != "no" or sys.argv[4] != "closed":
    raise SystemExit("Pixel Operations device boundary is not closed")
PY
    ods_sudo systemctl enable openclaw-gateway.service pixel-ingress.service \
        pixel-extension-manager.service pixel-artifact-promoter.service \
        pixel-workspace-preview.service || return 1
    ods_sudo systemctl start openclaw-gateway.service || return 1
    ods_sudo systemctl restart pixel-extension-manager.service || return 1
    ods_sudo systemctl restart pixel-artifact-promoter.service || return 1
    ods_sudo systemctl restart pixel-workspace-preview.service || return 1
    # `enable --now` does not refresh an already-running ingress after its
    # reviewed program or environment changes. Restart only the ingress here;
    # the Pixel gateway was already verified above and need not be disturbed.
    ods_sudo systemctl restart pixel-ingress.service || return 1
    ods_sudo systemctl is-active --quiet openclaw-gateway.service pixel-ingress.service \
        pixel-extension-manager.service pixel-artifact-promoter.service \
        pixel-workspace-preview.service || return 1
    local extension_id
    extension_id="$(jq -er '.matches[0].id | select(type == "string")' \
        <<<"$extension_probe")" || return 1
    [[ "$extension_id" =~ ^[a-z0-9][a-z0-9._-]{0,63}$ && "$extension_id" != *. ]] || return 1
    _ods_pixel_wait_extension_manager_probe "$installed_extension_manager" "$extension_id" || return 1
    _ods_pixel_wait_artifact_promoter_probe "$owner" "$home" "$system_artifact_promoter" || return 1
    _ods_pixel_wait_workspace_preview_probe "$owner" "$home" "$system_workspace_preview" \
        "$preview_port"
}

_ods_pixel_wait_ingress() {
    local owner="$1" home="$2" attempts="${3:-60}" delay="${4:-1}" response
    [[ "$attempts" =~ ^[0-9]+$ && "$attempts" -ge 1 && "$attempts" -le 300 ]] || return 1
    [[ "$delay" =~ ^[0-9]+$ && "$delay" -le 5 ]] || return 1
    local attempt
    for ((attempt = 1; attempt <= attempts; attempt++)); do
        if response="$(ods_pixel_run_as_owner "$owner" "$home" curl --fail --silent --show-error --max-time 10 \
            --unix-socket /run/ods-pixel/pixel-ingress.sock http://localhost/health 2>/dev/null)" \
            && jq -e '.status == "ok"' <<<"$response" >/dev/null 2>&1; then
            return 0
        fi
        if (( attempt < attempts && delay > 0 )); then
            sleep "$delay"
        fi
    done
    return 1
}

_ods_pixel_restart_ingress_and_verify() {
    local owner="$1" home="$2" answers="$3"
    local previous_pid current_pid unit_user restart_policy owner_uid process_uid attempt
    [[ "$answers" == /* && -f "$answers" && ! -L "$answers" ]] || return 1
    if ! systemctl is-active --quiet pixel-ingress.service; then
        # First installation reconciles the model before it installs ingress.
        return 0
    fi
    previous_pid="$(systemctl show pixel-ingress.service -p MainPID --value 2>/dev/null || true)"
    [[ "$previous_pid" =~ ^[1-9][0-9]*$ ]] || return 1
    if ods_sudo_available; then
        ods_sudo systemctl restart pixel-ingress.service || return 1
    else
        unit_user="$(systemctl show pixel-ingress.service -p User --value 2>/dev/null || true)"
        restart_policy="$(systemctl show pixel-ingress.service -p Restart --value 2>/dev/null || true)"
        owner_uid="$(id -u "$owner" 2>/dev/null || true)"
        process_uid="$(awk '/^Uid:/ { print $2; exit }' "/proc/${previous_pid}/status" 2>/dev/null || true)"
        [[ "$(id -un)" == "$owner" && "$unit_user" == "$owner" \
            && "$restart_policy" == "on-failure" && "$owner_uid" =~ ^[0-9]+$ \
            && "$process_uid" == "$owner_uid" ]] || return 1
        current_pid="$(systemctl show pixel-ingress.service -p MainPID --value 2>/dev/null || true)"
        [[ "$current_pid" == "$previous_pid" ]] || return 1
        kill -HUP "$previous_pid" || return 1
    fi
    current_pid=""
    for attempt in {1..60}; do
        current_pid="$(systemctl show pixel-ingress.service -p MainPID --value 2>/dev/null || true)"
        if [[ "$current_pid" =~ ^[1-9][0-9]*$ && "$current_pid" != "$previous_pid" ]] \
            && systemctl is-active --quiet pixel-ingress.service; then
            break
        fi
        sleep 1
    done
    [[ "$current_pid" =~ ^[1-9][0-9]*$ && "$current_pid" != "$previous_pid" ]] || return 1
    _ods_pixel_wait_ingress "$owner" "$home" || return 1
    for attempt in {1..30}; do
        if ods_pixel_run_as_owner "$owner" "$home" python3 - "$answers" \
            /run/ods-pixel/ods-status.json <<'PY'
import json, os, pathlib, re, stat, sys

answers_path = pathlib.Path(sys.argv[1])
status_path = pathlib.Path(sys.argv[2])
for path, private in ((answers_path, True), (status_path, False)):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.getuid() or info.st_size > 2 * 1024 * 1024
            or info.st_mode & 0o022 or (private and info.st_mode & 0o077)):
        raise SystemExit(1)
answers = json.loads(answers_path.read_text(encoding="utf-8"))
status = json.loads(status_path.read_text(encoding="utf-8"))
provider = answers.get("modelProvider")
model_id = answers.get("modelId")
model_name = answers.get("modelName")
if provider == "ods-local" and model_name == f"ODS Local {model_id}":
    concrete = model_id
elif provider == "ods-gateway" and model_id in {"default", "ods/current"}:
    label = "Current" if model_id == "ods/current" else "Default"
    match = re.fullmatch(
        rf"ODS {label} \(([A-Za-z0-9][A-Za-z0-9._+:/ @(),=-]{{0,255}})\)",
        model_name if isinstance(model_name, str) else "",
    )
    concrete = match.group(1) if match else None
else:
    concrete = None
context = answers.get("modelContextWindow")
runtime = status.get("runtime") if isinstance(status, dict) else None
if (not isinstance(concrete, str) or not 1 <= len(concrete) <= 256
        or type(context) is not int or not 4096 <= context <= 10_000_000
        or runtime != {"model": concrete, "context_length": context}):
    raise SystemExit(1)
PY
        then
            return 0
        fi
        (( attempt < 30 )) && sleep 1
    done
    return 1
}

ods_pixel_install_default_agent() {
    [[ "${ENABLE_PIXEL_RUNTIME:-false}" == true ]] || return 0
    local owner home source_root pixel_root plugin_root answers operations_policy extension_catalog extension_manager_unit artifact_promoter_unit workspace_preview_unit openclaw_bin plugin_digest contract_sha256 runtime_budget_status gateway_alias pixel_log
    local candidate_runtime_status reuse_active=false same_verified_source=false same_source_resume=false
    owner="${PIXEL_SERVICE_USER:-$(ods_pixel_install_owner)}" || return 1
    home="$(ods_pixel_owner_home "$owner")" || return 1
    _ods_pixel_assert_managed_state "$owner" "$home" || return 1
    pixel_log="$(_ods_pixel_prepare_attempt_log "$owner" "$home" "$INSTALL_DIR/logs/pixel-install.log")" || {
        ai_bad "Could not create Pixel's owner-private persistent install log."
        return 1
    }
    source_root="${INSTALL_DIR:?}/data/pixel/source-${PIXEL_SOURCE_REF:?}"
    pixel_root="$(_ods_pixel_source_checkout "$owner" "$home" "$source_root")" || {
        ai_bad "Pixel source checkout is absent, changed, or not at the configured exact commit."
        return 1
    }
    plugin_root="${INSTALL_DIR:?}/extensions/services/pixel-agent"
    [[ -f "$plugin_root/plugin/openclaw.plugin.json" \
        && -f "$plugin_root/host/pixel_ingress.mjs" \
        && -f "$plugin_root/host/extension_search.py" \
        && -f "$plugin_root/host/extension_manager.py" \
        && -f "$plugin_root/host/pixel-extension-manager.service" \
        && -f "$plugin_root/host/artifact_promoter.py" \
        && -f "$plugin_root/host/pixel-artifact-promoter.service" \
        && -f "$plugin_root/host/workspace_preview.py" \
        && -f "$plugin_root/host/pixel-workspace-preview.service" \
        && -f "$plugin_root/host/system_observe.py" \
        && -f "$plugin_root/host/pixel-ops-broker-ods.conf" \
        && -f "$plugin_root/host/cancellable-exec.sh" \
        && -f "$plugin_root/host/noninteractive-sudo.sh" ]] || return 1
    if ! _ods_pixel_secure_plugin_tree "$owner" "$home" "$plugin_root/plugin"; then
        ai_bad "The ODS Pixel plugin path is not a safe owner-controlled code tree."
        return 1
    fi
    if ! _ods_pixel_install_exec_control "$owner" "$home" \
        "$plugin_root/host/cancellable-exec.sh" "$plugin_root/host/noninteractive-sudo.sh"; then
        ai_bad "Could not install Pixel's owner-private cancellable execution control."
        return 1
    fi
    gateway_alias="$(_ods_pixel_gateway_model_alias)" || {
        ai_bad "Pixel received an unsupported ODS model Switchboard mode."
        return 1
    }
    ai "Starting the ODS model gateway, control API, and search prerequisites for Pixel review..."
    # The scoped extension manager validates its contract against dashboard-api
    # while Pixel is installed below. Start the API from this exact Compose
    # project before that probe. Otherwise a fresh install has no endpoint, and
    # a migration can accidentally probe a stale related install on the same
    # port. Treat Compose startup failure as authoritative instead of allowing
    # later endpoint checks to accept unrelated containers.
    if ! $DOCKER_COMPOSE_CMD "${COMPOSE_FLAGS_ARR[@]}" up -d --no-build --pull never \
        litellm searxng dashboard-api >>"$LOG_FILE" 2>&1; then
        ai_bad "Could not start Pixel's exact ODS prerequisite services. See $LOG_FILE."
        return 1
    fi
    _ods_pixel_wait_model_gateway "ODS model gateway" "${LITELLM_PORT:-4000}" \
        "${LITELLM_KEY:-}" "$gateway_alias" 180
    _ods_pixel_wait_http "ODS local search" "http://127.0.0.1:${SEARXNG_PORT:-8888}/search?q=pixel-preflight&format=json" 90 '.results | type == "array"'
    _ods_pixel_wait_http "ODS control API" \
        "http://127.0.0.1:${DASHBOARD_API_PORT:-3002}/health" 90

    ai "Bootstrapping the exact Pixel source and pinned runtime..."
    if ! declare -f ods_linux_node_tools_available >/dev/null 2>&1 \
        || ! ods_linux_node_tools_available; then
        ai_bad "Pixel requires Linux Node.js 20+ and Linux npm; Windows-mounted WSL tools are not accepted."
        return 1
    fi
    if ! ods_pixel_run_as_owner "$owner" "$home" "$pixel_root/pixel" bootstrap --apply >>"$pixel_log" 2>&1; then
        ai_bad "Pixel bootstrap failed. See $pixel_log for the exact Pixel error."
        return 1
    fi
    openclaw_bin="$(_ods_pixel_openclaw_bin "$owner" "$home")"
    [[ "$openclaw_bin" == /* && -x "$openclaw_bin" ]] || return 1
    plugin_digest="$(ods_pixel_run_as_owner "$owner" "$home" "$pixel_root/pixel" extension-hash "$plugin_root/plugin")"
    [[ "$plugin_digest" =~ ^[0-9a-f]{64}$ ]] || return 1

    answers="$INSTALL_DIR/data/pixel/onboarding.json"
    operations_policy="$INSTALL_DIR/data/pixel/operations-policy.json"
    extension_catalog="$INSTALL_DIR/data/pixel/extension-catalog.json"
    extension_manager_unit="$INSTALL_DIR/data/pixel/extension-manager.service"
    artifact_promoter_unit="$INSTALL_DIR/data/pixel/artifact-promoter.service"
    workspace_preview_unit="$INSTALL_DIR/data/pixel/workspace-preview.service"
    if ! _ods_pixel_write_extension_catalog "$owner" "$home" "$extension_catalog"; then
        ai_bad "Could not write Pixel's secret-free ODS extension catalog."
        return 1
    fi
    if ! _ods_pixel_write_operations_policy "$owner" "$home" "$operations_policy"; then
        ai_bad "Could not write the owner-private ODS Pixel Operations policy."
        return 1
    fi
    if ! _ods_pixel_write_extension_manager_unit "$owner" "$home" "$extension_manager_unit"; then
        ai_bad "Could not write the owner-private ODS Pixel extension manager service."
        return 1
    fi
    if ! _ods_pixel_write_artifact_promoter_unit "$owner" "$home" "$artifact_promoter_unit"; then
        ai_bad "Could not write the owner-private ODS Pixel artifact promoter service."
        return 1
    fi
    if ! _ods_pixel_write_workspace_preview_unit "$owner" "$home" "$workspace_preview_unit" \
        "${PIXEL_PREVIEW_PORT:-9437}"; then
        ai_bad "Could not write the owner-private ODS Pixel workspace preview service."
        return 1
    fi
    if ! _ods_pixel_write_onboarding "$owner" "$home" "$answers" "$openclaw_bin" "$plugin_root/plugin" "$plugin_digest"; then
        ai_bad "Could not write the ODS-managed Pixel onboarding contract."
        return 1
    fi
    contract_sha256="$(_ods_pixel_contract_sha256 "$owner" "$home" "$answers")" || {
        ai_bad "Could not hash the ODS-managed Pixel onboarding contract."
        return 1
    }
    [[ "$contract_sha256" =~ ^[0-9a-f]{64}$ ]] || return 1
    if _ods_pixel_managed_contract_matches "$owner" "$home" "$contract_sha256"; then
        reuse_active=true
    fi
    if _ods_pixel_verified_source_matches "$owner" "$home"; then
        same_verified_source=true
    fi
    _ods_pixel_mark_installing "$owner" "$home" || return 1
    if ! _ods_pixel_enable_chat_endpoint "$owner" "$home"; then
        ai_bad "Could not enable Pixel's loopback chat endpoint."
        return 1
    fi
    if [[ "$reuse_active" == true ]]; then
        ai "The exact ODS-managed Pixel contract is already active; verifying it without reapplying the same release..."
        if ! ods_pixel_run_as_owner "$owner" "$home" "$pixel_root/pixel" ops-broker --confirm >>"$pixel_log" 2>&1; then
            ai_bad "Pixel could not install and verify the isolated Operations Broker. See $pixel_log."
            return 1
        fi
        if ! _ods_pixel_harden_operations_state_profiles; then
            ai_bad "Pixel's Operations Broker service profiles could not be hardened safely."
            return 1
        fi
        if ! _ods_pixel_verify_operations_policy_custody "$owner" "$home" "$operations_policy"; then
            ai_bad "Pixel's root-custodied Operations policy does not match the ODS-managed policy."
            return 1
        fi
        if ! ods_pixel_run_as_owner "$owner" "$home" "$pixel_root/pixel" verify >>"$pixel_log" 2>&1; then
            ai_bad "The existing ODS-managed Pixel contract failed exact-source verification. See $pixel_log."
            return 1
        fi
    else
        if ! ods_pixel_run_as_owner "$owner" "$home" "$pixel_root/pixel" configure --answers "$answers" --force >>"$pixel_log" 2>&1; then
            ai_bad "Pixel configure failed. See $pixel_log for the exact Pixel error."
            return 1
        fi
        if ! ods_pixel_run_as_owner "$owner" "$home" "$pixel_root/pixel" plan >>"$pixel_log" 2>&1; then
            ai_bad "Pixel plan failed. See $pixel_log for the exact Pixel error."
            return 1
        fi
        if ! ods_pixel_run_as_owner "$owner" "$home" "$pixel_root/pixel" ops-broker --confirm >>"$pixel_log" 2>&1; then
            ai_bad "Pixel could not install and verify the isolated Operations Broker. See $pixel_log."
            return 1
        fi
        if ! _ods_pixel_harden_operations_state_profiles; then
            ai_bad "Pixel's Operations Broker service profiles could not be hardened safely."
            return 1
        fi
        if ! _ods_pixel_verify_operations_policy_custody "$owner" "$home" "$operations_policy"; then
            ai_bad "Pixel's root-custodied Operations policy does not match the ODS-managed policy."
            return 1
        fi
        if [[ "$same_verified_source" == true ]]; then
            if _ods_pixel_candidate_config_matches_live "$owner" "$home" "$pixel_root/dist/openclaw.json"; then
                same_source_resume=true
                ai "The exact Pixel release and runtime configuration are unchanged; refreshing the verified ODS extension without reapplying the release..."
            else
                # A run from an older installer may have atomically written the
                # deterministic ODS overlay before it could bind the updated
                # marker. Recreate that overlay on the reviewed candidate and
                # require whole-document equality before accepting this narrow
                # recovery path. Unrelated live changes still fail closed into
                # the transactional model-reconciliation path below.
                candidate_runtime_status="$(_ods_pixel_apply_runtime_budget "$owner" "$home" \
                    "$pixel_root/dist/openclaw.json" "$openclaw_bin")" || {
                    ai_bad "Could not validate the exact-source Pixel runtime candidate for safe recovery."
                    return 1
                }
                case "$candidate_runtime_status" in
                    changed|unchanged) ;;
                    *)
                        ai_bad "Pixel returned an invalid exact-source runtime recovery result."
                        return 1
                        ;;
                esac
                if _ods_pixel_candidate_config_matches_live "$owner" "$home" "$pixel_root/dist/openclaw.json"; then
                    same_source_resume=true
                    ai "The exact Pixel release and deterministic ODS runtime policy are already active; repairing the interrupted ownership checkpoint..."
                fi
            fi
            if [[ "$same_source_resume" == true ]]; then
                # Quiesce the gateway so a concurrent tool turn cannot create
                # a sandbox between retirement and the Docker postcondition.
                if ! ods_sudo systemctl stop openclaw-gateway.service >>"$pixel_log" 2>&1; then
                    ai_bad "The ODS-managed Pixel gateway could not enter maintenance mode. See $pixel_log."
                    return 1
                fi
                if ! _ods_pixel_recreate_agent_sandbox "$owner" "$home" "$openclaw_bin" \
                    >>"$pixel_log" 2>&1; then
                    # Restore the previously configured service when cleanup
                    # fails; the installer still fails closed and does not
                    # claim that the sandbox boundary was refreshed.
                    ods_sudo systemctl start openclaw-gateway.service >>"$pixel_log" 2>&1 || true
                    ai_bad "Pixel could not retire its stale agent sandbox during recovery. See $pixel_log."
                    return 1
                fi
                if ! ods_sudo systemctl start openclaw-gateway.service >>"$pixel_log" 2>&1; then
                    ai_bad "The ODS-managed Pixel gateway could not restart after sandbox recovery. See $pixel_log."
                    return 1
                fi
                if ! _ods_pixel_wait_http "Pixel gateway" "http://127.0.0.1:18789/health" \
                    60 '.ok == true and .status == "live"'; then
                    ai_bad "The ODS-managed Pixel gateway did not become healthy after sandbox recovery. See $pixel_log."
                    return 1
                fi
                if ! ods_pixel_run_as_owner "$owner" "$home" "$pixel_root/pixel" verify \
                    >>"$pixel_log" 2>&1; then
                    ai_bad "The recovered ODS-managed Pixel contract failed verification. See $pixel_log."
                    return 1
                fi
            else
                ai "The exact Pixel release is active with an older ODS route; reconciling the reviewed model/runtime policy..."
                if ! ods_pixel_reconcile_promoted_model "$owner" "$home" \
                    "$(_ods_pixel_runtime_model_identity)" installing >>"$pixel_log" 2>&1; then
                    ai_bad "The ODS-managed Pixel model route could not be reconciled safely. See $pixel_log."
                    return 1
                fi
            fi
        elif ! {
            ods_pixel_run_as_owner "$owner" "$home" env \
                PATH="$home/.openclaw/.ods-exec-control:$PATH" \
                "$pixel_root/pixel" apply --confirm </dev/null &&
            ods_pixel_run_as_owner "$owner" "$home" "$pixel_root/pixel" verify
        } >>"$pixel_log" 2>&1; then
            ai_bad "Pixel apply or verify failed. See $pixel_log for the exact Pixel error."
            return 1
        fi
    fi
    # Record the verified Pixel release before applying the ODS-owned runtime
    # overlay. If power is lost between the atomic config update and gateway
    # verification, the next installer run can safely enter the exact-source
    # reconciliation path instead of attempting to reapply an active release.
    if ! _ods_pixel_mark_verified_installing "$owner" "$home" "$contract_sha256" "$pixel_root"; then
        ai_bad "Could not bind the verified Pixel release before managed-runtime configuration."
        return 1
    fi
    runtime_budget_status="$(_ods_pixel_apply_runtime_budget "$owner" "$home" \
        "$home/.openclaw/openclaw.json" "$openclaw_bin")" || {
        ai_bad "Could not validate and apply Pixel's ODS managed-runtime policy."
        return 1
    }
    case "$runtime_budget_status" in
        changed) ai "Applying Pixel's bounded ODS managed-runtime policy..." ;;
        unchanged) ;;
        *)
            ai_bad "Pixel returned an invalid ODS managed-runtime policy result."
            return 1
            ;;
    esac
    # The runtime overlay above replaces the live configuration atomically.
    # Bind that exact canonical file before any fallible registry or service
    # operation. If either later step is interrupted, the next installer run
    # can prove the managed contract and resume without misclassifying ODS's
    # own runtime policy as unmanaged drift.
    if ! _ods_pixel_mark_verified_installing "$owner" "$home" "$contract_sha256" "$pixel_root"; then
        ai_bad "Could not bind the verified Pixel ODS managed-runtime configuration."
        return 1
    fi
    # OpenClaw persists plugin descriptors separately from its live config.
    # Rebuild that registry after any reviewed extension/config update, then
    # restart once so the gateway loads both the exact descriptor contract and
    # the final ODS runtime policy. A plain service restart can otherwise keep
    # stale tool descriptors across same-release extension refreshes.
    if ! _ods_pixel_refresh_plugin_registry "$owner" "$home" "$openclaw_bin" "$plugin_root/plugin" \
        >>"$pixel_log" 2>&1; then
        ai_bad "Pixel could not refresh the exact ODS plugin registry. See $pixel_log."
        return 1
    fi
    if ! _ods_pixel_recreate_agent_sandbox "$owner" "$home" "$openclaw_bin" \
        >>"$pixel_log" 2>&1; then
        ai_bad "Pixel could not recreate its agent sandbox for the reviewed ODS runtime. See $pixel_log."
        return 1
    fi
    if ! _ods_pixel_restart_gateway_and_verify "$owner" "$home" "$pixel_root" \
        >>"$pixel_log" 2>&1; then
        ai_bad "Pixel could not restart and verify its gateway after the ODS runtime update. See $pixel_log."
        return 1
    fi
    # Reconfirm the exact verified contract and canonical live config after the
    # gateway has loaded them while the marker remains non-ready. If ingress
    # setup is interrupted, a rerun can verify and reuse this same release.
    if ! _ods_pixel_mark_verified_installing "$owner" "$home" "$contract_sha256" "$pixel_root"; then
        ai_bad "Could not bind the verified Pixel contract for retry-safe ingress setup."
        return 1
    fi
    if ! _ods_pixel_install_ingress "$owner" "$home" "$plugin_root" \
        "$extension_catalog" "$extension_manager_unit" "$artifact_promoter_unit" \
        "$workspace_preview_unit"; then
        ai_bad "Could not install and start the private Pixel ingress."
        return 1
    fi
    # sudo -u starts a fresh owner session with the newly assigned ods-pixel
    # supplementary group; the original installer shell may not see that group
    # until the next login.
    if ! _ods_pixel_wait_ingress "$owner" "$home"; then
        ai_bad "Pixel ingress did not pass its authenticated loopback health check."
        return 1
    fi
    if ! _ods_pixel_verify_plugin_loaded "$owner" "$home" "$openclaw_bin" "$plugin_root/plugin"; then
        ai_bad "The reviewed ODS Pixel plugin and exact tool contract are not loaded by the active gateway."
        return 1
    fi
    if ! _ods_pixel_mark_ready "$owner" "$home" "$contract_sha256" "$pixel_root"; then
        ai_bad "Could not record the verified Pixel runtime as ready."
        return 1
    fi
    ai_ok "Pixel is installed, verified, and ready on the private ODS ingress"
}
