#!/usr/bin/env bash
# Regression coverage for model-update .env writes. A hard-link oracle catches
# in-place truncation: atomic replacement must update .env without changing a
# sibling hard link to the old inode.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOOTSTRAP="$ROOT_DIR/scripts/bootstrap-upgrade.sh"
UPGRADE="$ROOT_DIR/scripts/upgrade-model.sh"
WORK_DIR="$(mktemp -d)"
REAL_MV="$(command -v mv)"
REAL_CP="$(command -v cp)"
REAL_POWERSHELL="$(command -v powershell.exe || true)"

trap 'rm -rf "$WORK_DIR"' EXIT

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

pass() {
    echo "[PASS] $*"
}

function_block() {
    local target="$1"
    local function_name="$2"
    awk -v signature="^${function_name}[(][)]" '
        $0 ~ signature { in_block=1 }
        in_block { print }
        in_block && /^}/ { exit }
    ' "$target"
}

file_mode() {
    if stat -c '%a' "$1" >/dev/null 2>&1; then
        stat -c '%a' "$1"
    else
        stat -f '%Lp' "$1"
    fi
}

is_windows_bash() {
    case "$(uname -s)" in
        MINGW*|MSYS*|CYGWIN*) return 0 ;;
        *) return 1 ;;
    esac
}

windows_acl_sddl() {
    local windows_path
    windows_path="$(cygpath -aw "$1")" || return 1
    ODS_ENV_ACL_PATH="$windows_path" \
        powershell.exe -NoLogo -NoProfile -NonInteractive -Command '
$ErrorActionPreference = [System.Management.Automation.ActionPreference]::Stop
$section = [System.Security.AccessControl.AccessControlSections]::Access
$acl = [System.IO.File]::GetAccessControl($env:ODS_ENV_ACL_PATH, $section)
$acl.GetSecurityDescriptorSddlForm($section)
' | tr -d '\r'
}

restrict_windows_acl() {
    local windows_path
    windows_path="$(cygpath -aw "$1")" || return 1
    ODS_ENV_ACL_PATH="$windows_path" \
        powershell.exe -NoLogo -NoProfile -NonInteractive -Command '
$ErrorActionPreference = [System.Management.Automation.ActionPreference]::Stop
$section = [System.Security.AccessControl.AccessControlSections]::Access
$acl = [System.IO.File]::GetAccessControl($env:ODS_ENV_ACL_PATH, $section)
$acl.SetAccessRuleProtection($true, $false)
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$rule = [System.Security.AccessControl.FileSystemAccessRule]::new($identity, [System.Security.AccessControl.FileSystemRights]::FullControl, [System.Security.AccessControl.AccessControlType]::Allow)
$acl.SetAccessRule($rule)
[System.IO.File]::SetAccessControl($env:ODS_ENV_ACL_PATH, $acl)
' >/dev/null
}

assert_no_temp() {
    local env_file="$1"
    local pattern
    for pattern in "${env_file}.tmp.*" "${env_file}.bak.*"; do
        if compgen -G "$pattern" >/dev/null; then
            fail "temporary file leaked for $env_file"
        fi
    done
}

make_env_fixture() {
    local dir="$1"
    local env_file="$dir/.env"
    local mode="${2:-600}"
    mkdir -p "$dir"
    cat > "$env_file" <<'EOF'
KEEP=unchanged
LLM_MODEL=old-model
EOF
    chmod "$mode" "$env_file"
    ln "$env_file" "$dir/.env.before"
    printf '%s' "$env_file"
}

make_capturing_mv() {
    local bin_dir="$1"
    mkdir -p "$bin_dir"
    cat > "$bin_dir/mv" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

source_path=""
for arg in "$@"; do
    case "$arg" in
        -*) ;;
        *) source_path="$arg"; break ;;
    esac
done

if stat -c '%a' "$source_path" >/dev/null 2>&1; then
    stat -c '%a' "$source_path" > "$ODS_ATOMIC_CAPTURE_MODE"
else
    stat -f '%Lp' "$source_path" > "$ODS_ATOMIC_CAPTURE_MODE"
