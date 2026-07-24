import os
import stat
import sys
import tempfile
import unittest
from unittest import mock

# Allow running this test from repo root without installing the service as a package.
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from key_management import resolve_shield_api_key, persist_key


class TestKeyManagement(unittest.TestCase):
    def test_env_key_wins(self):
        with tempfile.TemporaryDirectory() as d:
            key_path = os.path.join(d, "shield_api_key")
            persist_key(key_path, "persisted")
            self.assertEqual(resolve_shield_api_key("from_env", key_path), "from_env")

    def test_loads_persisted_key(self):
        with tempfile.TemporaryDirectory() as d:
            key_path = os.path.join(d, "shield_api_key")
            persist_key(key_path, "persisted")
            self.assertEqual(resolve_shield_api_key(None, key_path), "persisted")

    def test_generates_and_persists_key(self):
        with tempfile.TemporaryDirectory() as d:
            key_path = os.path.join(d, "shield_api_key")
            key = resolve_shield_api_key(None, key_path)
            self.assertTrue(isinstance(key, str) and len(key) > 0)
            with open(key_path, "r", encoding="utf-8") as f:
                self.assertEqual(f.read().strip(), key)


@unittest.skipUnless(os.name == "posix", "file modes are POSIX-only")
class TestKeyFilePermissions(unittest.TestCase):
    """The key file must never be observable at the process umask.

    /data is a bind mount shared with the host, so any window where the
    credential sits at 0644 is a window where another local user can read it.
    """

    def _mode(self, path):
        return stat.S_IMODE(os.stat(path).st_mode)

    def test_persisted_key_is_owner_only(self):
        with tempfile.TemporaryDirectory() as d:
            key_path = os.path.join(d, "shield_api_key")
            persist_key(key_path, "secret")
            self.assertEqual(self._mode(key_path), 0o600)

    def test_mode_comes_from_the_create_not_a_later_chmod(self):
        """With chmod neutralised the file must still land at 0600.

        A write-then-chmod publishes the key at the umask default first; this
        pins the ordering rather than the end state.
        """
        with tempfile.TemporaryDirectory() as d:
            key_path = os.path.join(d, "shield_api_key")
            with mock.patch("os.chmod"):
                persist_key(key_path, "secret")
            self.assertEqual(
                self._mode(key_path), 0o600,
                "key file was created at the umask default and only tightened "
                "afterwards — readable by other users until the chmod lands",
            )

    def test_existing_loose_file_is_tightened(self):
        with tempfile.TemporaryDirectory() as d:
            key_path = os.path.join(d, "shield_api_key")
            with open(key_path, "w", encoding="utf-8") as f:
                f.write("old")
            os.chmod(key_path, 0o644)

            persist_key(key_path, "secret")

            self.assertEqual(self._mode(key_path), 0o600)
            with open(key_path, "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), "secret")

    def test_generated_key_lands_owner_only(self):
        with tempfile.TemporaryDirectory() as d:
            key_path = os.path.join(d, "shield_api_key")
            with mock.patch("os.chmod"):
                resolve_shield_api_key(None, key_path)
            self.assertEqual(self._mode(key_path), 0o600)


if __name__ == "__main__":
    unittest.main()
