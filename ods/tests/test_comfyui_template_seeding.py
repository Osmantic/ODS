"""Exercise the real container startup with private filesystem mounts, without a GPU."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

STARTUP = Path(__file__).resolve().parents[1] / "extensions/services/comfyui/startup.sh"


@unittest.skipUnless(sys.platform == "linux" and shutil.which("unshare"), "Linux namespaces required")
class TemplateSeedingTests(unittest.TestCase):
    def setUp(self):
        probe = subprocess.run(["unshare", "--user", "--map-root-user", "--mount", "true"],
                               capture_output=True, text=True, timeout=10)
        if probe.returncode:
            if os.environ.get("COMFYUI_REQUIRE_NAMESPACE") == "1":
                self.fail(probe.stderr)
            self.skipTest("The host does not permit private user/mount namespaces")
        temporary = tempfile.TemporaryDirectory(prefix="comfyui-startup-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for directory in ("usr", "bin", "lib", "lib64", "dev", "tmp", "opt/comfyui/models",
                          "models", "input", "output", "workflows", "user/default/workflows"):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        (self.root / "dev/null").touch()
        shutil.copyfile(STARTUP, self.root / "startup.sh")
        (self.root / "opt/comfyui/main.py").write_text(
            'from pathlib import Path\nPath("/started").write_text("ready")\n', encoding="utf-8")
        self.templates = self.root / "workflows"
        self.saved = self.root / "user/default/workflows"

    def start(self):
        result = subprocess.run([
            "unshare", "--user", "--map-root-user", "--mount", "--fork", "--propagation", "private",
            "sh", "-ec", '''
for directory in usr bin lib lib64; do
    if [ -d "/$directory" ]; then mount --rbind "/$directory" "$1/$directory"; fi
done
exec chroot "$1" /bin/bash /startup.sh
''', "comfyui-fixture", str(self.root),
        ], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.root / "started").exists())

    def test_newer_template_does_not_replace_a_saved_workflow(self):
        template = self.templates / "Text to image.json"
        saved = self.saved / template.name
        template.write_text('{"prompt":"stock prompt"}', encoding="utf-8")
        saved.write_text('{"prompt":"my edited pipeline"}', encoding="utf-8")
        saved.chmod(0o640)
        os.utime(saved, (1000000000, 1000000000))
        os.utime(template, (1700000000, 1700000000))
        before = saved.stat()
        self.start()
        self.assertEqual(saved.read_text(), '{"prompt":"my edited pipeline"}')
        self.assertEqual(saved.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(saved.stat().st_mode, before.st_mode)

    def test_new_templates_are_seeded_and_edits_survive_restart(self):
        template = self.templates / "new.json"
        template.write_text('{"prompt":"new template"}', encoding="utf-8")
        self.start()
        saved = self.saved / template.name
        self.assertEqual(saved.read_bytes(), template.read_bytes())
        saved.write_text('{"prompt":"edited after first start"}', encoding="utf-8")
        os.utime(saved, (1000000000, 1000000000))
        template.write_text('{"prompt":"updated template"}', encoding="utf-8")
        self.start()
        self.assertEqual(saved.read_text(), '{"prompt":"edited after first start"}')

    def test_empty_template_mount_still_starts(self):
        self.start()
        self.assertEqual(list(self.saved.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
