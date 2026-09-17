#!/usr/bin/env bash
# Docker rootless bind-mount ownership helpers for native Linux.

ODS_ROOTLESS_HELPER_IMAGE="${ODS_ROOTLESS_HELPER_IMAGE:-busybox:1.36.1}"

ods_docker_rootless_state() {
    case "${ODS_ASSUME_ROOTLESS:-}" in
        1|true) return 0 ;;
        0|false) return 1 ;;
    esac

    # Docker reports rootless via .SecurityOptions (contains "name=rootless").
    # This field is Docker-specific; the podman "docker" shim does not expose it
    # and the Go template errors out instead of returning empty.
    local security_options
    if security_options=$(docker info --format '{{json .SecurityOptions}}' 2>/dev/null) \
       && [[ -n "$security_options" && "$security_options" != "null" ]]; then
        grep -q 'rootless' <<<"$security_options"
        return
    fi

    # Podman (invoked through the docker CLI shim) reports rootless via
    # .Host.Security.Rootless — a plain "true"/"false" boolean.
    local podman_rootless
    if podman_rootless=$(docker info --format '{{.Host.Security.Rootless}}' 2>/dev/null) \
       && [[ -n "$podman_rootless" ]]; then
        [[ "$podman_rootless" == "true" ]]
        return
    fi

    echo "[error] Could not determine whether Docker is running in rootless mode." >&2
    return 2
}

ods_is_rootless_docker() {
    local state_rc=0
    ods_docker_rootless_state || state_rc=$?
    [[ "$state_rc" -eq 0 ]]
}

_ods_rootless_compose_flags() {
    if [[ -n "${ODS_ROOTLESS_COMPOSE_FLAGS:-}" ]]; then
        printf '%s\n' "$ODS_ROOTLESS_COMPOSE_FLAGS"
    elif [[ -n "${INSTALL_DIR:-}" && -f "$INSTALL_DIR/.compose-flags" ]]; then
        cat "$INSTALL_DIR/.compose-flags"
    fi
}

_ods_rootless_service_enabled() {
    local service="$1" flags="$2"

    if [[ -n "$flags" ]]; then
        case "$service" in
            comfyui)
                [[ "$flags" == *"extensions/services/comfyui/compose."* ]]
                ;;
            langfuse)
                [[ "$flags" == *"extensions/services/langfuse/compose.yaml"* ]]
                ;;
            *)
                [[ "$flags" == *"extensions/services/${service}/compose.yaml"* ]]
                ;;
        esac
        return
    fi

    case "$service" in
        ape|privacy-shield|token-spy) return 0 ;;
        n8n)      [[ "${ENABLE_WORKFLOWS:-false}" == "true" ]] ;;
        whisper)  [[ "${ENABLE_VOICE:-false}" == "true" ]] ;;
        hermes)   [[ "${ENABLE_HERMES:-false}" == "true" ]] ;;
        comfyui)  [[ "${ENABLE_COMFYUI:-false}" == "true" ]] ;;
        langfuse) [[ "${ENABLE_LANGFUSE:-${LANGFUSE_ENABLED:-false}}" == "true" ]] ;;
        *) return 1 ;;
    esac
}

_ods_rootless_should_repair() {
    local service="$1" flags="$2" target_service="${3:-}"

    if [[ -n "$target_service" ]]; then
        [[ "$service" == "$target_service" ]]
        return
    fi
    _ods_rootless_service_enabled "$service" "$flags"
}

