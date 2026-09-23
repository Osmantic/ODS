#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
BUILDKIT_IMAGE='moby/buildkit@sha256:2f5adac4ecd194d9f8c10b7b5d7bceb5186853db1b26e5abd3a657af0b7e26ec'
WORKSPACE_PATCH_SHA256='50b5a02e83a419e6da309efb2f78580b72e5b04c57babf6d34854ef3d3fb6dbe'
BUILDER="pixel-dsv4-repro-${PPID}-$$"
TEMP_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/pixel-dsv4-repro.XXXXXXXX")
BUILDER_CREATED=false

usage() {
  printf 'Usage: %s --workspace-patch /absolute/owner-reviewed/workspace.py\n' "$0" >&2
  exit 64
}

cleanup() {
  if [[ "$BUILDER_CREATED" == true ]]; then docker buildx rm "$BUILDER" >/dev/null 2>&1 || true; fi
  case "$TEMP_ROOT" in
    "${TMPDIR:-/tmp}"/pixel-dsv4-repro.*) rm -rf -- "$TEMP_ROOT" ;;
    *) printf '%s\n' 'Refusing to remove an unexpected DSV4 qualification path.' >&2; return 1 ;;
  esac
}
trap cleanup EXIT INT TERM

[[ $# -eq 2 && "$1" == --workspace-patch ]] || usage
workspace_patch=$2
[[ "$workspace_patch" == /* ]] || { printf '%s\n' 'DSV4 workspace patch path must be absolute.' >&2; exit 64; }

for command in docker sha256sum cmp mktemp awk install readlink find touch; do
  command -v "$command" >/dev/null || { printf 'Missing qualification command: %s\n' "$command" >&2; exit 1; }
done
[[ -f "$workspace_patch" && ! -L "$workspace_patch" ]] || { printf '%s\n' 'DSV4 workspace patch must be one regular non-link file.' >&2; exit 1; }
resolved_patch=$(readlink -f -- "$workspace_patch")
[[ "$resolved_patch" == "$workspace_patch" ]] || { printf '%s\n' 'DSV4 workspace patch path must already be canonical.' >&2; exit 1; }
if [[ -n $(find "$workspace_patch" -maxdepth 0 -perm /022 -print -quit) ]]; then
  printf '%s\n' 'DSV4 workspace patch must not be group- or world-writable.' >&2
  exit 1
fi
printf '%s  %s\n' "$WORKSPACE_PATCH_SHA256" "$workspace_patch" | sha256sum -c - >/dev/null

context="$TEMP_ROOT/context"
install -d -m 0700 "$context"
install -m 0444 "$ROOT/deploy/agent-comparison/Dockerfile.dsv4-vllm" "$context/Dockerfile"
install -m 0555 "$ROOT/deploy/agent-comparison/dsv4-serve.sh" "$context/dsv4-serve.sh"
install -m 0444 "$workspace_patch" "$context/workspace.py"
touch -d '@0' "$context" "$context/Dockerfile" "$context/dsv4-serve.sh" "$context/workspace.py"
printf '%s  %s\n' "$WORKSPACE_PATCH_SHA256" "$context/workspace.py" | sha256sum -c - >/dev/null
context_identity=$(sha256sum "$context/Dockerfile" "$context/dsv4-serve.sh" "$context/workspace.py")

docker buildx version >/dev/null
docker buildx inspect "$BUILDER" >/dev/null 2>&1 && { printf '%s\n' 'DSV4 qualification builder already exists.' >&2; exit 1; }
docker buildx create --name "$BUILDER" --driver docker-container --driver-opt "image=$BUILDKIT_IMAGE" --bootstrap >/dev/null
BUILDER_CREATED=true

for ordinal in 1 2; do
  [[ "$context_identity" == "$(sha256sum "$context/Dockerfile" "$context/dsv4-serve.sh" "$context/workspace.py")" ]] || {
    printf '%s\n' 'DSV4 qualification context changed before a build.' >&2
    exit 1
  }
  output="$TEMP_ROOT/dsv4-$ordinal.tar"
  SOURCE_DATE_EPOCH=0 docker buildx build \
    --builder "$BUILDER" --progress=quiet --no-cache --pull=false --provenance=false \
    --build-arg SOURCE_DATE_EPOCH=0 \
    --output "type=docker,dest=$output,rewrite-timestamp=true" \
    --file "$context/Dockerfile" "$context" >/dev/null
done

[[ "$context_identity" == "$(sha256sum "$context/Dockerfile" "$context/dsv4-serve.sh" "$context/workspace.py")" ]] || {
  printf '%s\n' 'DSV4 qualification context changed during the builds.' >&2
  exit 1
}
cmp --silent "$TEMP_ROOT/dsv4-1.tar" "$TEMP_ROOT/dsv4-2.tar" || {
  printf '%s\n' 'Pixel DSV4 image builds are not byte-reproducible.' >&2
  exit 1
}

first_load=$(docker load --input "$TEMP_ROOT/dsv4-1.tar")
second_load=$(docker load --input "$TEMP_ROOT/dsv4-2.tar")
first_id=$(printf '%s\n' "$first_load" | awk '/^Loaded image ID: sha256:[a-f0-9]{64}$/ { print $4 }')
second_id=$(printf '%s\n' "$second_load" | awk '/^Loaded image ID: sha256:[a-f0-9]{64}$/ { print $4 }')
if [[ ! "$first_id" =~ ^sha256:[a-f0-9]{64}$ || "$first_id" != "$second_id" ]]; then
  printf '%s\n' 'Pixel DSV4 archives did not load as one reproducible image identity.' >&2
  exit 1
fi
created=$(docker image inspect --format '{{.Created}}' "$first_id")
[[ "$created" == '1970-01-01T00:00:00Z' ]] || {
  printf '%s\n' 'Pixel DSV4 image creation time was not normalized.' >&2
  exit 1
}

printf 'Pixel DSV4 reproducible archive SHA-256: %s\n' "$(sha256sum "$TEMP_ROOT/dsv4-1.tar" | awk '{print $1}')"
printf 'Pixel DSV4 reproducible image ID: %s\n' "$first_id"
printf '%s\n' 'DSV4 image qualification passed without starting a container, mounting the model, using a credential, or calling a provider.'
