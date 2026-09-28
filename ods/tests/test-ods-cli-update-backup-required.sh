#!/usr/bin/env bash
# An update must have a restorable pre-update snapshot before changing state.
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT

run_case() {
    local mode="$1" install="$tmp/$1/install" bin="$tmp/$1/bin" env_home="$tmp/$1/home" status
    mkdir -p "$install" "$bin" "$env_home"
    cp "$root_dir/docker-compose.base.yml" "$install/docker-compose.base.yml"
    cp "$root_dir/manifest.json" "$install/manifest.json"
    # A stale cache is deleted by get_compose_flags; it must survive a
    # failed snapshot byte for byte, just like the environment file.
    printf '%s\n' '-f missing-old-compose.yml' > "$install/.compose-flags"
    cp "$install/.compose-flags" "$tmp/$mode/flags-before"
    cat > "$install/.env" <<'ENV'
ODS_VERSION=3.0.0
ODS_MODE=local
GPU_BACKEND=cpu
GPU_COUNT=1
TIER=1
ENV
    cp "$install/.env" "$tmp/$mode/env-before"
    if [[ "$mode" == real_failure || "$mode" == retention_zero ]]; then
        cp "$root_dir/ods-update.sh" "$install/ods-update.sh"
        if [[ "$mode" == real_failure ]]; then : > "$env_home/.ods"; fi
    elif [[ "$mode" == success ]]; then
        cat > "$install/ods-update.sh" <<'BACKUP'
#!/usr/bin/env bash
cp .env "${TEST_BACKUP_CAPTURE:?}/snapshot.env"
cp .compose-flags "${TEST_BACKUP_CAPTURE:?}/snapshot.flags"
exit 0
BACKUP
        chmod +x "$install/ods-update.sh"
    elif [[ "$mode" != missing ]]; then
        cat > "$install/ods-update.sh" <<'BACKUP'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${TEST_BACKUP_LOG:?}"
exit 42
BACKUP
        chmod +x "$install/ods-update.sh"
    fi
    cat > "$bin/docker" <<'DOCKER'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${TEST_DOCKER_LOG:?}"
if [[ "${1:-}" == info && "$*" == *NCPU* ]]; then printf '16\n'; fi
if [[ "${TEST_SUCCESS:-}" == 1 && "${1:-}" == compose ]]; then
    case " $* " in
        *' config --format json '*) printf '%s\n' '{"services":{"dashboard":{"image":"ods-dashboard:local"},"open-webui":{"image":"ghcr.io/open-webui/open-webui:main"}}}'; exit 0 ;;
        *' config --services '*) printf '%s\n' dashboard; exit 0 ;;
        *' ps --services --status running '*) printf '%s\n' dashboard; exit 0 ;;
    esac
fi
exit 0
DOCKER
    cat > "$bin/sleep" <<'SLEEP'
#!/usr/bin/env bash
exit 0
SLEEP
    chmod +x "$bin/docker" "$bin/sleep"
    : > "$tmp/$mode/backup.log"
    : > "$tmp/$mode/docker.log"
    set +e
    PATH="$bin:$PATH" HOME="$env_home" ODS_HOME="$install" NO_COLOR=1 \
        MAX_BACKUPS="$([[ "$mode" == retention_zero ]] && echo 0 || echo 10)" \
        TEST_SUCCESS="$([[ "$mode" == success ]] && echo 1 || echo 0)" \
        TEST_BACKUP_CAPTURE="$tmp/$mode" \
        TEST_BACKUP_LOG="$tmp/$mode/backup.log" TEST_DOCKER_LOG="$tmp/$mode/docker.log" \
        bash "$root_dir/ods-cli" update --force > "$tmp/$mode/update.log" 2>&1
    status=$?
    set -e
    if [[ "$mode" == success ]]; then
        if [[ "$status" != 0 ]] || ! cmp -s "$tmp/$mode/env-before" "$tmp/$mode/snapshot.env" \
            || ! cmp -s "$tmp/$mode/flags-before" "$tmp/$mode/snapshot.flags" \
            || ! grep -q '^SHIELD_API_KEY=' "$install/.env" \
            || ! grep -Eq 'compose .* (pull|up)($| )' "$tmp/$mode/docker.log"; then
            cat "$tmp/$mode/update.log" >&2
            printf '[FAIL] successful update lacked pre-mutation snapshot or normal runtime work\n' >&2
            return 1
        fi
        printf '[PASS] successful snapshot captures prior config before normal update\n'
        return 0
    fi
    if [[ "$status" == 0 ]] || ! cmp -s "$tmp/$mode/env-before" "$install/.env" \
        || ! cmp -s "$tmp/$mode/flags-before" "$install/.compose-flags" \
        || grep -Eq 'compose .* (pull|up)($| )' "$tmp/$mode/docker.log"; then
        cat "$tmp/$mode/update.log" >&2
        printf '[FAIL] %s: update continued or modified state without snapshot\n' "$mode" >&2
        return 1
    fi
    if [[ "$mode" == failing ]] && ! grep -q '^backup ' "$tmp/$mode/backup.log"; then
        printf '[FAIL] failing snapshot helper was not called\n' >&2
        return 1
    fi
    if [[ "$mode" == real_failure ]] && ! grep -q 'Creating backup:' "$tmp/$mode/update.log"; then
        printf '[FAIL] shipped snapshot helper was not reached\n' >&2
        return 1
    fi
    if [[ "$mode" == retention_zero ]] && ! grep -q 'MAX_BACKUPS must be a positive integer' "$tmp/$mode/update.log"; then
        printf '[FAIL] zero-retention snapshot was not rejected\n' >&2
        return 1
    fi
    printf '[PASS] %s snapshot aborts before state change\n' "$mode"
}

run_case failing
run_case missing
run_case real_failure
run_case retention_zero
run_case success
