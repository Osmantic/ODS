#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

build_harness() {
    local source_file="$1"
    local harness_file="$2"

    {
        printf '%s\n' '#!/usr/bin/env bash' 'set -euo pipefail'
        awk '
            /^HOST_LOCK=true$/ { capture = 1 }
            capture { print }
            capture && /^TARGETS=\(\)$/ { exit }
            capture && /^WORK_DIR=""$/ {
                print "TARGETS=()"
                exit
            }
        ' "$source_file"
        cat <<'HARNESS_HELPERS'
log() {
    printf '%s\n' "$*"
}

fail() {
    printf '[FAIL] %s\n' "$*" >&2
    exit 1
}
HARNESS_HELPERS
        sed -n '/^acquire_host_lock() {/,/^}/p' "$source_file"
        awk '
            /^while \[\[ \$# -gt 0 \]\]; do$/ ||
            /^while \(\(\$# > 0\)\); do$/ {
                capture = 1
            }
            capture && /^if .*#TARGETS/ { exit }
            capture { print }
        ' "$source_file"
        cat <<'HARNESS_RUN'
if [[ -n "${ODS_FLEET_TEST_ROOT:-}" ]]; then
    if [[ "$LOCK_FILE" == "/tmp/ods-fleet-heavy.lock" ]]; then
        LOCK_FILE="$ODS_FLEET_TEST_ROOT/ods-fleet-heavy.lock"
    fi
fi

acquire_host_lock
HARNESS_RUN
    } > "$harness_file"
    chmod +x "$harness_file"
}

assert_trace() {
    local trace_file="$1"
    local expected="$2"
    local description="$3"
    local actual=""

    [[ -f "$trace_file" ]] && actual="$(cat "$trace_file")"
    [[ "$actual" == "$expected" ]] \
        || fail "$description: expected flock calls [$expected], got [$actual]"
}

mkdir -p "$TMP_DIR/bin"
cat > "$TMP_DIR/bin/flock" <<'FAKE_FLOCK'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${ODS_FLOCK_TRACE:?}"
FAKE_FLOCK
chmod +x "$TMP_DIR/bin/flock"

for source_file in \
    "$ROOT_DIR/tests/fleet-multi-distro.sh" \
    "$ROOT_DIR/tests/fleet-incus-vm.sh"; do
    script_name="$(basename "$source_file" .sh)"
    harness_file="$TMP_DIR/$script_name-harness.sh"
    build_harness "$source_file" "$harness_file"

    default_dir="$TMP_DIR/$script_name-default"
    default_trace="$default_dir/flock.log"
    mkdir -p "$default_dir"
    (
        unset ODS_FLEET_HOST_LOCK ODS_FLEET_HOST_LOCK_TIMEOUT_SECONDS
        PATH="$TMP_DIR/bin:$PATH" \
            ODS_FLOCK_TRACE="$default_trace" \
            ODS_FLEET_TEST_ROOT="$default_dir" \
            bash "$harness_file"
    )
    assert_trace "$default_trace" "9" "$script_name default lock"
    [[ -f "$default_dir/ods-fleet-heavy.lock" ]] \
        || fail "$script_name did not open the ODS default lock"

    custom_dir="$TMP_DIR/$script_name-custom"
    custom_trace="$custom_dir/flock.log"
    custom_lock="$custom_dir/custom.lock"
    mkdir -p "$custom_dir"
    (
        unset ODS_FLEET_HOST_LOCK ODS_FLEET_HOST_LOCK_TIMEOUT_SECONDS
        PATH="$TMP_DIR/bin:$PATH" \
            ODS_FLOCK_TRACE="$custom_trace" \
            ODS_FLEET_TEST_ROOT="$custom_dir" \
            bash "$harness_file" --lock-file "$custom_lock"
    )
    assert_trace "$custom_trace" "9" "$script_name custom lock"
    [[ -f "$custom_lock" ]] || fail "$script_name did not open the custom lock"

    override_dir="$TMP_DIR/$script_name-override"
    override_trace="$override_dir/flock.log"
    override_lock="$override_dir/override.lock"
    mkdir -p "$override_dir"
    (
        unset ODS_FLEET_HOST_LOCK_TIMEOUT_SECONDS
        PATH="$TMP_DIR/bin:$PATH" \
            ODS_FLOCK_TRACE="$override_trace" \
            ODS_FLEET_TEST_ROOT="$override_dir" \
            ODS_FLEET_HOST_LOCK="$override_lock" \
            bash "$harness_file"
    )
    assert_trace "$override_trace" "9" "$script_name environment override"
    [[ -f "$override_lock" ]] || fail "$script_name did not honor the ODS lock override"

    timeout_dir="$TMP_DIR/$script_name-timeout"
    timeout_trace="$timeout_dir/flock.log"
    mkdir -p "$timeout_dir"
    (
        unset ODS_FLEET_HOST_LOCK
        PATH="$TMP_DIR/bin:$PATH" \
            ODS_FLOCK_TRACE="$timeout_trace" \
            ODS_FLEET_TEST_ROOT="$timeout_dir" \
            ODS_FLEET_HOST_LOCK_TIMEOUT_SECONDS=7 \
            bash "$harness_file"
    )
    assert_trace "$timeout_trace" "-w 7 9" "$script_name timeout environment override"

    disabled_dir="$TMP_DIR/$script_name-disabled"
    disabled_trace="$disabled_dir/flock.log"
    mkdir -p "$disabled_dir"
    (
        unset ODS_FLEET_HOST_LOCK ODS_FLEET_HOST_LOCK_TIMEOUT_SECONDS
        PATH="$TMP_DIR/bin:$PATH" \
            ODS_FLOCK_TRACE="$disabled_trace" \
            ODS_FLEET_TEST_ROOT="$disabled_dir" \
            bash "$harness_file" --no-host-lock
    )
    [[ ! -e "$disabled_trace" ]] || fail "$script_name called flock with --no-host-lock"
    [[ ! -e "$disabled_dir/ods-fleet-heavy.lock" ]] \
        || fail "$script_name opened the default lock with --no-host-lock"
done

echo "[PASS] Fleet host locks honor ODS defaults and overrides"
