"""Real numeric-identity regression for phase 06 (run with sudo on Linux)."""
import ast
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
import multiprocessing
import os
from pathlib import Path
import re
import secrets
import shlex
import subprocess
import tempfile
import traceback
import unittest
from urllib.parse import parse_qs, urlparse
from urllib.request import build_opener, ProxyHandler, Request


ROOT = Path(__file__).resolve().parents[1]


def _load_permission_fixture_source(path, namespace, names, *, handler_methods=()):
    """Load real leaf code without importing model/runtime startup dependencies.

    The installer CI job has only stdlib/manifest dependencies. Extract the
    original AST unchanged, including its HTTP dispatcher and authentication,
    before dropping IDs; runner homes need not be traversable by fixture UIDs.
    """
    nodes = []
    found = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names:
            nodes.append(node)
            found.add(node.name)
        elif isinstance(node, ast.Assign):
            targets = {target.id for target in node.targets if isinstance(target, ast.Name)}
            if targets & names:
                nodes.append(node)
                found.update(targets & names)
        elif isinstance(node, ast.ClassDef) and node.name == "AgentHandler" and handler_methods:
            node.body = [item for item in node.body if isinstance(item, ast.Assign)
                         or isinstance(item, ast.FunctionDef) and item.name in handler_methods]
            found_methods = {item.name for item in node.body if isinstance(item, ast.FunctionDef)}
            assert found_methods == set(handler_methods), "Expected host HTTP methods are missing"
            nodes.append(node)
    assert found == names, f"Expected source definitions are missing: {names - found}"
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    module = ast.fix_missing_locations(ast.Module(body=[future, *nodes], type_ignores=[]))
    exec(compile(module, str(path), "exec"), namespace)


