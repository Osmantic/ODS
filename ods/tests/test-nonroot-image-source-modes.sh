#!/usr/bin/env bash
# Build the three non-root Python services from a private source tree. This
# reproduces installer runs started with umask 077 without changing host files.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
scratch="$(mktemp -d)"
images=()
cleanup() {
    local image
    for image in "${images[@]}"; do docker image rm "$image" >/dev/null 2>&1 || true; done
    rm -rf -- "$scratch"
}
trap cleanup EXIT
umask 077

for service in dashboard-api model-router pixel-model-relay; do
    mkdir -p "$scratch/$service"
    : > "$scratch/$service/requirements.lock"
done
mkdir -p "$scratch/dashboard-api/routers" "$scratch/model-router/app"
printf 'app = None\n' > "$scratch/dashboard-api/main.py"
printf '{}\n' > "$scratch/dashboard-api/performance_evidence.json"
printf '\n' > "$scratch/dashboard-api/routers/__init__.py"
printf 'app = None\n' > "$scratch/model-router/app/main.py"
printf '\n' > "$scratch/pixel-model-relay/relay.py"

[[ "$(stat -c %a "$scratch/dashboard-api/main.py")" == 600 ]]
[[ "$(stat -c %a "$scratch/model-router/app")" == 700 ]]

for service in dashboard-api model-router pixel-model-relay; do
    image="ods-nonroot-modes-${service}-$$"
    images+=("$image")
    docker build --quiet -t "$image" \
        -f "$root/extensions/services/$service/Dockerfile" "$scratch/$service" >/dev/null
    case "$service" in
        dashboard-api) uid=1000; entry=/app/main.py ;;
        model-router) uid=10777; entry=/srv/model-router/app/main.py ;;
        pixel-model-relay) uid=1000; entry=/app/relay.py ;;
    esac
    entry_dir="${entry%/*}"
    docker run --rm --entrypoint sh "$image" -c \
        "test \"\$(id -u)\" -eq $uid && test -r '$entry' && test -x '$entry_dir'"
    if [[ "$service" == dashboard-api ]]; then
        docker run --rm --entrypoint sh "$image" -c \
            'test -r /app/routers/__init__.py && test -x /app/routers'
    fi
    echo "PASS: $service reads private-source code as its runtime user"
done

[[ "$(stat -c %a "$scratch/dashboard-api/main.py")" == 600 ]]
[[ "$(stat -c %a "$scratch/dashboard-api/routers/__init__.py")" == 600 ]]
[[ "$(stat -c %a "$scratch/model-router/app")" == 700 ]]
echo 'PASS: source file and directory modes remain private on the host'
