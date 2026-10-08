#!/usr/bin/env bash
# `ods-update.sh update` resolves the Compose flags before `git pull`. When the
# pull deletes a Compose file those flags name (a removed bundled service such
# as the legacy OpenClaw extension), the restart must use the updated tree:
# Compose cannot open a missing -f file, and `down --remove-orphans` with the
# new flags is what removes the retired container. A rollback restores
# configuration but not git, so it must not reuse the stale flags either.
#
# This runs the real updater against a throwaway git checkout whose origin
# deletes a service's compose file. Docker, Python and curl are stubs; the
# Docker stub fails, like Compose, when a -f file does not exist.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPDATE_SCRIPT="${ODS_UPDATE_UNDER_TEST:-$ROOT_DIR/ods-update.sh}"

fail() { echo "[FAIL] $*"; exit 1; }
pass() { echo "[PASS] $*"; }

command -v git >/dev/null 2>&1 || fail "git is required"
command -v jq >/dev/null 2>&1 || fail "jq is required"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

BIN_DIR="$TMP_DIR/bin"
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/docker" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$DOCKER_LOG"
args=("$@")
[[ "${args[0]:-}" == compose ]] && args=("${args[@]:1}")
for ((i = 0; i < ${#args[@]}; i++)); do
    if [[ "${args[$i]}" == -f && ! -f "${args[$((i + 1))]}" ]]; then
        echo "open ${args[$((i + 1))]}: no such file or directory" >&2
        exit 1
    fi
done
case " $* " in
    *" up -d "*)
        count="$(cat "$UP_FAILURES" 2>/dev/null || echo 0)"
        if (( count > 0 )); then
            echo $((count - 1)) > "$UP_FAILURES"
            exit 1
        fi
        ;;
    *" config --format json "*) echo '{}' ;;
    *" ps --services "*) echo dashboard-api ;;
    *" ps --format json "*) echo '{"State":"running"}' ;;
esac
exit 0
SH
cp "$BIN_DIR/docker" "$BIN_DIR/docker-compose"
# The source-update safety helper is Python; its own tests cover it.
cat > "$BIN_DIR/python3" <<'SH'
#!/usr/bin/env bash
[[ "${2:-}" == compose ]] && cat >/dev/null
exit 0
SH
printf '#!/usr/bin/env bash\nexit 0\n' > "$BIN_DIR/curl"
chmod +x "$BIN_DIR/docker" "$BIN_DIR/docker-compose" "$BIN_DIR/python3" "$BIN_DIR/curl"

git_quiet() { git -c user.name=ods-test -c user.email=ods-test@example.invalid "$@" >/dev/null 2>&1; }

# An origin and a checkout that still runs the retired service. The next
# release on the origin deletes that service; with "failing-migration" it also
# ships a migration that fails, so the update rolls back after the pull.
make_checkout() {
    local name="$1" variant="${2:-}" work="$TMP_DIR/$1-work" origin="$TMP_DIR/$1-origin.git"
    git_quiet init -q --bare "$origin"
    git_quiet init -q "$work"
    mkdir -p "$work/scripts" "$work/extensions/services/retired"
    cp "$UPDATE_SCRIPT" "$work/ods-update.sh"
    printf 'print("stub")\n' > "$work/scripts/source-update-preflight.py"
    printf '#!/usr/bin/env bash\necho "-f docker-compose.base.yml"\n' > "$work/scripts/resolve-compose-stack.sh"
    chmod +x "$work/scripts/resolve-compose-stack.sh"
    printf 'services: {dashboard-api: {image: example/dashboard-api:test}}\n' > "$work/docker-compose.base.yml"
    printf 'services: {retired: {image: example/retired:test}}\n' > "$work/extensions/services/retired/compose.yaml"
    printf 'data/\n.compose-flags\n.env\n.version\n' > "$work/.gitignore"
    git_quiet -C "$work" add -A
    git_quiet -C "$work" commit -q -m initial
    git_quiet -C "$work" branch -M main
    git_quiet -C "$work" remote add origin "$origin"
    git_quiet -C "$work" push -q origin main
    git_quiet clone -q -c core.autocrlf=false -b main "$origin" "$TMP_DIR/$name"
    printf '%s\n' '-f docker-compose.base.yml -f extensions/services/retired/compose.yaml' \
        > "$TMP_DIR/$name/.compose-flags"
    printf 'GPU_BACKEND=cpu\n' > "$TMP_DIR/$name/.env"
    git_quiet -C "$work" rm -q extensions/services/retired/compose.yaml
    if [[ "$variant" == failing-migration ]]; then
        mkdir -p "$work/migrations"
        printf '#!/usr/bin/env bash\nexit 1\n' > "$work/migrations/migrate-v99.sh"
        chmod +x "$work/migrations/migrate-v99.sh"
        git_quiet -C "$work" add migrations
    fi
    git_quiet -C "$work" commit -q -m "remove retired service"
    git_quiet -C "$work" push -q origin main
    mkdir -p "$TMP_DIR/home-$name"
}

