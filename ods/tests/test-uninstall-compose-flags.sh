#!/usr/bin/env bash
# Regression checks for ODS uninstall compose cleanup.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="$ROOT_DIR/ods-uninstall.sh"
TMP_DIR=""

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

pass() {
    echo "[PASS] $*"
}

make_stub_bin() {
    local stub_dir="$1"

    # Include unrelated same-prefix resources, as well as native Pixel archives.
    # None of these names may be passed to a name-based cleanup fallback.
    cat > "$stub_dir/docker" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${DOCKER_LOG:?}"

# Emit names the way `docker ps` / `docker volume ls` would, honouring
# `--filter name=<expr>` with Docker's own semantics: the expression is matched
# anywhere in the name, so an unanchored "ods" also matches "k3s_pods".
emit_filtered() {
    local expr="" arg name
    for arg in "$@"; do
        case "$arg" in
            name=*) expr="${arg#name=}" ;;
        esac
    done
    for name in $NAMES; do
        if [[ -z "$expr" || "$name" =~ $expr ]]; then
            printf '%s\n' "$name"
        fi
    done
}

if [[ "${1:-}" == "ps" ]]; then
    [[ " $* " == *" label=com.docker.compose.project="* ]] && exit 0
    NAMES="ods-litellm ods-llama-server ods-download-test-sentinel ods-inspection-blocked-test-sentinel kube-pods-proxy methods-runner ods-pixel-retired-0123456789abcdef"
    emit_filtered "$@"
    exit 0
fi
if [[ "${1:-}" == "volume" && "${2:-}" == "ls" ]]; then
    [[ " $* " == *" label=com.docker.compose.project="* ]] && exit 0
    NAMES="ods_perplexica-data ods-legacy-cache ods_download_test_data ods-download-test-volume k3s_pods methods_cache"
    emit_filtered "$@"
    exit 0
fi
if [[ "${1:-}" == "compose" && " $* " == *" config --format json "* ]]; then
    printf '{"name":"ods","volumes":{}}\n'
    exit 0
fi
if [[ "${1:-}" == "compose" && " $* " == *" down "* ]]; then
    [[ "${DOCKER_DOWN_EXIT_CODE:-0}" == "0" ]] || printf 'fixture Compose diagnostic\n' >&2
    exit "${DOCKER_DOWN_EXIT_CODE:-0}"
fi
if [[ "${1:-}" == "inspect" ]]; then
    printf '[]\n'
    exit 0
fi
exit 0
EOF
    chmod +x "$stub_dir/docker"

    cat > "$stub_dir/systemctl" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == "is-enabled" ]]; then
    exit 1
fi
exit 0
EOF
    chmod +x "$stub_dir/systemctl"

    cat > "$stub_dir/sudo" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${SUDO_LOG:?}"
if [[ "$*" == "-n true" ]]; then
    exit "${SUDO_VALIDATE_EXIT_CODE:-0}"
fi
exit 0
EOF
    chmod +x "$stub_dir/sudo"

    cat > "$stub_dir/id" <<'EOF'
#!/usr/bin/env bash
case "${1:-}" in
    -u|-g) printf '1000\n' ;;
    -un) printf 'fixture-owner\n' ;;
    *) exit 1 ;;
esac
EOF
    chmod +x "$stub_dir/id"

    cat > "$stub_dir/pgrep" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
    chmod +x "$stub_dir/pgrep"

    # This fixture models native Docker cleanup, not WSL task retirement.
    # Keep it isolated from the machine on which the test happens to run.
    cat > "$stub_dir/uname" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == "-r" ]]; then
    printf 'fixture-native-kernel\n'
else
    /usr/bin/uname "$@"
fi
EOF
    chmod +x "$stub_dir/uname"
}