fi

exec "$ODS_ATOMIC_REAL_MV" "$@"
EOF
    chmod +x "$bin_dir/mv"
}

make_capturing_cp() {
    local bin_dir="$1"
    mkdir -p "$bin_dir"
    cat > "$bin_dir/cp" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

target=""
for arg in "$@"; do
    target="$arg"
done

if stat -c '%a' "$target" >/dev/null 2>&1; then
    stat -c '%a' "$target" > "$ODS_ATOMIC_CAPTURE_INITIAL_MODE"
else
    stat -f '%Lp' "$target" > "$ODS_ATOMIC_CAPTURE_INITIAL_MODE"
fi

exec "$ODS_ATOMIC_REAL_CP" "$@"
EOF
    chmod +x "$bin_dir/cp"
}

make_failing_mv() {
    local bin_dir="$1"
    mkdir -p "$bin_dir"
    cat > "$bin_dir/mv" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
    chmod +x "$bin_dir/mv"
}

make_failing_windows_replace() {
    local bin_dir="$1"
    mkdir -p "$bin_dir"
    cat > "$bin_dir/powershell.exe" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

: "${ODS_ATOMIC_REAL_POWERSHELL:?}"
: "${ODS_ATOMIC_POWERSHELL_CALLS:?}"

calls=0
if [[ -f "$ODS_ATOMIC_POWERSHELL_CALLS" ]]; then
    calls="$(cat "$ODS_ATOMIC_POWERSHELL_CALLS")"
fi
calls=$((calls + 1))
printf '%s' "$calls" > "$ODS_ATOMIC_POWERSHELL_CALLS"

# The first two PowerShell calls copy the source DACL to the empty temp and
# backup. Fail the third call, which is the native File.Replace operation.
if [[ "$calls" -le 2 ]]; then
    exec "$ODS_ATOMIC_REAL_POWERSHELL" "$@"
fi
exit 1
EOF
    chmod +x "$bin_dir/powershell.exe"
}

make_failing_awk() {
    local bin_dir="$1"
    mkdir -p "$bin_dir"
    cat > "$bin_dir/awk" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
    chmod +x "$bin_dir/awk"
}

[[ -f "$BOOTSTRAP" ]] || fail "missing $BOOTSTRAP"
[[ -f "$UPGRADE" ]] || fail "missing $UPGRADE"

# The platform helpers are intentionally duplicated because these scripts run
# independently. Keep them byte-for-byte equivalent and execute their exact
# shared implementation rather than reimplementing it in this harness.
bootstrap_resolve_env_file="$(function_block "$BOOTSTRAP" resolve_env_file)"
upgrade_resolve_env_file="$(function_block "$UPGRADE" resolve_env_file)"
bootstrap_copy_env_permissions="$(function_block "$BOOTSTRAP" copy_env_permissions)"
upgrade_copy_env_permissions="$(function_block "$UPGRADE" copy_env_permissions)"
bootstrap_replace_env_file="$(function_block "$BOOTSTRAP" replace_env_file)"
upgrade_replace_env_file="$(function_block "$UPGRADE" replace_env_file)"
[[ "$bootstrap_resolve_env_file" == "$upgrade_resolve_env_file" ]] \
    || fail "model-upgrade scripts use different symlink-resolution helpers"
[[ "$bootstrap_copy_env_permissions" == "$upgrade_copy_env_permissions" ]] \
    || fail "model-upgrade scripts use different temp-permission helpers"
[[ "$bootstrap_replace_env_file" == "$upgrade_replace_env_file" ]] \
    || fail "model-upgrade scripts use different replacement helpers"
eval "$bootstrap_resolve_env_file"
eval "$bootstrap_copy_env_permissions"
eval "$bootstrap_replace_env_file"
eval "$(function_block "$BOOTSTRAP" write_env_value)"
eval "$(function_block "$UPGRADE" update_env_value)"

