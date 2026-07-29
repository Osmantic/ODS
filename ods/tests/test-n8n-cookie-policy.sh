#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../extensions/services/n8n/n8n-cookie-policy.sh
source "$ROOT_DIR/extensions/services/n8n/n8n-cookie-policy.sh"

failures=0

assert_policy() {
    local expected="$1" setting="$2" protocol="$3" bind="$4" actual
    actual="$(ods_n8n_secure_cookie_policy "$setting" "$protocol" "$bind")"
    if [[ "$actual" != "$expected" ]]; then
        printf 'FAIL setting=%s protocol=%s bind=%s: expected %s, got %s\n' \
            "$setting" "$protocol" "$bind" "$expected" "$actual" >&2
        failures=$((failures + 1))
    fi
}

assert_policy false auto http 127.0.0.1
assert_policy false auto http localhost
assert_policy false auto http ::1
assert_policy false auto http '[::1]'
assert_policy true auto https 127.0.0.1
assert_policy true auto http 0.0.0.0
assert_policy true auto http 192.168.1.10
assert_policy true true http 127.0.0.1
assert_policy false false https 0.0.0.0

[[ "$(ods_n8n_public_protocol http https://n8n.example.com/)" == "https" ]] || {
    printf 'FAIL HTTPS webhook URL did not override the internal protocol\n' >&2
    failures=$((failures + 1))
}
[[ "$(ods_n8n_public_protocol http http://localhost:5678/)" == "http" ]] || {
    printf 'FAIL HTTP webhook URL was not recognized\n' >&2
    failures=$((failures + 1))
}

if ods_n8n_secure_cookie_policy sometimes http 127.0.0.1 >/dev/null 2>&1; then
    printf 'FAIL invalid policy was accepted\n' >&2
    failures=$((failures + 1))
fi

compose_file="$ROOT_DIR/extensions/services/n8n/compose.yaml"
expected_entrypoint='entrypoint: ["tini", "--", "/bin/sh", "/opt/ods/n8n-entrypoint.sh"]'
if ! grep -Fq "$expected_entrypoint" "$compose_file"; then
    printf 'FAIL n8n entrypoint must run through /bin/sh for Windows bind mounts\n' >&2
    failures=$((failures + 1))
fi

if ((failures > 0)); then
    printf '%d n8n cookie policy test(s) failed\n' "$failures" >&2
    exit 1
fi

printf 'n8n cookie policy tests passed\n'
