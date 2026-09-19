#!/usr/bin/env bash
# Regression: Linux/macOS `restart` must replace running containers so the
# process is actually restarted and receives current Compose environment.

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root_dir="$(cd "$script_dir/.." && pwd)"
tmp_dir="$(mktemp -d)"
install_dir="$tmp_dir/install"
bin_dir="$tmp_dir/bin"
docker_log="$tmp_dir/docker.log"

cleanup() {
    local pid_file="$install_dir/data/.llama-server.pid"
    if [[ -f "$pid_file" ]]; then
        kill "$(cat "$pid_file")" 2>/dev/null || true
    fi
    rm -rf "$tmp_dir"
}
trap cleanup EXIT

mkdir -p "$install_dir/data/models" "$install_dir/bin" "$bin_dir"
cp "$root_dir/docker-compose.base.yml" "$install_dir/docker-compose.base.yml"
printf '%s\n' '-f docker-compose.base.yml' > "$install_dir/.compose-flags"
# A bare `restart` also restarts the native llama-server on macOS, which
# resolves the active model and requires an executable runtime. The fixture
# provides both so the native leg can complete instead of dying at model
# resolution.
: > "$install_dir/data/models/Qwen3.5-9B-Q4_K_M.gguf"
printf '%s\n' \
    'ODS_VERSION=2.6.0' \
    'ODS_MODE=local' \
    'GPU_BACKEND=apple' \
    'GPU_COUNT=1' \
    'TIER=1' \
    'GGUF_FILE=Qwen3.5-9B-Q4_K_M.gguf' \
    'ODS_NATIVE_LLAMA_PORT=18347' \
    'LLAMA_CPU_LIMIT=8.0' \
    'LLAMA_CPU_RESERVATION=2.0' \
    'HERMES_CPU_LIMIT=4.0' \
    'HERMES_CPU_RESERVATION=0.5' \
    > "$install_dir/.env"

cat > "$install_dir/bin/llama-server" <<'LLAMA_STUB'
#!/usr/bin/env bash
# Minimal llama-server stand-in: honour --host/--port and answer /health.
set -euo pipefail
host="127.0.0.1"
port="8080"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --host) host="$2"; shift 2 ;;
        --port) port="$2"; shift 2 ;;
        *) shift ;;
    esac
done
exec python3 - "$host" "$port" <<'PYEOF'
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("content-length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_args):
        pass


HTTPServer((sys.argv[1], int(sys.argv[2])), Handler).serve_forever()
PYEOF
LLAMA_STUB
chmod +x "$install_dir/bin/llama-server"

printf '%s\n' '#!/usr/bin/env bash' \
    'set -euo pipefail' \
    'printf '\''%s\n'\'' "$*" >> "${TEST_DOCKER_LOG:?}"' \
    'case "${1:-}" in' \
    '    info)' \
    '        [[ "$*" == *NCPU* ]] && printf '\''16\n'\''' \
    '        exit 0' \
    '        ;;' \
    '    ps) exit 0 ;;' \
    '    compose)' \
    '        [[ " $* " == *" up -d "* ]] && exit 0' \
    '        ;;' \
    'esac' \
    'printf '\''unexpected docker invocation: %s\n'\'' "$*" >&2' \
    'exit 1' \
    > "$bin_dir/docker"
chmod +x "$bin_dir/docker"

run_restart_contract() {
    local platform="$1"
    local cli="$2"
    local output="$tmp_dir/${platform}.out"
    : > "$docker_log"

    PATH="$bin_dir:$PATH" \
    ODS_HOME="$install_dir" \
    NO_COLOR=1 \
    TEST_DOCKER_LOG="$docker_log" \
        "$BASH" "$cli" restart dashboard > "$output" 2>&1 || {
            sed -n '1,200p' "$output" >&2
            return 1
        }
    grep -Eq 'compose .*docker-compose.base.yml up -d --force-recreate --no-build --pull never dashboard$' "$docker_log" || {
        sed -n '1,200p' "$docker_log" >&2
        printf '[FAIL] %s single-service restart did not replace the container from its pinned image\n' "$platform" >&2
        return 1
    }

    : > "$docker_log"
    PATH="$bin_dir:$PATH" \
    ODS_HOME="$install_dir" \
    NO_COLOR=1 \
    TEST_DOCKER_LOG="$docker_log" \
        "$BASH" "$cli" restart > "$output" 2>&1 || {
            sed -n '1,200p' "$output" >&2
            return 1
        }
    grep -Eq 'compose .*docker-compose.base.yml up -d --force-recreate --no-build --pull never$' "$docker_log" || {
        sed -n '1,200p' "$docker_log" >&2
        printf '[FAIL] %s all-service restart did not replace containers from pinned images\n' "$platform" >&2
        return 1
    }
}

run_restart_contract linux "$root_dir/ods-cli"
run_restart_contract macos "$root_dir/installers/macos/ods-macos.sh"

printf '[PASS] Linux and macOS restart recreate env-backed containers\n'
