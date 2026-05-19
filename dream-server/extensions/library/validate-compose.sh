#!/usr/bin/env bash
set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
BOLD='\033[1m'
RESET='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICES_DIR="${SCRIPT_DIR}/services"

# Check docker availability
if ! command -v docker >/dev/null 2>&1; then
    printf "%bERROR%b: docker not found in PATH\n" "$RED" "$RESET" >&2
    exit 2
fi
if ! docker compose version >/dev/null 2>&1; then
    printf "%bERROR%b: docker compose plugin not available\n" "$RED" "$RESET" >&2
    exit 2
fi

if [ ! -d "$SERVICES_DIR" ]; then
    printf "%bERROR%b: services directory not found: %s\n" "$RED" "$RESET" "$SERVICES_DIR" >&2
    exit 2
fi

total=0
passed=0
failed=0

validate_compose() {
    local file="$1"
    local extra_file="${2:-}"

    if [ -n "$extra_file" ]; then
        docker compose -f "$file" -f "$extra_file" config --quiet
    else
        docker compose -f "$file" config --quiet
    fi
}

for service_dir in "${SERVICES_DIR}"/*/; do
    service_name="$(basename "$service_dir")"
    base_compose="${service_dir}compose.yaml"

    # Skip if no compose.yaml
    if [ ! -f "$base_compose" ]; then
        if [ -f "${base_compose}.disabled" ]; then
            printf "SKIP  %s (compose.yaml.disabled)\n" "$service_name"
        fi
        continue
    fi

    # Validate base compose
    total=$((total + 1))
    if validate_compose "$base_compose"; then
        printf "%bPASS%b  %s (base)\n" "$GREEN" "$RESET" "$service_name"
        passed=$((passed + 1))
    else
        printf "%bFAIL%b  %s (base)\n" "$RED" "$RESET" "$service_name"
        failed=$((failed + 1))
    fi

    # Validate nvidia overlay if present
    nvidia_overlay="${service_dir}compose.nvidia.yaml"
    if [ -f "$nvidia_overlay" ]; then
        total=$((total + 1))
        if validate_compose "$base_compose" "$nvidia_overlay"; then
            printf "%bPASS%b  %s (base + nvidia)\n" "$GREEN" "$RESET" "$service_name"
            passed=$((passed + 1))
        else
            printf "%bFAIL%b  %s (base + nvidia)\n" "$RED" "$RESET" "$service_name"
            failed=$((failed + 1))
        fi
    fi

    # Validate amd overlay if present
    amd_overlay="${service_dir}compose.amd.yaml"
    if [ -f "$amd_overlay" ]; then
        total=$((total + 1))
        if validate_compose "$base_compose" "$amd_overlay"; then
            printf "%bPASS%b  %s (base + amd)\n" "$GREEN" "$RESET" "$service_name"
            passed=$((passed + 1))
        else
            printf "%bFAIL%b  %s (base + amd)\n" "$RED" "$RESET" "$service_name"
            failed=$((failed + 1))
        fi
    fi
done

printf "\n%b========================================%b\n" "$BOLD" "$RESET"
printf "Total: %d  Passed: %d  Failed: %d\n" "$total" "$passed" "$failed"

if [ "$failed" -gt 0 ]; then
    exit 1
fi
exit 0