# bootstrap-upgrade.sh: successful replacement preserves the source mode,
# replaces the live pathname, and leaves the old hard-linked inode untouched.
bootstrap_dir="$WORK_DIR/bootstrap-success"
bootstrap_env="$(make_env_fixture "$bootstrap_dir" 640)"
bootstrap_mode_before="$(file_mode "$bootstrap_env")"
bootstrap_acl_before=""
if is_windows_bash; then
    [[ -n "$REAL_POWERSHELL" ]] || fail "powershell.exe is required for Windows ACL coverage"
    restrict_windows_acl "$bootstrap_env" || fail "could not restrict bootstrap fixture ACL"
    bootstrap_acl_before="$(windows_acl_sddl "$bootstrap_env")" \
        || fail "could not read bootstrap fixture ACL"
    protected_tmp="$(umask 077; mktemp "${bootstrap_env}.tmp.XXXXXX")"
    copy_env_permissions "$bootstrap_env" "$protected_tmp" \
        || fail "could not protect bootstrap temporary file"
    [[ "$(windows_acl_sddl "$protected_tmp")" == "$bootstrap_acl_before" ]] \
        || fail "bootstrap temporary file did not inherit the .env ACL before writing"
    printf 'TEMP_SECRET=kept-private\n' > "$protected_tmp"
    [[ "$(windows_acl_sddl "$protected_tmp")" == "$bootstrap_acl_before" ]] \
        || fail "bootstrap temporary file lost its ACL after writing"
    rm -f "$protected_tmp"
    ENV_FILE="$bootstrap_env" write_env_value LLM_MODEL new-bootstrap-model
else
    capture_dir="$WORK_DIR/capture-bin"
    capture_mode="$WORK_DIR/bootstrap-temp-mode"
    capture_initial_mode="$WORK_DIR/bootstrap-initial-temp-mode"
    make_capturing_mv "$capture_dir"
    make_capturing_cp "$capture_dir"
    ENV_FILE="$bootstrap_env" ODS_ATOMIC_CAPTURE_MODE="$capture_mode" \
        ODS_ATOMIC_CAPTURE_INITIAL_MODE="$capture_initial_mode" \
        ODS_ATOMIC_REAL_MV="$REAL_MV" ODS_ATOMIC_REAL_CP="$REAL_CP" \
        PATH="$capture_dir:$PATH" \
        write_env_value LLM_MODEL new-bootstrap-model
fi

grep -qx 'LLM_MODEL=new-bootstrap-model' "$bootstrap_env" \
    || fail "bootstrap writer did not update LLM_MODEL"
grep -qx 'LLM_MODEL=old-model' "$bootstrap_dir/.env.before" \
    || fail "bootstrap writer modified the pre-replacement inode"
[[ "$(file_mode "$bootstrap_env")" == "$bootstrap_mode_before" ]] \
    || fail "bootstrap writer did not preserve .env mode"
if is_windows_bash; then
    [[ "$(windows_acl_sddl "$bootstrap_env")" == "$bootstrap_acl_before" ]] \
        || fail "bootstrap writer did not preserve the .env ACL"
else
    [[ "$bootstrap_mode_before" == "640" ]] \
        || fail "bootstrap fixture did not use a non-default mode"
    [[ "$(cat "$capture_initial_mode")" == "600" ]] \
        || fail "bootstrap temporary file was not owner-only before metadata copy"
    [[ "$(cat "$capture_mode")" == "$bootstrap_mode_before" ]] \
        || fail "bootstrap temporary file was not protected before mv"
fi
assert_no_temp "$bootstrap_env"
pass "bootstrap writer replaces .env atomically and preserves permissions"

# upgrade-model.sh: the same path must replace existing keys and append absent
# ones atomically before the runtime start is attempted.
upgrade_dir="$WORK_DIR/upgrade-success"
upgrade_env="$(make_env_fixture "$upgrade_dir" 640)"
upgrade_mode_before="$(file_mode "$upgrade_env")"
upgrade_acl_before=""
if is_windows_bash; then
    restrict_windows_acl "$upgrade_env" || fail "could not restrict upgrade fixture ACL"
    upgrade_acl_before="$(windows_acl_sddl "$upgrade_env")" \
        || fail "could not read upgrade fixture ACL"