@unittest.skipUnless(hasattr(os, "geteuid") and os.geteuid() == 0, "requires Linux root to drop numeric IDs")
class DashboardDataPermissions(unittest.TestCase):
    def test_real_helper_grants_runtime_group_without_touching_children(self):
        with tempfile.TemporaryDirectory() as temporary:
            install = Path(temporary)
            install.chmod(0o755)
            data = install / "data"
            data.mkdir(mode=0o700)
            os.chown(data, 1001, 2001)
            models = data / "models"
            models.mkdir(mode=0o700)
            os.chown(models, 1001, 2001)
            public = models / "public.gguf"
            public.write_bytes(b"public-weights")
            public.chmod(0o644)
            os.chown(public, 1001, 2001)
            private_weight = models / "private.gguf"
            private_weight.write_bytes(b"private-weights")
            private_weight.chmod(0o600)
            os.chown(private_weight, 1001, 2001)
            private = models / "private"
            private.mkdir(mode=0o700)
            os.chown(private, 1001, 2001)
            nested = private / "nested"
            nested.mkdir(mode=0o700)
            os.chown(nested, 1001, 2001)
            secret = nested / "turn.json"
            secret.write_bytes(b"private-chat")
            secret.chmod(0o600)
            os.chown(secret, 1001, 2001)
            outside = install / "outside"
            outside.write_bytes(b"untouched")
            os.chown(outside, 2001, 2001)
            linked = private / "external"
            linked.symlink_to(outside)
            snapshot = {}
            for path in (public, private_weight, private, nested, secret, linked):
                info = path.lstat()
                snapshot[path] = (info.st_uid, info.st_gid, info.st_mode & 0o777,
                                  path.read_bytes() if path.is_file() and not path.is_symlink() else None)
            script = ('set -euo pipefail\n'
                      + (ROOT / "installers/lib/dashboard-data.sh").read_text() + r'''
ods_sudo_available() { return 0; }
ods_sudo() { "$@"; }
ods_prepare_dashboard_data "$INSTALL_DIR" false
''')
            environment = dict(os.environ, SCRIPT_DIR=str(ROOT), INSTALL_DIR=str(install))
            probe = '''
import os, pathlib
data = pathlib.Path(os.environ["INSTALL_DIR"]) / "data"
os.setgroups([])
os.setgid(1000)
os.setuid(1000)
assert os.geteuid() == os.getegid() == 1000
assert sorted(p.name for p in (data / "models").iterdir()) == ["private", "private.gguf", "public.gguf"]
assert (data / "models/public.gguf").read_bytes() == b"public-weights"
for denied in (data / "models/private.gguf", data / "models/private", data / "models/private/nested",
               data / "models/private/nested/turn.json"):
    try:
        if denied.is_dir():
            list(denied.iterdir())
        else:
            denied.read_bytes()
    except PermissionError:
        pass
    else:
        raise AssertionError(f"UID1000 accessed private path {denied}")
'''
            denied = subprocess.run(["python3", "-c", probe], env=environment,
                                    capture_output=True, text=True, check=False)
            self.assertNotEqual(denied.returncode, 0)
            self.assertIn("PermissionError", denied.stderr)
            for attempt in range(2):
                subprocess.run(["bash", "-c", script], env=environment, check=True)
                subprocess.run(["python3", "-c", probe], env=environment, check=True)
                self.assertEqual((data.stat().st_uid, data.stat().st_gid), (1001, 1000))
                self.assertEqual(data.stat().st_mode & 0o070, 0o070)
                self.assertEqual((models.stat().st_uid, models.stat().st_gid), (1001, 1000))
                self.assertEqual(models.stat().st_mode & 0o050, 0o050)
                for path, expected in snapshot.items():
                    info = path.lstat()
                    self.assertEqual((info.st_uid, info.st_gid, info.st_mode & 0o777), expected[:3], path)
                    if expected[3] is not None:
                        self.assertEqual(path.read_bytes(), expected[3], path)
                self.assertEqual(outside.stat().st_uid, 2001)
                self.assertEqual(outside.read_bytes(), b"untouched")

    def test_rejects_modeldir_symlink_or_file_before_mutation(self):
        for kind in ("symlink", "file"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                install = Path(temporary)
                install.chmod(0o755)
                data = install / "data"
                data.mkdir(mode=0o700)
                os.chown(data, 1001, 2001)
                models = data / "models"
                if kind == "symlink":
                    target = install / "elsewhere"
                    target.mkdir()
                    models.symlink_to(target)
                else:
                    models.write_bytes(b"not-a-directory")
                before = (data.stat().st_uid, data.stat().st_gid, data.stat().st_mode & 0o777)
                script = ('set -euo pipefail\n'
                          + (ROOT / "installers/lib/dashboard-data.sh").read_text() + r'''
ods_sudo_available() { return 0; }
ods_sudo() { "$@"; }
ods_prepare_dashboard_data "$INSTALL_DIR" false
''')
                result = subprocess.run(["bash", "-c", script],
                    env=dict(os.environ, SCRIPT_DIR=str(ROOT), INSTALL_DIR=str(install)),
                    capture_output=True, text=True, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Model data must be a real directory", result.stderr)
                self.assertEqual((data.stat().st_uid, data.stat().st_gid, data.stat().st_mode & 0o777), before)
                self.assertFalse((data / ".extensions-lock").exists())
                if kind == "symlink":
                    self.assertTrue(models.is_symlink())
                else:
                    self.assertEqual(models.read_bytes(), b"not-a-directory")

    def test_private_env_mode_fallback_uses_owner_host_without_chmod(self):
        with tempfile.TemporaryDirectory() as temporary:
            install = Path(temporary)
            install.chmod(0o755)
            env_path = install / ".env"
            env_path.write_text("ODS_MODE=cloud\nOPENAI_API_KEY=private-fixture-secret\n", encoding="utf-8")
            env_path.chmod(0o600)
            os.chown(env_path, 1001, 2001)
            metadata = (1001, 2001, 0o600)
            api_source = ROOT / "extensions/services/dashboard-api"
            logger = logging.getLogger("dashboard-permission-fixture")
            logger.disabled = True
            namespace = {
                "Path": Path, "os": os, "re": re, "json": json, "secrets": secrets, "shlex": shlex,
                "BaseHTTPRequestHandler": BaseHTTPRequestHandler, "urlparse": urlparse,
                "parse_qs": parse_qs, "logger": logger, "INSTALL_DIR": install,
                "AGENT_API_KEY": "permission-fixture-key", "STARTUP_ODS_MODE": "local",
            }
            _load_permission_fixture_source(api_source / "env_values.py", namespace, {
                "parse_env_value", "strip_matching_quotes", "_DOUBLE_QUOTED_RE", "_SINGLE_QUOTED_RE",
            })
            _load_permission_fixture_source(api_source / "config.py", namespace, {
                "normalize_ods_mode", "ODS_MODES", "LEGACY_ODS_MODES",
            })
            _load_permission_fixture_source(api_source / "performance_oracle.py", namespace, {"read_env_file_value"})
            _load_permission_fixture_source(api_source / "host_agent_client.py", namespace, {"AgentClientError"})
            _load_permission_fixture_source(api_source / "routers/models.py", namespace, {"_configured_ods_mode"})
            _load_permission_fixture_source(ROOT / "bin/ods-host-agent.py", namespace, {
                "load_env", "parse_env_text", "_normalize_ods_mode", "_ODS_MODES",
                "_LEGACY_ODS_MODE_ALIASES", "check_auth", "json_response",
                "_configured_model_identity", "_valid_pixel_model_name", "_valid_gguf_filename",
            }, handler_methods={"do_GET", "log_message", "_handle_model_config"})
            context = multiprocessing.get_context("fork")
            port_receiver, port_sender = context.Pipe(duplex=False)

            def serve_owner_snapshot():
                os.setgroups([])
                os.setgid(2001)
                os.setuid(1001)
                assert os.geteuid() == env_path.stat().st_uid == 1001
                with HTTPServer(("127.0.0.1", 0), namespace["AgentHandler"]) as server:
                    port_sender.send(server.server_port)
                    server.serve_forever()

            host = context.Process(target=serve_owner_snapshot)
            host.start()
            try:
                self.assertTrue(port_receiver.poll(10), "Owner-side host fixture did not start")
                port = port_receiver.recv()
                for mode in ("cloud", "hybrid"):
                    env_path.write_text(f"ODS_MODE={mode}\nOPENAI_API_KEY=private-fixture-secret\n", encoding="utf-8")
                    expected_bytes = env_path.read_bytes()
                    result_receiver, result_sender = context.Pipe(duplex=False)

                    def probe_api():
                        try:
                            os.setgroups([])
                            os.setgid(1000)
                            os.setuid(1000)
                            assert os.geteuid() == os.getegid() == 1000
                            os.environ["ODS_MODE"] = "local"  # Startup state must not substitute for configured mode.
                            try:
                                namespace["read_env_file_value"]("ODS_MODE", install, raise_on_error=True)
                            except PermissionError:
                                denied = True
                            else:
                                raise AssertionError("UID1000 read the UID1001 private env")
                            assert namespace["read_env_file_value"]("ODS_MODE", install) == ""
                            snapshots = []

                            def request_owner_snapshot(method, path, *, timeout):
                                assert (method, path, timeout) == ("GET", "/v1/model/config", 5)
                                request = Request(f"http://127.0.0.1:{port}{path}", method=method,
                                                  headers={"Authorization": "Bearer permission-fixture-key"})
                                with build_opener(ProxyHandler({})).open(request, timeout=timeout) as response:
                                    assert response.headers["Cache-Control"] == "no-store"
                                    raw = response.read()
                                assert b"private-fixture-secret" not in raw and b"OPENAI_API_KEY" not in raw
                                snapshot = json.loads(raw)
                                assert set(snapshot) == {"configuredMode", "effectiveMode", "configuredModel"}
                                snapshots.append(snapshot)
                                return snapshot

                            namespace["request_agent_json"] = request_owner_snapshot
                            observed = namespace["_configured_ods_mode"]()
                            result_sender.send({"mode": observed, "denied": denied, "snapshots": snapshots})
                        except BaseException:
                            result_sender.send({"error": traceback.format_exc()})

                    client = context.Process(target=probe_api)
                    client.start()
                    try:
                        self.assertTrue(result_receiver.poll(10), "API permission probe did not finish")
                        result = result_receiver.recv()
                        client.join(5)
                        self.assertEqual(client.exitcode, 0)
                        no_model = {"llmModel": None, "ggufFile": None, "contextSize": None}
                        self.assertEqual(result, {"mode": mode, "denied": True, "snapshots": [
                            {"configuredMode": mode, "effectiveMode": "local", "configuredModel": no_model}]})
                    finally:
                        if client.is_alive():
                            client.terminate()
                            client.join(5)
                        result_receiver.close()
                        result_sender.close()
                    info = env_path.stat()
                    self.assertEqual((info.st_uid, info.st_gid, info.st_mode & 0o777), metadata)
                    self.assertEqual(env_path.read_bytes(), expected_bytes)
            finally:
                host.terminate()
                host.join(5)
                port_receiver.close()
                port_sender.close()

    def test_install_and_reinstall_preserve_private_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            install = Path(temporary)
            install.chmod(0o755)
            data = install / "data"
            data.mkdir(mode=0o750)
            os.chown(data, 1001, 2001)
            (data / "token-spy").mkdir()
            os.chown(data / "token-spy", 1001, 2001)
            private = data / "hermes"
            private.mkdir(mode=0o700)
            os.chown(private, 1001, 2001)
            phase = (ROOT / "installers/phases/06-directories.sh").read_text()
            start = 'prepare-dashboard-permissions' if 'prepare-dashboard-permissions' in phase else 'prepare-service-permissions'
            boundary = phase.split(f'_phase06_step "{start}"', 1)[1]
            boundary = boundary.split('_phase06_step "generate-env"', 1)[0]
            pre_copy = phase.split('    # Fix ownership of data/config dirs', 1)[1]
            pre_copy = pre_copy.split('    # Copy entire source tree', 1)[0]
            pre_copy = pre_copy[pre_copy.index('\n'):]
            pre_copy_script = ('set -euo pipefail\n_phase06_rootless=false\nENABLE_HERMES=true\n'
                               '_phase06_repair_host_path() { echo "Unexpected ownership repair: $1" >&2; return 1; }\n'
                               'error() { echo "$*" >&2; return 1; }\nrun_phase() {\n' + pre_copy + '\n:\n}\nrun_phase\n')
            script = ('set -euo pipefail\n_phase06_rootless=false\n_phase06_step() { :; }\nods_sudo() { "$@"; }\n'
                      'chown() { [[ "${!#}" != "$INSTALL_DIR/data/token-spy" ]] || return 0; command chown "$@"; }\n'
                      'error() { echo "$*" >&2; return 1; }\nwarn() { echo "$*" >&2; }\n' + boundary)
            environment = dict(os.environ, SCRIPT_DIR=str(ROOT), INSTALL_DIR=str(install))
            probe = '''
import os, pathlib, sqlite3, tempfile
data = pathlib.Path(os.environ["INSTALL_DIR"]) / "data"
os.setgroups([])
os.setgid(1000)
os.setuid(1000)
receipt = data / "pixel-chat-results"
receipt.mkdir(mode=0o700, exist_ok=True)
fd, path = tempfile.mkstemp(dir=data)
with os.fdopen(fd, "w") as stream:
    stream.write("retained-private-password")
os.replace(path, data / "dashboard-password.json")
(receipt / "turn.json").write_text("retained-chat-result")
images = data / "pixel-images"
images.mkdir(mode=0o700, exist_ok=True)
database = images / "images.sqlite3"
if not database.exists():
    os.close(os.open(database, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
with sqlite3.connect(database) as db:
    db.execute("CREATE TABLE IF NOT EXISTS retained_image (data BLOB)")
    if not db.execute("SELECT COUNT(*) FROM retained_image").fetchone()[0]:
        db.execute("INSERT INTO retained_image VALUES (?)", (b"private-image",))
    assert db.execute("SELECT data FROM retained_image").fetchall() == [(b"private-image",)]
'''
            denied = subprocess.run(["python3", "-c", probe], env=environment, capture_output=True, text=True, check=False)
            self.assertNotEqual(denied.returncode, 0)
            self.assertIn("PermissionError", denied.stderr)
            for attempt in range(2):
                if attempt:
                    def install_owner():
                        os.setgroups([])
                        os.setgid(2001)
                        os.setuid(1001)
                    subprocess.run(["bash", "-c", pre_copy_script], env=environment, preexec_fn=install_owner, check=True)
                subprocess.run(["bash", "-c", script], env=environment, check=True)
                if attempt:
                    self.assertEqual((data / "dashboard-password.json").read_text(), "retained-private-password")
                    self.assertEqual((data / "pixel-chat-results/turn.json").read_text(), "retained-chat-result")
                subprocess.run(["python3", "-c", probe], env=environment, check=True)
                self.assertEqual(data.stat().st_uid, 1001)
                self.assertEqual(data.stat().st_gid, 1000)
                self.assertEqual(data.stat().st_mode & 0o777, 0o770)
                self.assertEqual(private.stat().st_uid, 1001)
                self.assertEqual(private.stat().st_gid, 2001)
                self.assertEqual(private.stat().st_mode & 0o777, 0o700)
                self.assertEqual((data / "dashboard-password.json").stat().st_mode & 0o777, 0o600)
                self.assertEqual((data / "pixel-chat-results/turn.json").read_text(), "retained-chat-result")
                self.assertEqual((data / "pixel-images").stat().st_uid, 1000)
                self.assertEqual((data / "pixel-images").stat().st_mode & 0o777, 0o700)
                self.assertEqual((data / "pixel-images/images.sqlite3").stat().st_uid, 1000)
                self.assertEqual((data / "pixel-images/images.sqlite3").stat().st_mode & 0o777, 0o600)
            # Recover a private result tree already transferred by an older
            # reinstall, without destroying its receipt or changing modes.
            os.chown(data / "pixel-chat-results", 1001, 2001)
            os.chown(data / "pixel-chat-results/turn.json", 1001, 2001)
            images = data / "pixel-images"
            database = images / "images.sqlite3"
            retained_database = database.read_bytes()
            os.chown(images, 1001, 2001)
            os.chown(database, 1001, 2001)
            outside = install / "outside-private-state"
            outside.write_text("untouched")
            os.chown(outside, 2001, 2001)
            (data / "pixel-chat-results/external").symlink_to(outside)
            (images / "external").symlink_to(outside)
            subprocess.run(["bash", "-c", script], env=environment, check=True)
            self.assertEqual((data / "pixel-chat-results").stat().st_uid, 1000)
            self.assertEqual((data / "pixel-chat-results").stat().st_mode & 0o777, 0o700)
            self.assertEqual((data / "pixel-chat-results/turn.json").stat().st_uid, 1000)
            self.assertEqual((data / "pixel-chat-results/turn.json").read_text(), "retained-chat-result")
            self.assertEqual((images.stat().st_uid, images.stat().st_gid, images.stat().st_mode & 0o777),
                             (1000, 1000, 0o700))
            self.assertEqual((database.stat().st_uid, database.stat().st_gid, database.stat().st_mode & 0o777),
                             (1000, 1000, 0o600))
            self.assertEqual(database.read_bytes(), retained_database)
            subprocess.run(["python3", "-c", probe], env=environment, check=True)
            self.assertEqual(outside.stat().st_uid, 2001)
            self.assertEqual(outside.read_text(), "untouched")


    def test_private_images_select_namespace_repair_without_sudo(self):
        # Execute the exact helper shell payload with real numeric ownership.
        # Docker's namespace mapping itself belongs to the runtime smoke test.
        for rootless in ("false", "true"):
            with self.subTest(rootless=rootless), tempfile.TemporaryDirectory() as temporary:
                install = Path(temporary)
                data = install / "data"
                data.mkdir(mode=0o770)
                os.chown(data, 1001, 1000)
                models = data / "models"
                models.mkdir(mode=0o700)
                os.chown(models, 1001, 2001)
                weight = models / "model.gguf"
                weight.write_bytes(b"retained-weights")
                weight.chmod(0o644)
                os.chown(weight, 1001, 2001)
                images = data / "pixel-images"
                images.mkdir(mode=0o700)
                saved = images / "images.sqlite3"
                saved.write_bytes(b"retained-private-image-database")
                saved.chmod(0o600)
                os.chown(images, 1001, 2001)
                os.chown(saved, 1001, 2001)
                script = ('set -euo pipefail\n'
                          + (ROOT / "installers/lib/dashboard-data.sh").read_text() + r'''
ods_sudo_available() { return 1; }
_ods_rootless_ensure_helper_image() { :; }
_ods_dashboard_lock_helper_image() { ODS_DASHBOARD_LOCK_IMAGE=fixture-python; }
ODS_ROOTLESS_HELPER_IMAGE=fixture-pinned-helper
docker_run() {
    [[ "$*" == *"--network none --user 0:0"* ]]
    [[ "$*" == *"src=$INSTALL_DIR/data,dst=/data"* ]]
    touch "$INSTALL_DIR/helper-used"
    if [[ "$*" == *'python3 - repair /data' ]]; then
        python3 - repair "$INSTALL_DIR/data"
        return $?
    fi
    local payload="${!#}"
    payload="${payload//\/data/$INSTALL_DIR\/data}"
    bash -ec "$payload"
}
ods_prepare_dashboard_data "$INSTALL_DIR" "$ROOTLESS"
''')
                subprocess.run(["bash", "-c", script], check=True,
                               env=dict(os.environ, SCRIPT_DIR=str(ROOT), INSTALL_DIR=str(install), ROOTLESS=rootless))
                self.assertTrue((install / "helper-used").exists())
                self.assertEqual((models.stat().st_uid, models.stat().st_gid, models.stat().st_mode & 0o777),
                                 (1001, 1000, 0o750))
                self.assertEqual((weight.stat().st_uid, weight.stat().st_gid, weight.stat().st_mode & 0o777),
                                 (1001, 2001, 0o644))
                self.assertEqual(weight.read_bytes(), b"retained-weights")
                self.assertEqual((data.stat().st_uid, data.stat().st_gid), (1001, 1000))
                self.assertEqual((images.stat().st_uid, images.stat().st_gid, images.stat().st_mode & 0o777),
                                 (1000, 1000, 0o700))
                self.assertEqual((saved.stat().st_uid, saved.stat().st_gid, saved.stat().st_mode & 0o777),
                                 (1000, 1000, 0o600))
                self.assertEqual(saved.read_bytes(), b"retained-private-image-database")

    def test_no_sudo_uses_actual_owner_authority(self):
        for uid, succeeds in ((1000, True), (1001, False)):
            with self.subTest(uid=uid), tempfile.TemporaryDirectory() as temporary:
                install = Path(temporary)
                install.chmod(0o755)
                data = install / "data"
                data.mkdir(mode=0o755)
                os.chown(data, 1000, 1000)
                saved = data / "retained-password"
                saved.write_bytes(b"private-state")
                saved.chmod(0o600)
                os.chown(saved, 1000, 1000)
                helper = install / "installers/lib/dashboard-lock.py"
                helper.parent.mkdir(parents=True)
                helper.write_bytes((ROOT / "installers/lib/dashboard-lock.py").read_bytes())
                # Read the exact libraries before dropping IDs: a CI checkout
                # can be beneath a runner home that UID 1000 cannot traverse.
                script = ('set -euo pipefail\n'
                          + (ROOT / "installers/lib/sudo.sh").read_text() + '\n'
                          + (ROOT / "installers/lib/dashboard-data.sh").read_text() + '\n'
                          + 'ODS_SUDO_AVAILABLE=false\n'
                          'ods_prepare_dashboard_data "$INSTALL_DIR" false\n')

                def identity():
                    os.setgroups([])
                    os.setgid(uid)
                    os.setuid(uid)

                result = subprocess.run(["bash", "-c", script],
                    env=dict(os.environ, SCRIPT_DIR=str(install), INSTALL_DIR=str(install)),
                    preexec_fn=identity, capture_output=True, text=True, check=False)
                self.assertEqual(result.returncode == 0, succeeds, result.stderr)
                self.assertEqual(data.stat().st_uid, 1000)
                self.assertEqual(data.stat().st_gid, 1000)
                self.assertEqual(data.stat().st_mode & 0o777, 0o775 if succeeds else 0o755)
                self.assertEqual(saved.read_bytes(), b"private-state")
                self.assertEqual(saved.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
