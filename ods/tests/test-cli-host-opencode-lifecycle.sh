#!/usr/bin/env bash
# Regression: OpenCode host-systemd lifecycle through the real CLI subprocess.
# Stubs only external systemctl/docker/uname; never mocks the function under test.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }

make_install() {
    local install_dir="$1"
    mkdir -p \
        "$install_dir/lib" \
        "$install_dir/scripts" \
        "$install_dir/extensions/services/opencode" \
        "$install_dir/extensions/services/docker-test" \
        "$install_dir/extensions/services/pixel-agent" \
        "$install_dir/bin" \
        "$install_dir/data"

    cp "$ROOT_DIR/ods-cli" "$install_dir/ods-cli"
    cp "$ROOT_DIR/lib/service-registry.sh" "$install_dir/lib/"
    cp "$ROOT_DIR/lib/python-cmd.sh" "$install_dir/lib/"
    cp "$ROOT_DIR/lib/safe-env.sh" "$install_dir/lib/"

    cat > "$install_dir/scripts/resolve-compose-stack.sh" <<'RESOLVER'
#!/usr/bin/env bash
set -euo pipefail
script_dir=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --script-dir) script_dir="$2"; shift 2 ;;
        *) shift ;;
    esac
done
flags="-f docker-compose.base.yml"
for svc in opencode docker-test pixel-agent; do
    if [[ -f "$script_dir/extensions/services/$svc/compose.yaml" ]]; then
        flags="$flags -f extensions/services/$svc/compose.yaml"
    fi
done
printf '%s\n' "$flags"
RESOLVER
    chmod +x "$install_dir/scripts/resolve-compose-stack.sh"

    cat > "$install_dir/extensions/services/opencode/manifest.yaml" <<'MANIFEST'
schema_version: ods.services.v1
service:
  id: opencode
  name: OpenCode
  aliases: [opencode-web]
  category: optional
  type: host-systemd
  gpu_backends: all
MANIFEST

    cat > "$install_dir/extensions/services/docker-test/manifest.yaml" <<'MANIFEST'
schema_version: ods.services.v1
service:
  id: docker-test
  name: ODS Proxy
  category: optional
  type: docker
  gpu_backends: all
MANIFEST
    printf 'services: {}\n' > "$install_dir/extensions/services/docker-test/compose.yaml"

    cat > "$install_dir/extensions/services/pixel-agent/manifest.yaml" <<'MANIFEST'
schema_version: ods.services.v1
service:
  id: pixel-agent
  name: Pixel
  category: core
  type: host-systemd
  gpu_backends: all
MANIFEST
    printf 'services: {}\n' > "$install_dir/extensions/services/pixel-agent/compose.yaml"

    printf 'services: {}\n' > "$install_dir/docker-compose.base.yml"
    printf '%s\n' \
        'GPU_BACKEND=cpu' \
        'GPU_COUNT=1' \
        'ODS_MODE=local' \
        'TIER=1' \
        > "$install_dir/.env"
    printf '%s\n' '-f docker-compose.base.yml' > "$install_dir/.compose-flags"

    mkdir -p "$install_dir/data/opencode"
    printf 'sentinel-auth\n' > "$install_dir/data/opencode/auth.json"
    printf 'sentinel-config\n' > "$install_dir/data/opencode/config.json"

    cat > "$install_dir/bin/systemctl" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
log="${ODS_TEST_SYSTEMCTL_LOG:?}"
printf 'systemctl %s\n' "$*" >> "$log"
if [[ "$1" == "--user" ]]; then shift; fi
case "$1" in
    show)
        state="${ODS_TEST_LOAD_STATE:-loaded}"
        if [[ "${ODS_TEST_SHOW_FAIL:-0}" == "1" ]]; then exit 1; fi
        printf '%s\n' "$state"
        ;;
    enable|disable|start|stop|restart)
        if [[ "${ODS_TEST_ACTION_FAIL:-0}" == "1" ]]; then exit 1; fi
        case "$1" in
            enable) printf 'enabled active\n' > "${ODS_TEST_UNIT_STATE:?}" ;;
            disable) printf 'disabled inactive\n' > "${ODS_TEST_UNIT_STATE:?}" ;;
            start|restart) printf 'active\n' > "${ODS_TEST_UNIT_STATE:?}" ;;
            stop) printf 'inactive\n' > "${ODS_TEST_UNIT_STATE:?}" ;;
        esac
        ;;
    *) exit 0 ;;
esac
STUB
    chmod +x "$install_dir/bin/systemctl"

    cat > "$install_dir/bin/docker" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
log="${ODS_TEST_DOCKER_LOG:?}"
printf 'docker %s\n' "$*" >> "$log"
exit 0
STUB
    chmod +x "$install_dir/bin/docker"

    cat > "$install_dir/bin/uname" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "${ODS_TEST_UNAME:-Linux}"