fi
update_env_value "$upgrade_env" LLM_MODEL new-upgrade-model
grep -qx 'LLM_MODEL=new-upgrade-model' "$upgrade_env" \
    || fail "upgrade writer did not replace LLM_MODEL"
grep -qx 'LLM_MODEL=old-model' "$upgrade_dir/.env.before" \
    || fail "upgrade writer modified the pre-replacement inode"
update_env_value "$upgrade_env" EXTRA_MODEL extra-model
grep -qx 'EXTRA_MODEL=extra-model' "$upgrade_env" \
    || fail "upgrade writer did not append a missing key"
[[ "$(file_mode "$upgrade_env")" == "$upgrade_mode_before" ]] \
    || fail "upgrade writer did not preserve .env mode"
if is_windows_bash; then
    [[ "$(windows_acl_sddl "$upgrade_env")" == "$upgrade_acl_before" ]] \
        || fail "upgrade writer did not preserve the .env ACL"
else
    [[ "$upgrade_mode_before" == "640" ]] \
        || fail "upgrade fixture did not use a non-default mode"
fi
assert_no_temp "$upgrade_env"
pass "upgrade writer atomically replaces and appends .env values"

# The old in-place writes followed .env links. Both new writers must update
# the physical referent while retaining the link itself.
symlink_dir="$WORK_DIR/symlinked-env"
mkdir -p "$symlink_dir"
bootstrap_target="$symlink_dir/bootstrap-target.env"
cat > "$bootstrap_target" <<'EOF'
KEEP=unchanged
LLM_MODEL=old-model
EOF
chmod 640 "$bootstrap_target"
bootstrap_link="$symlink_dir/.env"
if ln -s "$(basename "$bootstrap_target")" "$bootstrap_link" 2>/dev/null && [[ -L "$bootstrap_link" ]]; then
    ENV_FILE="$bootstrap_link" write_env_value LLM_MODEL symlink-bootstrap-model
    [[ -L "$bootstrap_link" ]] || fail "bootstrap writer replaced the .env symlink"
    grep -qx 'LLM_MODEL=symlink-bootstrap-model' "$bootstrap_target" \
        || fail "bootstrap writer did not update the symlink target"
    assert_no_temp "$bootstrap_target"

    upgrade_target="$symlink_dir/upgrade-target.env"
    cat > "$upgrade_target" <<'EOF'
KEEP=unchanged
LLM_MODEL=old-model
EOF
    chmod 640 "$upgrade_target"
    upgrade_link="$symlink_dir/upgrade.env"
    ln -s "$(basename "$upgrade_target")" "$upgrade_link"
    [[ -L "$upgrade_link" ]] || fail "could not create upgrade .env symlink"
    update_env_value "$upgrade_link" LLM_MODEL symlink-upgrade-model
    [[ -L "$upgrade_link" ]] || fail "upgrade writer replaced the .env symlink"
    grep -qx 'LLM_MODEL=symlink-upgrade-model' "$upgrade_target" \
        || fail "upgrade writer did not update the symlink target"
    assert_no_temp "$upgrade_target"

    cycle_link="$symlink_dir/cycle.env"
    ln -s "$(basename "$cycle_link")" "$cycle_link"
    if resolve_env_file "$cycle_link" >/dev/null; then
        fail "symlink resolution accepted a cycle"
    fi
    pass "model-update writers preserve .env symlinks and reject cycles"
else
    rm -f "$bootstrap_link"
    echo "[SKIP] symlink regression coverage (symlinks unavailable)"
fi

# A failed replacement must retain the live file and clean up every generated
# temp or backup. On Windows, fail the native File.Replace call after both DACL
# copies have succeeded; elsewhere, fail mv directly.
failure_dir="$WORK_DIR/replacement-failure"
failure_env="$(make_env_fixture "$failure_dir")"
failure_before="$(cat "$failure_env")"
failure_mode="$(file_mode "$failure_env")"
failure_bin="$WORK_DIR/failing-replacement-bin"
if is_windows_bash; then
    failure_calls="$WORK_DIR/failing-replacement-calls"
    make_failing_windows_replace "$failure_bin"
    ENV_FILE="$failure_env" ODS_ATOMIC_REAL_POWERSHELL="$REAL_POWERSHELL" \
        ODS_ATOMIC_POWERSHELL_CALLS="$failure_calls" PATH="$failure_bin:$PATH" \
        write_env_value LLM_MODEL should-not-persist && fail "bootstrap writer reported success after replacement failure"