make_install() {
    local install_dir="$1"

    mkdir -p "$install_dir/data" "$install_dir/lib" "$install_dir/systemd"
    cp "$TARGET" "$install_dir/ods-uninstall.sh"
    cp "$ROOT_DIR/lib/safe-env.sh" "$install_dir/lib/safe-env.sh"
    cp "$ROOT_DIR/lib/system-uninstall.sh" "$install_dir/lib/system-uninstall.sh"
    mkdir -p "$install_dir/scripts"
    cp "$ROOT_DIR/scripts/compose-cache-policy.py" "$install_dir/scripts/"
    cp "$ROOT_DIR/scripts/uninstall-compose-volumes.py" "$install_dir/scripts/"
    cp "$ROOT_DIR/scripts/resolve-compose-stack.sh" "$install_dir/scripts/"
    mkdir -p "$install_dir/installers/macos/lib"
    cp "$ROOT_DIR/installers/macos/lib/pixel-native-uninstall.py" "$install_dir/installers/macos/lib/"
    touch "$install_dir/ods-cli"
    touch "$install_dir/docker-compose.base.yml"
    touch "$install_dir/docker-compose.cpu.yml"
    printf '%s\n' '-f docker-compose.base.yml -f docker-compose.cpu.yml' > "$install_dir/.compose-flags"
    printf '%s\n' 'GPU_BACKEND=cpu' > "$install_dir/.env"
}

run_uninstall() {
    local install_dir="$1"
    local home_dir="$2"
    local stub_dir="$3"
    shift 3

    HOME="$home_dir" \
    INSTALL_DIR="$install_dir" \
    PATH="$stub_dir:$PATH" \
    DOCKER_LOG="${DOCKER_LOG:?}" \
    SUDO_LOG="${SUDO_LOG:?}" \
    SUDO_VALIDATE_EXIT_CODE="${SUDO_VALIDATE_EXIT_CODE:-0}" \
    DOCKER_DOWN_EXIT_CODE="${DOCKER_DOWN_EXIT_CODE:-0}" \
    ODS_UNINSTALL_SYSTEMD_DIR="$install_dir/systemd" \
        bash "$install_dir/ods-uninstall.sh" --force "$@" >/dev/null
}

assert_no_name_cleanup() {
    local docker_log="$1"
    if grep -Eq '^(rm|container rm)( |$)|^ps .*--filter name=|^volume ls .*--filter name=' "$docker_log"; then
        fail "uninstall must not discover or remove Docker resources by name"
    fi
}