_ods_rootless_env_id_override() {
    local install_dir="$1" key="$2" value
    local env_file="$install_dir/.env"

    [[ -f "$env_file" ]] || return 0
    value=$(awk -v key="$key" '
        index($0, key "=") == 1 { value = substr($0, length(key) + 2) }
        END { print value }
    ' "$env_file")
    value="${value%$'\r'}"
    if [[ "$value" == '""' || "$value" == "''" ]]; then
        value=""
    fi
    if [[ "$value" =~ ^\"([0-9]+)\"$ || "$value" =~ ^\'([0-9]+)\'$ ]]; then
        value="${BASH_REMATCH[1]}"
    fi
    [[ -z "$value" ]] && return 0
    if [[ ! "$value" =~ ^[0-9]+$ ]]; then
        echo "[error] Invalid numeric $key override in $env_file: $value" >&2
        return 1
    fi
    printf '%s\n' "$value"
}

_ods_rootless_resolve_target() {
    local install_dir="$1" relative="$2" base target
    base=$(readlink -f "$install_dir/data") || return 1
    target=$(readlink -f "$install_dir/$relative") || return 1
    case "$target" in
        "$base"/*) printf '%s\n' "$target" ;;
        *)
            echo "[error] rootless ownership target escapes data/: $relative -> $target" >&2
            return 1
            ;;
    esac
}

_ods_rootless_ensure_helper_image() {
    if docker image inspect "$ODS_ROOTLESS_HELPER_IMAGE" >/dev/null 2>&1; then
        return 0
    fi
    echo "[ods] Pulling rootless ownership helper image $ODS_ROOTLESS_HELPER_IMAGE..."
    if ! docker pull "$ODS_ROOTLESS_HELPER_IMAGE" >/dev/null; then
        echo "[error] Could not pull $ODS_ROOTLESS_HELPER_IMAGE for rootless ownership repair." >&2
        return 1
    fi
}

ods_rootless_make_host_writable() {
    local install_dir="$1" relative="$2" install_root target

    case "$relative" in
        config/*) ;;
        *)
            echo "[error] Refusing rootless host-writable repair outside config/: $relative" >&2
            return 1
            ;;
    esac
    case "/$relative/" in
        */../*)
            echo "[error] Refusing rootless host-writable path traversal: $relative" >&2
            return 1
            ;;
    esac
    [[ ! -L "$install_dir/$relative" ]] || {
        echo "[error] Refusing rootless host-writable repair for symlink: $relative" >&2
        return 1
    }

    install_root=$(readlink -f "$install_dir") || return 1
    target=$(readlink -f "$install_dir/$relative") || return 1
    case "$target" in
        "$install_root"/config/*) ;;
        *)
            echo "[error] Rootless host-writable target escapes config/: $relative -> $target" >&2
            return 1
            ;;
    esac

    _ods_rootless_ensure_helper_image || return 1
    if ! docker run --rm --pull never --user 0:0 --network none \
        -v "$target:/target" \
        "$ODS_ROOTLESS_HELPER_IMAGE" \
        chown -R 0:0 /target; then
        echo "[error] Could not restore host-user ownership for $relative in the rootless namespace." >&2
        return 1
    fi
    [[ -w "$target" ]] || {
        echo "[error] Rootless host-writable verification failed for $relative." >&2
        return 1
    }
}

_ods_rootless_ensure_directory() {
    local install_dir="$1" relative="$2" data_root subpath

    case "$relative" in
        data/*) subpath="${relative#data/}" ;;
        *)
            echo "[error] Refusing rootless ownership path outside data/: $relative" >&2
            return 1
            ;;
    esac
    [[ -n "$subpath" ]] || return 1
    case "/$subpath/" in
        */../*)
            echo "[error] Refusing rootless ownership path traversal: $relative" >&2
            return 1
            ;;
    esac

    [[ ! -L "$install_dir/$relative" ]] || {
        echo "[error] Refusing rootless ownership repair for symlink: $relative" >&2
        return 1
    }
    [[ -d "$install_dir/$relative" ]] && return 0

    data_root=$(readlink -f "$install_dir/data") || {
        echo "[error] Could not resolve the ODS data directory: $install_dir/data" >&2
        return 1
    }
    if ! docker run --rm --pull never --user 0:0 --network none \
        -v "$data_root:/ods-data" \
        "$ODS_ROOTLESS_HELPER_IMAGE" \
        mkdir -p "/ods-data/$subpath"; then
        echo "[error] Could not create rootless ownership target: $relative" >&2
        return 1
    fi
    [[ -d "$install_dir/$relative" ]] || {
        echo "[error] Rootless ownership target was not created: $relative" >&2
        return 1
    }
}

_ods_rootless_container_state() {
    local container="$1" states state

    states=$(docker container ls -a --format '{{.Names}}:{{.State}}' 2>/dev/null) || {
        echo "[error] Could not query Docker container state for $container." >&2
        return 2
    }
    state=$(awk -F: -v name="$container" '$1 == name { print $2; exit }' <<<"$states")
    case "$state" in
        "") printf 'absent\n' ;;
        exited|dead|created) printf 'stopped\n' ;;
        running|restarting|paused|removing) printf 'running\n' ;;
        *)
            echo "[error] Unexpected container state for $container: $state" >&2
            return 2
            ;;
    esac
}

_ods_rootless_stat_metadata() {
    local target="$1"
    docker run --rm --pull never --user 0:0 --network none \
        -v "$target:/data:ro" \
        "$ODS_ROOTLESS_HELPER_IMAGE" \
        stat -c '%u:%g:%a' /data 2>/dev/null
}

_ods_rootless_fix_directory() {
    local install_dir="$1" relative="$2" owner="$3" container="${4:-}" mode="${5:-}"
    local target metadata current_owner current_mode container_state

    [[ "$owner" =~ ^[0-9]+:[0-9]+$ ]] || {
        echo "[error] Invalid rootless ownership value '$owner' for $relative." >&2
        return 1
    }
    [[ -z "$mode" || "$mode" =~ ^[0-7]{3,4}$ ]] || {
        echo "[error] Invalid rootless mode '$mode' for $relative." >&2
        return 1
    }
    _ods_rootless_ensure_directory "$install_dir" "$relative" || return 1
    target=$(_ods_rootless_resolve_target "$install_dir" "$relative") || return 1
    metadata=$(_ods_rootless_stat_metadata "$target") || {
        echo "[error] Could not inspect rootless ownership for $relative." >&2
        return 1
    }
    current_owner="${metadata%:*}"
    current_mode="${metadata##*:}"

    if [[ -n "$container" ]]; then
        container_state=$(_ods_rootless_container_state "$container") || return 1
    else
        container_state="absent"
    fi
    if [[ "$container_state" == "running" ]]; then
        if [[ "$current_owner" == "$owner" && ( -z "$mode" || "$current_mode" == "$mode" ) ]]; then
            echo "[ods]   $relative already matches $owner${mode:+ mode $mode}; $container is running"
            return 0
        fi
        echo "[error] Refusing recursive ownership repair while $container is running." >&2
        echo "        Current metadata: $current_owner mode $current_mode; expected: $owner${mode:+ mode $mode}." >&2
        echo "        Stop the affected service or ODS, then run: ods repair rootless-ownership" >&2
        return 1
    fi

    if ! docker run --rm --pull never --user 0:0 --network none \
        -v "$target:/data" \
        "$ODS_ROOTLESS_HELPER_IMAGE" \
        chown -R "$owner" /data; then
        echo "[error] Failed to set $relative ownership to $owner in the rootless namespace." >&2
        return 1
    fi
    if [[ -n "$mode" ]] && ! docker run --rm --pull never --user 0:0 --network none \
        -v "$target:/data" \
        "$ODS_ROOTLESS_HELPER_IMAGE" \
        chmod "$mode" /data; then
        echo "[error] Failed to set $relative mode to $mode in the rootless namespace." >&2
        return 1
    fi
    metadata=$(_ods_rootless_stat_metadata "$target") || return 1
    current_owner="${metadata%:*}"
    current_mode="${metadata##*:}"
    if [[ "$current_owner" != "$owner" || ( -n "$mode" && "$current_mode" != "$mode" ) ]]; then
        echo "[error] Rootless metadata verification failed for $relative:" >&2
        echo "        expected $owner${mode:+ mode $mode}, got $current_owner mode $current_mode." >&2
        return 1
    fi
    echo "[ods]   $relative -> $owner${mode:+ mode $mode}"
}

ods_fix_rootless_ownership() {
    local install_dir="${1:-${INSTALL_DIR:-}}" target_service="${2:-}" flags failures=0
    local uid_override gid_override service_uid service_gid hermes_uid hermes_gid rootless_state=0

    [[ -n "$install_dir" ]] || {
        echo "[error] INSTALL_DIR is required for rootless ownership repair." >&2
        return 2
    }
    if [[ -n "$target_service" ]]; then
        case "$target_service" in
            ape|comfyui|hermes|langfuse|n8n|privacy-shield|token-spy|whisper) ;;
            *) return 0 ;;
        esac
    fi
    ods_docker_rootless_state || rootless_state=$?
    case "$rootless_state" in
        0) ;;
        1) return 0 ;;
        *) return 1 ;;
    esac
    [[ "$(uname -s)" == "Linux" ]] || return 0
    _ods_rootless_ensure_helper_image || return 1
    flags=$(_ods_rootless_compose_flags)
    uid_override=$(_ods_rootless_env_id_override "$install_dir" ODS_UID) || return 1
    gid_override=$(_ods_rootless_env_id_override "$install_dir" ODS_GID) || return 1
    # Existing installs may still carry the legacy Compose-only keys. Honour
    # them until the next installer run migrates the values to ODS_UID/GID.
    [[ -n "$uid_override" ]] \
        || uid_override=$(_ods_rootless_env_id_override "$install_dir" UID) || return 1
    [[ -n "$gid_override" ]] \
        || gid_override=$(_ods_rootless_env_id_override "$install_dir" GID) || return 1
    service_uid="${uid_override:-1000}"
    service_gid="${gid_override:-1000}"
    hermes_uid="${uid_override:-10000}"
    hermes_gid="${gid_override:-10000}"

    echo "[ods] Verifying bind-mount ownership for Docker rootless mode..."

    if _ods_rootless_should_repair token-spy "$flags" "$target_service"; then
        _ods_rootless_fix_directory "$install_dir" data/token-spy 1000:1000 ods-token-spy || failures=$((failures + 1))
    fi
    if _ods_rootless_should_repair privacy-shield "$flags" "$target_service"; then
        _ods_rootless_fix_directory "$install_dir" data/privacy-shield "$service_uid:$service_gid" ods-privacy-shield || failures=$((failures + 1))
    fi
    if _ods_rootless_should_repair ape "$flags" "$target_service"; then
        _ods_rootless_fix_directory "$install_dir" data/ape 100:100 ods-ape || failures=$((failures + 1))
    fi
    if _ods_rootless_should_repair n8n "$flags" "$target_service"; then
        _ods_rootless_fix_directory "$install_dir" data/n8n "$service_uid:$service_gid" ods-n8n || failures=$((failures + 1))
    fi
    if _ods_rootless_should_repair whisper "$flags" "$target_service"; then
        _ods_rootless_fix_directory "$install_dir" data/whisper 1000:1000 ods-whisper || failures=$((failures + 1))
    fi
    if _ods_rootless_should_repair hermes "$flags" "$target_service"; then
        _ods_rootless_fix_directory "$install_dir" data/hermes "$hermes_uid:$hermes_gid" ods-hermes 700 || failures=$((failures + 1))
    fi
    if [[ "${GPU_BACKEND:-}" == "nvidia" ]] && _ods_rootless_should_repair comfyui "$flags" "$target_service"; then
        _ods_rootless_fix_directory "$install_dir" data/comfyui 1000:1000 ods-comfyui || failures=$((failures + 1))
    fi
    if _ods_rootless_should_repair langfuse "$flags" "$target_service"; then
        _ods_rootless_fix_directory "$install_dir" data/langfuse/postgres 70:70 ods-langfuse-postgres || failures=$((failures + 1))
        _ods_rootless_fix_directory "$install_dir" data/langfuse/clickhouse 101:101 ods-langfuse-clickhouse || failures=$((failures + 1))
    fi

    if [[ "$failures" -ne 0 ]]; then
        echo "[error] Rootless ownership repair failed for $failures path(s)." >&2
        return 1
    fi
    echo "[ods] Rootless ownership is ready."
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    ods_fix_rootless_ownership "${1:-}" "${2:-}"
fi