else
    make_failing_mv "$failure_bin"
    ENV_FILE="$failure_env" PATH="$failure_bin:$PATH" \
        write_env_value LLM_MODEL should-not-persist && fail "bootstrap writer reported success after replacement failure"
fi
[[ "$(cat "$failure_env")" == "$failure_before" ]] \
    || fail "bootstrap writer modified .env after replacement failure"
[[ "$(file_mode "$failure_env")" == "$failure_mode" ]] \
    || fail "bootstrap writer changed .env mode after replacement failure"
assert_no_temp "$failure_env"
pass "bootstrap writer leaves .env intact when atomic replacement fails"

# A failed render must likewise leave the original file untouched and remove
# the temporary file before returning an error.
awk_failure_dir="$WORK_DIR/awk-failure"
awk_failure_env="$(make_env_fixture "$awk_failure_dir")"
awk_failure_before="$(cat "$awk_failure_env")"
awk_failure_mode="$(file_mode "$awk_failure_env")"
awk_failure_bin="$WORK_DIR/failing-awk-bin"
make_failing_awk "$awk_failure_bin"
ENV_FILE="$awk_failure_env" PATH="$awk_failure_bin:$PATH" \
    write_env_value LLM_MODEL should-not-persist && fail "bootstrap writer reported success after awk failure"
[[ "$(cat "$awk_failure_env")" == "$awk_failure_before" ]] \
    || fail "bootstrap writer modified .env after awk failure"
[[ "$(file_mode "$awk_failure_env")" == "$awk_failure_mode" ]] \
    || fail "bootstrap writer changed .env mode after awk failure"
assert_no_temp "$awk_failure_env"
pass "bootstrap writer leaves .env intact when rendering fails"

# start_llm must stop before launching the service if persistence fails. Stub
# its dependencies, then call the real function extracted from upgrade-model.
start_dir="$WORK_DIR/start-failure"
start_env="$(make_env_fixture "$start_dir")"
start_before="$(cat "$start_env")"
start_bin="$WORK_DIR/start-failing-replacement-bin"
if is_windows_bash; then
    start_calls="$WORK_DIR/start-failing-replacement-calls"
    make_failing_windows_replace "$start_bin"
else
    make_failing_mv "$start_bin"
fi
start_called="$WORK_DIR/start-called"

resolve_inference_runtime() { :; }
log() { :; }
error() { :; }
docker() { : > "$start_called"; return 0; }
COMPOSE_FILE_ARGS=()
INFERENCE_SERVICE="llama-server"
INFERENCE_CONTAINER="ods-llama-server"
MODEL_ENV_KEY="LLM_MODEL"
ODS_DIR="$start_dir"

eval "$(function_block "$UPGRADE" start_llm)"
if is_windows_bash; then
    if ODS_ATOMIC_REAL_POWERSHELL="$REAL_POWERSHELL" \
        ODS_ATOMIC_POWERSHELL_CALLS="$start_calls" PATH="$start_bin:$PATH" \
        start_llm /models/should-not-start; then
        fail "start_llm reported success after env persistence failure"
    fi
else
    if PATH="$start_bin:$PATH" start_llm /models/should-not-start; then
        fail "start_llm reported success after env persistence failure"
    fi
fi
[[ ! -e "$start_called" ]] || fail "start_llm launched a service after env persistence failure"
[[ "$(cat "$start_env")" == "$start_before" ]] \
    || fail "start_llm modified .env after replacement failure"
assert_no_temp "$start_env"
pass "upgrade startup aborts before launch when .env replacement fails"

echo "[PASS] atomic .env write regression coverage"
