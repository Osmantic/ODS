#!/usr/bin/env bash
# Explicit owner recovery after retiring a WSL deployment, never installer cleanup.
set -euo pipefail

fail() {
    printf 'Recovery stopped: %s\n' "$*" >&2
    exit 1
}

[[ $# == 0 ]] || fail 'Run this helper without arguments in an interactive Ubuntu terminal.'
command -v docker >/dev/null 2>&1 || fail 'Docker is unavailable. Start Docker Desktop and check WSL integration.'
[[ -t 0 ]] || fail 'An interactive terminal is required; no image tags were changed.'
current_uid=$(id -u) || fail 'Cannot identify the Ubuntu account.'
[[ "$current_uid" =~ ^[1-9][0-9]*$ ]] || fail 'Run as your Ubuntu deployment account, without sudo.'

live='openclaw-sandbox:bookworm-slim'
pixel_state_dir="${HOME:?}/.local/share/pixel"
image_format='{{.Id}}|{{index .Config.Labels "org.osmantic.pixel.sandbox-version"}}|{{index .Config.Labels "org.osmantic.pixel.sandbox-uid"}}|{{.Config.User}}'

check_local_state() {
    local name
    for name in current runtime-attestation.json .ods-uninstall-current .ods-uninstall-runtime-attestation; do
        [[ ! -e "$pixel_state_dir/$name" && ! -L "$pixel_state_dir/$name" ]] \
            || fail 'This account retains an active Pixel release or attestation. Use its own uninstall instead.'
    done
}

engine_identity() {
    local value
    value=$(docker info --format '{{.ID}}') || fail 'Docker engine inspection failed.'
    [[ "$value" =~ ^[A-Za-z0-9][A-Za-z0-9:._-]{0,255}$ ]] || fail 'Docker returned an invalid engine identity.'
    printf '%s\n' "$value"
}

image_identity() {
    local value
    value=$(docker image inspect --format "$image_format" "$1") || fail "Cannot inspect $1."
    [[ "$value" =~ ^sha256:[a-f0-9]{64}\|[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}\|[1-9][0-9]*\|sandbox$ ]] \
        || fail 'The shared image is not a valid labeled Pixel sandbox. Nothing was removed.'
    printf '%s\n' "$value"
}

list_image() {
    docker image ls --quiet --no-trunc --filter "reference=$1" \
        || fail "Docker image listing failed for $1."
}

check_consumers() {
    local consumers
    consumers=$(docker ps --all --no-trunc --filter "ancestor=$old_id" \
        --format '{{.ID}} {{.Names}} {{.Status}}') || fail 'Docker container listing failed.'
    if [[ -n "$consumers" ]]; then
        printf 'Containers still use the old image (including stopped containers):\n%s\n' "$consumers" >&2
        fail 'Retire these consumers through their own deployment before recovery.'
    fi
}

check_local_state
engine=$(engine_identity)
listed=$(list_image "$live")
if [[ -z "$listed" ]]; then
    printf 'No shared Pixel sandbox tag remains. Rerun the ODS installation command.\n'
    exit 0
fi
observed=$(image_identity "$live")
IFS='|' read -r old_id version sandbox_uid sandbox_user <<<"$observed"
[[ "$listed" == "$old_id" ]] || fail 'The shared image changed while it was inspected.'
retained="pixel-sandbox-retained:sha256-${old_id#sha256:}"

printf 'Docker engine: %s\nShared tag: %s\nImage: %s\nPixel version: %s\nOld sandbox UID: %s (user %s)\nCurrent account UID: %s\n' \
    "$engine" "$live" "$old_id" "$version" "$sandbox_uid" "$sandbox_user" "$current_uid"
check_consumers
printf '\nNo containers reference this image. Other WSL installations may still own its tag.\n'
printf 'Confirm that ALL old installations using this tag are retired, and that no Docker/Pixel installation or recovery runs in parallel.\n'
printf 'The exact old image will be retained as %s; only the shared tag will be removed.\n' "$retained"
read -r -p 'Type RETIRED to confirm both conditions, or press Enter to stop: ' confirmation \
    || fail 'No confirmation received; no image tags were changed.'
[[ "$confirmation" == RETIRED ]] || fail 'Confirmation declined; no image tags were changed.'

# Recheck after the owner reviewed the evidence and before preserving anything.
[[ $(engine_identity) == "$engine" ]] || fail 'The Docker engine changed during review.'
[[ $(image_identity "$live") == "$observed" ]] || fail 'The shared image changed during review.'
check_local_state
check_consumers
listed=$(list_image "$retained")
if [[ -n "$listed" ]]; then
    [[ "$listed" == "$old_id" ]] || fail 'The retention tag already identifies another image.'
    [[ $(image_identity "$retained") == "$observed" ]] || fail 'The retention image identity does not match.'
else
    docker image tag "$old_id" "$retained" || fail 'Could not preserve the old image; the shared tag was retained.'
fi

# Docker has no atomic compare-and-remove for tags. The owner's no-concurrency
# confirmation is required even though all evidence is checked again here.
[[ $(image_identity "$retained") == "$observed" ]] || fail 'The preserved image failed verification; the shared tag was retained.'
[[ $(engine_identity) == "$engine" ]] || fail 'The Docker engine changed; the shared tag was retained.'
check_local_state
check_consumers
[[ $(image_identity "$live") == "$observed" ]] || fail 'The shared image changed; its tag was retained.'
docker image rm -- "$live" || fail "Tag removal failed. The old image is preserved as $retained; do not retry with force."
[[ $(engine_identity) == "$engine" ]] || fail "The Docker engine changed after removal. Check the retained image $retained."
[[ $(image_identity "$retained") == "$observed" ]] || fail 'The retained image changed after removal; inspect Docker before continuing.'
listed=$(list_image "$live")
[[ -z "$listed" ]] || fail 'The shared tag reappeared; inspect other deployments before continuing.'
printf 'Old image preserved as %s. Rerun the same ODS installation command.\n' "$retained"
