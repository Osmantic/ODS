#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIB="$ROOT_DIR/lib/rootless-ownership.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

pass_count=0
fail() {
    echo "FAIL: $*" >&2
    exit 1
}
pass() {
    pass_count=$((pass_count + 1))
    echo "PASS: $*"
}

[[ -s "$LIB" ]] || fail "rootless ownership helper is missing"
bash -n "$LIB"
pass "helper parses"

(
    # shellcheck source=../lib/rootless-ownership.sh
    source "$LIB"
    ODS_ASSUME_ROOTLESS=1
    ods_is_rootless_docker
    ODS_ASSUME_ROOTLESS=0
    ! ods_is_rootless_docker
) || fail "rootless detection override"
pass "rootless detection has deterministic test override"

(
    source "$LIB"
    install_dir="$TMP_DIR/config-repair"
    mkdir -p "$install_dir/config/searxng"
    _ods_rootless_ensure_helper_image() { return 0; }
    docker() {
        [[ "$*" == *'-v '*'/config/searxng:/target'* ]] || return 1
        return 0
    }
    ods_rootless_make_host_writable "$install_dir" config/searxng
) || fail "rootless config ownership repair"
pass "rootless config ownership repair stays inside config/"

(
    source "$LIB"
    install_dir="$TMP_DIR/config-escape"
    mkdir -p "$install_dir/config" "$TMP_DIR/outside"
    ln -s "$TMP_DIR/outside" "$install_dir/config/escape"
    ! ods_rootless_make_host_writable "$install_dir" config/escape
) || fail "rootless config symlink escape guard"
pass "rootless config ownership repair rejects symlinks"

(
    source "$LIB"
    unset ODS_ASSUME_ROOTLESS
    docker() { return 1; }
    state_rc=0
    ods_docker_rootless_state || state_rc=$?
    [[ "$state_rc" -eq 2 ]]
) || fail "docker-info failure was treated as rootful"
pass "indeterminate Docker rootless state fails closed"

INSTALL_DIR="$TMP_DIR/ods"
mkdir -p "$INSTALL_DIR/data"/{ape,privacy-shield,token-spy,n8n,whisper,hermes,comfyui}
mkdir -p "$INSTALL_DIR/data/langfuse"/{postgres,clickhouse}

CALLS="$TMP_DIR/calls"
: > "$CALLS"
(
    source "$LIB"
    ods_docker_rootless_state() { return 0; }
    uname() { printf 'Linux\n'; }
    _ods_rootless_ensure_helper_image() { return 0; }
    _ods_rootless_fix_directory() {
        printf '%s|%s|%s\n' "$2" "$3" "$4" >> "$CALLS"
    }
    ODS_ROOTLESS_COMPOSE_FLAGS="-f docker-compose.base.yml -f extensions/services/token-spy/compose.yaml -f extensions/services/n8n/compose.yaml"
    GPU_BACKEND=nvidia
    ods_fix_rootless_ownership "$INSTALL_DIR"
)
grep -q '^data/token-spy|1000:1000|ods-token-spy$' "$CALLS" || fail "active core service was skipped"
grep -q '^data/n8n|1000:1000|ods-n8n$' "$CALLS" || fail "active optional service was skipped"
[[ "$(wc -l < "$CALLS" | tr -d ' ')" == "2" ]] || fail "disabled service directories were modified"
pass "only active compose services are repaired"

: > "$CALLS"
(
    source "$LIB"
    ods_docker_rootless_state() { return 0; }
    uname() { printf 'Linux\n'; }
    _ods_rootless_ensure_helper_image() { return 0; }
    _ods_rootless_fix_directory() {
        printf '%s|%s\n' "$2" "$3" >> "$CALLS"
    }
    ODS_ROOTLESS_COMPOSE_FLAGS="-f extensions/services/token-spy/compose.yaml"
    ods_fix_rootless_ownership "$INSTALL_DIR" hermes
)
grep -q '^data/hermes|10000:10000$' "$CALLS" \
    || fail "explicitly enabled target was not repaired"
