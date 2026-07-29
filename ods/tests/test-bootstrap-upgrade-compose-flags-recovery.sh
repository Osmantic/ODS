#!/usr/bin/env bash
# Regression: when .compose-flags is missing, the Docker restart path recovers
# it via resolve-compose-stack.sh and persists the result. The resolver reads
# neither GPU_COUNT nor ODS_MODE from the environment, so both must be passed
# explicitly -- otherwise the recovered cache pins a single-GPU, local-mode
# stack over a multi-GPU or cloud-mode install.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="$ROOT_DIR/scripts/bootstrap-upgrade.sh"

fail() {
    echo "[FAIL] $*" >&2
    if [[ -n "${tmp:-}" ]]; then
        [[ -f "$tmp/bootstrap.log" ]] && sed 's/^/[bootstrap] /' "$tmp/bootstrap.log" >&2
        [[ -f "${resolver_calls:-}" ]] && sed 's/^/[resolver] /' "$resolver_calls" >&2
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
resolver_calls="$tmp/resolver-calls.log"
mkdir -p "$fakebin" "$install_dir/data/models" "$install_dir/config/llama-server" "$install_dir/scripts"

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

# Docker is "up" with a running llama-server so the restart branch is taken.
# inspect reports a restarting container so the health wait aborts quickly.
cat > "$fakebin/docker" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
case "${1:-}" in
    info) exit 0 ;;
    compose) exit 0 ;;
    ps)
        if [[ " $* " == *"name=ods-llama-server"* ]]; then
            printf 'ods-llama-server\n'
        fi
        exit 0
        ;;
    inspect)
        if [[ "${2:-}" == "ods-llama-server" && " $* " == *".State.Status"* ]]; then
            printf 'restarting true 1\n'
        fi
        exit 0
        ;;
esac
exit 0
EOF
chmod +x "$fakebin/docker"

# Stub resolver: record argv, then emit an --env style payload.
cat > "$install_dir/scripts/resolve-compose-stack.sh" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$resolver_calls"
printf 'COMPOSE_FLAGS="-f docker-compose.base.yml -f docker-compose.multigpu-nvidia.yml"\n'
EOF
chmod +x "$install_dir/scripts/resolve-compose-stack.sh"

# Multi-GPU, cloud-mode install. Neither value reaches the resolver unless
# bootstrap-upgrade.sh forwards it explicitly.
cat > "$install_dir/.env" <<'EOF'
GGUF_FILE=Bootstrap.gguf
LLM_MODEL=bootstrap-model
MAX_CONTEXT=8192
CTX_SIZE=8192
GPU_BACKEND=nvidia
GPU_COUNT=2
ODS_MODE=cloud
TIER=3
OLLAMA_PORT=11434
EOF

cat > "$install_dir/config/llama-server/models.ini" <<'EOF'
[bootstrap-model]
filename = Bootstrap.gguf
load-on-startup = true
n-ctx = 8192
EOF

printf 'bootstrap\n' > "$install_dir/data/models/Bootstrap.gguf"
printf 'full-model\n' > "$install_dir/data/models/Full.gguf"

# No .compose-flags on purpose -- that is what triggers the recovery path.
[[ ! -f "$install_dir/.compose-flags" ]] || fail "fixture must not pre-create .compose-flags"

# The upgrade itself is expected to fail (the container never goes healthy);
# we only care that the recovery invoked the resolver correctly beforehand.
set +e
PATH="$fakebin:$PATH" bash "$TARGET" \
    "$install_dir" \
    "Full.gguf" \
    "https://example.invalid/Full.gguf" \
    "" \
    "full-model" \
    "32768" \
    "Bootstrap.gguf" \
    > "$tmp/bootstrap.log" 2>&1
set -e

[[ -s "$resolver_calls" ]] \
    || fail "recovery path never invoked resolve-compose-stack.sh"

grep -q -- '--gpu-count 2' "$resolver_calls" \
    || fail "resolver called without --gpu-count 2; multi-GPU overlays would be stripped from the persisted cache"

grep -q -- '--ods-mode cloud' "$resolver_calls" \
    || fail "resolver called without --ods-mode cloud; local-mode overlays would be forced onto a cloud install"

grep -q -- '--tier 3' "$resolver_calls" \
    || fail "resolver called without the installed tier"

grep -q -- '--gpu-backend nvidia' "$resolver_calls" \
    || fail "resolver called without the installed GPU backend"

[[ -f "$install_dir/.compose-flags" ]] \
    || fail "recovered compose flags were not persisted"

grep -q 'docker-compose.multigpu-nvidia.yml' "$install_dir/.compose-flags" \
    || fail "persisted .compose-flags dropped the resolver's multi-GPU overlay"

pass "compose-flags recovery forwards GPU_COUNT and ODS_MODE to the resolver"
