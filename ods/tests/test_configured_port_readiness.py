"""Execute installer/status fragments; never contact Docker or installed services."""
import http.server
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import threading
import unittest


ROOT = Path(os.environ.get("ODS_TEST_SOURCE_ROOT", Path(__file__).resolve().parents[1]))
PORTS = ROOT / "installers/macos/lib/service-ports.sh"
KEYS = ("DASHBOARD_PORT", "WEBUI_PORT", "DASHBOARD_API_PORT", "LITELLM_PORT",
        "PERPLEXICA_PORT", "N8N_PORT")


class ConfiguredPorts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env_file = self.root / ".env"
        self.env_file.write_text("")

    def run_shell(self, script, overrides=None):
        env = {key: value for key, value in os.environ.items() if key not in KEYS}
        env.update(overrides or {})
        prefix = f"set -euo pipefail\nINSTALL_DIR={shlex.quote(str(self.root))}\n"
        # Prior-source controls can still exercise the old caller fragments.
        if PORTS.exists():
            prefix += f"source {shlex.quote(str(PORTS))}\n"
        return subprocess.run(["bash", "-c", prefix + script], env=env,
                              capture_output=True, text=True, timeout=15)

    def port(self, overrides=None):
        return self.run_shell('macos_service_port "$INSTALL_DIR/.env" DASHBOARD_PORT 3001', overrides)

    def test_default_and_empty_saved_port(self):
        for saved in ("", "DASHBOARD_PORT=\n"):
            self.env_file.write_text(saved)
            result = self.port()
            self.assertEqual((result.returncode, result.stdout), (0, "3001"), result.stderr)

    def test_quoted_commented_crlf_last_value_without_newline(self):
        self.env_file.write_bytes(b'DASHBOARD_PORT=3100\r\nDASHBOARD_PORT="3101" # saved')
        result = self.port()
        self.assertEqual((result.returncode, result.stdout), (0, "3101"), result.stderr)

    def test_process_override_and_explicit_empty_match_compose_precedence(self):
        self.env_file.write_text("DASHBOARD_PORT=3101\n")
        for value, expected in (("3201", "3201"), ("", "3001")):
            result = self.port({"DASHBOARD_PORT": value})
            self.assertEqual((result.returncode, result.stdout), (0, expected), result.stderr)

    def test_installed_flattened_library_layout(self):
        lib = self.root / "lib"
        lib.mkdir()
        shutil.copy(PORTS, lib)
        shutil.copy(ROOT / "lib/safe-env.sh", lib)
        self.env_file.write_text("DASHBOARD_PORT='3101' # owner\n")
        result = self.run_shell(f'source {shlex.quote(str(lib / "service-ports.sh"))}\n'
                                'macos_service_port "$INSTALL_DIR/.env" DASHBOARD_PORT 3001')
        self.assertEqual((result.returncode, result.stdout), (0, "3101"), result.stderr)

    def test_valid_port_boundaries(self):
        for value in ("1", "65535"):
            result = self.port({"DASHBOARD_PORT": value})
            self.assertEqual((result.returncode, result.stdout), (0, value), result.stderr)

    def test_invalid_ports_do_not_fall_back_or_execute(self):
        marker = self.root / "executed"
        for value in ("0", "65536", "03001", "-1", "9999999999999999999999",
                      f"$(touch {marker})", "3001/path", "3001\"", "${OTHER_PORT}"):
            with self.subTest(value=value):
                self.env_file.write_text(f"DASHBOARD_PORT={value}\n")
                result = self.port()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Invalid DASHBOARD_PORT", result.stderr)
                self.assertEqual(result.stdout, "")
        self.assertFalse(marker.exists())

    def mac_summary(self, overrides=None, real_probe=False):
        source = (ROOT / "installers/macos/install-macos.sh").read_text()
        tail = source[source.index("# ── Success card ──"):]
        script = '''ALL_HEALTHY=true; ENABLE_OPEN_WEBUI=true; CLOUD_MODE=true
ENABLE_PERPLEXICA=true; ENABLE_VOICE=false; ENABLE_WORKFLOWS=true; ENABLE_OPENCODE=false
ODS_LOG_FILE=''; ODS_REINSTALL_REMOTE_ROUTE_REMOVED=false
show_success_card() { printf 'CARD:%s:%s\\n' "${1:-3000}" "${2:-3001}"; }
'''
        if real_probe:
            script += f"source {shlex.quote(str(ROOT / 'installers/lib/readiness-summary.sh'))}\n"
            script += '''ENABLE_OPEN_WEBUI=false; ENABLE_PERPLEXICA=false; ENABLE_WORKFLOWS=false
docker() { printf 'healthy\\n'; }
'''
        else:
            script += 'ods_readiness_summary() { printf "OPEN:%s\\n" "$3"; cat; }\n'
        return self.run_shell(script + tail, overrides)

    def test_mac_readiness_and_completion_use_all_selected_ports(self):
        self.env_file.write_text("\n".join(f"{key}='{port}' # saved" for key, port in zip(
            KEYS, (3101, 3100, 3102, 4100, 3104, 5778))))
        result = self.mac_summary()
        self.assertEqual(result.returncode, 0, result.stderr)
        for label, port in (("Dashboard", 3101), ("Chat UI (Open WebUI)", 3100),
                            ("Dashboard API", 3102), ("LiteLLM", 4100),
                            ("Perplexica", 3104), ("n8n", 5778)):
            self.assertIn(f"{label}|http://127.0.0.1:{port}", result.stdout)
            self.assertIn(f"|http://localhost:{port}\n", result.stdout)
        self.assertIn("OPEN:http://localhost:3101", result.stdout)
        self.assertIn("CARD:3100:3101", result.stdout)

    def test_mac_readiness_keeps_defaults(self):
        result = self.mac_summary()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Dashboard|http://127.0.0.1:3001", result.stdout)
        self.assertIn("CARD:3000:3001", result.stdout)

    def test_actual_mac_required_probe_reaches_custom_loopback_port(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            self.env_file.write_text("\n".join(f"{key}={server.server_port}" for key in KEYS))
            result = self.mac_summary(real_probe=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(f"CARD:3000:{server.server_port}", result.stdout)
            self.assertIn("Ready now: 3/3", result.stdout)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_mac_invalid_required_port_never_reaches_success(self):
        self.env_file.write_text("DASHBOARD_PORT=65536\n")
        result = self.mac_summary()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid DASHBOARD_PORT", result.stderr)
        self.assertNotIn("CARD:", result.stdout)

    def test_mac_status_probes_custom_dashboard_and_webui(self):
        self.env_file.write_text("DASHBOARD_PORT=3101\nWEBUI_PORT='3100' # owner\n")
        source = (ROOT / "installers/macos/ods-macos.sh").read_text()
        start = source.index("cmd_status() {")
        status = source[start:source.index("\n}\n", start) + 3]
        script = '''GRN=''; NC=''; DGRN=''; WHT=''; OPENCODE_PLIST=/absent-fixture-opencode
test_install() { :; }
resolve_cli_llm_route() {
    CLI_LLM_MODE=cloud; CLI_LLM_NAME=LLM; CLI_LLM_HEALTH_URL=http://model.fixture/health
    CLI_LLM_API_KEY=fixture
}
get_compose_flags() { :; }
get_apple_silicon_info() { APPLE_CHIP=fixture; }
get_system_ram_gb() { SYSTEM_RAM_GB=32; }
docker() { :; }
ai() { :; }; ai_ok() { :; }; ai_warn() { :; }
webui_is_selected() { return 0; }
curl_with_bearer() { printf '%s\\n' "${@: -1}" >> "$INSTALL_DIR/probes"; printf 200; }
'''
        result = self.run_shell(script + status + "\ncmd_status\n", {"DASHBOARD_PORT": "3201"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "probes").read_text().splitlines(),
                         ["http://model.fixture/health", "http://127.0.0.1:3201", "http://127.0.0.1:3100"])

    def test_linux_success_card_and_written_desktop_use_resolved_port(self):
        source = (ROOT / "installers/phases/13-summary.sh").read_text()
        start = source.index('    _summary_chat_url=""')
        card = source[start:source.index('\nfi\n', start)]
        start = source.index('    DESKTOP_FILE=')
        desktop = source[start:source.index('\nDESKTOP_EOF', start) + len('\nDESKTOP_EOF')]
        for port, expected in (("3101", "3101"), ("", "3001")):
            script = f'''declare -A SERVICE_PORTS=([dashboard]={shlex.quote(port)})
ENABLE_OPEN_WEBUI=false; ENABLE_PIXEL_RUNTIME=true; LOCAL_IP=''; HOME="$INSTALL_DIR"
show_success_card() {{ printf 'CARD:%s:%s\\n' "$1" "$2"; }}
{card}
{desktop}
'''
            result = self.run_shell(script)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"CARD:http://localhost:{expected}/pixel:http://localhost:{expected}", result.stdout)
            self.assertIn(f"Exec=xdg-open http://localhost:{expected}\n",
                          (self.root / ".local/share/applications/ods.desktop").read_text())


if __name__ == "__main__":
    unittest.main()