STUB
    chmod +x "$install_dir/bin/uname"
}

run_cli() {
    local install_dir="$1"; shift
    ODS_HOME="$install_dir" \
    PATH="$install_dir/bin:$PATH" \
    ODS_TEST_SYSTEMCTL_LOG="$install_dir/systemctl.log" \
    ODS_TEST_DOCKER_LOG="$install_dir/docker.log" \
    ODS_TEST_UNIT_STATE="$install_dir/unit-state" \
    ODS_TEST_LOAD_STATE="${ODS_TEST_LOAD_STATE:-loaded}" \
    ODS_TEST_SHOW_FAIL="${ODS_TEST_SHOW_FAIL:-0}" \
    ODS_TEST_ACTION_FAIL="${ODS_TEST_ACTION_FAIL:-0}" \
    ODS_TEST_UNAME="${ODS_TEST_UNAME:-Linux}" \
    bash "$install_dir/ods-cli" "$@"
}

snapshot() {
    local install_dir="$1"
    {
        cat "$install_dir/.env" 2>/dev/null || true
        echo '---'
        cat "$install_dir/.compose-flags" 2>/dev/null || true
        echo '---'
        cat "$install_dir/data/opencode/auth.json" 2>/dev/null || true
        echo '---'
        cat "$install_dir/data/opencode/config.json" 2>/dev/null || true
    } | sha256sum | cut -d' ' -f1
}

assert_no_docker() {
    local install_dir="$1" label="$2"
    if [[ -s "$install_dir/docker.log" ]]; then
        fail "$label: unexpected docker calls: $(cat "$install_dir/docker.log")"
    fi
}

assert_no_native_action() {
    local install_dir="$1" label="$2"
    if grep -Eq 'systemctl --user (enable|disable|start|stop|restart|unmask) ' "$install_dir/systemctl.log"; then
        fail "$label: an invalid service state caused a native action"
    fi
}

assert_systemctl_has() {
    local install_dir="$1" label="$2" needle="$3"
    grep -Fq -- "$needle" "$install_dir/systemctl.log" \
        || fail "$label: systemctl log missing '$needle'"
}

# --- Case 1: canonical + alias lifecycle, no docker, snapshot unchanged ---
for action in enable disable start stop restart; do
    for name in opencode opencode-web; do
        install_dir="$TMP_DIR/lifecycle-$action-$name"
        make_install "$install_dir"
        before="$(snapshot "$install_dir")"
        if ! run_cli "$install_dir" "$action" "$name" >"$install_dir/cli-output" 2>&1; then
            cat "$install_dir/cli-output" >&2
            fail "$action $name: expected success"
        fi
        after="$(snapshot "$install_dir")"
        [[ "$before" == "$after" ]] || fail "$action $name: fixture snapshot changed"
        assert_no_docker "$install_dir" "$action $name"
        case "$action" in
            enable|disable) assert_systemctl_has "$install_dir" "$action $name" "$action --now opencode-web.service" ;;
            *) assert_systemctl_has "$install_dir" "$action $name" "$action opencode-web.service" ;;
        esac
        assert_systemctl_has "$install_dir" "$action $name" "show -p LoadState --value opencode-web.service"
    done
done
pass "canonical and alias lifecycle actions succeed without docker"

# --- Case 2: repeated enable idempotent at stub unit state ---
install_dir="$TMP_DIR/idempotent"
make_install "$install_dir"
run_cli "$install_dir" enable opencode >/dev/null 2>&1
first_state="$(cat "$install_dir/unit-state")"
run_cli "$install_dir" enable opencode >/dev/null 2>&1
[[ "$first_state" == "enabled active" && "$(cat "$install_dir/unit-state")" == "$first_state" ]] || fail "enable changed the enabled active state"
count="$(grep -c 'enable --now opencode-web.service' "$install_dir/systemctl.log" || true)"
[[ "$count" == "2" ]] || fail "repeated enable: expected 2 enable calls, got $count"
assert_no_docker "$install_dir" "repeated enable"
pass "repeated enable is idempotent at stub unit state"

# --- Case 3: --rebuild-images rejected before systemctl show/mutation ---
for action in start restart; do
    install_dir="$TMP_DIR/rebuild-$action"
    make_install "$install_dir"
    if run_cli "$install_dir" "$action" opencode --rebuild-images >/dev/null 2>&1; then
        fail "$action --rebuild-images: expected rejection"
    fi
    if [[ -s "$install_dir/systemctl.log" ]]; then
        fail "$action --rebuild-images: systemctl was invoked"
    fi
    assert_no_docker "$install_dir" "$action --rebuild-images"
done
pass "--rebuild-images rejected before systemctl show or mutation"

