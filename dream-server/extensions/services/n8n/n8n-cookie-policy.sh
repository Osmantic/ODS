#!/bin/sh

# Resolve n8n's session-cookie policy from the public transport and host bind.
# The caller remains responsible for exporting the returned value.
dream_n8n_secure_cookie_policy() {
    _dream_cookie_setting="${1:-auto}"
    _dream_protocol="${2:-http}"
    _dream_bind="${3:-127.0.0.1}"

    case "$_dream_cookie_setting" in
        true|false)
            printf '%s\n' "$_dream_cookie_setting"
            return 0
            ;;
        auto|"")
            ;;
        *)
            printf 'Invalid N8N_SECURE_COOKIE value: %s (expected auto, true, or false)\n' \
                "$_dream_cookie_setting" >&2
            return 64
            ;;
    esac

    case "$_dream_protocol:$_dream_bind" in
        http:127.0.0.1|http:localhost|http:::1|http:\[::1\])
            printf 'false\n'
            ;;
        *)
            printf 'true\n'
            ;;
    esac
}

dream_n8n_is_loopback_bind() {
    case "${1:-}" in
        127.0.0.1|localhost|::1|\[::1\]) return 0 ;;
        *) return 1 ;;
    esac
}

dream_n8n_public_protocol() {
    case "${2:-}" in
        https://*) printf 'https\n' ;;
        http://*) printf 'http\n' ;;
        *) printf '%s\n' "${1:-http}" ;;
    esac
}
