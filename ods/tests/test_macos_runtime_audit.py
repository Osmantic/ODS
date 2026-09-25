import importlib.util
from pathlib import Path
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "installers/macos/lib/native-runtime-audit.py"
spec = importlib.util.spec_from_file_location("native_runtime_audit", SOURCE)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

COMMANDS = "cmd LC_BUILD_VERSION\n minos 14.0\ncmd LC_RPATH\n cmdsize 32\n path @loader_path (offset 12)"


class RuntimeAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.binary = self.root / "llama-server"
        self.binary.touch()
        (self.root / "libggml.dylib").touch()

    def inspect(self, dependency="@rpath/libggml.dylib", commands=COMMANDS, arch="arm64"):
        libraries = "llama-server:\n\t" + dependency + " (compatibility version 0.0.0, current version 0.0.0)\n"
        return audit.inspect_metadata(self.binary, self.root, arch, commands, libraries, "14.0")

    def test_local_and_system_dependencies(self):
        for dependency in ("@rpath/libggml.dylib", "@loader_path/libggml.dylib", "/usr/lib/libSystem.B.dylib"):
            self.assertEqual(self.inspect(dependency), [])

    def test_external_dependency_rejected(self):
        self.assertTrue(self.inspect("/opt/homebrew/lib/libssl.dylib"))

    def test_build_rpath_rejected(self):
        self.assertTrue(self.inspect(commands=COMMANDS.replace("@loader_path", "/Users/builder/build")))

    def test_missing_library_rejected(self):
        self.assertTrue(self.inspect("@rpath/missing.dylib"))

    def test_parent_traversal_rejected(self):
        for dependency in ("/usr/lib/../../tmp/lib.dylib", "@rpath/../lib.dylib"):
            self.assertTrue(self.inspect(dependency))

    def test_symlink_escape_rejected(self):
        (self.root / "outside.dylib").symlink_to(SOURCE)
        self.assertTrue(self.inspect("@rpath/outside.dylib"))

    def test_missing_and_newer_deployment_target_rejected(self):
        self.assertTrue(self.inspect(commands=COMMANDS.replace("minos 14.0", "minos 26.0")))
        self.assertTrue(self.inspect(commands=COMMANDS.replace("minos 14.0", "")))

    def test_architecture_rejected(self):
        self.assertTrue(self.inspect(arch="x86_64"))

    def test_dylib_identity_is_not_dependency(self):
        commands = 'cmd LC_BUILD_VERSION\n minos 14.0\ncmd LC_ID_DYLIB\n cmdsize 48\n name @rpath/libggml.dylib (offset 24)'
        self.assertEqual(self.inspect(commands=commands), [])

    def test_older_loader_metadata(self):
        old = COMMANDS.replace("cmd LC_BUILD_VERSION\n minos 14.0", "cmd LC_VERSION_MIN_MACOSX\n cmdsize 16\n version 13.0")
        self.assertEqual(self.inspect(commands=old), [])


if __name__ == "__main__":
    unittest.main()
