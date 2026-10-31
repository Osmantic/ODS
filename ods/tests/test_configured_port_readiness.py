"""Execute caller fragments and Compose config parsing; never contact a daemon."""
import http.server
import json
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
BASH = os.environ.get("ODS_TEST_BASH", "bash")
KEYS = ("DASHBOARD_PORT", "WEBUI_PORT", "DASHBOARD_API_PORT", "LITELLM_PORT",
        "PERPLEXICA_PORT", "N8N_PORT")


class ConfiguredPorts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env_file = self.root / ".env"
        self.env_file.write_text("")
        self.compose = self.root / "compose.yml"
        self.document = {"name": "ods-configured-ports-fixture", "services": {}}
        for name, key, default, target in (
            ("dashboard", "DASHBOARD_PORT", 3001, 3001),
            ("dashboard-api", "DASHBOARD_API_PORT", 3002, 3002),
            ("open-webui", "WEBUI_PORT", 3000, 8080),
            ("litellm", "LITELLM_PORT", 4000, 4000),
            ("perplexica", "PERPLEXICA_PORT", 3004, 3000),
            ("n8n", "N8N_PORT", 5678, 5678),
        ):
            self.document["services"][name] = {
                "image": "scratch", "ports": [{"target": target,
                    "published": "${" + key + ":-" + str(default) + "}",
                    "host_ip": "127.0.0.1", "protocol": "tcp"}]}
        self.compose.write_text(json.dumps(self.document))

    @classmethod
    def setUpClass(cls):
        try:
            cls.compose_available = subprocess.run(
                ["docker", "compose", "version"], capture_output=True, timeout=10).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            cls.compose_available = False

    def require_compose(self):
        if not self.compose_available:
            self.skipTest("Docker Compose config parser unavailable; no daemon is needed")

    def run_shell(self, script, overrides=None):
        excluded = (*KEYS, "OTHER_PORT", "COMPOSE_FILE", "COMPOSE_ENV_FILES",
                    "COMPOSE_PROJECT_NAME", "COMPOSE_PROFILES", "COMPOSE_DISABLE_ENV_FILE")
        env = {key: value for key, value in os.environ.items() if key not in excluded}
        env.update(overrides or {})
        prefix = f"set -euo pipefail\nINSTALL_DIR={shlex.quote(str(self.root))}\n"
        # Prior-source controls can still exercise the old caller fragments.
        if PORTS.exists():
            prefix += f"source {shlex.quote(str(PORTS))}\n"
        prefix += '''COMPOSE_FLAGS=(-f "$INSTALL_DIR/compose.yml")
# Only config rendering may reach the real CLI; never a Docker daemon action.
docker() {
    if [[ "$1" == compose && "$*" == *" config --format json" ]]; then
        printf 'config\\n' >> "$INSTALL_DIR/config-calls"
        command docker "$@"
    elif [[ "$1" == inspect ]]; then
        printf 'healthy\\n'
    else
        return 0
    fi
}
'''
        return subprocess.run([BASH, "-c", prefix + script], env=env,
                              capture_output=True, text=True, timeout=30)

    def port(self, overrides=None):
        self.require_compose()
        return self.run_shell('macos_load_service_ports "$INSTALL_DIR" dashboard "${COMPOSE_FLAGS[@]}"\n'
                              'macos_service_port dashboard', overrides)

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

    def test_compose_export_prefix_and_interpolation(self):
        for saved, expected in (
            ("export DASHBOARD_PORT=3101\n", "3101"),
            ("OTHER_PORT=3001\nDASHBOARD_PORT=${OTHER_PORT}\n", "3001"),
            ("OTHER_PORT=3101\nDASHBOARD_PORT=\"${OTHER_PORT}\" # owner\n", "3101"),
            ("DASHBOARD_PORT=03001\n", "3001"),
        ):
            with self.subTest(saved=saved):
                self.env_file.write_text(saved)
                result = self.port()
                self.assertEqual((result.returncode, result.stdout), (0, expected), result.stderr)

    def test_overlay_and_explicit_env_file_use_actual_compose_flags(self):
        self.require_compose()
        env = self.root / "owner settings.env"
        env.write_text("OWNER_PORT=3201\n")
        overlay = self.root / "override ports.yml"
        overlay.write_text('''services:
  dashboard:
    ports: !override
      - "127.0.0.1:${OWNER_PORT:-3301}:3001"
''')
        result = self.run_shell('''macos_load_service_ports "$INSTALL_DIR" dashboard \
    --env-file "$INSTALL_DIR/owner settings.env" "${COMPOSE_FLAGS[@]}" \
    -f "$INSTALL_DIR/override ports.yml"
macos_service_port dashboard
''')
        self.assertEqual((result.returncode, result.stdout), (0, "3201"), result.stderr)

    def test_selected_services_ignore_unrelated_bindings_but_refuse_ambiguous_ports(self):
        self.require_compose()
        dashboard = self.document["services"]["dashboard"]
        dashboard["ports"].append({"target": 3011, "published": "3011", "host_ip": "0.0.0.0"})
        self.document["services"]["n8n"].pop("ports")
        self.compose.write_text(json.dumps(self.document))
        self.assertEqual(self.port().stdout, "3001")
        dashboard["ports"].append({"target": 3001, "published": "3201", "host_ip": "127.0.0.1"})
        self.compose.write_text(json.dumps(self.document))
        result = self.port()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_missing_dynamic_non_loopback_and_udp_bindings_are_not_defaulted(self):
        self.require_compose()
        for bindings in ([], [{"target": 3001}],
                         [{"target": 3001, "published": "3101", "host_ip": "192.0.2.2"}],
                         [{"target": 3001, "published": "3101", "protocol": "udp"}]):
            with self.subTest(bindings=bindings):
                self.document["services"]["dashboard"]["ports"] = bindings
                self.compose.write_text(json.dumps(self.document))
                result = self.port()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")

    def test_compose_failure_and_secret_output_are_not_leaked(self):
        # A failing producer can still write valid JSON. pipefail must prevent
        # accepting it, even when the caller tests the loader in an if clause.
        config = json.dumps({"services": {"dashboard": {
            "environment": {"PRIVATE_TEST_VALUE": "fixture-secret-value"},
            "ports": [{"target": 3001, "published": "3101"}]}}})
        (self.root / "rendered.json").write_text(config)
        result = self.run_shell('''docker() {
    cat "$INSTALL_DIR/rendered.json"
    printf 'fixture-secret-value\\n' >&2
    return 17
}
if macos_load_service_ports "$INSTALL_DIR" dashboard; then
    printf 'incorrectly-accepted'
else
    printf 'refused'
fi
''')
        self.assertEqual(result.stdout, "refused")
        self.assertEqual(result.stderr, "Cannot resolve selected service ports from Compose configuration.\n")
        result = self.run_shell('''docker() { cat "$INSTALL_DIR/rendered.json"; }
macos_load_service_ports "$INSTALL_DIR" dashboard
macos_service_port dashboard
''')
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "3101", ""))

    def test_installed_flattened_library_layout(self):
        lib = self.root / "lib"
        lib.mkdir()
        shutil.copy(PORTS, lib)
        self.env_file.write_text("DASHBOARD_PORT='3101' # owner\n")
        self.require_compose()
        result = self.run_shell(f'source {shlex.quote(str(lib / "service-ports.sh"))}\n'
                                'macos_load_service_ports "$INSTALL_DIR" dashboard "${COMPOSE_FLAGS[@]}"\n'
                                'macos_service_port dashboard')
        self.assertEqual((result.returncode, result.stdout), (0, "3101"), result.stderr)

    def test_valid_port_boundaries(self):
        for value in ("1", "65535"):
            result = self.port({"DASHBOARD_PORT": value})
            self.assertEqual((result.returncode, result.stdout), (0, value), result.stderr)

    def test_invalid_ports_do_not_fall_back_or_execute(self):
        marker = self.root / "executed"
        for value in ("0", "65536", "-1", "9999999999999999999999",
                      f"$(touch {marker})", "3001/path", "3001\"", "not-a-port"):
            with self.subTest(value=value):
                self.env_file.write_text(f"DASHBOARD_PORT={value}\n")
                result = self.port()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Cannot resolve selected service ports", result.stderr)
                self.assertEqual(result.stdout, "")
        self.assertFalse(marker.exists())

    def mac_summary(self, overrides=None, real_probe=False):
        self.require_compose()
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
        self.assertEqual((self.root / "config-calls").read_text().splitlines(), ["config"])

    def test_mac_readiness_keeps_defaults(self):
        result = self.mac_summary()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Dashboard|http://127.0.0.1:3001", result.stdout)
        self.assertIn("CARD:3000:3001", result.stdout)

    def test_mac_summary_accepts_compose_export_assignment(self):
        self.env_file.write_text("export DASHBOARD_PORT=3101\n")
        result = self.mac_summary()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Dashboard|http://127.0.0.1:3101|", result.stdout)
        self.assertIn("CARD:3000:3101", result.stdout)

    def test_mac_summary_accepts_compose_interpolated_assignment(self):
        self.env_file.write_text("OTHER_PORT=3001\nDASHBOARD_PORT=${OTHER_PORT}\n")
        result = self.mac_summary()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Dashboard|http://127.0.0.1:3001|", result.stdout)
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
        self.assertIn("Cannot resolve selected service ports", result.stderr)
        self.assertNotIn("CARD:", result.stdout)

    def test_mac_status_probes_custom_dashboard_and_webui(self):
        self.require_compose()
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
get_compose_flags() { printf '%s' '-f compose.yml'; }
get_apple_silicon_info() { APPLE_CHIP=fixture; }
get_system_ram_gb() { SYSTEM_RAM_GB=32; }
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
