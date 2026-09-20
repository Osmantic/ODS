#!/usr/bin/env bash
# Regression: dashboard entrypoint must inject API keys containing sed
# metacharacters (notably the '|' substitution delimiter) verbatim (#2926).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENTRYPOINT="$ROOT/extensions/services/dashboard/entrypoint.sh"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# Stub nginx so the entrypoint's final `exec nginx` is a no-op.
mkdir -p "$tmp/bin"
printf '#!/bin/sh\nexit 0\n' > "$tmp/bin/nginx"
chmod +x "$tmp/bin/nginx"

# The entrypoint runs in Alpine, where `sed -i` takes no suffix argument.
# On BSD/macOS shim in gsed so the same script is exercised unmodified.
if ! sed --version >/dev/null 2>&1; then
    if command -v gsed >/dev/null 2>&1; then
        ln -s "$(command -v gsed)" "$tmp/bin/sed"
    else
        echo "SKIP: GNU sed (or gsed) required to exercise the entrypoint"
        exit 0
    fi
fi

fail=0
check_key() {
    local key="$1" conf="$tmp/nginx.conf"
    # shellcheck disable=SC2016  # the literal placeholder is the point
    printf '%s\n' '        proxy_set_header Authorization "Bearer ${DASHBOARD_API_KEY}";' > "$conf"

    PATH="$tmp/bin:$PATH" NGINX_CONF="$conf" DASHBOARD_API_KEY="$key" \
        sh "$ENTRYPOINT" >/dev/null

    local expected="        proxy_set_header Authorization \"Bearer ${key}\";"
    if [ "$(cat "$conf")" = "$expected" ]; then
        echo "PASS: key '$key' injected verbatim"
    else
        echo "FAIL: key '$key' -> $(cat "$conf")"
        fail=1
    fi
}

check_key 'plainkey123'
check_key 'aa|bb'
check_key 'a&b\c|d'

[ "$fail" -eq 0 ] || exit 1
echo "All dashboard entrypoint key-injection tests passed"