run_update() {
    local name="$1"
    (cd "$TMP_DIR/$name" && HOME="$TMP_DIR/home-$name" PATH="$BIN_DIR:$PATH" \
        DOCKER_LOG="$TMP_DIR/$name.docker" UP_FAILURES="$TMP_DIR/$name.up-failures" \
        HEALTH_TIMEOUT=5 bash ./ods-update.sh update) > "$TMP_DIR/$name.out" 2>&1
}

no_stale_restart() {
    local name="$1" what="$2"
    if grep -E ' (down|up) ' "$TMP_DIR/$name.docker" | grep -Fq 'extensions/services/retired'; then
        cat "$TMP_DIR/$name.docker"
        fail "$what reused the compose file the pull deleted"
    fi
}

# ── The restart after the pull uses the updated tree ─────────────────────────
make_checkout updated
run_update updated || { cat "$TMP_DIR/updated.out"; fail "update failed after the pull deleted a compose file"; }
[[ ! -e "$TMP_DIR/updated/extensions/services/retired/compose.yaml" ]] \
    || fail "fixture pull did not delete the retired compose file"
grep -Fxq 'compose -f docker-compose.base.yml down --remove-orphans' "$TMP_DIR/updated.docker" \
    || { cat "$TMP_DIR/updated.docker"; fail "restart did not take the stack down with the updated flags"; }
grep -Fxq 'compose -f docker-compose.base.yml up -d' "$TMP_DIR/updated.docker" \
    || { cat "$TMP_DIR/updated.docker"; fail "restart did not start the stack with the updated flags"; }
no_stale_restart updated "restart"
grep -Fq 'Update complete' "$TMP_DIR/updated.out" || fail "update did not complete"
pass "update restarts with flags resolved after the pull"

# ── A failed restart rolls back with the updated flags ───────────────────────
make_checkout failed-start
# Compose v2 and v1 both fail the update's start; the rollback's start works.
echo 2 > "$TMP_DIR/failed-start.up-failures"
if run_update failed-start; then
    fail "update must report the failed restart"
fi
grep -Fq 'Rollback complete' "$TMP_DIR/failed-start.out" \
    || { cat "$TMP_DIR/failed-start.out"; fail "update did not roll back"; }
[[ "$(grep -cE ' up -d$' "$TMP_DIR/failed-start.docker")" == 3 \
    && "$(grep -E ' up -d$' "$TMP_DIR/failed-start.docker" | tail -1)" == 'compose -f docker-compose.base.yml up -d' ]] \
    || { cat "$TMP_DIR/failed-start.docker"; fail "rollback did not restart with the updated flags"; }
no_stale_restart failed-start "rollback"
pass "a failed restart rolls back with the updated flags"

# ── A rollback before the restart resolves the flags itself ──────────────────
# A failing migration rolls back with the flags resolved before the pull.
make_checkout failed-migration failing-migration
if run_update failed-migration; then
    fail "update must report the failed migration"
fi
grep -Fq 'Migration failed' "$TMP_DIR/failed-migration.out" \
    || { cat "$TMP_DIR/failed-migration.out"; fail "fixture migration did not fail"; }
grep -Fxq 'compose -f docker-compose.base.yml down --remove-orphans' "$TMP_DIR/failed-migration.docker" \
    && grep -Fxq 'compose -f docker-compose.base.yml up -d' "$TMP_DIR/failed-migration.docker" \
    || { cat "$TMP_DIR/failed-migration.docker"; fail "rollback did not restart with flags resolved from the current tree"; }
no_stale_restart failed-migration "rollback"
pass "a rollback after the pull resolves flags the current tree can start"

echo "All update compose-flag refresh tests passed."
