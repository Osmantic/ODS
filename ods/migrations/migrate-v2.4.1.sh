#!/bin/bash
# Migration: pre-PR-#1069 → SHIELD_API_KEY present
# Description: Backfill SHIELD_API_KEY when missing so the dashboard
#              Privacy Shield stats panel works after upgrade. Existing
#              installs predating PR #1069 have no SHIELD_API_KEY in .env;
#              without it dashboard-api's authenticated /stats proxy
#              short-circuits and the UI shows a config error.
# Date: 2026-05-01
# Idempotent: only writes when the key is absent or empty.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${INSTALL_DIR:-$SCRIPT_DIR/..}"
ENV_FILE="${INSTALL_DIR}/.env"

[[ -f "$ENV_FILE" ]] || { echo "Migration v2.4.1: no .env at $ENV_FILE — skipping"; exit 0; }

# Skip when ANY SHIELD_API_KEY line already carries a non-empty value.
# Reading only the first line mishandles duplicates: an empty line shadowed by
# a later real value would pass the old check, and the awk rewrite below would
# then clobber the real key with a fresh random one.
if ! grep -qE '^SHIELD_API_KEY=[^[:space:]]' "$ENV_FILE" 2>/dev/null; then
    # od is POSIX and always available; xxd is not. Whatever produced the key,
    # it must be 64 hex chars — a silent empty key would leave Privacy Shield
    # authentication broken while reporting migration success.
    new_key=$(openssl rand -hex 32 2>/dev/null || od -An -tx1 -N32 /dev/urandom 2>/dev/null | tr -d ' \n')
    if [[ ! "$new_key" =~ ^[0-9a-f]{64}$ ]]; then
        echo "Migration v2.4.1: could not generate a SHIELD_API_KEY (no usable random source) — aborting" >&2
        exit 1
    fi
    if grep -qE '^SHIELD_API_KEY=' "$ENV_FILE" 2>/dev/null; then
        # Update empty value in place. Use awk to dodge sed delimiter pitfalls.
        awk -v v="$new_key" '
            { if (index($0, "SHIELD_API_KEY=") == 1) print "SHIELD_API_KEY=" v; else print }
        ' "$ENV_FILE" > "${ENV_FILE}.tmp" && cat "${ENV_FILE}.tmp" > "$ENV_FILE" && rm -f "${ENV_FILE}.tmp"
    else
        echo "" >> "$ENV_FILE"
        echo "# Privacy Shield cross-service auth (PR #1069)" >> "$ENV_FILE"
        echo "SHIELD_API_KEY=${new_key}" >> "$ENV_FILE"
    fi
    echo "Added SHIELD_API_KEY to .env"
fi

echo "Migration v2.4.1 complete"
