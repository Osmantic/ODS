"""Real pinned OpenClaw imports and wiki tools in a private disposable gateway.

No installed ODS state, Google account, model, or GPU is used. The ordinary
host suite skips this file without OPENCLAW_PACKAGE_DIR; portal-runtime.yml
runs it explicitly against the package already installed for runtime checks.
"""
import hashlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[4]
PACKAGE = os.environ.get("OPENCLAW_PACKAGE_DIR")


@unittest.skipUnless(PACKAGE and os.name == "posix", "Set OPENCLAW_PACKAGE_DIR on Linux/WSL for real plugin checks")
class PluginRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path[:0] = [str(ROOT / "bin"), str(Path(__file__).resolve().parents[1] / "host")]
        import connection_transaction
        from pixel_connections import plugins

        cls.transaction = connection_transaction
        cls.catalog = plugins
        cls.package = Path(PACKAGE)
        cls.openclaw = str(cls.package / "openclaw.mjs")
        if not cls.package.is_absolute():
            raise ValueError("OPENCLAW_PACKAGE_DIR must be absolute")
        if json.loads((cls.package / "package.json").read_text())["version"] != plugins.OPENCLAW:
            raise ValueError("Run against the reviewed OpenClaw version")
        for relative, digest in {
            "dist/extensions/memory-wiki/index.js": "676a10132a7e25f8b8407f07f08e4106094c25f557ecd52e65836bc7acb3be27",
            "dist/cli-DwNmF7SN.js": "2ece5f0c272fe5a19dae3418fff398da8028716686c64266537c4d2f48c8fb3f",
            "dist/config-CUjuK5-Y.js": "c746402e58e314d6f9af086baba2be67c186fb6d4b17d023f3b20724f6be5a7c",
        }.items():
            if hashlib.sha256((cls.package / relative).read_bytes()).hexdigest() != digest:
                raise ValueError(f"Unreviewed plugin bytes: {relative}")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ods-plugin-runtime-")
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.state = self.home / ".openclaw"
        self.state.mkdir(mode=0o700)
        # Do not inherit the owner's provider keys, gateway identity, config or logs.
        self.env = {"PATH": os.environ["PATH"], "HOME": str(self.home),
                    "OPENCLAW_STATE_DIR": str(self.state),
                    "OPENCLAW_CONFIG_PATH": str(self.state / "openclaw.json"), "NO_COLOR": "1"}

    def write_config(self, config):
        path = self.state / "openclaw.json"
        path.write_text(json.dumps(config))
        path.chmod(0o600)

    def cli(self, *arguments):
        result = subprocess.run([self.openclaw, *arguments], cwd=self.home, env=self.env,
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_a_visible_plugin_whose_module_throws_is_not_ready(self):
        install = self.home / "install"
        entry = install / "current/plugin"
        entry.mkdir(parents=True)
        for catalog in (False, True):
            with self.subTest(catalog=catalog):
                plugin_id = "ods-runtime-probe" if catalog else self.transaction.planner.PLUGIN_ID
                (entry / "package.json").write_text(json.dumps({"name": plugin_id, "version": "0.0.1",
                    "type": "module", "openclaw": {"extensions": ["./index.mjs"]}}))
                (entry / "openclaw.plugin.json").write_text(json.dumps({"id": plugin_id,
                    "configSchema": {"type": "object", "additionalProperties": False}}))
                (entry / "index.mjs").write_text("throw new Error('fixture-runtime-failure');\n")
                self.write_config({"plugins": {"allow": [plugin_id], "load": {"paths": [str(entry)]},
                                               "entries": {plugin_id: {"enabled": True}}}})
                self.cli("plugins", "registry", "--refresh", "--json")
                cold = self.cli("plugins", "inspect", plugin_id, "--json")["plugin"]
                self.assertEqual(cold["status"], "loaded")
                self.assertIs(cold["imported"], False)
                runtime = self.cli("plugins", "inspect", plugin_id, "--runtime", "--json")["plugin"]
                self.assertEqual(runtime["status"], "error")
                self.assertIn("fixture-runtime-failure", runtime["error"])
                # Exercise the shipped readiness gate with real subprocesses too.
                with patch.dict(os.environ, self.env, clear=True):
                    ready = self.transaction._catalog_ready(self.openclaw, [plugin_id]) if catalog else (
                        self.transaction._plugin_ready(self.openclaw, str(install), enabled=True))
                self.assertFalse(ready)

    def start_gateway(self, port, label):
        output = (self.home / f"{label}.log").open("w")
        child = subprocess.Popen([self.openclaw, "gateway", "run", "--port", str(port)],
                                 cwd=self.home, env=self.env, stdout=output, stderr=subprocess.STDOUT)
        self.addCleanup(output.close)
        self.addCleanup(self.stop_gateway, child)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            self.assertIsNone(child.poll(), (self.home / f"{label}.log").read_text())
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    return child
            except OSError:
                time.sleep(0.1)
        self.fail("private gateway did not start within 30 seconds")

    @staticmethod
    def stop_gateway(child):
        if child.poll() is not None:
            return
        child.terminate()
        try:
            child.wait(timeout=20)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=10)
            raise

    def invoke(self, port, token, tool, arguments):
        request = urllib.request.Request(f"http://127.0.0.1:{port}/tools/invoke",
            data=json.dumps({"tool": tool, "args": arguments, "sessionKey": "agent:pixel:main"}).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def test_wiki_files_a_fact_finds_it_and_keeps_pages_when_turned_off(self):
        token = secrets.token_hex(24)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        tools = self.catalog.MEMORY_WIKI["tools"]
        config = {"gateway": {"mode": "local", "port": port, "bind": "loopback",
                               "auth": {"mode": "token", "token": token}},
                  "logging": {"file": str(self.home / "openclaw.log")},
                  "plugins": {"allow": ["memory-wiki"], "entries": {"memory-wiki": {
                      "enabled": True, "config": self.catalog.MEMORY_WIKI["config"]}}},
                  "tools": {"allow": tools}, "agents": {"defaults": {"heartbeat": {"every": "0m"}},
                      "list": [{"id": "pixel", "default": True, "workspace": str(self.home / "workspace"),
                                "tools": {"allow": tools}}]}}
        self.write_config(config)
        with patch.dict(os.environ, self.env, clear=True):
            self.assertTrue(self.transaction._catalog_ready(self.openclaw, ["memory-wiki"]))
        child = self.start_gateway(port, "on")
        code, body = self.invoke(port, token, "wiki_apply", {"op": "create_synthesis",
            "title": "Portal qualification fact", "body": "The synthetic project Cedar meets on Tuesday.",
            "sourceIds": ["source.fixture.portal-qualification"],
            "claims": [{"text": "Project Cedar meets on Tuesday", "evidence": [
                {"kind": "owner", "note": "Synthetic qualification fixture"}]}]})
        self.assertEqual(code, 200, body)
        self.assertTrue(body["ok"])
        code, body = self.invoke(port, token, "wiki_search", {"query": "Cedar"})
        self.assertEqual(code, 200, body)
        matches = body["result"]["details"]["results"]
        self.assertEqual(len(matches), 1, body)
        self.assertIn("Project Cedar meets on Tuesday", matches[0]["snippet"])
        code, body = self.invoke(port, token, "wiki_get", {"lookup": matches[0]["path"]})
        self.assertEqual(code, 200, body)
        self.assertTrue(body["result"]["details"]["found"])
        self.assertIn("The synthetic project Cedar meets on Tuesday.", body["result"]["details"]["content"])
        self.stop_gateway(child)
        vault = self.state / "wiki/portal"
        pages = {str(path.relative_to(vault)): path.read_bytes() for path in vault.rglob("*.md")}
        self.assertIn("syntheses/portal-qualification-fact.md", pages)
        config["plugins"] = {"allow": [], "entries": {"memory-wiki": {"enabled": False}}}
        self.write_config(config)
        child = self.start_gateway(port, "off")
        code, _body = self.invoke(port, token, "wiki_search", {"query": "Cedar"})
        self.assertEqual(code, 404)
        self.stop_gateway(child)
        self.assertEqual(pages, {str(path.relative_to(vault)): path.read_bytes() for path in vault.rglob("*.md")})

    @unittest.skipUnless(os.environ.get("PIXEL_SOURCE_PLUGIN_DIR"), "Stage the vendored Source Broker with npm ci")
    def test_the_vendored_source_broker_imports_and_reads_through_the_gateway(self):
        source = Path(os.environ["PIXEL_SOURCE_PLUGIN_DIR"])
        # Test ODS's own plugin, with its lockfile dependency, rather than another
        # installed Pixel release. SDK imports resolve through the pinned loader.
        for original in (ROOT / "vendor/pixel/plugin").iterdir():
            if original.suffix in {".js", ".mjs", ".json"}:
                self.assertEqual((source / original.name).read_bytes(), original.read_bytes())
        dependency = json.loads((source / "node_modules/typebox/package.json").read_text())
        expected = json.loads((ROOT / "vendor/pixel/plugin/package.json").read_text())["dependencies"]["typebox"]
        self.assertEqual(dependency["version"], expected)
        install = self.home / "install"
        (install / "current").mkdir(parents=True)
        shutil.copytree(source, install / "current/plugin")
        projection = self.home / "projection"
        projection.mkdir()
        (projection / "email.json").write_text(json.dumps({"schemaVersion": 1, "sanitizerRevision": 3, "records": [
            {"id": "fixture-message", "subject": "Project Cedar", "summary": "Synthetic meeting on Tuesday"}],
            "boundary": {"projectionOnly": True}, "generatedAt": datetime.now(timezone.utc).isoformat()}))
        self.env.update(PIXEL_SOURCE_PROJECTION_DIR=str(projection), PIXEL_LIMB_EMAIL_ENABLED="1",
                        PIXEL_LIMB_CALENDAR_ENABLED="1", PIXEL_LIMB_CALENDAR_WRITE_ENABLED="0",
                        PIXEL_LIMB_EMAIL_DRAFTS_ENABLED="0", PIXEL_LIMB_SOCIAL_ENABLED="0", PIXEL_LIMB_WEB_ENABLED="0")
        plugin_id = self.transaction.planner.PLUGIN_ID
        token = secrets.token_hex(24)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        tools = ["pixel_limb_status", "pixel_gmail_read"]
        self.write_config({"gateway": {"mode": "local", "port": port, "bind": "loopback",
            "auth": {"mode": "token", "token": token}}, "logging": {"file": str(self.home / "openclaw.log")},
            "plugins": {"allow": [plugin_id], "load": {"paths": [str(install / "current/plugin")]},
                        "entries": {plugin_id: {"enabled": True}}}, "tools": {"allow": tools},
            "agents": {"defaults": {"heartbeat": {"every": "0m"}}, "list": [{"id": "pixel", "default": True,
                "workspace": str(self.home / "workspace"), "tools": {"allow": tools}}]}})
        with patch.dict(os.environ, self.env, clear=True):
            ready = self.transaction._plugin_ready(self.openclaw, str(install), enabled=True)
        if not ready:
            self.fail(json.dumps(self.cli("plugins", "list", "--json").get("diagnostics")))
        child = self.start_gateway(port, "source")
        code, body = self.invoke(port, token, "pixel_limb_status", {})
        self.assertEqual(code, 200, body)
        self.assertTrue(body["result"]["details"]["limbs"]["email"])
        self.assertFalse(body["result"]["details"]["limbs"]["calendarWrite"])
        code, body = self.invoke(port, token, "pixel_gmail_read", {"messageId": "fixture-message"})
        self.assertEqual(code, 200, body)
        self.assertEqual(body["result"]["details"]["summary"], "Synthetic meeting on Tuesday")
        self.stop_gateway(child)


if __name__ == "__main__":
    unittest.main()