[[ "$(wc -l < "$CALLS" | tr -d ' ')" == "1" ]] \
    || fail "targeted repair modified unrelated active services"
pass "targeted repair covers newly enabled service only"

: > "$CALLS"
(
    source "$LIB"
    ods_docker_rootless_state() { return 0; }
    uname() { printf 'Linux\n'; }
    _ods_rootless_ensure_helper_image() { return 0; }
    _ods_rootless_fix_directory() {
        printf '%s|%s\n' "$2" "$3" >> "$CALLS"
    }
    ODS_ROOTLESS_COMPOSE_FLAGS="-f extensions/services/comfyui/compose.nvidia.yaml -f extensions/services/langfuse/compose.yaml"
    GPU_BACKEND=nvidia
    ods_fix_rootless_ownership "$INSTALL_DIR"
)
grep -q '^data/comfyui|1000:1000$' "$CALLS" || fail "NVIDIA ComfyUI ownership missing"
grep -q '^data/langfuse/postgres|70:70$' "$CALLS" || fail "Langfuse postgres ownership missing"
grep -q '^data/langfuse/clickhouse|101:101$' "$CALLS" || fail "Langfuse clickhouse ownership missing"
pass "backend-specific and nested database ownership is mapped"

: > "$CALLS"
(
    source "$LIB"
    ods_docker_rootless_state() { return 0; }
    uname() { printf 'Linux\n'; }
    _ods_rootless_ensure_helper_image() { return 0; }
    _ods_rootless_fix_directory() {
        printf '%s|%s|%s\n' "$2" "$3" "$5" >> "$CALLS"
    }
    ODS_ROOTLESS_COMPOSE_FLAGS="-f extensions/services/hermes/compose.yaml"
    ods_fix_rootless_ownership "$INSTALL_DIR"
)
grep -q '^data/hermes|10000:10000|700$' "$CALLS" \
    || fail "Hermes ownership/mode contract is incomplete"
pass "Hermes keeps UID 10000 and mode 0700"

: > "$CALLS"
cat > "$INSTALL_DIR/.env" <<'EOF'
ODS_UID=12001
ODS_GID=12002
EOF
(
    source "$LIB"
    ods_docker_rootless_state() { return 0; }
    uname() { printf 'Linux\n'; }
    _ods_rootless_ensure_helper_image() { return 0; }
    _ods_rootless_fix_directory() {
        printf '%s|%s\n' "$2" "$3" >> "$CALLS"
    }
    ODS_ROOTLESS_COMPOSE_FLAGS="-f extensions/services/privacy-shield/compose.yaml -f extensions/services/n8n/compose.yaml -f extensions/services/hermes/compose.yaml"
    ods_fix_rootless_ownership "$INSTALL_DIR"
)
grep -q '^data/privacy-shield|12001:12002$' "$CALLS" || fail "Privacy Shield ignored UID/GID override"
grep -q '^data/n8n|12001:12002$' "$CALLS" || fail "n8n ignored UID/GID override"
grep -q '^data/hermes|12001:12002$' "$CALLS" || fail "Hermes ignored UID/GID override"
pass "compose ODS_UID/ODS_GID overrides are preserved"

cat > "$INSTALL_DIR/.env" <<'EOF'
UID=13001
GID=13002
EOF
: > "$CALLS"
(
    source "$LIB"
    ods_docker_rootless_state() { return 0; }
    uname() { printf 'Linux\n'; }
    _ods_rootless_ensure_helper_image() { return 0; }
    _ods_rootless_fix_directory() {
        printf '%s|%s\n' "$2" "$3" >> "$CALLS"
    }
    ODS_ROOTLESS_COMPOSE_FLAGS="-f extensions/services/n8n/compose.yaml -f extensions/services/hermes/compose.yaml"
    ods_fix_rootless_ownership "$INSTALL_DIR"
)
grep -q '^data/n8n|13001:13002$' "$CALLS" || fail "n8n ignored legacy UID/GID override"
grep -q '^data/hermes|13001:13002$' "$CALLS" || fail "Hermes ignored legacy UID/GID override"
pass "legacy UID/GID overrides remain compatible until installer migration"

