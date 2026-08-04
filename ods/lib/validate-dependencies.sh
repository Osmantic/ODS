#!/bin/bash
# validate-dependencies.sh - Service dependency validation
# Part of: lib/
# Purpose: Validate service dependencies before compose up
#
# Expects: SERVICE_IDS, SERVICE_DEPENDS, SERVICE_COMPOSE (from service-registry.sh)
# Provides: validate_service_dependencies()

# Validate that all service dependencies are satisfied
# Returns 0 if all dependencies are met, 1 if any are missing
validate_service_dependencies() {
    local errors=0
    local warnings=0

    # Build list of enabled services (have compose files)
    local -A enabled_services
    for sid in "${SERVICE_IDS[@]}"; do
        local cf="${SERVICE_COMPOSE[$sid]}"
        if [[ -n "$cf" && -f "$cf" ]]; then
            enabled_services[$sid]=1
        fi
    done

    # Core services defined in docker-compose.base.yml are always enabled
    # (they have no extension manifest, so the registry does not know about it).
    #
    # Read only the keys under `services:`. The previous sed matched every
    # two-space-indented key in the file, so anchors and top-level sections
    # leaked in as "services": today that is `driver` and `options` (from
    # `x-logging`) and `default` (from `networks`). Any name that appears in
    # one of those blocks and also names a real extension would satisfy a
    # depends_on entry for a service that is not actually enabled, which is
    # the failure this function exists to catch.
    local _base_compose="${INSTALL_DIR:-$SCRIPT_DIR}/docker-compose.base.yml"
    if [[ -f "$_base_compose" ]]; then
        local _svc
        while IFS= read -r _svc; do
            [[ -n "$_svc" ]] && enabled_services[$_svc]=1
        done < <(awk '
            /^[^[:space:]#]/ { in_services = ($0 ~ /^services:/); next }
            in_services && /^  [a-z][a-z0-9_-]*:/ {
                line = $0
                sub(/^  /, "", line)
                sub(/:.*$/, "", line)
                print line
            }
        ' "$_base_compose" 2>/dev/null)
    fi

    # Check each enabled service's dependencies
    for sid in "${SERVICE_IDS[@]}"; do
        [[ -z "${enabled_services[$sid]:-}" ]] && continue

        local deps="${SERVICE_DEPENDS[$sid]:-}"
        [[ -z "$deps" ]] && continue

        # Parse space-separated dependency list
        for dep in $deps; do
            if [[ -z "${enabled_services[$dep]:-}" ]]; then
                echo "ERROR: Service '$sid' depends on '$dep', but '$dep' is not enabled" >&2
                errors=$((errors + 1))
            fi
        done
    done

    if [[ $errors -gt 0 ]]; then
        echo "" >&2
        echo "Dependency validation failed: $errors missing dependencies" >&2
        echo "Fix by enabling required services or disabling dependent services" >&2
        return 1
    fi

    return 0
}
