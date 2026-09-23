#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
BUILDKIT_IMAGE='moby/buildkit@sha256:2f5adac4ecd194d9f8c10b7b5d7bceb5186853db1b26e5abd3a657af0b7e26ec'
BUILDER="pixel-codex-repro-${PPID}-$$"
TEMP_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/pixel-codex-repro.XXXXXXXX")
BUILDER_CREATED=false

cleanup() {
  if [[ "$BUILDER_CREATED" == true ]]; then docker buildx rm "$BUILDER" >/dev/null 2>&1 || true; fi
  case "$TEMP_ROOT" in
    "${TMPDIR:-/tmp}"/pixel-codex-repro.*) rm -rf -- "$TEMP_ROOT" ;;
    *) printf '%s\n' 'Refusing to remove an unexpected Codex image qualification path.' >&2; return 1 ;;
  esac
}
trap cleanup EXIT INT TERM

for command in docker sha256sum cmp mktemp awk; do
  command -v "$command" >/dev/null || { printf 'Missing qualification command: %s\n' "$command" >&2; exit 1; }
done
docker buildx version >/dev/null
docker buildx inspect "$BUILDER" >/dev/null 2>&1 && { printf '%s\n' 'Codex image qualification builder already exists.' >&2; exit 1; }
docker buildx create --name "$BUILDER" --driver docker-container --driver-opt "image=$BUILDKIT_IMAGE" --bootstrap >/dev/null
BUILDER_CREATED=true

build_twice() {
  local label=$1 dockerfile=$2 first="$TEMP_ROOT/$1-1.tar" second="$TEMP_ROOT/$1-2.tar"
  local first_load second_load first_id second_id
  for output in "$first" "$second"; do
    SOURCE_DATE_EPOCH=0 docker buildx build \
      --builder "$BUILDER" --progress=quiet --no-cache --pull=false --provenance=false \
      --build-arg SOURCE_DATE_EPOCH=0 \
      --output "type=docker,dest=$output,rewrite-timestamp=true" \
      --file "$ROOT/$dockerfile" "$ROOT" >/dev/null
  done
  cmp --silent "$first" "$second" || { printf 'Codex %s image builds are not byte-reproducible.\n' "$label" >&2; exit 1; }
  first_load=$(docker load --input "$first")
  second_load=$(docker load --input "$second")
  first_id=$(printf '%s\n' "$first_load" | awk '/^Loaded image ID: sha256:[a-f0-9]{64}$/ { print $4 }')
  second_id=$(printf '%s\n' "$second_load" | awk '/^Loaded image ID: sha256:[a-f0-9]{64}$/ { print $4 }')
  if [[ ! "$first_id" =~ ^sha256:[a-f0-9]{64}$ || "$first_id" != "$second_id" ]]; then
    printf 'Codex %s archive did not load as one reproducible image identity.\n' "$label" >&2
    exit 1
  fi
  printf 'Codex %s image reproducible archive SHA-256: %s\n' "$label" "$(sha256sum "$first" | awk '{print $1}')"
  printf 'Codex %s image reproducible image ID: %s\n' "$label" "$first_id"
}

build_twice runner deploy/work-codex-provider/Dockerfile
build_twice egress-proxy deploy/work-codex-provider/Dockerfile.egress-proxy
build_twice comparison-runner deploy/agent-comparison/Dockerfile.codex-runner
printf '%s\n' 'Codex image qualification passed without a credential or provider call.'