cat > "$INSTALL_DIR/.env" <<'EOF'
ODS_UID=""
ODS_GID=''
EOF
: > "$CALLS"
(
    source "$LIB"
    ods_docker_rootless_state() { return 0; }
    uname() { printf 'Linux\n'; }
    _ods_rootless_ensure_helper_image() { return 0; }
    _ods_rootless_fix_directory() {
        printf '%s|%s\n' "$2" "$3" >> "$CALLS"
    }
    ODS_ROOTLESS_COMPOSE_FLAGS="-f extensions/services/privacy-shield/compose.yaml -f extensions/services/hermes/compose.yaml"
    ods_fix_rootless_ownership "$INSTALL_DIR"
)
grep -q '^data/privacy-shield|1000:1000$' "$CALLS" \
    || fail "quoted empty UID/GID did not use the compose default"
grep -q '^data/hermes|10000:10000$' "$CALLS" \
    || fail "quoted empty UID/GID did not use the Hermes compose default"
pass "empty UID/GID overrides follow compose default semantics"

cat > "$INSTALL_DIR/.env" <<'EOF'
ODS_UID=not-a-number
ODS_GID=12002
EOF
(
    source "$LIB"
    ods_docker_rootless_state() { return 0; }
    uname() { printf 'Linux\n'; }
    _ods_rootless_ensure_helper_image() { return 0; }
    _ods_rootless_fix_directory() { fail "invalid UID reached mutation"; }
    ODS_ROOTLESS_COMPOSE_FLAGS="-f extensions/services/n8n/compose.yaml"
    ! ods_fix_rootless_ownership "$INSTALL_DIR"
) || fail "invalid UID override was accepted"
pass "invalid UID/GID override fails before mutation"
rm -f "$INSTALL_DIR/.env"

: > "$CALLS"
(
    source "$LIB"
    ods_docker_rootless_state() { return 0; }
    uname() { printf 'Linux\n'; }
    _ods_rootless_ensure_helper_image() { return 0; }
    _ods_rootless_fix_directory() {
        printf '%s\n' "$2" >> "$CALLS"
    }
    ODS_ROOTLESS_COMPOSE_FLAGS="-f extensions/services/comfyui/compose.amd.yaml"
    GPU_BACKEND=amd
    ods_fix_rootless_ownership "$INSTALL_DIR"
)
[[ ! -s "$CALLS" ]] || fail "AMD ComfyUI root-owned paths were remapped to UID 1000"
pass "AMD ComfyUI is excluded from the NVIDIA UID contract"

(
    source "$LIB"
    ods_docker_rootless_state() { return 1; }
    _ods_rootless_ensure_helper_image() { fail "rootful path pulled helper image"; }
    ods_fix_rootless_ownership "$INSTALL_DIR"
) || fail "rootful no-op failed"
pass "rootful Docker is a side-effect-free no-op"

for platform in Darwin MINGW64_NT-10.0; do
    (
        source "$LIB"
        ods_docker_rootless_state() { return 0; }
        uname() { printf '%s\n' "$platform"; }
        _ods_rootless_ensure_helper_image() { fail "$platform path pulled helper image"; }
        ods_fix_rootless_ownership "$INSTALL_DIR"
    ) || fail "$platform no-op failed"
done
pass "macOS and Windows are side-effect-free no-ops"

rm -rf "$INSTALL_DIR/data/comfyui"
: > "$CALLS"
(
    source "$LIB"
    ods_docker_rootless_state() { return 0; }
    uname() { printf 'Linux\n'; }
    _ods_rootless_ensure_helper_image() { return 0; }
    _ods_rootless_fix_directory() {
        printf '%s\n' "$2" >> "$CALLS"
    }
    ODS_ROOTLESS_COMPOSE_FLAGS="-f docker-compose.base.yml"
    ods_fix_rootless_ownership "$INSTALL_DIR"
)
[[ ! -e "$INSTALL_DIR/data/comfyui" && ! -s "$CALLS" ]] \
    || fail "disabled service data was created or repaired"
