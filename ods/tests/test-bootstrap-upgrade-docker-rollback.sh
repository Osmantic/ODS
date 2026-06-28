#!/usr/bin/env bash
# Regression: Docker full-model hot-swap failures must restore the last
# known-good model config and recreate llama-server from that config.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="$ROOT_DIR/scripts/bootstrap-upgrade.sh"

fail() {
    echo "[FAIL] $*" >&2
    if [[ -n "${tmp:-}" ]]; then
        [[ -f "$tmp/bootstrap.log" ]] && sed 's/^/[bootstrap] /' "$tmp/bootstrap.log" >&2
        [[ -f "${install_dir:-}/.env" ]] && sed 's/^/[env] /' "$install_dir/.env" >&2
        [[ -f "${install_dir:-}/config/llama-server/models.ini" ]] && sed 's/^/[models.ini] /' "$install_dir/config/llama-server/models.ini" >&2
        [[ -f "${docker_calls:-}" ]] && sed 's/^/[docker] /' "$docker_calls" >&2
    fi
    exit 1
}

pass() {
    echo "[PASS] $*"
}

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

fakebin="$tmp/bin"
install_dir="$tmp/install"
docker_calls="$tmp/docker-calls.log"
mkdir -p "$fakebin" "$install_dir/data/models" "$install_dir/config/llama-server"

cat > "$fakebin/curl" <<'EOF'
#!/usr/bin/env bash
case " $* " in
  *" -sI "*)
    printf 'HTTP/2 200\r\ncontent-length: 10\r\n\r\n'
    exit 0
    ;;
esac
exit 7
EOF
chmod +x "$fakebin/curl"

cat > "$fakebin/sleep" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$fakebin/sleep"

cat > "$fakebin/uname" <<'EOF'
#!/usr/bin/env bash
printf 'Linux\n'
EOF
chmod +x "$fakebin/uname"

cat > "$fakebin/docker" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

env_value() {
    local key="$1"
    [[ -f .env ]] || return 0
    grep -E "^${key}=" .env 2>/dev/null | head -1 | cut -d= -f2-
}

case "${1:-}" in
    info)
        exit 0
        ;;
    compose)
        if [[ "${2:-}" == "version" ]]; then
            exit 0
        fi
        if [[ " $* " == *" up -d --force-recreate --no-deps llama-server "* ]]; then
            printf 'compose-up:%s\n' "$(env_value GGUF_FILE)" >> "${ODS_FAKE_DOCKER_LOG:?}"
            exit 0
        fi
        exit 0
        ;;
    ps)
        if [[ " $* " == *"name=ods-llama-server"* ]]; then
            printf 'ods-llama-server\n'
        fi
        exit 0
        ;;
    inspect)
        if [[ "${2:-}" == "ods-llama-server" ]]; then
            printf '/app/llama-server --model /models/%s --ctx-size %s\n' \
                "$(env_value GGUF_FILE)" "$(env_value CTX_SIZE)"
        fi
        exit 0
        ;;
esac

exit 0
EOF
chmod +x "$fakebin/docker"

cat > "$install_dir/.env" <<'EOF'
GGUF_FILE=Bootstrap.gguf
LLM_MODEL=bootstrap-model
MAX_CONTEXT=8192
CTX_SIZE=8192
GPU_BACKEND=nvidia
OLLAMA_PORT=11434
EOF

cat > "$install_dir/config/llama-server/models.ini" <<'EOF'
[bootstrap-model]
filename = Bootstrap.gguf
load-on-startup = true
n-ctx = 8192
EOF

cat > "$install_dir/.compose-flags" <<'EOF'
-f docker-compose.base.yml -f docker-compose.nvidia.yml
EOF

printf 'bootstrap\n' > "$install_dir/data/models/Bootstrap.gguf"
printf 'full-model\n' > "$install_dir/data/models/Full.gguf"

set +e
PATH="$fakebin:$PATH" ODS_FAKE_DOCKER_LOG="$docker_calls" bash "$TARGET" \
    "$install_dir" \
    "Full.gguf" \
    "https://example.invalid/Full.gguf" \
    "" \
    "full-model" \
    "32768" \
    "Bootstrap.gguf" \
    > "$tmp/bootstrap.log" 2>&1
rc=$?
set -e

[[ $rc -ne 0 ]] || fail "bootstrap-upgrade must fail when Docker llama-server never becomes healthy"
grep -q '^GGUF_FILE=Bootstrap.gguf$' "$install_dir/.env" \
    || fail "Docker hot-swap failure must restore previous GGUF_FILE"
grep -q '^LLM_MODEL=bootstrap-model$' "$install_dir/.env" \
    || fail "Docker hot-swap failure must restore previous LLM_MODEL"
grep -q '^CTX_SIZE=8192$' "$install_dir/.env" \
    || fail "Docker hot-swap failure must restore previous CTX_SIZE"
grep -q 'filename = Bootstrap.gguf' "$install_dir/config/llama-server/models.ini" \
    || fail "Docker hot-swap failure must restore previous models.ini"
grep -q 'compose-up:Full.gguf' "$docker_calls" \
    || fail "test did not exercise the full-model compose recreate"
grep -q 'compose-up:Bootstrap.gguf' "$docker_calls" \
    || fail "rollback must recreate llama-server from the restored bootstrap config"
grep -q 'Restoring previous active model config after Docker llama-server swap failure' "$tmp/bootstrap.log" \
    || fail "bootstrap-upgrade should log the Docker rollback"
grep -q '"status": "failed"' "$install_dir/data/bootstrap-status.json" \
    || fail "failed Docker hot-swap must mark bootstrap-status failed"

pass "Docker hot-swap failure restores previous active model config"
