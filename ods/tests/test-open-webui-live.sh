#!/usr/bin/env bash
# Live check on a running ODS installation with Open WebUI: the pinned image
# and version run and are healthy, the database is fully migrated, Open
# WebUI's settings come from ODS (ENABLE_PERSISTENT_CONFIG=false), signup is
# closed, and the copies saved before upgrades are in place. Read-only: every
# probe runs inside the container and opens webui.db read-only.
#
#   bash tests/test-open-webui-live.sh [install-dir]
#
# Right after an upgrade from an earlier Open WebUI, set
# ODS_EXPECT_UPGRADE_BACKUP=1 to also require the pre-upgrade copy.
set -euo pipefail

INSTALL_DIR="${1:-${ODS_INSTALL_DIR:-$HOME/ods}}"
CONTAINER=ods-webui
COMPOSE="$INSTALL_DIR/docker-compose.base.yml"

failures=0
pass() { printf 'PASS %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1"; failures=$((failures + 1)); }
check() {
    local label="$1"
    shift
    if "$@"; then pass "$label"; else fail "$label"; fi
}
in_container() { docker exec "$CONTAINER" "$@"; }
# For the Python probes, which read their program from this script's heredocs.
python_in_container() { docker exec -i "$CONTAINER" python3 -; }

[[ -f "$INSTALL_DIR/.env" && -f "$COMPOSE" ]] || {
    printf 'No ODS installation at %s\n' "$INSTALL_DIR" >&2
    exit 2
}
[[ "$(docker inspect --format '{{.State.Running}}' "$CONTAINER" 2>/dev/null)" == true ]] || {
    printf '%s is not running; select and start Open WebUI first\n' "$CONTAINER" >&2
    exit 2
}

pinned_ref="$(sed -n 's#^ *image: \(ghcr\.io/open-webui/open-webui:v[0-9.]*@sha256:[0-9a-f]\{64\}\)$#\1#p' "$COMPOSE" | head -1)"
[[ -n "$pinned_ref" ]] || { printf 'No Open WebUI pin found in %s\n' "$COMPOSE" >&2; exit 2; }
pinned_version="${pinned_ref#*:v}"
pinned_version="${pinned_version%%@*}"
pinned_digest="${pinned_ref##*@}"

repo_digests="$(docker image inspect --format '{{join .RepoDigests " "}}' \
    "$(docker inspect --format '{{.Image}}' "$CONTAINER")")"
check "the container runs the pinned image ($pinned_digest)" \
    grep -q "ghcr.io/open-webui/open-webui@$pinned_digest" <<< "$repo_digests"
check "the container is healthy" \
    test "$(docker inspect --format '{{.State.Health.Status}}' "$CONTAINER")" = healthy

# One read of Open WebUI's public endpoints, from inside the container.
read -r health_status app_version signup <<< "$(python_in_container <<'PY'
import json, urllib.request

def get(path):
    with urllib.request.urlopen(f"http://127.0.0.1:8080{path}", timeout=15) as response:
        return json.load(response)

print(get("/health")["status"], get("/api/version")["version"], get("/api/config")["features"]["enable_signup"])
PY
)"
check "/health answers status true" test "$health_status" = True
check "Open WebUI reports $app_version, the pinned version ($pinned_version)" test "$app_version" = "$pinned_version"
check "signup is closed (ENABLE_SIGNUP=false)" test "$signup" = False

# The server process, not just the container definition, has the setting.
# Only this one variable is printed; the environment also holds secrets.
persistent="$(in_container sh -c 'tr "\0" "\n" < /proc/1/environ | sed -n "s/^ENABLE_PERSISTENT_CONFIG=//p"')"
check "Open WebUI runs with ENABLE_PERSISTENT_CONFIG=false" test "$persistent" = false

# Published beyond loopback, the server must run with sign-in on whatever .env
# says. Only these two variables are printed.
published="$(in_container sh -c 'tr "\0" "\n" < /proc/1/environ | sed -n "s/^ODS_WEBUI_BIND_ADDRESS=//p"')"
proxied="$(in_container sh -c 'tr "\0" "\n" < /proc/1/environ | sed -n "s/^ODS_WEBUI_PROXY_BIND=//p"')"
signin="$(in_container sh -c 'tr "\0" "\n" < /proc/1/environ | sed -n "s/^WEBUI_AUTH=//p"' | tr '[:upper:]' '[:lower:]')"
reachable=""
for address in "$published" "$proxied"; do
    case "$(printf '%s' "$address" | tr -d " \t\"'" | tr '[:upper:]' '[:lower:]')" in
        ''|127.0.0.1|::1|'[::1]'|localhost) ;;
        *) reachable="$address" ;;
    esac
