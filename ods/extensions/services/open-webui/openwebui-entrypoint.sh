#!/bin/sh
set -eu

# Back up Open WebUI's database before a new Open WebUI version migrates it,
# and refuse a start that would leave it half-migrated (openwebui-prepare.py).
# Then run the image's own start command, which compose passes as arguments
# (`bash start.sh` in the image's working directory, /app/backend).
if [ "$#" -eq 0 ]; then
    echo "openwebui-entrypoint.sh: no start command given" >&2
    exit 64
fi

# Whether an address publishes beyond this machine. Loopback follows the host
# agent's rule: trimmed, unquoted, any case; empty means 127.0.0.1.
publishes_beyond_loopback() {
    case "$(printf '%s' "$1" | tr -d " \t\"'" | tr '[:upper:]' '[:lower:]')" in
        ''|127.0.0.1|::1|'[::1]'|localhost) return 1 ;;
        *) return 0 ;;
    esac
}

# Other devices reach Open WebUI when compose publishes it beyond loopback
# (ODS_WEBUI_BIND_ADDRESS, from BIND_ADDRESS in .env) or when the ODS proxy
# routes to it and is published beyond loopback (ODS_WEBUI_PROXY_BIND, set by
# the proxy's compose file only while the proxy is enabled).
reachable=""
if publishes_beyond_loopback "${ODS_WEBUI_BIND_ADDRESS:-}"; then
    reachable="published on ${ODS_WEBUI_BIND_ADDRESS}"
elif publishes_beyond_loopback "${ODS_WEBUI_PROXY_BIND:-}"; then
    reachable="reachable through the ODS proxy on ${ODS_WEBUI_PROXY_BIND}"
fi

if [ -n "$reachable" ]; then
    # With sign-in off, Open WebUI makes whoever reaches it its administrator.
    # Start with sign-in on, whatever WEBUI_AUTH says, so a recreate that
    # skipped the CLI's check (the Dashboard's update, a rollback, a plain
    # `docker compose up`) cannot expose it without sign-in. Open WebUI reads
    # sign-in as on only when WEBUI_AUTH is "true" in any case, or unset; an
    # empty value counts as off.
    if [ "$(printf '%s' "${WEBUI_AUTH-true}" | tr '[:upper:]' '[:lower:]')" != true ]; then
        echo "openwebui-entrypoint.sh: Open WebUI is $reachable, so it starts with sign-in on (WEBUI_AUTH=true)" >&2
        WEBUI_AUTH=true
        export WEBUI_AUTH
    fi
    # The step also refuses while the built-in administrator still has the
    # default password, which sign-in alone does not protect.
    python3 /opt/ods/openwebui-prepare.py --published-on-network
else
    python3 /opt/ods/openwebui-prepare.py
fi
exec "$@"