pass "disabled services remain untouched"

mkdir -p "$TMP_DIR/outside"
ln -s "$TMP_DIR/outside" "$INSTALL_DIR/data/escape"
(
    source "$LIB"
    _ods_rootless_ensure_helper_image() { return 0; }
    ! _ods_rootless_fix_directory "$INSTALL_DIR" data/escape 1000:1000 ods-escape
) || fail "symlink target was accepted"
pass "symlink escape is rejected"

ln -s "$TMP_DIR/does-not-exist" "$INSTALL_DIR/data/dangling"
(
    source "$LIB"
    ! _ods_rootless_fix_directory "$INSTALL_DIR" data/dangling 1000:1000 ods-dangling
) || fail "dangling symlink target was accepted"
pass "dangling symlink is rejected before creation"

rm -rf "$INSTALL_DIR/data/comfyui"
CREATE_CALLS="$TMP_DIR/create-calls"
: > "$CREATE_CALLS"
(
    source "$LIB"
    _ods_rootless_ensure_helper_image() { return 0; }
    docker() {
        printf '%s\n' "$*" >> "$CREATE_CALLS"
        if [[ "$*" == *"mkdir -p /ods-data/comfyui"* ]]; then
            mkdir -p "$INSTALL_DIR/data/comfyui"
        fi
        return 0
    }
    _ods_rootless_stat_metadata() {
        if [[ "$(wc -l < "$CREATE_CALLS" | tr -d ' ')" -le 1 ]]; then
            printf '0:0:755\n'
        else
            printf '1000:1000:755\n'
        fi
    }
    _ods_rootless_container_state() { printf 'absent\n'; }
    _ods_rootless_fix_directory "$INSTALL_DIR" data/comfyui 1000:1000 ods-comfyui
) || fail "missing active directory was not created and repaired"
grep -q 'mkdir -p /ods-data/comfyui' "$CREATE_CALLS" \
    || fail "missing active directory did not use the rootless helper namespace"
pass "missing active directory is created before ownership repair"

(
    source "$LIB"
    _ods_rootless_stat_metadata() { printf '1000:1000:755\n'; }
    _ods_rootless_container_state() { printf 'running\n'; }
    _ods_rootless_fix_directory "$INSTALL_DIR" data/token-spy 1000:1000 ods-token-spy
) || fail "running container with correct ownership was rejected"
pass "healthy running service is not mutated"

RERUN_CALLS="$TMP_DIR/rerun-calls"
: > "$RERUN_CALLS"
(
    source "$LIB"
    _ods_rootless_stat_metadata() { printf '1000:1000:755\n'; }
    _ods_rootless_container_state() { printf 'stopped\n'; }
    docker() {
        printf '%s\n' "$*" >> "$RERUN_CALLS"
        return 0
    }
    _ods_rootless_fix_directory "$INSTALL_DIR" data/token-spy 1000:1000 ods-token-spy
) || fail "stopped-service rerun failed"
grep -q 'chown -R 1000:1000 /data' "$RERUN_CALLS" \
    || fail "stopped-service rerun did not repair potentially stale descendants"
pass "rerun repairs descendants even when root metadata already matches"

(
    source "$LIB"
    _ods_rootless_stat_metadata() { printf '0:0:755\n'; }
    _ods_rootless_container_state() { printf 'running\n'; }
    ! _ods_rootless_fix_directory "$INSTALL_DIR" data/token-spy 1000:1000 ods-token-spy
) || fail "live recursive chown was allowed"
pass "mismatched running service fails closed"

(
    source "$LIB"
    _ods_rootless_stat_metadata() { printf '10000:10000:755\n'; }
    _ods_rootless_container_state() { printf 'running\n'; }
    ! _ods_rootless_fix_directory "$INSTALL_DIR" data/hermes 10000:10000 ods-hermes 700
) || fail "running Hermes with an unsafe mode was accepted"
pass "running service mode mismatch fails closed"