done
if [[ -z "$reachable" ]]; then
    pass "Open WebUI is reachable only from this machine; sign-in follows .env"
else
    check "Open WebUI reachable on $reachable runs with sign-in on" test "$signin" = true
    # The same check the start-up step runs, from the copy mounted in the container.
    default_admin="$(in_container python3 -c 'import importlib.util, pathlib
spec = importlib.util.spec_from_file_location("prepare", "/opt/ods/openwebui-prepare.py")
step = importlib.util.module_from_spec(spec)
spec.loader.exec_module(step)
print("yes" if step.default_administrator_active(pathlib.Path("/app/backend/data/webui.db")) else "no")')"
    check "admin@localhost no longer has the default password while Open WebUI is reachable" test "$default_admin" = no
fi

read -r revision head stored_keys users chats <<< "$(python_in_container <<'PY'
import pathlib, re, sqlite3

versions = pathlib.Path("/app/backend/open_webui/migrations/versions")
revisions, parents = set(), set()
for script in versions.glob("*.py"):
    text = script.read_text(encoding="utf-8")
    found = re.search(r"""^revision(?:\s*:[^=\n]*)?\s*=\s*['"](\w+)['"]""", text, re.M)
    down = re.search(r"^down_revision(?:\s*:[^=\n]*)?\s*=\s*(.+)$", text, re.M)
    if found:
        revisions.add(found.group(1))
    if down:
        parents.update(re.findall(r"""['"](\w+)['"]""", down.group(1)))
head = ",".join(sorted(revisions - parents))
database = sqlite3.connect("file:/app/backend/data/webui.db?mode=ro", uri=True)
revision = ",".join(row[0] for row in database.execute("SELECT version_num FROM alembic_version"))
# Keys Open WebUI writes only when it stores its settings itself: 0.10 and
# later seed every setting on a start with persistent config on. None of these
# can come from a database migrated from 0.7.2.
seeded_only = ("task.model.params", "chat.context_compaction.enable", "web.search.confirmation.enable")
if "key" in {row[1] for row in database.execute("PRAGMA table_info(config)")}:
    stored = database.execute(
        f"SELECT count(*) FROM config WHERE key IN ({','.join('?' * len(seeded_only))})", seeded_only).fetchone()[0]
else:
    stored = "no-per-key-config-table"
users = database.execute('SELECT count(*) FROM "user"').fetchone()[0]
chats = database.execute("SELECT count(*) FROM chat").fetchone()[0]
print(revision, head, stored, users, chats)
PY
)"
check "the database is fully migrated (revision $revision, image head $head)" test "$revision" = "$head"
check "Open WebUI has not stored its own settings in webui.db" test "$stored_keys" = 0
printf 'INFO %s account(s), %s chat(s) in webui.db\n' "$users" "$chats"

# A missing marker or backup directory reads as empty; the checks below report it.
recorded="$(in_container sh -c 'cat /app/backend/data/.ods-open-webui-version 2>/dev/null || true')"
check "the recorded version is the pinned one ($recorded)" test "$recorded" = "$pinned_version"

copies="$(in_container sh -c 'ls -A /app/backend/data/ods-backups 2>/dev/null || true')"
# grep -c prints 0 and exits 1 when nothing matches.
count="$(grep -c -- '^[^.].*-open-webui-.*\.db$' <<< "$copies" || true)"
printf 'INFO %s database copy(ies) in data/open-webui/ods-backups%s\n' "$count" "${copies:+:}"
[[ -z "$copies" ]] || sed 's/^/  /' <<< "$copies"
check "at most two copies are kept" test "$count" -le 2
check "no partial copy is left behind" test "$(grep -c '^\.partial-' <<< "$copies" || true)" = 0
if [[ "${ODS_EXPECT_UPGRADE_BACKUP:-0}" == 1 ]]; then
    check "a copy was saved before the upgrade" test "$count" -ge 1
fi

printf '%d check(s) failed\n' "$failures"
exit $((failures > 0))
