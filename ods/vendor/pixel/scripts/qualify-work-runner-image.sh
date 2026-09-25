#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
BUILDKIT_IMAGE='moby/buildkit@sha256:2f5adac4ecd194d9f8c10b7b5d7bceb5186853db1b26e5abd3a657af0b7e26ec'
BUILDER="pixel-work-runner-repro-${PPID}-$$"
TEMP_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/pixel-work-runner-repro.XXXXXXXX")
BUILDER_CREATED=false

cleanup() {
  if [[ "$BUILDER_CREATED" == true ]]; then docker buildx rm "$BUILDER" >/dev/null 2>&1 || true; fi
  case "$TEMP_ROOT" in
    "${TMPDIR:-/tmp}"/pixel-work-runner-repro.*) rm -rf -- "$TEMP_ROOT" ;;
    *) printf '%s\n' 'Refusing to remove an unexpected Work runner qualification path.' >&2; return 1 ;;
  esac
}
trap cleanup EXIT INT TERM

for command in docker sha256sum cmp mktemp awk; do
  command -v "$command" >/dev/null || { printf 'Missing qualification command: %s\n' "$command" >&2; exit 1; }
done
docker buildx version >/dev/null
docker buildx inspect "$BUILDER" >/dev/null 2>&1 && { printf '%s\n' 'Work runner qualification builder already exists.' >&2; exit 1; }
docker buildx create --name "$BUILDER" --driver docker-container --driver-opt "image=$BUILDKIT_IMAGE" --bootstrap >/dev/null
BUILDER_CREATED=true

for ordinal in 1 2; do
  output="$TEMP_ROOT/runner-$ordinal.tar"
  SOURCE_DATE_EPOCH=0 docker buildx build \
    --builder "$BUILDER" --progress=quiet --no-cache --pull=false --provenance=false \
    --build-arg SOURCE_DATE_EPOCH=0 \
    --output "type=docker,dest=$output,rewrite-timestamp=true" \
    --file "$ROOT/deploy/work-runner/Dockerfile" "$ROOT" >/dev/null
done

cmp --silent "$TEMP_ROOT/runner-1.tar" "$TEMP_ROOT/runner-2.tar" || {
  printf '%s\n' 'Pixel Work runner image builds are not byte-reproducible.' >&2
  exit 1
}

first_load=$(docker load --input "$TEMP_ROOT/runner-1.tar")
second_load=$(docker load --input "$TEMP_ROOT/runner-2.tar")
first_id=$(printf '%s\n' "$first_load" | awk '/^Loaded image ID: sha256:[a-f0-9]{64}$/ { print $4 }')
second_id=$(printf '%s\n' "$second_load" | awk '/^Loaded image ID: sha256:[a-f0-9]{64}$/ { print $4 }')
if [[ ! "$first_id" =~ ^sha256:[a-f0-9]{64}$ || "$first_id" != "$second_id" ]]; then
  printf '%s\n' 'Pixel Work runner archive did not load as one reproducible image identity.' >&2
  exit 1
fi

printf 'Pixel Work runner reproducible archive SHA-256: %s\n' "$(sha256sum "$TEMP_ROOT/runner-1.tar" | awk '{print $1}')"
printf 'Pixel Work runner reproducible image ID: %s\n' "$first_id"
printf '%s\n' 'Work runner image qualification passed without starting a container, using a credential, or calling a provider.'
