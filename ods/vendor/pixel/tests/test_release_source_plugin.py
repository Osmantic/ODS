import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class ReleaseSourcePluginTests(unittest.TestCase):
    """Every release carries the Source Broker plugin's dependency.

    A source turned on after apply loads the plugin from the active release, so the
    release must not depend on which limbs were on when it was built. The fake npm
    stands in for the registry; PIXEL_SKIP_NPM_CI=1 keeps offline test builds offline.
    """

    DEPENDENCY = "plugin/node_modules/typebox/package.json"

    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        if shutil.which("bash") is None or os.name == "nt":
            raise unittest.SkipTest("the release build runs under bash on Linux")

    def build(self, temporary: Path, **settings: str) -> Path:
        root = temporary / "repo"
        shutil.copytree(self.root, root, ignore=shutil.ignore_patterns(
            ".git", ".generated", ".runtime", "dist", "node_modules", "__pycache__", "*.pyc"))
        dist = root / "dist"
        dist.mkdir()
        for name in ("openclaw.json", "openclaw.sha256", "release-identity.json",
                     "deployment.sha256", "source-runtime.sha256"):
            (dist / name).write_text("dummy plan artifact\n", encoding="utf-8")
        tools = temporary / "bin"
        tools.mkdir()
        shutil.copy2(self.root / "tests" / "fixtures" / "bin" / "npm", tools / "npm")
        (tools / "npm").chmod(0o755)
        env = dict(os.environ, PATH=f"{tools}{os.pathsep}{os.environ.get('PATH', '')}",
                   PIXEL_OPS_BROKER_ENABLED="0", PIXEL_FRONTIER_BROKER_ENABLED="0",
                   PIXEL_WEB_COURIER_ENABLED="0")
        env.pop("PIXEL_SKIP_NPM_CI", None)
        env.update(settings)
        target = root / "release-stage"
        script = (
            "set -euo pipefail\n"
            "pixel_die(){ echo \"PIXEL_DIE: $*\" >&2; exit 70; }\n"
            f"source \"{root}/scripts/lib/release-build.sh\"\n"
            f"pixel_build_release_stage \"{root}\" \"{target}\"\n"
        )
        result = subprocess.run(["bash", "-c", script], cwd=root, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return target

    def manifest(self, target: Path) -> set[str]:
        lines = (target / "install-manifest.sha256").read_text(encoding="utf-8").splitlines()
        return {line.partition("  ")[2].removeprefix("./") for line in lines}

    def test_a_release_with_the_broker_off_still_carries_the_plugin_dependency(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = self.build(Path(temporary), PIXEL_SOURCE_BROKER_ENABLED="0")
            self.assertTrue((target / self.DEPENDENCY).is_file())
            self.assertIn(self.DEPENDENCY, self.manifest(target))

    def test_an_offline_test_build_of_a_disabled_broker_skips_npm(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = self.build(Path(temporary), PIXEL_SOURCE_BROKER_ENABLED="0", PIXEL_SKIP_NPM_CI="1")
            self.assertFalse((target / "plugin" / "node_modules").exists())
            self.assertNotIn(self.DEPENDENCY, self.manifest(target))

    def test_an_enabled_broker_installs_the_dependency_as_before(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = self.build(Path(temporary), PIXEL_SOURCE_BROKER_ENABLED="1", PIXEL_SKIP_NPM_CI="1")
            self.assertTrue((target / self.DEPENDENCY).is_file())
            self.assertIn(self.DEPENDENCY, self.manifest(target))


if __name__ == "__main__":
    unittest.main()
