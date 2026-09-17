#!/usr/bin/env bash
# Regression coverage for the Docker permission fallback in phase 05.
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
[[ "${DOCKER_CMD:-docker}" == "docker" ]] \
    && pass "no-sudo run keeps the unprivileged Docker command" \
    || fail "no-sudo run promoted Docker to sudo"
[[ ! -e "$SUDO_MARKER" ]] \
    && pass "no-sudo run never invokes raw sudo" \
    || fail "no-sudo run invoked raw sudo"

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

printf 'Results: %d passed, %d failed\n' "$pass_count" "$fail_count"
[[ "$fail_count" -eq 0 ]]
