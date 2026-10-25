#!/usr/bin/env bash
# Exact SDXL Lightning checkpoint selected by ODS for optional ComfyUI.
# Source: ByteDance/SDXL-Lightning at the immutable revision below.
ODS_SDXL_LIGHTNING_REVISION=c9a24f48e1c025556787b0c58dd67a091ece2e44
ODS_SDXL_LIGHTNING_FILE=sdxl_lightning_4step.safetensors
ODS_SDXL_LIGHTNING_BYTES=6938040682
ODS_SDXL_LIGHTNING_SHA256=e0d996ee0013e79d9d3561f50fcafb9a17e3ff07b780358e3b66d67932c4d490
ODS_SDXL_LIGHTNING_URL="https://huggingface.co/ByteDance/SDXL-Lightning/resolve/${ODS_SDXL_LIGHTNING_REVISION}/${ODS_SDXL_LIGHTNING_FILE}"

# Keep FD 9 open for the whole download. 75 means another ODS download owns
# the lock; all other failures, including opening the lock file, are errors.
ods_acquire_checkpoint_lock() {
    local lock_file="$1" result
    [[ ! -L "$lock_file" && ( ! -e "$lock_file" || -f "$lock_file" ) ]] || return 1
    # Append mode avoids truncating a foreign file if the path changes between
    # the check and open. The installer passes a file outside Comfy bind mounts.
    if ! exec 9>> "$lock_file"; then return 1; fi
    if flock -n -E 75 9; then
        return 0
    else
        result=$?
    fi
    [[ "$result" -eq 75 ]] && return 75
    return 1
}

ods_verify_checkpoint_file() {
    local file="$1" expected_bytes="$2" expected_sha256="$3" actual_bytes actual_sha256
    [[ -f "$file" && ! -L "$file" && "$expected_bytes" =~ ^[0-9]+$ &&
       "$expected_sha256" =~ ^[[:xdigit:]]{64}$ ]] || return 1
    actual_bytes="$(wc -c < "$file")" || return 1
    actual_bytes="${actual_bytes//[[:space:]]/}"
    [[ "$actual_bytes" == "$expected_bytes" ]] || return 1
    actual_sha256="$(sha256sum -- "$file")" || return 1
    actual_sha256="${actual_sha256%% *}"
    [[ "${actual_sha256,,}" == "${expected_sha256,,}" ]]
}

ods_promote_verified_checkpoint() {
    local part="$1" final="$2" expected_bytes="$3" expected_sha256="$4"
    [[ "$(dirname -- "$part")" == "$(dirname -- "$final")" &&
       ! -e "$final" && ! -L "$final" ]] || return 1
    ods_verify_checkpoint_file "$part" "$expected_bytes" "$expected_sha256" || return 1
    # A hard link is an atomic no-clobber promotion on the same filesystem.
    # A concurrent installer must not replace a checkpoint already promoted.
    ln -- "$part" "$final" || return 1
    rm -f -- "$part"
}
