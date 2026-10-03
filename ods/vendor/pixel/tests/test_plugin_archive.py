import base64
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "verify-plugin-archive.py"
SPEC = importlib.util.spec_from_file_location("pixel_verify_plugin_archive", MODULE_PATH)
verifier = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(verifier)


class PluginArchiveVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "plugin"
        self.root.mkdir()
        (self.root / "package.json").write_bytes(b'{"name":"fixture"}\n')
        (self.root / "dist").mkdir()
        (self.root / "dist" / "index.js").write_bytes(b"export default true;\n")
        self.archive = Path(self.temporary.name) / "plugin.tgz"
        self.make_archive({
            "package/package.json": b'{"name":"fixture"}\n',
            "package/dist/index.js": b"export default true;\n",
        })

    def tearDown(self):
        self.temporary.cleanup()

    def make_archive(self, files):
        with tarfile.open(self.archive, "w:gz") as bundle:
            for name, payload in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                bundle.addfile(info, io.BytesIO(payload))

    def test_matching_install_is_verified(self):
        result = verifier.verify(self.archive, self.root)
        self.assertEqual(result["verifiedFiles"], 2)

    def test_modified_installed_file_is_refused(self):
        (self.root / "dist" / "index.js").write_text("tampered\n", encoding="utf-8")
        with self.assertRaisesRegex(verifier.VerificationError, "differs"):
            verifier.verify(self.archive, self.root)

    def test_missing_file_is_refused(self):
        (self.root / "dist" / "index.js").unlink()
        with self.assertRaisesRegex(verifier.VerificationError, "missing"):
            verifier.verify(self.archive, self.root)

    def test_archive_path_escape_is_refused(self):
        self.make_archive({"../outside": b"bad", "package/package.json": b"{}"})
        with self.assertRaisesRegex(verifier.VerificationError, "outside package"):
            verifier.verify(self.archive, self.root)

    def optional_archive(self, *, optional=True, installed=False, tampered=False):
        manifest = b'{"name":"fixture","bundledDependencies":["@fixture/native"]}\n'
        lock = {
            "name": "fixture", "version": "1.0.0", "lockfileVersion": 3,
            "packages": {
                "": {"name": "fixture", "version": "1.0.0"},
                "node_modules/@fixture/native": {
                    "version": "1.0.0", "optional": optional,
                    "integrity": "sha512-fixture",
                    "peerDependencies": {"@fixture/peer": "^1.0.0"},
                },
            },
        }
        package_json = b'{"name":"@fixture/native","version":"1.0.0"}\n'
        files = {
            "package/package.json": manifest,
            "package/dist/index.js": b"export default true;\n",
            "package/npm-shrinkwrap.json": json.dumps(lock, sort_keys=True).encode() + b"\n",
            "package/node_modules/@fixture/native/package.json": package_json,
            "package/node_modules/@fixture/native/native.node": b"pinned-native-bytes",
        }
        self.make_archive(files)
        (self.root / "package.json").write_bytes(manifest)
        (self.root / "npm-shrinkwrap.json").write_bytes(files["package/npm-shrinkwrap.json"])
        if installed:
            native = self.root / "node_modules/@fixture/native"
            native.mkdir(parents=True)
            native.joinpath("package.json").write_bytes(package_json)
            native.joinpath("native.node").write_bytes(b"tampered" if tampered else b"pinned-native-bytes")

    def test_lock_declared_optional_package_may_be_pruned(self):
        self.optional_archive(optional=True)
        result = verifier.verify(self.archive, self.root)
        self.assertEqual(result["omittedOptionalPackages"], 1)
        self.assertEqual(result["verifiedFiles"], 3)

    def test_present_optional_package_remains_byte_exact(self):
        self.optional_archive(optional=True, installed=True, tampered=True)
        with self.assertRaisesRegex(verifier.VerificationError, "differs"):
            verifier.verify(self.archive, self.root)

    def test_missing_required_package_is_never_pruned(self):
        self.optional_archive(optional=False)
        with self.assertRaisesRegex(verifier.VerificationError, "missing"):
            verifier.verify(self.archive, self.root)

    def test_duplicate_archive_member_is_refused(self):
        with tarfile.open(self.archive, "w:gz") as bundle:
            for payload in (b'{"name":"fixture"}\n', b'{"name":"other"}\n'):
                info = tarfile.TarInfo("package/package.json")
                info.size = len(payload)
                bundle.addfile(info, io.BytesIO(payload))
        with self.assertRaisesRegex(verifier.VerificationError, "duplicate"):
            verifier.verify(self.archive, self.root)

    def test_optional_flag_requires_an_archived_package_identity(self):
        self.optional_archive(optional=True)
        with tarfile.open(self.archive, "w:gz") as bundle:
            files = {
                "package/package.json": b'{"name":"fixture"}\n',
                "package/dist/index.js": b"export default true;\n",
                "package/npm-shrinkwrap.json": self.root.joinpath("npm-shrinkwrap.json").read_bytes(),
                "package/node_modules/@fixture/native/native.node": b"unidentified-native",
            }
            for name, payload in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                bundle.addfile(info, io.BytesIO(payload))
        with self.assertRaisesRegex(verifier.VerificationError, "no archived package.json"):
            verifier.verify(self.archive, self.root)

    def test_metadata_only_optional_package_does_not_authorize_an_omission(self):
        self.optional_archive(optional=True)
        with tarfile.open(self.archive, "w:gz") as bundle:
            files = {
                "package/package.json": self.root.joinpath("package.json").read_bytes(),
                "package/dist/index.js": b"export default true;\n",
                "package/npm-shrinkwrap.json": self.root.joinpath("npm-shrinkwrap.json").read_bytes(),
            }
            for name, payload in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                bundle.addfile(info, io.BytesIO(payload))
        result = verifier.verify(self.archive, self.root)
        self.assertEqual(result["omittedOptionalPackages"], 0)

    def rewrite_install_lock(self, *, peer_name="@fixture/peer", materialize_peer=False):
        lock = json.loads((self.root / "npm-shrinkwrap.json").read_text(encoding="utf-8"))
        lock["packages"][""]["bundleDependencies"] = ["@fixture/native"]
        lock["packages"]["node_modules/@fixture/native"]["inBundle"] = True
        lock["packages"][f"node_modules/{peer_name}"] = {
            "version": "1.2.0",
            "resolved": f"https://registry.npmjs.org/{peer_name}/-/{peer_name.split('/')[-1]}-1.2.0.tgz",
            "integrity": "sha512-" + base64.b64encode(b"x" * 64).decode(),
            "optional": True,
            "peer": True,
            "inBundle": True,
        }
        (self.root / "npm-shrinkwrap.json").write_text(
            json.dumps(lock, indent=2) + "\n", encoding="utf-8",
        )
        hidden = self.root / "node_modules/.package-lock.json"
        hidden.parent.mkdir(exist_ok=True)
        hidden.write_text('{"lockfileVersion":3,"packages":{}}\n', encoding="utf-8")
        if materialize_peer:
            peer = self.root / "node_modules/@fixture/peer"
            peer.mkdir(parents=True)
            peer.joinpath("index.js").write_text("unverified\n", encoding="utf-8")

    def test_npm_rewrite_may_add_only_absent_reachable_optional_peer_metadata(self):
        self.optional_archive(optional=True)
        self.rewrite_install_lock()
        result = verifier.verify(self.archive, self.root)
        self.assertEqual(result["omittedOptionalPackages"], 1)
        self.assertEqual(result["omittedPeerMetadata"], 1)

    def test_npm_rewrite_cannot_change_archived_package_metadata(self):
        self.optional_archive(optional=True)
        self.rewrite_install_lock()
        lock = json.loads((self.root / "npm-shrinkwrap.json").read_text(encoding="utf-8"))
        lock["packages"]["node_modules/@fixture/native"]["version"] = "9.9.9"
        (self.root / "npm-shrinkwrap.json").write_text(json.dumps(lock), encoding="utf-8")
        with self.assertRaisesRegex(verifier.VerificationError, "changed package metadata"):
            verifier.verify(self.archive, self.root)

    def test_unarchived_peer_payload_is_refused_even_when_metadata_is_valid(self):
        self.optional_archive(optional=True)
        self.rewrite_install_lock(materialize_peer=True)
        with self.assertRaisesRegex(verifier.VerificationError, "unarchived peer package"):
            verifier.verify(self.archive, self.root)

    def test_unreachable_added_peer_metadata_is_refused(self):
        self.optional_archive(optional=True)
        self.rewrite_install_lock(peer_name="@fixture/unreachable")
        with self.assertRaisesRegex(verifier.VerificationError, "unreachable peer metadata"):
            verifier.verify(self.archive, self.root)

    def test_unexpected_installed_payload_is_refused(self):
        (self.root / "unexpected.js").write_text("unverified\n", encoding="utf-8")
        with self.assertRaisesRegex(verifier.VerificationError, "unexpected payload"):
            verifier.verify(self.archive, self.root)

    def test_generated_install_lock_must_be_bounded_json(self):
        hidden = self.root / "node_modules/.package-lock.json"
        hidden.parent.mkdir()
        hidden.write_text("not-json\n", encoding="utf-8")
        with self.assertRaisesRegex(verifier.VerificationError, "invalid JSON"):
            verifier.verify(self.archive, self.root)

    def peer_link_fixture(self, *, peer_version="2026.6.33"):
        manifest = {
            "name": "fixture",
            "peerDependencies": {"openclaw": ">=2026.6.33"},
            "peerDependenciesMeta": {"openclaw": {"optional": True}},
        }
        payload = json.dumps(manifest, sort_keys=True).encode() + b"\n"
        self.make_archive({
            "package/package.json": payload,
            "package/dist/index.js": b"export default true;\n",
        })
        (self.root / "package.json").write_bytes(payload)
        peer_root = Path(self.temporary.name) / "openclaw"
        peer_root.mkdir()
        peer_root.joinpath("package.json").write_text(
            json.dumps({"name": "openclaw", "version": peer_version}) + "\n", encoding="utf-8",
        )
        peer_link = self.root / "node_modules/openclaw"
        peer_link.parent.mkdir()
        try:
            peer_link.symlink_to(peer_root, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"directory symlinks unavailable: {exc}")
        return peer_root, peer_link

    def test_exact_declared_optional_peer_link_is_verified(self):
        peer_root, _ = self.peer_link_fixture()
        result = verifier.verify(self.archive, self.root, {"openclaw": ("2026.6.33", peer_root)})
        self.assertEqual(result["linkedPeers"], 1)

    def test_peer_link_requires_an_explicit_expected_target(self):
        self.peer_link_fixture()
        with self.assertRaisesRegex(verifier.VerificationError, "unexpected payload"):
            verifier.verify(self.archive, self.root)

    def test_peer_link_cannot_target_a_different_runtime(self):
        peer_root, peer_link = self.peer_link_fixture()
        other = Path(self.temporary.name) / "other-openclaw"
        other.mkdir()
        other.joinpath("package.json").write_text(
            '{"name":"openclaw","version":"2026.6.33"}\n', encoding="utf-8",
        )
        peer_link.unlink()
        peer_link.symlink_to(other, target_is_directory=True)
        with self.assertRaisesRegex(verifier.VerificationError, "different runtime"):
            verifier.verify(self.archive, self.root, {"openclaw": ("2026.6.33", peer_root)})

    def test_peer_link_target_must_have_the_exact_expected_version(self):
        peer_root, _ = self.peer_link_fixture(peer_version="2026.6.34")
        with self.assertRaisesRegex(verifier.VerificationError, "different identity"):
            verifier.verify(self.archive, self.root, {"openclaw": ("2026.6.33", peer_root)})

    def test_allowed_peer_cli_requires_an_exact_identity_and_absolute_path(self):
        peer_root = Path(self.temporary.name) / "openclaw"
        peer_root.mkdir()
        parsed = verifier.parse_allowed_peers([f"openclaw@2026.6.33={peer_root}"])
        self.assertEqual(parsed["openclaw"], ("2026.6.33", peer_root.resolve()))
        with self.assertRaisesRegex(verifier.VerificationError, "NAME@VERSION"):
            verifier.parse_allowed_peers(["openclaw"])
        with self.assertRaisesRegex(verifier.VerificationError, "must be absolute"):
            verifier.parse_allowed_peers(["openclaw@2026.6.33=relative"])

    def unbundled_dependency_fixture(self, *, resolved=None):
        manifest = {
            "name": "fixture", "version": "1.0.0",
            "optionalDependencies": {"locked-dependency": "2.0.0"},
        }
        integrity = "sha512-" + base64.b64encode(b"y" * 64).decode()
        lock = {
            "name": "fixture", "version": "1.0.0", "lockfileVersion": 3, "requires": True,
            "packages": {
                "": {"name": "fixture", "version": "1.0.0"},
                "node_modules/locked-dependency": {
                    "version": "2.0.0", "optional": True,
                    "resolved": resolved or "https://registry.npmjs.org/locked-dependency/-/locked-dependency-2.0.0.tgz",
                    "integrity": integrity,
                },
            },
        }
        files = {
            "package/package.json": json.dumps(manifest, sort_keys=True).encode() + b"\n",
            "package/dist/index.js": b"export default true;\n",
            "package/npm-shrinkwrap.json": json.dumps(lock, sort_keys=True).encode() + b"\n",
        }
        self.make_archive(files)
        (self.root / "package.json").write_bytes(files["package/package.json"])
        (self.root / "npm-shrinkwrap.json").write_bytes(files["package/npm-shrinkwrap.json"])
        dependency = self.root / "node_modules/locked-dependency"
        dependency.mkdir(parents=True)
        dependency.joinpath("package.json").write_text(
            '{"name":"locked-dependency","version":"2.0.0"}\n', encoding="utf-8",
        )
        dependency.joinpath("index.js").write_text("export default 2;\n", encoding="utf-8")
        return dependency

    def test_unbundled_lock_dependency_requires_and_verifies_a_tree_receipt(self):
        dependency = self.unbundled_dependency_fixture()
        receipt = Path(self.temporary.name) / "installed.json"
        with self.assertRaisesRegex(verifier.VerificationError, "require an integrity receipt"):
            verifier.verify(self.archive, self.root)
        recorded = verifier.verify(self.archive, self.root, write_receipt=receipt)
        self.assertTrue(recorded["receiptBound"])
        self.assertEqual(recorded["verifiedLockPackages"], 1)
        checked = verifier.verify(self.archive, self.root, receipt=receipt)
        self.assertTrue(checked["receiptBound"])
        dependency.joinpath("index.js").write_text("tampered\n", encoding="utf-8")
        with self.assertRaisesRegex(verifier.VerificationError, "differs from its integrity receipt"):
            verifier.verify(self.archive, self.root, receipt=receipt)

    def test_unbundled_dependency_must_use_the_exact_registry_source(self):
        self.unbundled_dependency_fixture(resolved="https://example.invalid/dependency.tgz")
        receipt = Path(self.temporary.name) / "installed.json"
        with self.assertRaisesRegex(verifier.VerificationError, "untrusted package source"):
            verifier.verify(self.archive, self.root, write_receipt=receipt)

    def test_unlocked_dependency_payload_is_refused(self):
        self.unbundled_dependency_fixture()
        extra = self.root / "node_modules/not-locked"
        extra.mkdir()
        extra.joinpath("index.js").write_text("untrusted\n", encoding="utf-8")
        receipt = Path(self.temporary.name) / "installed.json"
        with self.assertRaisesRegex(verifier.VerificationError, "unexpected payload"):
            verifier.verify(self.archive, self.root, write_receipt=receipt)

    def test_dependency_symlink_cannot_escape_the_installed_tree(self):
        dependency = self.unbundled_dependency_fixture()
        link = dependency / "escape"
        try:
            link.symlink_to(Path(self.temporary.name), target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"directory symlinks unavailable: {exc}")
        receipt = Path(self.temporary.name) / "installed.json"
        with self.assertRaisesRegex(verifier.VerificationError, "escaping or broken symlink"):
            verifier.verify(self.archive, self.root, write_receipt=receipt)


if __name__ == "__main__":
    unittest.main()
