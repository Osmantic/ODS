#!/usr/bin/env bash
# Regression coverage for the sudo helper and Docker permission fallback.
set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIVE="$ROOT_DIR/installers/phases/05-docker.sh"
. "$ROOT_DIR/installers/lib/sudo.sh"

pass_count=0
fail_count=0
pass() { printf 'PASS: %s\n' "$1"; pass_count=$((pass_count + 1)); }
fail() { printf 'FAIL: %s\n' "$1" >&2; fail_count=$((fail_count + 1)); }

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

cat > "$tmp_dir/docker" <<'DOCKER'
#!/usr/bin/env bash
printf '%s\n' 'permission denied while connecting to the Docker socket' >&2
exit 1
DOCKER
cat > "$tmp_dir/sudo" <<'SUDO'
#!/usr/bin/env bash
touch "$SUDO_MARKER"
"$@"
SUDO
chmod +x "$tmp_dir/docker" "$tmp_dir/sudo"

export PATH="$tmp_dir:$PATH"
export SUDO_MARKER="$tmp_dir/sudo-called"
export INTERACTIVE=false
export DRY_RUN=false
export LOG_FILE=/dev/null

# Source only the production helpers under test; sourcing the full phase would
# perform installer work. Keep function bodies exact so this is behavioral,
# not a static string assertion.
python3 - "$FIVE" "$tmp_dir/helpers.sh" <<'PY'
import pathlib
import sys

text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
lines = text.splitlines()
targets = ("_docker_cmd_arr()", "docker_run()", "_docker_try_with_optional_sudo()")
out = []
capturing = False
depth = 0
for line in lines:
    stripped = line.strip()
    if any(stripped.startswith(target) for target in targets):
        capturing = True
        depth = 0
    if capturing:
        out.append(line)
        depth += line.count("{") - line.count("}")
        if depth <= 0 and stripped == "}":
            capturing = False
            out.append("")
pathlib.Path(sys.argv[2]).write_text("\n".join(out), encoding="utf-8")
PY
. "$tmp_dir/helpers.sh"

rm -f "$SUDO_MARKER"
ODS_SUDO_AVAILABLE=false
DOCKER_CMD=""
DOCKER_COMPOSE_CMD=""
if _docker_try_with_optional_sudo info; then
    fail "no-sudo Docker fallback unexpectedly succeeded"
else
    pass "no-sudo Docker fallback returns promptly with failure"
fi
if [[ $EUID -eq 0 ]]; then
    # Root is always privileged regardless of the non-root availability flag.
    [[ "${DOCKER_CMD:-}" == "sudo docker" && -e "$SUDO_MARKER" ]] \
        && pass "root availability is independent of the non-root sudo flag" \
        || fail "root unexpectedly used the unavailable-sudo branch"
else
    [[ "${DOCKER_CMD:-docker}" == "docker" ]] \
        && pass "no-sudo run keeps the unprivileged Docker command" \
        || fail "no-sudo run promoted Docker to sudo"
    [[ ! -e "$SUDO_MARKER" ]] \
        && pass "no-sudo run never invokes raw sudo" \
        || fail "no-sudo run invoked raw sudo"
fi

rm -f "$SUDO_MARKER"
ODS_SUDO_AVAILABLE=true
DOCKER_CMD=""
DOCKER_COMPOSE_CMD=""
_docker_try_with_optional_sudo info || true
[[ -e "$SUDO_MARKER" ]] \
    && pass "available sudo still enables the Docker fallback" \
    || fail "available sudo did not enable the Docker fallback"
[[ "${DOCKER_CMD:-}" == "sudo docker" ]] \
    && pass "available sudo promotes the Docker command" \
    || fail "available sudo did not promote the Docker command"

# From here on, sudo only records arguments and returns a selected status. No
# command passed through it is executed, even when this fixture runs as root.
sudo() {
    printf '%s\n' "$@" > "$tmp_dir/sudo.args"
    return "${SUDO_TEST_STATUS:-0}"
}
probe_command() {
    printf '%s\n' "$@" > "$tmp_dir/direct.args"
    return "${DIRECT_TEST_STATUS:-0}"
}
assert_sudo_args() {
    printf '%s\n' "$@" > "$tmp_dir/expected.args"
    cmp -s "$tmp_dir/expected.args" "$tmp_dir/sudo.args"
}
ODS_SUDO_AVAILABLE=true
INTERACTIVE=false
rm -f "$tmp_dir/sudo.args" "$tmp_dir/direct.args"
ods_sudo probe_command 'a value with spaces' '--literal-option'
if [[ $EUID -eq 0 ]]; then
    [[ -f "$tmp_dir/direct.args" && ! -e "$tmp_dir/sudo.args" ]] \
        && pass "root runs ordinary commands directly" \
        || fail "root unnecessarily invokes sudo for an ordinary command"
    printf '%s\n' 'a value with spaces' '--literal-option' > "$tmp_dir/expected.args"
    cmp -s "$tmp_dir/expected.args" "$tmp_dir/direct.args" \
        && pass "root direct execution preserves argument boundaries" \
        || fail "root direct execution changed arguments"
    DIRECT_TEST_STATUS=37
    ods_sudo probe_command inert
    [[ $? -eq 37 ]] && pass "root preserves the command failure" \
        || fail "root swallowed the command failure"
    unset DIRECT_TEST_STATUS
else
    assert_sudo_args -n probe_command 'a value with spaces' '--literal-option' \
        && pass "non-root commands use noninteractive sudo with exact arguments" \
        || fail "non-root sudo arguments changed"
fi

ods_sudo -u 'fixture-owner' -- env 'HOME=/home/fixture owner' probe_command inert
assert_sudo_args -n -u 'fixture-owner' -- env 'HOME=/home/fixture owner' probe_command inert \
    && pass "identity options reach sudo intact, including under root" \
    || fail "identity options were executed as a command or lost"
ods_sudo -E bash '/tmp/inert fixture.sh'
assert_sudo_args -n -E bash '/tmp/inert fixture.sh' \
    && pass "environment options reach sudo intact" \
    || fail "environment options were executed as a command or lost"
ods_sudo -- probe_command inert
assert_sudo_args -n -- probe_command inert \
    && pass "sudo option terminator is preserved" \
    || fail "sudo option terminator was executed as a command or lost"
SUDO_TEST_STATUS=41
ods_sudo -u fixture-owner -- probe_command inert
[[ $? -eq 41 ]] && pass "identity-switch failure propagates" \
    || fail "identity-switch failure was swallowed"
SUDO_TEST_STATUS=127
ods_sudo -u fixture-owner -- probe_command inert
[[ $? -eq 127 ]] && pass "unavailable sudo fails without a direct-execution fallback" \
    || fail "unavailable sudo did not fail closed"
unset SUDO_TEST_STATUS
if [[ $EUID -ne 0 ]]; then
    INTERACTIVE=true
    ods_sudo -u fixture-owner -- probe_command inert
    assert_sudo_args -u fixture-owner -- probe_command inert \
        && pass "non-root interactive sudo keeps its existing prompt behavior" \
        || fail "non-root interactive behavior changed"
fi

printf 'Results: %d passed, %d failed\n' "$pass_count" "$fail_count"
[[ "$fail_count" -eq 0 ]]
