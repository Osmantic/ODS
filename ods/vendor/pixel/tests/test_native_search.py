"""Exercise configure and rendering with independent search-provider inputs."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class NativeSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.name != "posix" or not shutil.which("node"):
            raise unittest.SkipTest("Linux/Node configuration fixture required")
        cls.suite = tempfile.TemporaryDirectory(prefix="pixel-search-contract-")
        cls.root = Path(cls.suite.name) / "repo"
        shutil.copytree(Path(__file__).resolve().parents[1], cls.root,
                        ignore=shutil.ignore_patterns(".git", "node_modules", ".env", ".generated", "dist", "__pycache__"))

    @classmethod
    def tearDownClass(cls):
        cls.suite.cleanup()

    def setUp(self):
        self.case = tempfile.TemporaryDirectory(dir=self.suite.name)
        self.addCleanup(self.case.cleanup)
        self.base = Path(self.case.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.plugin = self.base / "parallel"
        self.plugin.mkdir()
        (self.plugin / "openclaw.plugin.json").write_text('{"id":"parallel"}\n')
        digest = hashlib.sha256()
        for path in sorted(self.plugin.iterdir()):
            data = path.read_bytes()
            digest.update(path.name.encode() + b"\0" + str(len(data)).encode() + b"\0" + data + b"\0")
        self.extension = {"id": "parallel", "path": str(self.plugin), "sha256": digest.hexdigest()}
        self.answers = {
            "deploymentProfile": "prepared", "ownerName": "Test Owner", "agentName": "My Portal",
            "openclawBin": str(self.root / "tests/fixtures/bin/openclaw"),
            "openclawHome": str(self.home / ".openclaw"), "installDir": str(self.home / "pixel"),
            "workspace": str(self.home / ".openclaw/workspace-pixel"),
            "embeddingCache": str(self.home / "embedding-cache"), "modelApiKey": "local-no-auth",
            "webCourierEnabled": False, "emailLimbEnabled": False, "calendarLimbEnabled": False,
            "webLimbEnabled": True, "socialLimbEnabled": False,
        }
        self.env = dict(os.environ, HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / ".config"))

    def configure(self, expected=0):
        path = self.base / "answers.json"
        path.write_text(json.dumps(self.answers))
        result = subprocess.run(["node", "scripts/configure.mjs", "--answers", str(path), "--force"],
                                cwd=self.root, env=self.env, capture_output=True, text=True)
        if expected == 0:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def configured_env(self):
        result = subprocess.run(["bash", "-c", 'set -a; source "$1"; python3 -c "import os,json; print(json.dumps(dict(os.environ)))"',
                                 "search-fixture", str(self.root / ".env")],
                                env=self.env, capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    def render(self, env=None, expected=0):
        out = self.base / "rendered.json"
        result = subprocess.run(["node", "scripts/render-config.mjs", str(out)], cwd=self.root,
                                env=env or self.configured_env(), capture_output=True, text=True)
        if expected == 0:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(out.read_text())
        self.assertNotEqual(result.returncode, 0)
        return result.stderr

    def select_parallel(self):
        self.answers.update(webSearchProvider="parallel-free", gatewayExtensions=[self.extension])

    def extension_with_tools(self, count):
        tools = [f"pixel_fixture_{index:02d}" for index in range(count)]
        (self.plugin / "openclaw.plugin.json").write_text(json.dumps({
            "id": "parallel", "contracts": {"tools": tools},
        }) + "\n")
        digest = hashlib.sha256()
        for path in sorted(self.plugin.iterdir()):
            data = path.read_bytes()
            digest.update(path.name.encode() + b"\0" + str(len(data)).encode() + b"\0" + data + b"\0")
        return tools, {"id": "parallel", "path": str(self.plugin), "sha256": digest.hexdigest(), "tools": tools}

    def test_gateway_tool_cap_accepts_ods_surface_and_rejects_overflow(self):
        for count in (22, 24):
            tools, extension = self.extension_with_tools(count)
            self.answers["gatewayExtensions"] = [extension]
            self.configure()
            env = self.configured_env()
            env["PIXEL_AGENT_TOOL_ALLOWLIST"] = json.dumps([tools[0]])
            config = self.render(env)
            self.assertTrue(set(tools).issubset(config["tools"]["alsoAllow"]))
            self.assertEqual(config["tools"]["sandbox"]["tools"]["allow"], [tools[0]])
            self.assertTrue(set(tools[1:]).issubset(config["agents"]["list"][0]["tools"]["deny"]))

        _, overflow = self.extension_with_tools(25)
        self.answers["gatewayExtensions"] = [overflow]
        self.assertIn("gatewayExtensions", self.configure(expected=1).stderr)
        env["PIXEL_GATEWAY_EXTENSIONS"] = json.dumps([overflow])
        self.assertIn("PIXEL_GATEWAY_EXTENSIONS", self.render(env, expected=1))

    def test_legacy_provider_and_url_remain_default(self):
        self.configure()
        env = self.configured_env()
        env.pop("PIXEL_WEB_SEARCH_PROVIDER")
        config = self.render(env)
        self.assertEqual(config["tools"]["web"]["search"]["provider"], "searxng")
        self.assertEqual(config["plugins"]["entries"]["searxng"]["config"]["webSearch"]["baseUrl"], "http://127.0.0.1:8890")

    def test_keyless_provider_survives_reconfigure_without_searxng_url(self):
        self.select_parallel()
        for _ in range(2):
            self.configure()
            env = self.configured_env()
            self.assertEqual(env["PIXEL_WEB_SEARCH_PROVIDER"], "parallel-free")
            env.pop("PIXEL_SEARXNG_BASE_URL", None)
            config = self.render(env)
            self.assertEqual(config["tools"]["web"]["search"]["provider"], "parallel-free")
            self.assertIn("parallel", config["plugins"]["allow"])
            self.assertNotIn("searxng", config["plugins"]["allow"])
            self.assertNotIn("searxng", config["plugins"]["entries"])
            deployment = json.loads((self.root / ".generated/deployment.json").read_text())
            self.assertEqual(deployment["webSearchProvider"], "parallel-free")

    def test_paid_or_unknown_provider_is_not_silently_substituted(self):
        for provider in ["parallel", "invented"]:
            self.answers["webSearchProvider"] = provider
            self.assertIn("webSearchProvider", self.configure(expected=1).stderr)

    def test_parallel_requires_digest_bound_extension(self):
        self.answers["webSearchProvider"] = "parallel-free"
        for extensions in [[], [{"id": "parallel"}]]:
            self.answers["gatewayExtensions"] = extensions
            self.configure(expected=1)

    def test_tampering_is_rejected_after_configuration(self):
        self.select_parallel()
        self.configure()
        (self.plugin / "unexpected.js").write_text("throw new Error('changed');\n")
        self.assertIn("digest", self.render(expected=1))

    def test_environment_cannot_bypass_provider_checks(self):
        self.configure()
        env = self.configured_env()
        env["PIXEL_WEB_SEARCH_PROVIDER"] = "parallel-free"
        self.assertIn("parallel", self.render(env, expected=1))
        env["PIXEL_WEB_SEARCH_PROVIDER"] = "parallel"
        self.assertIn("PIXEL_WEB_SEARCH_PROVIDER", self.render(env, expected=1))


if __name__ == "__main__":
    unittest.main()
