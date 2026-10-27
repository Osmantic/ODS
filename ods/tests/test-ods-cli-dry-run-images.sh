#!/usr/bin/env bash
# `ods update --dry-run` lists the images `ods update` would pull and the
# image IDs of the running containers.
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
PASS=0
FAIL=0

install="$tmp/install"
bin="$tmp/bin"
mkdir -p "$install/installers/lib" "$bin" "$tmp/home"
cp "$root_dir/docker-compose.base.yml" "$root_dir/manifest.json" "$install/"
cp "$root_dir/installers/lib/compose-images.sh" "$install/installers/lib/"
printf '%s\n' '-f docker-compose.base.yml' > "$install/.compose-flags"
cat > "$install/.env" <<'ENV'
ODS_VERSION=3.0.0
ODS_MODE=local
GPU_BACKEND=nvidia
GPU_COUNT=2
TIER=4
DASHBOARD_API_KEY=test-key
ENV

# Workstation06 (2026-10-27): the preview printed the API's list, which named
# every backend's images as raw ${VAR:-default} strings, and its digest column
# printed linux/arm64.
cat > "$bin/curl" <<'CURL'
#!/usr/bin/env bash
case "$*" in
    *api/update/dry-run*)
        printf '%s\n' '{"current_version":"3.0.0","latest_version":"3.0.0","update_available":false,"images":["${LLAMA_ARC_IMAGE:-ods-llama-sycl:local}","ghcr.io/ggml-org/llama.cpp:server-rocm-b9014"],"env_keys":{}}'
        ;;
    *) exit 7 ;;
esac
CURL
cat > "$bin/docker" <<'DOCKER'
#!/usr/bin/env bash
case " $* " in
    *' config --format json '*)
        printf '%s\n' '{"services":{"dashboard":{"image":"ods-dashboard:local"},"dashboard-api":{"build":{"context":"."},"image":"registry.example/dashboard-api:dev"},"llama-server":{"image":"ghcr.io/ggml-org/llama.cpp:server-cuda-b11429@sha256:aaaa"},"open-webui":{"image":"ghcr.io/open-webui/open-webui:v0.11.4@sha256:bbbb"}}}'
        ;;
    *' ps -q '*) printf 'c1\nc2\n' ;;
    *' inspect '*) printf '/ods-llama-server sha256:0123456789abcdef0000\n/ods-open-webui sha256:fedcba9876543210ffff\n' ;;
    *' images '*) printf 'CONTAINER REPOSITORY TAG PLATFORM IMAGE ID SIZE\nods-llama-server x y linux/arm64 0123 1GB\n' ;;
esac
exit 0
DOCKER
# Guard every host mutator; a preview must not need one.
for mutator in systemctl sudo pkill killall service launchctl; do
    printf '#!/bin/sh\nexit 97\n' > "$bin/$mutator"
done
chmod +x "$bin"/*

out=$(PATH="$bin:$PATH" HOME="$tmp/home" INSTALL_DIR="$install" ODS_HOME="$install" NO_COLOR=1 \
    bash "$root_dir/ods-cli" update --dry-run 2>&1)

images=$(sed -n '/Image tags (would be pulled)/,/Running image digests/p' <<< "$out")
digests=$(sed -n '/Running image digests/,/Model configuration/p' <<< "$out")

check() {
    local label="$1"
    shift
    if "$@"; then
        echo "  PASS: $label"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $label"
        FAIL=$((FAIL + 1))
    fi
}
has() { [[ "$1" == *"$2"* ]]; }
lacks() { [[ "$1" != *"$2"* ]]; }
matches() { [[ "$1" =~ $2 ]]; }

echo "=== ods update --dry-run lists what the update pulls ==="
check "lists the resolved llama-server image" has "$images" "ghcr.io/ggml-org/llama.cpp:server-cuda-b11429@sha256:aaaa"
check "lists the resolved Open WebUI image" has "$images" "ghcr.io/open-webui/open-webui:v0.11.4@sha256:bbbb"
check "skips local build tags" lacks "$images" "ods-dashboard:local"
check "skips services built from source" lacks "$images" "registry.example/dashboard-api"
check "does not print the API's unresolved list" lacks "$images" "LLAMA_ARC_IMAGE"
check "does not print other backends' images" lacks "$images" "server-rocm"
check "prints each running container's image ID" matches "$digests" "ods-llama-server[[:space:]]+0123456789ab"
check "does not print platforms as digests" lacks "$digests" "linux/arm64"

echo ""
echo "Passed: $PASS  Failed: $FAIL"
if (( FAIL > 0 )); then
    printf '%s\n' "$out"
    exit 1
fi
