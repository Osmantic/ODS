#!/usr/bin/env bash
set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
BOLD='\033[1m'
RESET='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICES_DIR="${SCRIPT_DIR}/services"
ODS_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ROOT_COMPOSE="${ODS_ROOT}/docker-compose.base.yml"

# Static validation needs non-secret values for variables that intentionally
# fail closed at runtime. Preserve caller-provided values when present.
export WEBUI_SECRET="${WEBUI_SECRET:-ci-placeholder}"
export ANYTHINGLLM_JWT_SECRET="${ANYTHINGLLM_JWT_SECRET:-ci-placeholder}"
export ANYTHINGLLM_AUTH_TOKEN="${ANYTHINGLLM_AUTH_TOKEN:-ci-placeholder}"
export FLOWISE_USERNAME="${FLOWISE_USERNAME:-ci-user}"
export FLOWISE_PASSWORD="${FLOWISE_PASSWORD:-ci-placeholder}"
export FRIGATE_RTSP_PASSWORD="${FRIGATE_RTSP_PASSWORD:-ci-placeholder}"
export OPEN_INTERPRETER_API_KEY="${OPEN_INTERPRETER_API_KEY:-ci-placeholder}"
export JUPYTER_TOKEN="${JUPYTER_TOKEN:-ci-placeholder}"
export PAPERLESS_SECRET_KEY="${PAPERLESS_SECRET_KEY:-ci-placeholder}"
export LIBRECHAT_MONGO_PASSWORD="${LIBRECHAT_MONGO_PASSWORD:-ci-placeholder}"
export CREDS_KEY="${CREDS_KEY:-ci-placeholder}"
export CREDS_IV="${CREDS_IV:-ci-placeholder}"
export JWT_SECRET="${JWT_SECRET:-ci-placeholder}"
export JWT_REFRESH_SECRET="${JWT_REFRESH_SECRET:-ci-placeholder}"
export LIBRECHAT_MEILI_KEY="${LIBRECHAT_MEILI_KEY:-ci-placeholder}"
export WEAVIATE_API_KEY="${WEAVIATE_API_KEY:-ci-placeholder}"
export LITELLM_KEY="${LITELLM_KEY:-ci-placeholder}"

# Check docker availability
if ! command -v docker >/dev/null 2>&1; then
    printf "%bERROR%b: docker not found in PATH\n" "$RED" "$RESET" >&2
    exit 2
fi
if ! docker compose version >/dev/null 2>&1; then
    printf "%bERROR%b: docker compose plugin not available\n" "$RED" "$RESET" >&2
    exit 2
fi
PYTHON_CMD="${PYTHON_CMD:-python3}"
if ! command -v "$PYTHON_CMD" >/dev/null 2>&1; then
    PYTHON_CMD="python"
fi
if ! command -v "$PYTHON_CMD" >/dev/null 2>&1 || ! "$PYTHON_CMD" -c 'import yaml' >/dev/null 2>&1; then
    printf "%bERROR%b: Python with PyYAML is required to resolve catalog dependencies\n" "$RED" "$RESET" >&2
    exit 2
fi

if [ ! -d "$SERVICES_DIR" ]; then
    printf "%bERROR%b: services directory not found: %s\n" "$RED" "$RESET" "$SERVICES_DIR" >&2
    exit 2
fi
if [ ! -f "$ROOT_COMPOSE" ]; then
    printf "%bERROR%b: root compose file not found: %s\n" "$RED" "$RESET" "$ROOT_COMPOSE" >&2
    exit 2
fi

total=0
passed=0
failed=0

validate_compose() {
    local service_name="$1"
    local file="$2"
    local extra_file="${3:-}"
    local backend="${4:-}"
    local -a compose_args=(-f "$ROOT_COMPOSE")

    if [ -n "$backend" ]; then
        local root_overlay="${ODS_ROOT}/docker-compose.${backend}.yml"
        if [ "$backend" = "apple" ]; then
            root_overlay="${ODS_ROOT}/installers/macos/docker-compose.macos.yml"
        fi
        if [ ! -f "$root_overlay" ]; then
            printf "Missing root %s overlay: %s\n" "$backend" "$root_overlay" >&2
            return 1
        fi
        compose_args+=(-f "$root_overlay")
    fi

    local dependencies
    if ! dependencies="$("$PYTHON_CMD" - "$SERVICES_DIR" "$service_name" <<'PY'
import pathlib
import sys

import yaml

services_dir = pathlib.Path(sys.argv[1])
root_service = sys.argv[2]
visited = set()
visiting = set()
ordered = []


def visit(service_id):
    if service_id in visited:
        return
    if service_id in visiting:
        raise SystemExit(f"catalog dependency cycle involving {service_id}")
    service_dir = services_dir / service_id
    manifest_path = service_dir / "manifest.yaml"
    if not manifest_path.is_file():
        return
    visiting.add(service_id)
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    dependencies = manifest.get("service", {}).get("depends_on", []) or []
    if not isinstance(dependencies, list):
        raise SystemExit(f"{service_id}: service.depends_on must be a list")
    for dependency in dependencies:
        if not isinstance(dependency, str) or not dependency:
            raise SystemExit(f"{service_id}: invalid dependency id")
        visit(dependency)
        dependency_compose = services_dir / dependency / "compose.yaml"
        if dependency_compose.is_file() and dependency_compose not in ordered:
            ordered.append(dependency_compose)
    visiting.remove(service_id)
    visited.add(service_id)


visit(root_service)
for compose_path in ordered:
    print(compose_path)
PY
    )"; then
        return 1
    fi

    local dependency_file
    while IFS= read -r dependency_file; do
        [ -n "$dependency_file" ] || continue
        compose_args+=(-f "$dependency_file")
        if [ -n "$backend" ]; then
            local dependency_overlay
            dependency_overlay="$(dirname "$dependency_file")/compose.${backend}.yaml"
            if [ -f "$dependency_overlay" ]; then
                compose_args+=(-f "$dependency_overlay")
            fi
        fi
    done <<< "$dependencies"
    compose_args+=(-f "$file")
    if [ -n "$extra_file" ]; then
        compose_args+=(-f "$extra_file")
    fi
    docker compose "${compose_args[@]}" config --quiet
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
    if validate_compose "$service_name" "$base_compose"; then
        printf "%bPASS%b  %s (base)\n" "$GREEN" "$RESET" "$service_name"
        passed=$((passed + 1))
    else
        printf "%bFAIL%b  %s (base)\n" "$RED" "$RESET" "$service_name"
        failed=$((failed + 1))
    fi

    # Validate every backend overlay in the same root/backend/extension order
    # used by the runtime compose resolver.
    for backend_overlay in "${service_dir}"compose.*.yaml; do
        if [ ! -f "$backend_overlay" ]; then
            continue
        fi
        backend="$(basename "$backend_overlay")"
        backend="${backend#compose.}"
        backend="${backend%.yaml}"
        total=$((total + 1))
        if validate_compose "$service_name" "$base_compose" "$backend_overlay" "$backend"; then
            printf "%bPASS%b  %s (base + %s)\n" "$GREEN" "$RESET" "$service_name" "$backend"
            passed=$((passed + 1))
        else
            printf "%bFAIL%b  %s (base + %s)\n" "$RED" "$RESET" "$service_name" "$backend"
            failed=$((failed + 1))
        fi
    done
done

printf "\n%b========================================%b\n" "$BOLD" "$RESET"
printf "Total: %d  Passed: %d  Failed: %d\n" "$total" "$passed" "$failed"

if [ "$failed" -gt 0 ]; then
    exit 1
fi
exit 0
