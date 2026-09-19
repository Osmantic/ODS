#!/usr/bin/env bash
# Regression: `ods disable` must not rename the compose file when the
# container is still running after a failed `docker compose stop`.
# Previously the stop was `|| true`, so a daemon-side stop failure left a
# running container whose compose file had been renamed to .disabled —
# an unmanaged orphan that `ods stop`/`ods start` could no longer reach.
#
# Strategy: fixture install dir + a `docker` stub on PATH whose
# `compose ... stop` fails. Scenario A reports the container still running
# (disable must abort); scenario B reports nothing running (disable must
# proceed — stop can fail for an already-absent container).

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

PASSED=0
FAILED=0
pass() { printf 'PASS: %s\n' "$1"; PASSED=$((PASSED + 1)); }
fail() { printf 'FAIL: %s\n' "$1" >&2; FAILED=$((FAILED + 1)); }

make_install() {
    local install_dir="$1"
    mkdir -p \
        "$install_dir/lib" \
        "$install_dir/scripts" \
        "$install_dir/extensions/services/fakesvc" \
        "$install_dir/bin"

    cp "$ROOT_DIR/ods-cli" "$install_dir/ods-cli"
    cp "$ROOT_DIR/lib/service-registry.sh" "$install_dir/lib/"
    cp "$ROOT_DIR/lib/python-cmd.sh" "$install_dir/lib/"
    cp "$ROOT_DIR/lib/safe-env.sh" "$install_dir/lib/"
    cat > "$install_dir/scripts/resolve-compose-stack.sh" <<'RESOLVER'
#!/usr/bin/env bash
printf '%s\n' "-f docker-compose.base.yml"
RESOLVER
    chmod +x "$install_dir/scripts/resolve-compose-stack.sh"

    cat > "$install_dir/extensions/services/fakesvc/manifest.yaml" <<'MANIFEST'
schema_version: ods.services.v1
service:
  id: fakesvc
  name: Fake Service
  container_name: ods-fakesvc
  port: 18080
  type: docker
  compose_file: compose.yaml
  category: optional
MANIFEST
    printf 'services:\n  fakesvc:\n    image: fake:latest\n' \
        > "$install_dir/extensions/services/fakesvc/compose.yaml"

    printf 'services: {}\n' > "$install_dir/docker-compose.base.yml"
    printf 'GPU_BACKEND=cpu\nODS_MODE=local\n' > "$install_dir/.env"
}

# docker stub: `compose ... stop` always fails; `ps` prints $DOCKER_PS_NAMES.
make_docker_stub() {
    local bin_dir="$1"
    cat > "$bin_dir/docker" <<'STUB'
#!/usr/bin/env bash
case "${1:-}" in
    compose)
        # any compose subcommand used on this path fails
        exit 1
        ;;
    ps)
        printf '%s\n' "${DOCKER_PS_NAMES:-}"
        ;;
    *)
        exit 0
        ;;
esac
STUB
    chmod +x "$bin_dir/docker"
}

# --- Scenario A: stop fails AND container still running → abort, keep compose
inst_a="$TMP_DIR/still-running"
make_install "$inst_a"
make_docker_stub "$inst_a/bin"

rc=0
DOCKER_PS_NAMES="ods-fakesvc" ODS_HOME="$inst_a" PATH="$inst_a/bin:$PATH" \
    bash "$inst_a/ods-cli" disable fakesvc >/dev/null 2>&1 || rc=$?

if [[ $rc -ne 0 && -f "$inst_a/extensions/services/fakesvc/compose.yaml" \
   && ! -e "$inst_a/extensions/services/fakesvc/compose.yaml.disabled" ]]; then
    pass "disable aborts and keeps compose.yaml when the container is still running"
else
    fail "disable orphaned the running container (rc=$rc, compose moved)"
fi

# --- Scenario B: stop fails and nothing is running → disable proceeds
inst_b="$TMP_DIR/not-running"
make_install "$inst_b"
make_docker_stub "$inst_b/bin"

rc=0
DOCKER_PS_NAMES="" ODS_HOME="$inst_b" PATH="$inst_b/bin:$PATH" \
    bash "$inst_b/ods-cli" disable fakesvc >/dev/null 2>&1 || rc=$?

if [[ $rc -eq 0 && -f "$inst_b/extensions/services/fakesvc/compose.yaml.disabled" ]]; then
    pass "disable proceeds when the failed stop left nothing running"
else
    fail "disable refused a safe rename (rc=$rc)"
fi

echo ""
echo "Results: $PASSED passed, $FAILED failed"
exit "$FAILED"
