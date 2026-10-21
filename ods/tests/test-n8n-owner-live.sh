#!/usr/bin/env bash
# Live check on a running ODS installation with n8n enabled: the pinned n8n
# version runs, no first-run owner screen is open, an owner set from .env
# signs in with N8N_USER/N8N_PASS, the stored owner hash is private, and the
# plaintext password never reaches n8n's processes. Read-only.
#
#   bash tests/test-n8n-owner-live.sh [install-dir]
set -euo pipefail

INSTALL_DIR="${1:-${ODS_INSTALL_DIR:-$HOME/ods}}"
CONTAINER=ods-n8n
COMPOSE="$INSTALL_DIR/extensions/services/n8n/compose.yaml"

failures=0
pass() { printf 'PASS %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1"; failures=$((failures + 1)); }
check() {
    local label="$1"
    shift
    if "$@"; then pass "$label"; else fail "$label"; fi
}

[[ -f "$INSTALL_DIR/.env" && -f "$COMPOSE" ]] || {
    printf 'No ODS installation with n8n at %s\n' "$INSTALL_DIR" >&2
    exit 2
}
[[ "$(docker inspect --format '{{.State.Running}}' "$CONTAINER" 2>/dev/null)" == true ]] || {
    printf '%s is not running; enable n8n first (ods enable n8n && ods start n8n)\n' "$CONTAINER" >&2
    exit 2
}
port="$(sed -n 's/^N8N_PORT=//p' "$INSTALL_DIR/.env" | tail -1 | tr -d '"'"'")"
base="http://127.0.0.1:${port:-5678}"

pinned="$(sed -n 's#.*image: n8nio/n8n:\([0-9.]*\)@.*#\1#p' "$COMPOSE")"
running_version="$(docker exec "$CONTAINER" node -e \
    "process.stdout.write(require('/usr/local/lib/node_modules/n8n/package.json').version)")"
check "n8n $running_version runs the pinned version ($pinned)" test "$running_version" = "$pinned"
check "the container is healthy" \
    test "$(docker inspect --format '{{.State.Health.Status}}' "$CONTAINER")" = healthy

setup_open="$(python3 - "$base" <<'PY'
import json, sys, urllib.request
with urllib.request.urlopen(sys.argv[1] + "/rest/settings", timeout=15) as response:
    settings = json.load(response)
print(str(settings["data"]["userManagement"]["showSetupOnFirstLoad"]).lower())
PY
)"
check "no first-run owner screen is open" test "$setup_open" = false

if docker exec "$CONTAINER" test -f /tmp/.n8n/.ods-owner-from-env; then
    printf 'INFO the owner comes from .env\n'
    check "the owner hash is mode 600 and owned by the container user" test \
        "$(docker exec "$CONTAINER" stat -c '%a %u' /tmp/.n8n/.ods-owner-password.bcrypt)" \
        = "600 $(docker exec "$CONTAINER" id -u)"
    # Credentials go to python on stdin; nothing prints them.
    login_status="$(
        { sed -n 's/^N8N_USER=//p' "$INSTALL_DIR/.env" | tail -1; sed -n 's/^N8N_PASS=//p' "$INSTALL_DIR/.env" | tail -1; } |
        python3 -c '
import json, sys, urllib.error, urllib.request
email, password = (line.rstrip("\n").strip("\"\x27") for line in sys.stdin.readlines()[:2])
request = urllib.request.Request(sys.argv[1] + "/rest/login", method="POST",
    data=json.dumps({"emailOrLdapLoginId": email, "password": password}).encode(),
    headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(request, timeout=15) as response:
        print(response.status)
except urllib.error.HTTPError as error:
    print(error.code)
' "$base"
    )"
    check "N8N_USER/N8N_PASS sign in (HTTP $login_status)" test "$login_status" = 200
else
    printf 'INFO the owner was created on n8n'"'"'s first-run screen; it keeps its own password\n'
fi

# No process in the container may carry the plaintext password, PID 1
# included. `docker exec` starts this checker with the container's configured
# environment, so it skips itself and the commands it runs.
leaks="$(docker exec "$CONTAINER" sh -c '
    for dir in /proc/[0-9]*; do
        pid="${dir#/proc/}"
        [ "$pid" = "$$" ] && continue
        [ "$(awk "/^PPid:/ { print \$2 }" "$dir/status" 2>/dev/null)" = "$$" ] && continue
        tr "\0" "\n" < "$dir/environ" 2>/dev/null \
            | grep -q -e "^ODS_N8N_OWNER_PASSWORD=" -e "^N8N_DEFAULT_ADMIN_PASSWORD=" \
            && echo "${dir#/proc/}"
    done; true')"
check "no n8n process carries the plaintext password${leaks:+ (pids: ${leaks//$'\n'/ })}" test -z "$leaks"

backups="$(docker exec "$CONTAINER" sh -c 'ls /tmp/.n8n/ods-backups 2>/dev/null | wc -l')"
printf 'INFO %s file(s) in data/n8n/ods-backups\n' "$backups"

printf '%d check(s) failed\n' "$failures"
exit $((failures > 0))
