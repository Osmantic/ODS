"""Run pinned configure/render against the actual installer-generated tool list."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
ONBOARDING = Path(sys.argv.pop(1)) if __name__ == "__main__" else None
spec = importlib.util.spec_from_file_location(
    "native_search_fixture", ROOT / "vendor/pixel/tests/test_native_search.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


@unittest.skipUnless(ONBOARDING, "requires actual installer-generated onboarding")
class InstallerGatewayToolsTest(fixture.NativeSearchTests):
    @classmethod
    def setUpClass(cls):
        cls.suite = tempfile.TemporaryDirectory(prefix="ods-configure-tools-")
        cls.root = Path(cls.suite.name) / "repo"
        # Use the exact install artifact, not a copied or patched configure stub.
        subprocess.run(["git", "-c", "core.autocrlf=false", "clone", "--quiet",
                        str(ROOT / "vendor/pixel.bundle"), str(cls.root)], check=True)

    def test_actual_installer_extension_tools_configure_and_render(self):
        settings = json.loads(ONBOARDING.read_text())
        supplied = next(x for x in settings["gatewayExtensions"] if x["id"] == "pixel-ods")
        manifest = json.loads((ROOT / "extensions/services/pixel-agent/plugin/openclaw.plugin.json").read_text())
        self.assertEqual(set(supplied["tools"]), set(manifest["contracts"]["tools"]))
        # Only relocate the extension and rebind its digest into private fixture
        # storage. Preserve the exact generated names and cardinality.
        (self.plugin / "openclaw.plugin.json").write_text(json.dumps(manifest) + "\n")
        digest = hashlib.sha256()
        for path in sorted(self.plugin.iterdir()):
            data = path.read_bytes()
            digest.update(path.name.encode() + b"\0" + str(len(data)).encode() + b"\0" + data + b"\0")
        self.answers["gatewayExtensions"] = [dict(supplied, path=str(self.plugin), sha256=digest.hexdigest())]
        self.configure()
        env = self.configured_env()
        env["PIXEL_AGENT_TOOL_ALLOWLIST"] = json.dumps(sorted(supplied["tools"]))
        rendered = self.render(env)
        self.assertTrue(set(supplied["tools"]).issubset(rendered["tools"]["alsoAllow"]))
        self.assertEqual(set(rendered["tools"]["sandbox"]["tools"]["allow"]), set(supplied["tools"]))


if __name__ == "__main__":
    suite = unittest.TestSuite([InstallerGatewayToolsTest("test_actual_installer_extension_tools_configure_and_render")])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
