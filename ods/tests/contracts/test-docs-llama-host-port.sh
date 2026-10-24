#!/usr/bin/env bash
# Contract: owner-facing docs for Docker-based installs (Linux, WSL2, Windows
# Docker) send owners to llama-server's published host port, not its
# container port.
#
# docker-compose.base.yml publishes llama-server as
# 127.0.0.1:${OLLAMA_PORT:-11434}:8080. Port 8080 exists only inside the
# container (and on native macOS / Windows AMD hosts), so a host-side
# curl to localhost port 8080, or an SDK base URL on that port, is refused.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FAILURES=0

fail() {
    echo "[FAIL] $*" >&2
    FAILURES=$((FAILURES + 1))
}

pass() {
    echo "[PASS] $*"
}

docs=(
    "$ROOT_DIR/docs/INTEGRATION-GUIDE.md"
    "$ROOT_DIR/docs/WINDOWS-TROUBLESHOOTING-GUIDE.md"
    "$ROOT_DIR/docs/WSL2-GPU-TROUBLESHOOTING.md"
    "$ROOT_DIR/docs/M1-OFFLINE-MODE.md"
    "$ROOT_DIR/extensions/services/llama-server/README.md"
    "$ROOT_DIR/extensions/services/open-webui/README.md"
)

published="$(sed -nE 's/.*"127\.0\.0\.1:\$\{OLLAMA_PORT:-([0-9]+)\}:8080".*/\1/p' "$ROOT_DIR/docker-compose.base.yml")"
if [[ "$published" == 11434 ]]; then
    pass "docker-compose.base.yml publishes llama-server on host port 11434"
else
    fail "docker-compose.base.yml llama-server host port is '${published:-missing}', expected 11434"
fi

hits="$(grep -nE '(localhost|127\.0\.0\.1):8080\b' "${docs[@]}" || true)"
if [[ -z "$hits" ]]; then
    pass "owner-facing llama-server docs use the published host port"
else
    fail "owner-facing docs send host clients to container port 8080:"
    echo "$hits" >&2
fi

for doc in "${docs[@]}"; do
    grep -q 'localhost:11434' "$doc" || fail "${doc#"$ROOT_DIR"/} no longer shows the host port 11434"
done
[[ "$FAILURES" -gt 0 ]] || pass "each doc shows llama-server at localhost:11434"

exit "$FAILURES"