main() {
    [[ -f "$TARGET" ]] || fail "missing $TARGET"
    # The search text is intentionally literal shell source.
    # shellcheck disable=SC2016
    if grep -qF 'source "$INSTALL_DIR/.env"' "$TARGET"; then
        fail "uninstall must load .env through lib/safe-env.sh, not source it"
    fi

    TMP_DIR="$(mktemp -d -t ods-uninstall-test-XXXXXX)"
    trap 'rm -rf "$TMP_DIR"' EXIT

    local stub_dir="$TMP_DIR/bin"
    mkdir -p "$stub_dir"
    make_stub_bin "$stub_dir"

    # Docker may still have ODS containers even when its CLI has disappeared
    # from this shell's PATH. In that state no ownership or completion proof is
    # possible, so refuse before retiring services or deleting owner data.
    cat > "$TMP_DIR/hide-docker-cli.sh" <<'EOF'
ODS_DOCKER_PROBE_COUNT=0
command() {
    if [[ "${1:-}" == "-v" && "${2:-}" == "docker" ]]; then
        ODS_DOCKER_PROBE_COUNT=$((ODS_DOCKER_PROBE_COUNT + 1))
        if (( ODS_DOCKER_PROBE_COUNT >= ODS_DOCKER_MISSING_AT )); then return 1; fi
    fi
    builtin command "$@"
}
EOF
    local missing_at mode
    for missing_at in 1 2 3; do
        for mode in purge keep-data; do
            local no_docker_install="$TMP_DIR/no-docker-$missing_at-$mode-install"
            local no_docker_home="$TMP_DIR/no-docker-$missing_at-$mode-home"
            local no_docker_log="$TMP_DIR/no-docker-$missing_at-$mode.log"
            local no_docker_sudo="$TMP_DIR/no-docker-$missing_at-$mode-sudo.log"
            make_install "$no_docker_install"
            mkdir -p "$no_docker_home/.local/bin"
            ln -s "$no_docker_install/ods-cli" "$no_docker_home/.local/bin/ods"
            printf 'retain owner data\n' > "$no_docker_install/data/owner.txt"
            cat > "$no_docker_install/lib/pixel-uninstall.sh" <<'EOF'
ods_pixel_uninstall_managed() { touch "$INSTALL_DIR/pixel-retired"; }
EOF
            : > "$no_docker_log"
            : > "$no_docker_sudo"
            local -a no_docker_args=()
            [[ "$mode" == keep-data ]] && no_docker_args=(--keep-data)
            if BASH_ENV="$TMP_DIR/hide-docker-cli.sh" ODS_DOCKER_MISSING_AT="$missing_at" \
                DOCKER_LOG="$no_docker_log" SUDO_LOG="$no_docker_sudo" \
                run_uninstall "$no_docker_install" "$no_docker_home" "$stub_dir" \
                    "${no_docker_args[@]}" 2>"$TMP_DIR/no-docker-error"; then
                fail "uninstall must refuse when Docker CLI disappears at probe $missing_at ($mode)"
            fi
            [[ -f "$no_docker_install/ods-uninstall.sh" &&
               -f "$no_docker_install/data/owner.txt" &&
               -L "$no_docker_home/.local/bin/ods" ]] ||
                fail "missing Docker CLI must retain installed files, data, and links ($missing_at/$mode)"
            if [[ "$missing_at" -lt 3 ]]; then
                [[ ! -e "$no_docker_install/pixel-retired" && ! -s "$no_docker_sudo" ]] ||
                    fail "early missing Docker CLI must refuse before Pixel or sudo ($missing_at/$mode)"
                grep -qiF 'installation untouched' "$TMP_DIR/no-docker-error" ||
                    fail "early Docker refusal must explain intact installation ($missing_at/$mode)"
            else
                grep -qF 'Pixel or host services may already be retired' "$TMP_DIR/no-docker-error" ||
                    fail "late Docker refusal must disclose partial service retirement ($mode)"
            fi
            if grep -Eq ' down |^volume rm ' "$no_docker_log"; then
                fail "missing Docker CLI must not attempt unverified Docker cleanup ($missing_at/$mode)"
            fi
        done
    done
    pass "missing Docker CLI refuses purge and keep-data at all three custody gates"

    # Refusal must precede every privileged/service cleanup and preserve data.
    local unsafe_install="$TMP_DIR/unsafe-install" unsafe_home="$TMP_DIR/unsafe-home"
    local unsafe_docker="$TMP_DIR/unsafe-docker.log" unsafe_sudo="$TMP_DIR/unsafe-sudo.log"
    make_install "$unsafe_install"
    mkdir -p "$unsafe_home" "$unsafe_install/data/user-extensions/example"
    printf '%s\n' '{"services":{"example":{"image":"example/app:1","privileged":true}}}' \
        > "$unsafe_install/data/user-extensions/example/compose.yaml"
    printf '%s\n' '-f docker-compose.base.yml -f data/user-extensions/example/compose.yaml' \
        > "$unsafe_install/.compose-flags"
    printf 'retain owner data\n' > "$unsafe_install/data/owner.txt"
    if DOCKER_LOG="$unsafe_docker" SUDO_LOG="$unsafe_sudo" \
        run_uninstall "$unsafe_install" "$unsafe_home" "$stub_dir" 2>"$TMP_DIR/unsafe-error"; then
        fail "unsafe cached extension must block uninstall"
    fi
    [[ ! -s "$unsafe_docker" && ! -s "$unsafe_sudo" ]] \
        || fail "unsafe recipe rejection must precede Docker and sudo"
    [[ -f "$unsafe_install/ods-uninstall.sh" && -f "$unsafe_install/data/owner.txt" ]] \
        || fail "unsafe recipe rejection must preserve installation and owner data"
    grep -qF 'requires review' "$TMP_DIR/unsafe-error" \
        || fail "unsafe recipe rejection must identify the recipe policy failure"
    pass "unsafe cached recipes are refused before any uninstall mutation"

    rm "$unsafe_install/scripts/compose-cache-policy.py"
    if DOCKER_LOG="$unsafe_docker" SUDO_LOG="$unsafe_sudo" \
        run_uninstall "$unsafe_install" "$unsafe_home" "$stub_dir" 2>"$TMP_DIR/missing-policy-error"; then
        fail "missing security policy must not bypass uninstall validation"
    fi
    [[ ! -s "$unsafe_docker" && ! -s "$unsafe_sudo" && -f "$unsafe_install/data/owner.txt" ]] \
        || fail "missing security policy must retain the installation"
    grep -qF 'complete current ODS checkout' "$TMP_DIR/missing-policy-error" \
        || fail "missing policy failure must explain recovery"
    pass "missing policy fails closed with a recovery instruction"

    local missing_install="$TMP_DIR/missing-install" missing_home="$TMP_DIR/missing-home"
    local missing_docker="$TMP_DIR/missing-docker.log" missing_sudo="$TMP_DIR/missing-sudo.log"
    make_install "$missing_install"
    mkdir -p "$missing_home"
    rm "$missing_install/.compose-flags" "$missing_install/docker-compose.base.yml" \
        "$missing_install/docker-compose.cpu.yml"
    # Model a resolver that cannot select any Compose files, without touching
    # Docker or borrowing the test machine's installed stack.
    printf '#!/bin/bash\nexit 1\n' > "$missing_install/scripts/resolve-compose-stack.sh"
    printf 'retain owner data\n' > "$missing_install/data/owner.txt"
    cat > "$missing_install/lib/pixel-uninstall.sh" <<'EOF'
ods_pixel_uninstall_managed() { touch "$INSTALL_DIR/pixel-retired"; }
EOF
    if DOCKER_LOG="$missing_docker" SUDO_LOG="$missing_sudo" \
        run_uninstall "$missing_install" "$missing_home" "$stub_dir" 2>"$TMP_DIR/missing-error"; then
        fail "missing Compose flags must block uninstall"
    fi
    [[ ! -s "$missing_docker" && ! -s "$missing_sudo" && ! -e "$missing_install/pixel-retired" ]] \
        || fail "missing Compose flags must be refused before Docker, sudo, or Pixel retirement"
    [[ -f "$missing_install/ods-uninstall.sh" && -f "$missing_install/data/owner.txt" ]] \
        || fail "missing Compose flags must preserve installation and owner data"
    grep -qF 'No Compose files resolved; installation untouched' "$TMP_DIR/missing-error" \
        || fail "missing Compose flags must explain the refusal"
    pass "missing Compose flags are refused before uninstall mutation"

    if [[ "$(uname -s)" == "Linux" ]]; then
        local changed_install="$TMP_DIR/changed-install" changed_home="$TMP_DIR/changed-home"
        local changed_docker="$TMP_DIR/changed-docker.log"
        make_install "$changed_install"
        mkdir -p "$changed_home" "$changed_install/data/user-extensions/example"
        printf '%s\n' '{"services":{"example":{"image":"example/app:1"}}}' \
            > "$changed_install/data/user-extensions/example/compose.yaml"
        printf '%s\n' '-f docker-compose.base.yml -f data/user-extensions/example/compose.yaml' \
            > "$changed_install/.compose-flags"
        # Inject recipe drift after the initial preflight, before Compose down.
        cat > "$changed_install/lib/pixel-uninstall.sh" <<'EOF'
ods_pixel_uninstall_managed() {
    printf '%s\n' '{"services":{"example":{"image":"example/app:1","privileged":true}}}' \
        > "$INSTALL_DIR/data/user-extensions/example/compose.yaml"
}
EOF
        if DOCKER_LOG="$changed_docker" SUDO_LOG="$unsafe_sudo" \
            run_uninstall "$changed_install" "$changed_home" "$stub_dir" 2>"$TMP_DIR/changed-error"; then
            fail "recipe drift before Compose down must abort remaining cleanup"
        fi
        if grep -q ' down ' "$changed_docker" || [[ ! -d "$changed_install" ]]; then
            fail "changed recipes must not reach Compose down or data removal"
        fi
        grep -qF 'changed during uninstall' "$TMP_DIR/changed-error" \
            || fail "mid-uninstall drift must explain the partial retirement state"
        pass "recipe drift during retirement is rechecked before Compose down"
    fi

    local install_keep="$TMP_DIR/install-keep"
    local home_keep="$TMP_DIR/home-keep"
    local log_keep="$TMP_DIR/docker-keep.log"
    local sudo_log="$TMP_DIR/sudo.log"
    : > "$sudo_log"
    mkdir -p "$home_keep"
    make_install "$install_keep"
    mkdir -p "$home_keep/.local/bin"
    ln -s "$install_keep/ods-cli" "$home_keep/.local/bin/ods"
    DOCKER_LOG="$log_keep" SUDO_LOG="$sudo_log" run_uninstall "$install_keep" "$home_keep" "$stub_dir" --keep-data

    grep -qF 'compose -f docker-compose.base.yml -f docker-compose.cpu.yml --profile * down --remove-orphans' "$log_keep" \
        || fail "uninstall must use saved .compose-flags and all profiles for docker compose down"
    if grep -qF 'down -v --remove-orphans' "$log_keep"; then
        fail "--keep-data must not remove compose volumes with -v"
    fi
    pass "uninstall uses saved compose flags and preserves volumes with --keep-data"
    assert_no_name_cleanup "$log_keep"
    [[ ! -L "$home_keep/.local/bin/ods" ]] \
        || fail "uninstall must remove the user-level ods CLI symlink"
    pass "uninstall removes user-level ods CLI symlink"

    local install_purge="$TMP_DIR/install-purge"
    local home_purge="$TMP_DIR/home-purge"
    local log_purge="$TMP_DIR/docker-purge.log"
    mkdir -p "$home_purge"
    make_install "$install_purge"
    DOCKER_LOG="$log_purge" SUDO_LOG="$sudo_log" run_uninstall "$install_purge" "$home_purge" "$stub_dir"

    grep -qF 'compose -f docker-compose.base.yml -f docker-compose.cpu.yml --profile * down --remove-orphans' "$log_purge" \
        || fail "normal uninstall must stop Compose with all profiles without deleting volumes before custody review"
    if grep -qF 'down -v' "$log_purge"; then
        fail "normal uninstall must not let Compose delete volumes before custody review"
    fi
    pass "normal uninstall defers volume removal to the custody helper"
    assert_no_name_cleanup "$log_purge"

    local failed_install="$TMP_DIR/failed-install" failed_home="$TMP_DIR/failed-home"
    local failed_docker="$TMP_DIR/failed-docker.log" failed_sudo="$TMP_DIR/failed-sudo.log"
    make_install "$failed_install"
    mkdir -p "$failed_home/.local/bin"
    ln -s "$failed_install/ods-cli" "$failed_home/.local/bin/ods"
    printf 'retain owner data\n' > "$failed_install/data/owner.txt"
    if DOCKER_LOG="$failed_docker" SUDO_LOG="$failed_sudo" DOCKER_DOWN_EXIT_CODE=37 \
        run_uninstall "$failed_install" "$failed_home" "$stub_dir" 2>"$TMP_DIR/failed-error"; then
        fail "Compose down failure must fail uninstall"
    fi
    grep -qF 'compose -f docker-compose.base.yml -f docker-compose.cpu.yml --profile * down --remove-orphans' "$failed_docker" \
        || fail "failure fixture must reach the profile-aware Compose down command"
    assert_no_name_cleanup "$failed_docker"
    [[ -f "$failed_install/ods-uninstall.sh" && -f "$failed_install/data/owner.txt" && \
        -L "$failed_home/.local/bin/ods" ]] \
        || fail "Compose failure must retain remaining installation, data, and CLI link"
    grep -qF 'Docker Compose cleanup failed; remaining installation retained' "$TMP_DIR/failed-error" \
        || fail "Compose failure must explain the incomplete uninstall"
    local diagnostic
    diagnostic="$(sed -n 's/.*Details: \(.*\)$/\1/p' "$TMP_DIR/failed-error" | tail -n 1)"
    if [[ ! -f "$diagnostic" ]] || ! grep -qF 'fixture Compose diagnostic' "$diagnostic"; then
        fail "Compose failure must retain its original diagnostic"
    fi
    grep -qF 'Pixel or host services may already be retired' "$TMP_DIR/failed-error" \
        || fail "Compose failure must disclose the partial retirement state"
    rm -f -- "$diagnostic"
    pass "Compose down failure retains remaining installation without a name-based fallback"

    # A surviving owned container (e.g. a profile-disabled service that
    # `down` did not enumerate) must block install-root deletion and must
    # not be removed by name.
    local survivor_install="$TMP_DIR/survivor-install" survivor_home="$TMP_DIR/survivor-home"
    local survivor_docker="$TMP_DIR/survivor-docker.log" survivor_sudo="$TMP_DIR/survivor-sudo.log"
    local survivor_stub="$TMP_DIR/survivor-bin"
    make_install "$survivor_install"
    mkdir -p "$survivor_home" "$survivor_stub"
    printf 'retain owner data\n' > "$survivor_install/data/owner.txt"
    # Override the docker stub to report a surviving owned container after
    # down, and to fail if any name-based removal is attempted. Use a
    # separate stub directory so earlier tests are unaffected.
    cp "$stub_dir/systemctl" "$stub_dir/sudo" "$stub_dir/id" "$stub_dir/pgrep" "$stub_dir/uname" "$survivor_stub/"
    cat > "$survivor_stub/docker" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${DOCKER_LOG:?}"
if [[ "${1:-}" == "compose" && " $* " == *" config --format json "* ]]; then
    printf '{"name":"ods","volumes":{}}\n'
    exit 0
fi
if [[ "${1:-}" == "compose" && " $* " == *" down "* ]]; then
    exit 0
fi
if [[ "${1:-}" == "ps" ]]; then
    if [[ " $* " == *" label=com.docker.compose.project=ods"* ]]; then
        printf '%s\n' "$(printf 'a%.0s' {1..64})"
        exit 0
    fi
    exit 0
fi
if [[ "${1:-}" == "inspect" ]]; then
    printf '[{"Id":"%s","Config":{"Labels":{"com.docker.compose.project":"ods","com.docker.compose.service":"llama-server","com.docker.compose.project.working_dir":"%s","com.docker.compose.project.config_files":"%s/docker-compose.base.yml"}},"Mounts":[]}]\n' \
        "$(printf 'a%.0s' {1..64})" "$SURVIVOR_INSTALL" "$SURVIVOR_INSTALL"
    exit 0
fi
exit 0
EOF
    chmod +x "$survivor_stub/docker"
    if DOCKER_LOG="$survivor_docker" SUDO_LOG="$survivor_sudo" \
        SURVIVOR_INSTALL="$survivor_install" \
        run_uninstall "$survivor_install" "$survivor_home" "$survivor_stub" 2>"$TMP_DIR/survivor-error"; then
        fail "surviving owned container must block uninstall"
    fi
    [[ -f "$survivor_install/ods-uninstall.sh" && -f "$survivor_install/data/owner.txt" ]] \
        || fail "surviving owned container must retain installation and data"
    grep -qF 'Owned ODS containers remain after Compose down' "$TMP_DIR/survivor-error" \
        || fail "surviving owned container must explain the refusal"
    assert_no_name_cleanup "$survivor_docker"
    pass "surviving owned container blocks uninstall without name-based removal"

    : > "$survivor_docker"
    if DOCKER_LOG="$survivor_docker" SUDO_LOG="$survivor_sudo" \
        SURVIVOR_INSTALL="$survivor_install" \
        run_uninstall "$survivor_install" "$survivor_home" "$survivor_stub" --keep-data 2>"$TMP_DIR/survivor-keep-error"; then
        fail "--keep-data must still reject a surviving owned container"
    fi
    [[ -f "$survivor_install/ods-uninstall.sh" && -f "$survivor_install/data/owner.txt" ]] \
        || fail "--keep-data survivor refusal must retain installation and data"
    grep -qF 'Owned ODS containers remain after Compose down' "$TMP_DIR/survivor-keep-error" \
        || fail "--keep-data must reach the owned-container completion gate"
    grep -qF -- '--profile * down --remove-orphans' "$survivor_docker" \
        || fail "--keep-data must request all profiles"
    if grep -Eq '^volume rm | down -v' "$survivor_docker"; then
        fail "--keep-data survivor refusal must not remove volumes"
    fi
    assert_no_name_cleanup "$survivor_docker"
    pass "--keep-data still rejects surviving containers and retains data"

    mapfile -t sudo_calls < "$sudo_log"
    local sudo_credentials_seen=0
    local sudo_chown_seen=0
    local sudo_call
    for sudo_call in "${sudo_calls[@]}"; do
        if [[ "$sudo_call" == "-v" ]]; then
            sudo_credentials_seen=1
            continue
        fi
        [[ "$sudo_credentials_seen" -eq 1 ]] \
            || fail "uninstall must acquire sudo credentials directly before privileged commands"
        [[ "$sudo_call" == "-n -- "* ]] \
            || fail "privileged uninstall commands must use cached credentials non-interactively"
        if [[ "$sudo_call" == "-n -- chown -R "* ]]; then
            sudo_chown_seen=1
        fi
    done
    [[ "$sudo_credentials_seen" -eq 1 ]] \
        || fail "uninstall must acquire sudo credentials directly before privileged commands"
    [[ "$sudo_chown_seen" -eq 1 ]] \
        || fail "privileged uninstall must chown retained data through cached sudo credentials"
    pass "uninstall separates the interactive sudo prompt from privileged commands"

    local install_noninteractive="$TMP_DIR/install-noninteractive"
    local home_noninteractive="$TMP_DIR/home-noninteractive"
    local log_noninteractive="$TMP_DIR/docker-noninteractive.log"
    local sudo_noninteractive="$TMP_DIR/sudo-noninteractive.log"
    local out_noninteractive="$TMP_DIR/uninstall-noninteractive.out"
    local noninteractive_rc
    mkdir -p "$home_noninteractive"
    make_install "$install_noninteractive"
    : > "$log_noninteractive"
    : > "$sudo_noninteractive"
    set +e
    HOME="$home_noninteractive" \
    INSTALL_DIR="$install_noninteractive" \
    PATH="$stub_dir:$PATH" \
    DOCKER_LOG="$log_noninteractive" \
    SUDO_LOG="$sudo_noninteractive" \
    SUDO_VALIDATE_EXIT_CODE=1 \
        python3 - "$install_noninteractive/ods-uninstall.sh" >"$out_noninteractive" 2>&1 <<'PY'
import subprocess
import sys

try:
    raise SystemExit(subprocess.run(
        ["bash", sys.argv[1], "--force", "--non-interactive"], timeout=5).returncode)
except subprocess.TimeoutExpired:
    raise SystemExit(124)
PY
    noninteractive_rc=$?
    set -e
    [[ "$noninteractive_rc" -ne 0 && "$noninteractive_rc" -ne 124 ]] \
        || fail "non-interactive uninstall must fail promptly when sudo cannot authenticate (rc=$noninteractive_rc)"
    [[ -d "$install_noninteractive" ]] \
        || fail "failed non-interactive sudo preflight must not mutate the install tree"
    if grep -Eq ' down |^volume rm ' "$log_noninteractive"; then
        fail "failed non-interactive sudo preflight must happen before Docker cleanup"
    fi
    grep -qx -- '-n true' "$sudo_noninteractive" \
        || fail "non-interactive uninstall must validate sudo without prompting"
    grep -qF 'Non-interactive uninstall requires cached or passwordless sudo' "$out_noninteractive" \
        || fail "non-interactive sudo failure must explain how to retry"
    pass "non-interactive uninstall fails promptly and before mutation when sudo is unavailable"

    local install_safe="$TMP_DIR/install-safe-env"
    local home_safe="$TMP_DIR/home-safe-env"
    local log_safe="$TMP_DIR/docker-safe-env.log"
    mkdir -p "$home_safe"
    make_install "$install_safe"
    cat > "$install_safe/.env" <<'EOF'
GPU_BACKEND=$(touch "$HOME/uninstall-env-sourced")
EOF
    DOCKER_LOG="$log_safe" SUDO_LOG="$sudo_log" run_uninstall "$install_safe" "$home_safe" "$stub_dir" --keep-data

    if [[ -e "$home_safe/uninstall-env-sourced" ]]; then
        fail "uninstall must not execute command substitutions from .env"
    fi
    pass "uninstall loads .env without executing shell substitutions"

    # These stubs exercise the removed name fallback, not Docker Compose's
    # own project selection or the independent native Pixel retirement helper.
    local name
    for name in ods-download-test-sentinel ods-inspection-blocked-test-sentinel \
        kube-pods-proxy methods-runner ods-pixel-retired-0123456789abcdef \
        ods_download_test_data ods-download-test-volume k3s_pods methods_cache; do
        if grep -Fq "$name" "$log_purge" "$failed_docker" "$log_keep"; then
            fail "uninstall must not pass unrelated or archived resource $name to Docker cleanup"
        fi
    done
    pass "same-prefix resources and native sandbox archives are excluded from name-based cleanup"
}

main "$@"