# --- Case 4: missing / masked / show failure / action failure ---
install_dir="$TMP_DIR/missing"
make_install "$install_dir"
if ODS_TEST_LOAD_STATE=not-found run_cli "$install_dir" start opencode >/dev/null 2>&1; then
    fail "missing service: expected nonzero"
fi
assert_no_docker "$install_dir" "missing service"
assert_no_native_action "$install_dir" "missing service"

install_dir="$TMP_DIR/masked"
make_install "$install_dir"
if ODS_TEST_LOAD_STATE=masked run_cli "$install_dir" start opencode >/dev/null 2>&1; then
    fail "masked service: expected nonzero"
fi
assert_no_docker "$install_dir" "masked service"
assert_no_native_action "$install_dir" "masked service"

install_dir="$TMP_DIR/showfail"
make_install "$install_dir"
if ODS_TEST_SHOW_FAIL=1 run_cli "$install_dir" start opencode >/dev/null 2>&1; then
    fail "show failure: expected nonzero"
fi
assert_no_docker "$install_dir" "show failure"
assert_no_native_action "$install_dir" "show failure"

install_dir="$TMP_DIR/actionfail"
make_install "$install_dir"
if ODS_TEST_ACTION_FAIL=1 run_cli "$install_dir" start opencode >/dev/null 2>&1; then
    fail "action failure: expected nonzero"
fi
assert_no_docker "$install_dir" "action failure"
pass "missing/masked/show-fail/action-fail all nonzero without docker"

# --- Case 5: core Pixel disable rejected, no native core actions ---
install_dir="$TMP_DIR/core"
make_install "$install_dir"
if run_cli "$install_dir" disable pixel-agent >/dev/null 2>&1; then
    fail "core disable: expected rejection"
fi
if [[ -s "$install_dir/systemctl.log" ]]; then
    fail "core disable: native systemctl was invoked"
fi
assert_no_docker "$install_dir" "core disable"
pass "core service disable rejected without native actions"

# --- Case 6: docker service stop uses compose; enable/disable marker behavior ---
install_dir="$TMP_DIR/docker-stop"
make_install "$install_dir"
run_cli "$install_dir" stop docker-test >/dev/null 2>&1
grep -Fq 'docker compose' "$install_dir/docker.log" \
    || fail "docker stop: compose not invoked"
grep -Fq 'stop docker-test' "$install_dir/docker.log" \
    || fail "docker stop: service not passed to compose"

install_dir="$TMP_DIR/docker-enable"
make_install "$install_dir"
mv "$install_dir/extensions/services/docker-test/compose.yaml" \
   "$install_dir/extensions/services/docker-test/compose.yaml.disabled"
run_cli "$install_dir" enable docker-test >/dev/null 2>&1
[[ -f "$install_dir/extensions/services/docker-test/compose.yaml" ]] \
    || fail "docker enable: compose.yaml not restored"
[[ ! -f "$install_dir/extensions/services/docker-test/compose.yaml.disabled" ]] \
    || fail "docker enable: disabled marker not removed"

run_cli "$install_dir" disable docker-test >/dev/null 2>&1
[[ -f "$install_dir/extensions/services/docker-test/compose.yaml.disabled" ]] \
    || fail "docker disable: disabled marker not created"
pass "docker service stop uses compose; enable/disable marker behavior intact"

# --- Case 7: registry manifest type host-systemd vs absent type docker ---
install_dir="$TMP_DIR/registry"
make_install "$install_dir"
cat > "$install_dir/extensions/services/opencode/manifest.yaml" <<'MANIFEST'
schema_version: ods.services.v1
service:
  id: opencode
  name: OpenCode
  aliases: [opencode-web]
  category: optional
  type: host-systemd
  gpu_backends: all
MANIFEST
cat > "$install_dir/extensions/services/docker-test/manifest.yaml" <<'MANIFEST'
schema_version: ods.services.v1
service:
  id: docker-test
  name: ODS Proxy
  category: optional
  gpu_backends: all
MANIFEST
run_cli "$install_dir" start opencode >/dev/null 2>&1
assert_systemctl_has "$install_dir" "registry host-systemd" "start opencode-web.service"
assert_no_docker "$install_dir" "registry host-systemd"

install_dir="$TMP_DIR/registry-docker"
make_install "$install_dir"
sed -i '/  type: docker/d' "$install_dir/extensions/services/docker-test/manifest.yaml"
run_cli "$install_dir" start docker-test >/dev/null 2>&1
grep -Fq 'docker compose' "$install_dir/docker.log" \
    || fail "registry absent type: docker path not taken"
if [[ -s "$install_dir/systemctl.log" ]]; then
    fail "registry absent type: systemctl invoked for docker service"
fi
pass "registry type host-systemd vs absent type docker routing correct"

printf 'ALL PASS\n'
