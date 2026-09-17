#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

command -v git >/dev/null 2>&1 || fail "git is required"
command -v jq >/dev/null 2>&1 || fail "jq is required"

TEST_DIR="$(mktemp -d)"
trap 'rm -rf "$TEST_DIR"' EXIT

REMOTE="$TEST_DIR/remote.git"
SEED="$TEST_DIR/seed"
INSTALL="$TEST_DIR/install"
TEST_HOME="$TEST_DIR/home"
mkdir -p "$SEED" "$TEST_HOME"

git init --bare --quiet "$REMOTE"
git -C "$SEED" init --quiet
git -C "$SEED" config user.name "ODS Update Test"
git -C "$SEED" config user.email "ods-update-test@example.invalid"
git -C "$SEED" branch -M main
git -C "$SEED" remote add origin "$REMOTE"

cp "$ROOT_DIR/ods-update.sh" "$SEED/ods-update.sh"
chmod +x "$SEED/ods-update.sh"
cat > "$SEED/.env" <<'EOF'
GPU_BACKEND=cpu
GPU_COUNT=1
TIER=1
DASHBOARD_API_PORT=3002
OLLAMA_PORT=8080
EOF
cat > "$SEED/.version" <<'EOF'
{"version":"before-token-migration"}
EOF
cat > "$SEED/docker-compose.yml" <<'EOF'
services:
  hermes:
    image: busybox:1.36
EOF
git -C "$SEED" add .
git -C "$SEED" commit --quiet -m "old source checkout"
git -C "$SEED" push --quiet -u origin main
git --git-dir="$REMOTE" symbolic-ref HEAD refs/heads/main
git clone --quiet "$REMOTE" "$INSTALL"

# Simulate the source release that first makes the Hermes dashboard token a
# required Compose input. The checked-out update manager must migrate .env
# after pulling this commit and before it asks Compose to recreate services.
cat > "$SEED/docker-compose.yml" <<'EOF'
services:
  hermes:
    image: busybox:1.36
    environment:
      - HERMES_DASHBOARD_SESSION_TOKEN=${HERMES_DASHBOARD_SESSION_TOKEN:?source update must migrate the Hermes dashboard token}
EOF
git -C "$SEED" add docker-compose.yml
git -C "$SEED" commit --quiet -m "require Hermes dashboard session token"
git -C "$SEED" push --quiet

STUB_BIN="$TEST_DIR/bin"
DOCKER_LOG="$TEST_DIR/docker.log"
mkdir -p "$STUB_BIN"
export DOCKER_LOG

cat > "$STUB_BIN/docker" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
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

command=""
for argument in "$@"; do
    case "$argument" in
        down|up|ps)
            command="$argument"
            break
            ;;
    esac
done

case "$command" in
    down)
        exit 0
        ;;
    up)
        token="$(awk -F= '$1 == "HERMES_DASHBOARD_SESSION_TOKEN" { value = $2 } END { print value }' .env)"
        [[ "$token" =~ ^[0-9a-f]{64}$ ]]
        ;;
    ps)
        if [[ " $* " == *" --services "* ]]; then
            printf '%s\n' hermes
        else
            printf '%s\n' '{"State":"running"}'
        fi
        ;;
    *)
        exit 0
        ;;
esac
SH
chmod +x "$STUB_BIN/docker"

cat > "$STUB_BIN/docker-compose" <<'SH'
#!/usr/bin/env bash
exec docker compose "$@"
SH
chmod +x "$STUB_BIN/docker-compose"

cat > "$STUB_BIN/curl" <<'SH'
#!/usr/bin/env bash
exit 0
SH
chmod +x "$STUB_BIN/curl"

UPDATE_OUTPUT="$TEST_DIR/update.out"
if ! (
    cd "$INSTALL"
    HOME="$TEST_HOME" HEALTH_TIMEOUT=1 PATH="$STUB_BIN:$PATH" \
        bash ./ods-update.sh update
) > "$UPDATE_OUTPUT" 2>&1; then
    cat "$UPDATE_OUTPUT" >&2
    fail "source update did not migrate the required Hermes dashboard token"
fi

token="$(awk -F= '$1 == "HERMES_DASHBOARD_SESSION_TOKEN" { value = $2 } END { print value }' "$INSTALL/.env")"
[[ "$token" =~ ^[0-9a-f]{64}$ ]] || fail "source update wrote an invalid Hermes dashboard token"
[[ "$(grep -c '^HERMES_DASHBOARD_SESSION_TOKEN=' "$INSTALL/.env")" -eq 1 ]] \
    || fail "source update left duplicate Hermes dashboard token assignments"
grep -q 'compose .*up -d' "$DOCKER_LOG" || fail "updated stack was not recreated"
pass "source update migrates the Hermes dashboard token before Compose restart"

# A later source update must preserve the established browser-session token.
printf 'next release\n' > "$SEED/release-marker"
git -C "$SEED" add release-marker
git -C "$SEED" commit --quiet -m "next source release"
git -C "$SEED" push --quiet
if ! (
    cd "$INSTALL"
    HOME="$TEST_HOME" HEALTH_TIMEOUT=1 PATH="$STUB_BIN:$PATH" \
        bash ./ods-update.sh update
) > "$UPDATE_OUTPUT" 2>&1; then
    cat "$UPDATE_OUTPUT" >&2
    fail "subsequent source update failed"
fi

token_after="$(awk -F= '$1 == "HERMES_DASHBOARD_SESSION_TOKEN" { value = $2 } END { print value }' "$INSTALL/.env")"
[[ "$token_after" == "$token" ]] || fail "source update rotated an existing Hermes dashboard token"
pass "subsequent source updates preserve the existing Hermes dashboard token"
