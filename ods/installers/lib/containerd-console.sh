#!/bin/bash
# Prepare the known NVIDIA-only containerd device policy before image unpack.
# Runtime discovery remains in phase 05; the Python helper owns the narrow,
# revalidated host change and never restarts the daemon.
ods_prepare_containerd_console() {
    [[ "${GPU_BACKEND:-}" == nvidia && "${GPU_COUNT:-0}" -gt 0 ]] || return 0
    [[ "$(uname -s)" == Linux ]] || return 0
    if [[ "${DRY_RUN:-false}" == true ]]; then
        log "[DRY RUN] Would check local containerd image-unpack permissions"
        return 0
    fi
    _runtime_is_podman && return 0
    _docker_is_desktop_engine && return 0

    local endpoint security rc
    if [[ -n "${DOCKER_HOST:-}" && -z "${DOCKER_CONTEXT:-}" ]]; then
        endpoint="$DOCKER_HOST"
    else
        endpoint="$(docker_run context inspect --format '{{.Endpoints.docker.Host}}')" || return 1
    fi
    # Never repair this host for a remote daemon or a rootless user socket.
    case "$endpoint" in
        unix:///var/run/docker.sock|unix:///run/docker.sock) ;;
        *) return 0 ;;
    esac
    security="$(docker_run info --format '{{json .SecurityOptions}}')" || return 1
    [[ "$security" != *rootless* ]] || return 0

    if python3 "$SCRIPT_DIR/scripts/prepare-containerd-console.py" --check; then
        return 0
    else
        rc=$?
    fi
    [[ "$rc" == 10 ]] || return "$rc"
    ai "Allowing containerd to create the standard console node while unpacking images..."
    ods_sudo python3 "$SCRIPT_DIR/scripts/prepare-containerd-console.py" --apply || return 1
    ai_ok "Containerd image-unpack permission verified; existing device rules retained"
}
