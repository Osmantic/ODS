#!/usr/bin/env bash
set -euo pipefail

# macOS preserves operator port overrides in .env across reruns (the env
# generator upserts rather than rewriting), but the post-install health
# checks and the readiness summary still probe hardcoded ports. An install
# with WEBUI_PORT=4000 in .env reports "Chat UI unhealthy" while the service
# is perfectly fine on 4000 — and prints wrong URLs in the final summary.
#
# Contract: every post-install probe/print resolves the port from
# $INSTALL_DIR/.env via read_env_value, falling back to the documented
# default — the same pattern already used for the native llama port.

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
script="$root/installers/macos/install-macos.sh"

fail() {
    printf '[FAIL] %s\n' "$1" >&2
    exit 1
}

# The resolver must read .env (not process env only).
grep -Fq 'read_env_value "$INSTALL_DIR/.env"' "$script" \
    || fail "health port resolver does not read .env"

for key in WEBUI_PORT N8N_PORT WHISPER_PORT LITELLM_PORT DASHBOARD_PORT DASHBOARD_API_PORT PERPLEXICA_PORT; do
    grep -Eq "_health_env_port ${key} [0-9]+" "$script" \
        || fail "macOS health/summary never resolves $key from .env"
done

# No literal health-probe port remains for the customizable services.
for literal in '127.0.0.1:3000' '127.0.0.1:3001' '127.0.0.1:3002' '127.0.0.1:3004' '127.0.0.1:4000/health' '127.0.0.1:5678'; do
    if grep -Fq "$literal" "$script"; then
        fail "literal post-install port still present: $literal"
    fi
done
if grep -Fq 'localhost:3001' "$script" || grep -Fq 'localhost:3000' "$script"; then
    fail "readiness summary still prints literal dashboard/webui port"
fi

tr -d '\r' < "$script" | bash -n

echo 'macOS post-install port resolution contract passed'
