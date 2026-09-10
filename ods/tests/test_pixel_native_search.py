"""Exercise provider selection and immutable provisioning without contacting npm."""

import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "native_search", Path(__file__).resolve().parents[1]
    / "extensions/services/pixel-agent/host/native_search.py")
ns = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ns)


def archive(overrides=None, extra=()):
    files = {
        "package/package.json": json.dumps({"name": ns.PACKAGE, "version": ns.VERSION}).encode(),
        "package/openclaw.plugin.json": json.dumps({
            "id": "parallel", "contracts": {"webSearchProviders": ["parallel-free"]}}).encode(),
        "package/dist/index.js": b"// inert fixture\n",
    }
    files.update(overrides or {})
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as bundle:
        for name, content in files.items():
            if content is None:
                continue
            entry = tarfile.TarInfo(name)
            entry.size = len(content)
            bundle.addfile(entry, io.BytesIO(content))
        for entry, content in extra:
            bundle.addfile(entry, io.BytesIO(content))
    return out.getvalue()


class NativeSearchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data = archive()
        # A valid baseline is required before each negative mutation.
        self.assertEqual(set(ns.archive_files(self.data)),
                         {"package.json", "openclaw.plugin.json", "dist/index.js"})

    def test_provider_choices_and_legacy_preservation(self):
        answers = self.root / "answers.json"
        self.assertEqual(ns.select_provider(answers), "parallel-free")
        for document, expected in [({}, "searxng"),
                                   ({"webSearchProvider": "searxng"}, "searxng"),
                                   ({"webSearchProvider": "parallel-free"}, "parallel-free")]:
            answers.write_text(json.dumps(document))
            answers.chmod(0o600)
            self.assertEqual(ns.select_provider(answers), expected)
            for explicit in ["searxng", "parallel-free"]:
                self.assertEqual(ns.select_provider(answers, explicit), explicit)
        for value in ["parallel", "unknown", None, 3, [], {}]:
            answers.write_text(json.dumps({"webSearchProvider": value}))
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "search provider"):
                ns.select_provider(answers)

    def test_onboarding_requires_private_regular_file(self):
        answers = self.root / "answers.json"
        answers.write_text('{}')
        for mode in [0o640, 0o620, 0o602]:
            answers.chmod(mode)
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, "owner-private"):
                ns.select_provider(answers)
        answers.chmod(0o600)
        link = self.root / "link"
        link.symlink_to(answers)
        with self.assertRaises(OSError):
            ns.select_provider(link)
        self.assertEqual(answers.read_text(), '{}')

    def test_archive_rejects_unsafe_names_and_duplicates(self):
        for name in ["package/../escape", "/escape", "package//empty", "package/./dot",
                     "package/a\\b", "package/a\nb", "package/a\x7fb", "package/dist/index.js"]:
            entry = tarfile.TarInfo(name)
            entry.size = 1
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "invalid search plugin archive entry"):
                ns.archive_files(archive(extra=[(entry, b'x')]))

    def test_archive_rejects_links_and_special_files(self):
        for kind in [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE]:
            entry = tarfile.TarInfo("package/dist/extra")
            entry.type = kind
            entry.linkname = "outside"
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "invalid search plugin archive entry"):
                ns.archive_files(archive(extra=[(entry, b'')]))

    def test_archive_metadata_and_dependency_boundaries(self):
        for path, content in [
            ("package/package.json", b'{"name":"wrong","version":"2026.6.33"}'),
            ("package/package.json", b'{"name":"@openclaw/parallel-plugin","version":"0"}'),
            ("package/openclaw.plugin.json", b'{"id":"parallel","contracts":{"webSearchProviders":["parallel"]}}'),
            ("package/openclaw.plugin.json", None),
        ]:
            with self.subTest(path=path, content=content), self.assertRaisesRegex(ValueError, "identity or keyless provider contract"):
                ns.archive_files(archive({path: content}))
        with self.assertRaisesRegex(ValueError, "node_modules"):
            ns.archive_files(archive({"package/node_modules/x.js": b'x'}))

    def test_archive_size_and_entry_limits(self):
        with self.assertRaisesRegex(ValueError, "size limit"):
            ns.archive_files(b'x' * (ns.MAX_ARCHIVE + 1))
        with self.assertRaisesRegex(ValueError, "content exceeds"):
            ns.archive_files(archive({"package/huge": b'x' * (ns.MAX_ARCHIVE + 1)}))
        with self.assertRaisesRegex(ValueError, "content exceeds"):
            ns.archive_files(archive({f"package/big-{i}": b'x' * ns.MAX_ARCHIVE for i in range(5)}))
        with self.assertRaisesRegex(ValueError, "invalid search plugin archive entry"):
            ns.archive_files(archive({f"package/extra-{i}": b'' for i in range(65)}))

    def seeded(self, name="base"):
        base = self.root / name
        base.mkdir(mode=0o755)
        cache = base / f"parallel-{ns.VERSION}.tgz"
        cache.write_bytes(self.data)
        cache.chmod(0o600)
        return base, cache

    def prepare(self, base):
        integrity = base64.b64encode(hashlib.sha512(self.data).digest()).decode()
        with patch.object(ns, "INTEGRITY", integrity), patch.object(
                ns.urllib.request, "build_opener", side_effect=AssertionError("unexpected network")):
            return ns.prepare(base)

    def test_provision_and_reuse_preserve_exact_tree(self):
        base, _ = self.seeded()
        first = self.prepare(base)
        self.assertTrue(first["changed"])
        self.assertEqual(Path(first["path"], "dist/index.js").read_bytes(), b'// inert fixture\n')
        self.assertFalse(self.prepare(base)["changed"])

    def test_changed_installed_tree_is_retained_and_rejected(self):
        for change in ["modified", "missing", "extra-directory"]:
            with self.subTest(change=change):
                base, _ = self.seeded(change)
                target = Path(self.prepare(base)["path"])
                entry = target / "dist/index.js"
                if change == "modified":
                    entry.write_bytes(b'owner change')
                elif change == "missing":
                    entry.unlink()
                else:
                    (target / "extra").mkdir(mode=0o755)
                with self.assertRaisesRegex(ValueError, "immutable directory was retained"):
                    self.prepare(base)
                self.assertEqual(entry.exists(), change != "missing")
                if change == "modified":
                    self.assertEqual(entry.read_bytes(), b'owner change')
                if change == "extra-directory":
                    self.assertTrue((target / "extra").is_dir())

    def test_corrupt_cache_does_not_publish_or_overwrite(self):
        base, cache = self.seeded()
        cache.write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, "pinned release"):
            self.prepare(base)
        self.assertEqual(cache.read_bytes(), b'corrupt')
        self.assertFalse((base / f"parallel-{ns.VERSION}").exists())

    def test_symlink_base_and_cache_reject_without_external_change(self):
        base, cache = self.seeded()
        link = self.root / "link"
        link.symlink_to(base)
        with self.assertRaisesRegex(ValueError, "symbolic links"):
            self.prepare(link)
        outside = self.root / "outside"
        cache.rename(outside)
        cache.symlink_to(outside)
        with self.assertRaises(OSError):
            self.prepare(base)
        self.assertEqual(outside.read_bytes(), self.data)


if __name__ == "__main__":
    unittest.main()
