#!/bin/sh
# Purpose: validate the native space-separated auth contract before opening listeners.
# Expects: MAILPIT_UI_PASSWORD and MAILPIT_SMTP_PASSWORD from the installed Compose recipe.
# Provides: separate inbox/API and SMTP credentials for the fixed ods account.
# Modder notes: retain validation if changing the native Mailpit authentication encoding.
set -eu

validate_password() {
    case "$2" in
        ''|*[!A-Za-z0-9_-]*)
            printf '%s must use only letters, digits, underscores or hyphens\n' "$1" >&2
            exit 1
            ;;
    esac
    if [ "${#2}" -lt 16 ] || [ "${#2}" -gt 128 ]; then
        printf '%s must contain 16 to 128 characters\n' "$1" >&2
        exit 1
    fi
}

validate_password MAILPIT_UI_PASSWORD "${MAILPIT_UI_PASSWORD:-}"
validate_password MAILPIT_SMTP_PASSWORD "${MAILPIT_SMTP_PASSWORD:-}"
export MP_UI_AUTH="ods:$MAILPIT_UI_PASSWORD"
export MP_SMTP_AUTH="ods:$MAILPIT_SMTP_PASSWORD"
unset MAILPIT_UI_PASSWORD MAILPIT_SMTP_PASSWORD
exec /mailpit
