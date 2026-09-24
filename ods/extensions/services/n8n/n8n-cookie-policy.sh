#!/bin/sh

ods_n8n_is_loopback_bind() {
    case "${1:-}" in
        127.0.0.1|localhost|::1|\[::1\]|0:0:0:0:0:0:0:1|\[0:0:0:0:0:0:0:1\]) return 0 ;;
        *) return 1 ;;
    esac
}

# Resolve n8n's session-cookie policy from the public transport and host bind.
# The caller remains responsible for exporting the returned value.
ods_n8n_secure_cookie_policy() {
    _ods_cookie_setting="${1:-auto}"
    _ods_protocol="${2:-http}"
    _ods_bind="${3:-127.0.0.1}"

    case "$_ods_cookie_setting" in
        true|false)
            printf '%s\n' "$_ods_cookie_setting"
            return 0
            ;;
        auto|"")
            ;;
        *)
            printf 'Invalid N8N_SECURE_COOKIE value: %s (expected auto, true, or false)\n' \
                "$_ods_cookie_setting" >&2
            return 64
            ;;
    esac

    if [ "$_ods_protocol" = "http" ] && ods_n8n_is_loopback_bind "$_ods_bind"; then
        printf 'false\n'
        return 0
    fi
    printf 'true\n'
}

ods_n8n_public_protocol() {
    case "${2:-}" in
        https://*) printf 'https\n' ;;
        http://*) printf 'http\n' ;;
        *) printf '%s\n' "${1:-http}" ;;
    esac
}
