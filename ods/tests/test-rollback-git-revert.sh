#!/usr/bin/env bash
# Verifies that ods-update.sh rollback reverts the source tree, not just
# configuration:
#   - snapshot_pre_update records the pre-pull HEAD in snapshot.json
#   - `rollback` resets the checkout to that recorded revision
#   - legacy snapshots without git_head still restore configs only
#   - a snapshot carrying git_head in a non-git install fails loudly
# Hermetic: docker/curl are stubbed; the git repo is local commits only.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPDATE_SCRIPT="$ROOT_DIR/ods-update.sh"

fail() { echo "[FAIL] $*"; exit 1; }
pass() { echo "[PASS] $*"; }

command -v jq >/dev/null 2>&1 || fail "jq is required"
command -v git >/dev/null 2>&1 || fail "git is required"
[[ -f "$UPDATE_SCRIPT" ]] || fail "ods-update.sh not found"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

BIN_DIR="$TMP_DIR/bin"
mkdir -p "$BIN_DIR"

DOCKER_LOG=""
export DOCKER_LOG
cat > "$BIN_DIR/docker" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${DOCKER_LOG:?}"

if [[ "${1:-}" == "info" ]]; then
    exit 0
fi

if [[ "${1:-}" == "compose" && "${2:-}" == "version" ]]; then
    exit 0
fi

if [[ "${1:-}" == "compose" ]]; then
    shift
fi

args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
    if [[ "${args[$i]}" == "ps" ]]; then
        next="${args[$((i + 1))]:-}"
        if [[ "$next" == "--services" ]]; then
            printf '%s\n' dashboard-api litellm
            exit 0
        fi
        if [[ "$next" == "--format" ]]; then
            printf '%s\n' '{"State":"running"}'
            exit 0
        fi
    fi
done

exit 0
SH
chmod +x "$BIN_DIR/docker"

cat > "$BIN_DIR/curl" <<'SH'
#!/usr/bin/env bash
exit 0
SH
chmod +x "$BIN_DIR/curl"

# make_git_install <dir> — install dir that is a git checkout at repo root.
# ods-update.sh and a tracked marker file are committed so `git reset --hard`
# has something to revert.
make_git_install() {
    local dir="$1"
    mkdir -p "$dir/data/backups"
    cp "$UPDATE_SCRIPT" "$dir/ods-update.sh"
    chmod +x "$dir/ods-update.sh"

    cat > "$dir/.env" <<'EOF'
ODS_MODE=local
GPU_BACKEND=cpu
EOF
    echo '{"version":"old"}' > "$dir/.version"
    cat > "$dir/docker-compose.base.yml" <<'EOF'
services:
  dashboard-api:
    image: example/dashboard-api:test
EOF
    printf '%s\n' '-f docker-compose.base.yml' > "$dir/.compose-flags"
    echo "tracked-v1" > "$dir/tracked-marker.txt"

    git -C "$dir" init -q
    git -C "$dir" config user.email "test@example.com"
    git -C "$dir" config user.name "Test"
    git -C "$dir" add ods-update.sh tracked-marker.txt
    git -C "$dir" commit -qm "v1"
}