(
    source "$LIB"
    _ods_rootless_stat_metadata() { printf '0:0:755\n'; }
    _ods_rootless_container_state() { return 2; }
    docker() { fail "container-state failure reached mutation"; }
    ! _ods_rootless_fix_directory "$INSTALL_DIR" data/token-spy 1000:1000 ods-token-spy
) || fail "container-state query failure was treated as stopped"
pass "container-state query failure fails closed"

METADATA_CALLS="$TMP_DIR/metadata-calls"
DOCKER_CALLS="$TMP_DIR/docker-calls"
: > "$METADATA_CALLS"
: > "$DOCKER_CALLS"
(
    source "$LIB"
    _ods_rootless_stat_metadata() {
        local count
        count=$(wc -l < "$METADATA_CALLS" | tr -d ' ')
        printf 'called\n' >> "$METADATA_CALLS"
        if [[ "$count" == "0" ]]; then
            printf '0:0:755\n'
        else
            printf '10000:10000:700\n'
        fi
    }
    _ods_rootless_container_state() { printf 'stopped\n'; }
    docker() {
        printf '%s\n' "$*" >> "$DOCKER_CALLS"
        return 0
    }
    _ods_rootless_fix_directory "$INSTALL_DIR" data/hermes 10000:10000 ods-hermes 700
) || fail "stopped Hermes repair did not complete"
grep -q 'chown -R 10000:10000 /data' "$DOCKER_CALLS" || fail "stopped repair skipped recursive chown"
grep -q 'chmod 700 /data' "$DOCKER_CALLS" || fail "stopped repair skipped mode correction"
[[ "$(wc -l < "$METADATA_CALLS" | tr -d ' ')" == "2" ]] || fail "stopped repair was not verified"
pass "stopped service repair is applied and verified"

(
    source "$LIB"
    _ods_rootless_stat_metadata() { printf '0:0:755\n'; }
    _ods_rootless_container_state() { printf 'stopped\n'; }
    docker() { return 1; }
    ! _ods_rootless_fix_directory "$INSTALL_DIR" data/token-spy 1000:1000 ods-token-spy
) || fail "chown failure was swallowed"
pass "helper failure propagates"

grep -q 'ods_fix_rootless_ownership' "$ROOT_DIR/installers/phases/06-directories.sh" \
    || fail "phase 06 does not invoke ownership repair"
env_chmod_line=$(grep -n 'chmod 600 "$INSTALL_DIR/.env"' "$ROOT_DIR/installers/phases/06-directories.sh" | cut -d: -f1)
phase_repair_line=$(grep -n 'ods_fix_rootless_ownership "$INSTALL_DIR"' "$ROOT_DIR/installers/phases/06-directories.sh" | cut -d: -f1)
[[ -n "$env_chmod_line" && -n "$phase_repair_line" && "$phase_repair_line" -gt "$env_chmod_line" ]] \
    || fail "phase 06 rootless repair must run after final .env generation"
grep -q 'rootless-ownership|rootless)' "$ROOT_DIR/ods-cli" \
    || fail "CLI repair command is missing"
grep -q '_ods_rootless_fix_directory.*data/langfuse/postgres' \
    "$ROOT_DIR/extensions/services/langfuse/hooks/post_install.sh" \
    || fail "Langfuse post-install rootless path is missing"
grep -q '"DOCKER_HOST", "XDG_RUNTIME_DIR"' "$ROOT_DIR/bin/ods-host-agent.py" \
    || fail "host-agent hook drops rootless Docker routing variables"
grep -q '_repair_rootless_data_ownership(service_id)' "$ROOT_DIR/bin/ods-host-agent.py" \
    || fail "host-agent start path omits rootless ownership repair"
grep -q '_ods_cli_repair_rootless_ownership "$resolved_service"' "$ROOT_DIR/ods-cli" \
    || fail "CLI start path omits rootless ownership repair"
[[ "$(grep -c '_ods_cli_repair_rootless_ownership "$resolved_service"' "$ROOT_DIR/ods-cli")" -eq 2 ]] \
    || fail "CLI start and restart must both invoke rootless ownership repair"
pass "installer, CLI, and Langfuse lifecycle hooks are wired"

echo "All $pass_count rootless ownership tests passed."
