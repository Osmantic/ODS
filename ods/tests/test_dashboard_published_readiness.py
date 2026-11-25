"""Real loopback probes with production port checks and summary pipeline.

Docker ownership/health is a fixture; no daemon or installed services are used.
"""
import http.server
import os
from pathlib import Path
import shlex
import socket
import subprocess
import tempfile
import threading
import unittest


ROOT = Path(os.environ.get("ODS_TEST_SOURCE_ROOT", Path(__file__).resolve().parents[1]))
PHASE = (ROOT / "installers/phases/04-requirements.sh").read_text()


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class PublishedReadiness(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.addCleanup(self.listener.close)
        self.occupied = self.listener.getsockname()[1]
        self.local = free_port()

    def ports(self, remote=None, saved="", owner="", extra=""):
        (self.root / ".env").write_text(saved)
        # Extract all actual port functions and the required preflight call.
        block = PHASE[PHASE.index("_port_check_warned=false"):PHASE.index("# Ollama conflict detection")]
        source = self.root / "ports.sh"
        source.write_text(block)
        env = {k: v for k, v in os.environ.items()
               if k not in ("DASHBOARD_PORT", "DASHBOARD_REMOTE_PORT")}
        env.update(DASHBOARD_PORT=str(self.local), INSTALL_DIR=str(self.root), OWNER=owner)
        if remote is not None:
            env["DASHBOARD_REMOTE_PORT"] = str(remote)
        script = f'''set -euo pipefail
source {shlex.quote(str(ROOT / 'lib/safe-env.sh'))}
source {shlex.quote(str(ROOT / 'installers/lib/external-services.sh'))}
warn() {{ printf '%s\\n' "$*" >&2; }}
sudo() {{ return 1; }}
docker() {{
    if [[ "$1 $2" == 'container inspect' ]]; then
        [[ -n "$OWNER" ]] || return 1
        printf '%s\\n' "$OWNER"
    elif [[ "$1" == ps ]]; then
        printf 'same-project-other-service\\n'
    elif [[ "$1" == inspect ]]; then
        printf '/different-install|ods\\n'
    else return 1; fi
}}
{extra}
source {shlex.quote(str(source))}
printf 'accepted:%s:%s\\n' "$DASHBOARD_PORT" "$DASHBOARD_REMOTE_PORT"
'''
        return subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=15)

    def test_real_foreign_listener_refuses_before_continuation(self):
        result = self.ports(self.occupied)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("DASHBOARD_REMOTE_PORT", result.stderr)
        self.assertNotIn("accepted:", result.stdout)

    def test_real_local_listener_also_refuses(self):
        self.local = self.occupied
        result = self.ports(free_port())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DASHBOARD_PORT", result.stderr)

    def test_saved_quoted_alternate_is_checked_and_exported(self):
        alternate = free_port()
        result = self.ports(saved=f'DASHBOARD_REMOTE_PORT="{alternate}" # owner choice\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"accepted:{self.local}:{alternate}", result.stdout)

    def test_explicit_alternate_wins_over_occupied_saved_port(self):
        alternate = free_port()
        result = self.ports(alternate, saved=f"DASHBOARD_REMOTE_PORT={self.occupied}\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f":{alternate}", result.stdout)

    def test_exact_running_dashboard_with_actual_publication_can_rerun(self):
        result = self.ports(self.occupied, owner=f"true|ods|dashboard|{self.root}|{self.occupied} ")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_noninteractive_sudo_docker_can_prove_existing_dashboard(self):
        extra = '''docker() { return 1; }
sudo() {
    [[ "$1 $2 $3 $4" == '-n docker container inspect' ]] || return 1
    printf '%s\\n' "$OWNER"
}'''
        result = self.ports(self.occupied, owner=f"true|ods|dashboard|{self.root}|{self.occupied} ", extra=extra)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_sudo_inspection_still_refuses_unowned_published_port(self):
        extra = '''docker() { return 1; }
sudo() {
    [[ "$1 $2 $3 $4" == '-n docker container inspect' ]] || return 1
    printf '%s\\n' "$OWNER"
}'''
        result = self.ports(self.occupied, owner=f"true|other|dashboard|{self.root}|{self.occupied} ", extra=extra)
        self.assertNotEqual(result.returncode, 0)

    def test_unconnected_foreign_or_stopped_dashboard_cannot_exempt_listener(self):
        for owner in (f"true|ods|dashboard|{self.root}|",
                      f"false|ods|dashboard|{self.root}|{self.occupied}",
                      f"true|ods|dashboard|/different-root|{self.occupied}",
                      f"true|foreign|dashboard|{self.root}|{self.occupied}",
                      f"true|ods|other-service|{self.root}|{self.occupied}"):
            with self.subTest(owner=owner):
                result = self.ports(self.occupied, owner=owner)
                self.assertNotEqual(result.returncode, 0)

    def test_invalid_or_duplicate_ports_refuse(self):
        for port in ("0", "65536", "03011", "3011; touch unwanted", str(self.local)):
            with self.subTest(port=port):
                result = self.ports(port)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("accepted:", result.stdout)
        self.assertFalse((self.root / "unwanted").exists())

    def test_windows_host_only_listener_refuses(self):
        alternate = free_port()
        result = self.ports(alternate, extra=f'ods_windows_host_port_in_use() {{ [[ "$1" == {alternate} ]]; }}')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Windows host process", result.stderr)

    def summary(self, rows, required="Dashboard"):
        script = f'''set -euo pipefail
source {shlex.quote(str(ROOT / 'installers/lib/readiness-summary.sh'))}
docker() {{ printf 'healthy\\n'; }}
ods_readiness_summary 'ods status' '' 'http://localhost:3001' {shlex.quote(required)}
printf 'success card reached\\n'
'''
        return subprocess.run(["bash", "-c", script], input=rows, text=True, capture_output=True, timeout=15)

    def test_internal_health_does_not_pass_unreachable_required_dashboard(self):
        result = self.summary(f"Dashboard|http://127.0.0.1:{free_port()}/|ods-dashboard|\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("container healthy, HTTP 000", result.stdout)
        self.assertNotIn("success card reached", result.stdout)

    def test_missing_required_service_does_not_pass(self):
        for rows in ("", f"Optional|http://127.0.0.1:{free_port()}/|optional|\n"):
            self.assertNotEqual(self.summary(rows).returncode, 0)

    def test_diagnostic_only_caller_preserves_optional_failure_behavior(self):
        result = self.summary(f"Optional|http://127.0.0.1:{free_port()}/|optional|\n", required="")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_real_http_dashboard_passes_even_with_optional_failure(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"dashboard fixture")

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = self.summary(f"Dashboard|http://127.0.0.1:{server.server_port}/|ods-dashboard|\n"
                                  f"Optional|http://127.0.0.1:{free_port()}/|optional|\n")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Ready now: 1/2", result.stdout)
            self.assertIn("success card reached", result.stdout)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_actual_linux_summary_pipeline_stops_success_card(self):
        phase = (ROOT / "installers/phases/13-summary.sh").read_text()
        start = phase.index("if ! $DRY_RUN && command -v ods_readiness_summary")
        stop = phase.index('\nfi\n', start) + len('\nfi\n')
        block = phase[start:stop]
        script = f'''set -euo pipefail
source {shlex.quote(str(ROOT / 'installers/lib/readiness-summary.sh'))}
DRY_RUN=false; LOG_FILE=''; ENABLE_OPEN_WEBUI=false; ODS_MODE=cloud
ENABLE_VOICE=false; ENABLE_WORKFLOWS=false; ENABLE_PERPLEXICA=false
ENABLE_QDRANT=false; ENABLE_COMFYUI=false
declare -A SERVICE_PORTS=([dashboard]={free_port()}) SERVICE_HEALTH=()
docker() {{ printf 'healthy\\n'; }}
sr_container() {{ printf 'ods-%s' "$1"; }}
{block}
printf 'success card reached\\n'
'''
        result = subprocess.run(["bash", "-c", script], text=True, capture_output=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("required Dashboard is not reachable", result.stdout)
        self.assertNotIn("success card reached", result.stdout)

    def test_actual_installer_errexit_disabled_tail_preserves_readiness_failure(self):
        # Source the entire production phase; only its surrounding registry and
        # service-health dependencies are fixtures. The gate must stop before
        # success presentation, desktop creation, or setup-complete writes.
        phase = (ROOT / "installers/phases/13-summary.sh").read_text()
        phases = self.root / "installers/phases"
        phases.mkdir(parents=True)
        (phases / "13-summary.sh").write_text(phase)
        (self.root / "lib").mkdir()
        (self.root / "lib/service-registry.sh").write_text(
            'sr_load() { :; }\nsr_resolve_ports() { :; }\n'
            'sr_container() { printf "ods-%s" "$1"; }\n')
        core = (ROOT / "install-core.sh").read_text()
        tail = core[core.index('INSTALL_PHASE="13-summary"'):]
        cleanup = core[core.index('cleanup_on_error() {'):
                       core.index('trap cleanup_on_error ERR') + len('trap cleanup_on_error ERR')]
        for error_trap in ("", cleanup):
            with self.subTest(real_error_trap=bool(error_trap)):
                script = f'''set -euo pipefail
source {shlex.quote(str(ROOT / 'installers/lib/readiness-summary.sh'))}
SCRIPT_DIR={shlex.quote(str(self.root))}
INSTALL_DIR={shlex.quote(str(self.root / 'installation'))}
DRY_RUN=false; LOG_FILE=''; ENABLE_OPEN_WEBUI=false; ODS_MODE=cloud
ENABLE_VOICE=false; ENABLE_WORKFLOWS=false; ENABLE_PERPLEXICA=false
ENABLE_QDRANT=false; ENABLE_COMFYUI=false
declare -A SERVICE_PORTS=([dashboard]={free_port()}) SERVICE_HEALTH=()
ods_progress() {{ :; }}
show_success_card() {{ printf 'SUCCESS_CARD_SENTINEL\\n'; }}
docker() {{ printf 'healthy\\n'; }}
{error_trap}
{tail}
'''
                result = subprocess.run(["bash", "-c", script], text=True, capture_output=True, timeout=20)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("required Dashboard is not reachable", result.stdout)
                self.assertNotIn("SUCCESS_CARD_SENTINEL", result.stdout)
                self.assertNotIn("YOUR ODS IS LIVE", result.stdout)
                self.assertFalse((self.root / 'installation').exists())

    def test_actual_installer_tail_keeps_cosmetic_summary_failure_nonfatal(self):
        phases = self.root / "installers/phases"
        phases.mkdir(parents=True)
        (phases / "13-summary.sh").write_text('return 7\n')
        core = (ROOT / "install-core.sh").read_text()
        tail = core[core.index('INSTALL_PHASE="13-summary"'):]
        result = subprocess.run(["bash", "-c", f'set -euo pipefail\nSCRIPT_DIR={shlex.quote(str(self.root))}\n{tail}'],
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