# run_snapshot <dir> — echo the snapshot dir created by snapshot_pre_update.
run_snapshot() {
    (cd "$1" && PATH="$BIN_DIR:$PATH" bash -c '
        source ./ods-update.sh help >/dev/null
        snapshot_pre_update 20260104-000000
    ' 2>/dev/null | tail -1)
}

# ── Scenario 1: snapshot records pre-pull HEAD ───────────────────────────────
INSTALL_A="$TMP_DIR/ods-a"
make_git_install "$INSTALL_A"
head_v1="$(git -C "$INSTALL_A" rev-parse HEAD)"

snap_a="$(run_snapshot "$INSTALL_A")"
[[ -n "$snap_a" && -f "$snap_a/snapshot.json" ]] \
    || fail "snapshot_pre_update did not produce snapshot.json"

recorded="$(jq -r '.git_head // empty' "$snap_a/snapshot.json")"
[[ "$recorded" == "$head_v1" ]] \
    || fail "snapshot.json git_head '$recorded' != pre-pull HEAD '$head_v1'"
pass "snapshot recorded pre-pull HEAD"

# ── Scenario 2: rollback reverts the source tree ─────────────────────────────
# Simulate the update pull: advance HEAD and change a tracked file.
echo "tracked-v2" > "$INSTALL_A/tracked-marker.txt"
git -C "$INSTALL_A" add tracked-marker.txt
git -C "$INSTALL_A" commit -qm "v2"
# Post-update config state that rollback must also undo.
sed -i 's/"old"/"new"/' "$INSTALL_A/.version"

DOCKER_LOG="$TMP_DIR/docker-a.log"; : > "$DOCKER_LOG"
PATH="$BIN_DIR:$PATH" HEALTH_TIMEOUT=30 \
    bash "$INSTALL_A/ods-update.sh" rollback 20260104-000000 \
    > "$TMP_DIR/rollback-a.out" 2>&1 \
    || { cat "$TMP_DIR/rollback-a.out"; fail "rollback should succeed"; }

now="$(git -C "$INSTALL_A" rev-parse HEAD)"
[[ "$now" == "$head_v1" ]] \
    || fail "rollback should reset HEAD to $head_v1, still at $now"
pass "rollback reverted source tree to pre-update commit"

grep -q 'tracked-v1' "$INSTALL_A/tracked-marker.txt" \
    || fail "tracked file content should revert to v1"
pass "tracked file content reverted"

grep -q '"old"' "$INSTALL_A/.version" \
    || fail ".version should be restored from the snapshot"
pass "config files still restored"

# ── Scenario 3: legacy snapshot without git_head skips the revert ────────────
INSTALL_B="$TMP_DIR/ods-b"
make_git_install "$INSTALL_B"
snap_b="$(run_snapshot "$INSTALL_B")"
# Strip git_head to emulate a snapshot from an older release.
jq 'del(.git_head)' "$snap_b/snapshot.json" > "$snap_b/snapshot.json.tmp"
mv "$snap_b/snapshot.json.tmp" "$snap_b/snapshot.json"
head_b="$(git -C "$INSTALL_B" rev-parse HEAD)"

DOCKER_LOG="$TMP_DIR/docker-b.log"; : > "$DOCKER_LOG"
PATH="$BIN_DIR:$PATH" HEALTH_TIMEOUT=30 \
    bash "$INSTALL_B/ods-update.sh" rollback 20260104-000000 \
    > "$TMP_DIR/rollback-b.out" 2>&1 \
    || { cat "$TMP_DIR/rollback-b.out"; fail "legacy rollback should succeed"; }

[[ "$(git -C "$INSTALL_B" rev-parse HEAD)" == "$head_b" ]] \
    || fail "legacy snapshot without git_head must not move HEAD"
pass "legacy snapshot: HEAD untouched, rollback still succeeds"

# ── Scenario 4: git_head recorded but install is not a checkout → loud fail ──
INSTALL_C="$TMP_DIR/ods-c"
make_git_install "$INSTALL_C"
snap_c="$(run_snapshot "$INSTALL_C")"
# Copy the snapshot into a non-git install dir.
INSTALL_D="$TMP_DIR/ods-d"
mkdir -p "$INSTALL_D/data/backups"
cp "$UPDATE_SCRIPT" "$INSTALL_D/ods-update.sh"; chmod +x "$INSTALL_D/ods-update.sh"
cp "$INSTALL_C/.env" "$INSTALL_C/.version" "$INSTALL_C/docker-compose.base.yml" "$INSTALL_D/"
printf '%s\n' '-f docker-compose.base.yml' > "$INSTALL_D/.compose-flags"
cp -a "$snap_c" "$INSTALL_D/data/backups/"

DOCKER_LOG="$TMP_DIR/docker-d.log"; : > "$DOCKER_LOG"
if PATH="$BIN_DIR:$PATH" HEALTH_TIMEOUT=30 \
    bash "$INSTALL_D/ods-update.sh" rollback 20260104-000000 \
    > "$TMP_DIR/rollback-d.out" 2>&1; then
    cat "$TMP_DIR/rollback-d.out"
    fail "rollback with git_head on a non-git install must fail"
fi
pass "git_head on non-git install fails loudly instead of claiming success"

echo ""
echo "All rollback git-revert tests passed."
