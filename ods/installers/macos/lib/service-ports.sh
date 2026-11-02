#!/bin/bash
# Resolve selected public ports from the same Compose files/cwd as launch.
# Compose owns dotenv interpolation, environment precedence and overlay merging.
# Full configuration and parser diagnostics can contain secrets: keep the JSON
# in the pipe and emit only validated service/port pairs or a static error.
macos_load_service_ports() {
    local install_dir="$1" selected="$2" resolved service port
    shift 2
    MACOS_PORT_SERVICES=()
    MACOS_PORT_VALUES=()
    if ! resolved="$(
        cd "$install_dir" || exit 1
        set -o pipefail
        docker compose "$@" config --format json 2>/dev/null | python3 -c '
import json, re, sys
targets = {"dashboard": 3001, "dashboard-api": 3002, "open-webui": 8080,
           "litellm": 4000, "perplexica": 3000, "n8n": 5678}
try:
    services = json.load(sys.stdin)["services"]
    result = []
    for name in sys.argv[1].split():
        ports = set()
        for binding in services[name].get("ports", []):
            if binding.get("target") != targets[name] or binding.get("protocol", "tcp") != "tcp":
                continue
            # Callers probe IPv4 loopback, not a different interface.
            if binding.get("host_ip", "") not in ("", "0.0.0.0", "127.0.0.1"):
                continue
            value = str(binding.get("published", ""))
            if not re.fullmatch(r"[0-9]{1,5}", value) or not 1 <= int(value) <= 65535:
                raise ValueError()
            ports.add(int(value))
        if len(ports) != 1:
            raise ValueError()
        result.append(name + " " + str(ports.pop()))
    if not result:
        raise ValueError()
    sys.stdout.buffer.write(("\n".join(result) + "\n").encode("ascii"))
except (KeyError, TypeError, ValueError, AttributeError):
    sys.exit(1)
' "$selected" 2>/dev/null
    )"; then
        printf 'Cannot resolve selected service ports from Compose configuration.\n' >&2
        return 1
    fi
    while read -r service port; do
        MACOS_PORT_SERVICES+=("$service")
        MACOS_PORT_VALUES+=("$port")
    done <<< "$resolved"
}

macos_service_port() {
    local service="$1" index
    for ((index=0; index<${#MACOS_PORT_SERVICES[@]}; index++)); do
        if [[ "${MACOS_PORT_SERVICES[$index]}" == "$service" ]]; then
            printf '%s' "${MACOS_PORT_VALUES[$index]}"
            return 0
        fi
    done
    return 1
}
