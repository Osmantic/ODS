#!/usr/bin/env bash
# Phase 05 falls back to `sudo docker` whenever the installing user cannot
# reach the Docker socket, and then tells them to log out and back in. That
# advice only works when the user is already in the docker group: ODS adds
# them to it solely when it installs Docker itself, so on a host where Docker
# was already present, re-logging in changes nothing and every later `ods`
# command fails on the socket. Check that the hint names the missing group.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE="$ROOT_DIR/installers/phases/05-docker.sh"

PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); printf '[PASS] %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf '[FAIL] %s\n' "$1" >&2; }

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

hint_body="$(awk '
    /^_docker_sudo_fallback_hint\(\) *\{/ { emit = 1 }
    emit { print }
    emit && $0 == "}" { exit }
' "$PHASE")"
[[ -n "$hint_body" ]] || {
    echo "[FAIL] 05-docker.sh defines no _docker_sudo_fallback_hint: the sudo fallback tells every user to re-login, even one outside the docker group" >&2
    exit 1
}

# `id` shim: report the groups the case under test needs.
mkdir -p "$TMP_DIR/bin"
cat > "$TMP_DIR/bin/id" <<'SH'
#!/usr/bin/env bash
if [[ "${1:-}" == "-nG" ]]; then
    printf '%s\n' "${FAKE_GROUPS?FAKE_GROUPS must be set}"
    exit "${FAKE_ID_RC:-0}"
fi
exec /usr/bin/id "$@"
SH
chmod +x "$TMP_DIR/bin/id"

run_hint() {  # run_hint <groups> [id exit status]
    (
        PATH="$TMP_DIR/bin:$PATH"
        export FAKE_GROUPS="$1" FAKE_ID_RC="${2:-0}"
        SUDO_USER="installer-user"
        warn() { printf '%s\n' "$*"; }
        eval "$hint_body"
        _docker_sudo_fallback_hint
    )
}

out="$(run_hint "installer-user docker sudo")"
if grep -q "log out/in (or run 'newgrp docker')" <<< "$out" && ! grep -q 'usermod -aG docker' <<< "$out"; then
    pass "a user already in the docker group is told to start a new login shell"
else
    printf '%s\n' "$out"
    fail "expected only the re-login hint for a user in the docker group"
fi

out="$(run_hint "installer-user sudo")"
if grep -q 'sudo usermod -aG docker installer-user' <<< "$out"; then
    pass "a user outside the docker group gets the command that grants access"
else
    printf '%s\n' "$out"
    fail "expected the usermod command naming the user"
fi
if grep -q "not in the 'docker' group" <<< "$out"; then
    pass "the hint says why ods commands cannot reach Docker"
else
    printf '%s\n' "$out"
    fail "expected the hint to state the missing group membership"
fi

# `id` failing (no such user, restricted environment) must not claim membership.
out="$(run_hint "" 1)"
if grep -q 'sudo usermod -aG docker installer-user' <<< "$out"; then
    pass "an unreadable group list falls back to the actionable hint"
else
    printf '%s\n' "$out"
    fail "expected the usermod hint when the group list cannot be read"
fi

printf 'Results: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
