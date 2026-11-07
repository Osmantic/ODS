#!/usr/bin/env python3
"""Tests for ods/scripts/prepare-extension-manifests.py.

Covers:
  - real temp dirs with umask 077 and 022
  - in-place rerun and idempotence
  - private file modes unchanged (private.key 0600)
  - all three manifest extensions
  - symlink / fifo / hardlink refusal before any mode changes
  - invalid root refusal
  - mixed-UID actual read (optional, skipped unless env set)

Run: python3 -m unittest ods/tests/test_public_extension_manifest_permissions.py
"""

from __future__ import annotations

import importlib.util
import os
import stat
import tempfile
import unittest
from pathlib import Path


_HERE = Path(__file__).resolve()
_SCRIPT = _HERE.parent.parent / "scripts" / "prepare-extension-manifests.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "prepare_extension_manifests", _SCRIPT
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_mod = _load_module()


class _Umask:
    def __init__(self, mask: int) -> None:
        self.mask = mask
        self.prev = None

    def __enter__(self):
        self.prev = os.umask(self.mask)
        return self

    def __exit__(self, *exc):
        os.umask(self.prev)
        return False


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


def _make_tree(root: Path, umask: int) -> dict[str, Path]:
    """Create a realistic ODS install tree under root with given umask."""
    with _Umask(umask):
        extensions = root / "extensions"
        services = extensions / "services"
        services.mkdir(parents=True)

        paths: dict[str, Path] = {
            "root": root,
            "extensions": extensions,
            "services": services,
        }

        for name, manifest_name in (
            ("ape", "manifest.yaml"),
            ("beta", "manifest.yml"),
            ("gamma", "manifest.json"),
        ):
            child = services / name
            child.mkdir()
            manifest = child / manifest_name
            manifest.write_text(f"name: {name}\n", encoding="utf-8")
            private = child / "private.key"
            private.write_text("secret\n", encoding="utf-8")
            private.chmod(0o600)
            paths[f"child_{name}"] = child
            paths[f"manifest_{name}"] = manifest
            paths[f"private_{name}"] = private

        # A child with no manifest is valid.
        empty = services / "empty"
        empty.mkdir()
        paths["child_empty"] = empty

        # Unrelated sibling dir under install root must be untouched.
        unrelated = root / "data"
        unrelated.mkdir()
        paths["unrelated"] = unrelated
        env = root / ".env"
        env.write_text("fixture-only\n", encoding="utf-8")
        env.chmod(0o600)
        paths["env"] = env

    return paths


def _snapshot_modes(paths: dict[str, Path]) -> dict[str, int]:
    return {k: _mode(v) for k, v in paths.items()}


def _make_dashboard_files(root: Path) -> dict[str, Path]:
    paths = {}
    with _Umask(0o077):
        for directory, names in _mod.PUBLIC_DASHBOARD_FILES.items():
            parent = root / directory
            parent.mkdir(exist_ok=True)
            paths[directory] = parent
            for name in names:
                leaf = parent / name
                leaf.write_text("{}", encoding="utf-8")
                paths[f"public_{directory}_{name}"] = leaf
        (root / "config/n8n").mkdir()
        for name in ("config/private.json", "config/n8n/catalog.json", "templates/custom-private.yaml"):
            leaf = root / name
            leaf.write_text("private fixture", encoding="utf-8")
            paths[f"private_{name}"] = leaf
    return paths


class PrepareExtensionManifestsTests(unittest.TestCase):
    def test_factory_model_discovery_metadata_under_private_umask(self) -> None:
        names = ("model-library.json", "llama-cpp-architectures.json")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paths = _make_tree(root, 0o077)
            paths.update(_make_dashboard_files(root))
            # Independent factory inputs: a missing allowlist entry must fail,
            # even though the general fixture follows PUBLIC_DASHBOARD_FILES.
            with _Umask(0o077):
                for name in names:
                    leaf = root / "config" / name
                    leaf.write_bytes((_HERE.parent.parent / "config" / name).read_bytes())
                    self.assertEqual(_mode(leaf), 0o600)
            before = {name: (root / "config" / name).read_bytes() for name in names}
            self._run(root)
            for name in names:
                self.assertEqual(_mode(root / "config" / name), 0o644)
                self.assertEqual((root / "config" / name).read_bytes(), before[name])
            self.assertEqual(_mode(root / "config/private.json"), 0o600)
            self.assertEqual(_mode(root / "config/n8n"), 0o700)
            self.assertEqual(_mode(root / ".env"), 0o600)

    def test_dashboard_public_scope_and_private_preservation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paths = _make_tree(root, 0o077)
            paths.update(_make_dashboard_files(root))
            before = {key: path.read_bytes() for key, path in paths.items() if path.is_file()}
            self._run(root)
            self.assertEqual(_mode(paths["config"]), 0o711)
            self.assertEqual(_mode(paths["templates"]), 0o755)
            for key, path in paths.items():
                if key.startswith("public_"):
                    self.assertEqual(_mode(path), 0o644)
                if key.startswith("private_") or key == "env":
                    self.assertEqual(_mode(path), 0o600)
            self.assertEqual(_mode(root / "config/n8n"), 0o700)
            self.assertEqual({key: paths[key].read_bytes() for key in before}, before)
            self.assertEqual(self._run(root)[1], 0)

    def test_dashboard_unsafe_leaf_refused_before_any_mutation(self) -> None:
        for kind in ("symlink", "hardlink", "fifo"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                paths = _make_tree(root, 0o077)
                paths.update(_make_dashboard_files(root))
                leaf = root / "config/core-service-ids.json"
                if kind == "hardlink":
                    os.link(leaf, root / "linked-copy")
                else:
                    leaf.unlink()
                    if kind == "symlink":
                        leaf.symlink_to(root / ".env")
                    else:
                        os.mkfifo(leaf)
                before = _snapshot_modes(paths)
                with self.assertRaises(_mod.ManifestPermissionError):
                    self._run(root)
                self.assertEqual(_snapshot_modes(paths), before)

    def test_dashboard_catalogs_outside_pixel_inventory_are_repaired_during_upgrade(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paths = _make_tree(root, 0o077)
            paths.update(_make_dashboard_files(root))
            for key, path in paths.items():
                if key.startswith("manifest_") or key.startswith("public_"):
                    path.chmod(0o644)
            (root / "templates/voice-assistant.yaml").chmod(0o600)
            _mod.prepare(root, preserve_file_modes=True)
            self.assertEqual(_mode(root / "templates/voice-assistant.yaml"), 0o644)
            for key, path in paths.items():
                if key.startswith("private_") or key == "env":
                    self.assertEqual(_mode(path), 0o600)

    def test_dashboard_template_allowlist_matches_bundled_templates(self) -> None:
        templates = _HERE.parent.parent / "templates"
        self.assertEqual(set(_mod.PUBLIC_DASHBOARD_FILES["templates"]),
                         {path.name for path in templates.glob("*.yaml")})

    def test_protected_source_inventory_is_unchanged(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "pixel_source_upgrade", _HERE.parent.parent / "bin/pixel_source_upgrade.py"
        )
        upgrade = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(upgrade)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "install"
            root.mkdir(mode=0o700)
            paths = _make_tree(root, 0o077)
            paths.update(_make_dashboard_files(root))
            for name in upgrade.ROOTS:
                (root / name).mkdir(exist_ok=True)
            # This repair is intentionally outside the exact source inventory.
            # Fail if its ownership expands; never silently normalize a newly
            # protected public catalog in place.
            self.assertTrue(set(_mod.PUBLIC_DASHBOARD_FILES).isdisjoint(upgrade.ROOTS))
            for key, mode in (("manifest_ape", 0o644), ("manifest_beta", 0o755),
                              ("manifest_gamma", 0o444)):
                paths[key].chmod(mode)
            before = upgrade.inventory(root, os.getuid())
            _mod.prepare(root, preserve_file_modes=True)
            self.assertEqual(upgrade.inventory(root, os.getuid()), before)
            self.assertEqual(_mode(paths["services"]), 0o755)
            for key, path in paths.items():
                if key.startswith("public_"):
                    self.assertEqual(_mode(path), 0o644)

    def test_private_manifest_in_protected_source_refused_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "install"
            root.mkdir(mode=0o700)
            paths = _make_tree(root, 0o077)
            before = {key: _mode(path) for key, path in paths.items()}
            with self.assertRaisesRegex(_mod.ManifestPermissionError, "protected source"):
                _mod.prepare(root, preserve_file_modes=True)
            self.assertEqual({key: _mode(path) for key, path in paths.items()}, before)

    def _run(self, root: Path) -> tuple[int, int]:
        return _mod.prepare(root)

    def test_relative_symlink_root_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "install"
            root.mkdir()
            paths = _make_tree(root, 0o077)
            (Path(td) / "linked").symlink_to(root, target_is_directory=True)
            previous = Path.cwd()
            try:
                os.chdir(td)
                with self.assertRaises(_mod.ManifestPermissionError):
                    self._run(Path("linked"))
            finally:
                os.chdir(previous)
            self.assertEqual(_mode(paths["manifest_ape"]), 0o600)

    def test_umask_077_fixes_public_surface(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "install"
            root.mkdir()
            paths = _make_tree(root, 0o077)

            # Preconditions: umask 077 produced restrictive modes.
            self.assertEqual(_mode(paths["services"]), 0o700)
            self.assertEqual(_mode(paths["manifest_ape"]), 0o600)
            self.assertEqual(_mode(paths["private_ape"]), 0o600)

            manifest_count, changed = self._run(root)
            self.assertEqual(manifest_count, 3)
            self.assertGreater(changed, 0)
            self.assertEqual(_mode(paths["env"]), 0o600)

            # Services root 0755.
            self.assertEqual(_mode(paths["services"]), 0o755)
            # Child dirs: traversal (x added, g/o w removed, read bits kept).
            for key in ("child_ape", "child_beta", "child_gamma", "child_empty"):
                m = _mode(paths[key])
                self.assertTrue(m & stat.S_IXUSR, key)
                self.assertTrue(m & stat.S_IXGRP, key)
                self.assertTrue(m & stat.S_IXOTH, key)
                self.assertFalse(m & stat.S_IWGRP, key)
                self.assertFalse(m & stat.S_IWOTH, key)
            # Manifests 0644.
            for key in ("manifest_ape", "manifest_beta", "manifest_gamma"):
                self.assertEqual(_mode(paths[key]), 0o644, key)
            # Private keys unchanged.
            for key in ("private_ape", "private_beta", "private_gamma"):
                self.assertEqual(_mode(paths[key]), 0o600, key)
            # Unrelated dir untouched.
            self.assertEqual(_mode(paths["unrelated"]), 0o700)

    def test_umask_022_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "install"
            root.mkdir()
            paths = _make_tree(root, 0o022)

            self._run(root)
            first = _snapshot_modes(paths)
            manifest_count, changed = self._run(root)
            second = _snapshot_modes(paths)

            self.assertEqual(manifest_count, 3)
            self.assertEqual(changed, 0)
            self.assertEqual(first, second)
            self.assertEqual(_mode(paths["manifest_ape"]), 0o644)
            self.assertEqual(_mode(paths["private_ape"]), 0o600)

    def test_in_place_rerun_after_umask_077(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "install"
            root.mkdir()
            paths = _make_tree(root, 0o077)

            self._run(root)
            first = _snapshot_modes(paths)
            _, changed = self._run(root)
            second = _snapshot_modes(paths)

            self.assertEqual(changed, 0)
            self.assertEqual(first, second)

    def test_symlink_manifest_refused_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "install"
            root.mkdir()
            paths = _make_tree(root, 0o077)

            target = paths["manifest_ape"]
            real = target.with_name("real.yaml")
            real.write_text("name: ape\n", encoding="utf-8")
            target.unlink()
            target.symlink_to(real)

            before = _snapshot_modes(paths)
            with self.assertRaises(_mod.ManifestPermissionError):
                self._run(root)
            after = _snapshot_modes(paths)
            self.assertEqual(before, after)

    def test_symlink_child_dir_refused_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "install"
            root.mkdir()
            paths = _make_tree(root, 0o077)

            real = paths["child_ape"]
            link = paths["services"] / "linked"
            link.symlink_to(real)

            before = _snapshot_modes(paths)
            with self.assertRaises(_mod.ManifestPermissionError):
                self._run(root)
            after = _snapshot_modes(paths)
            self.assertEqual(before, after)

    def test_fifo_manifest_refused_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "install"
            root.mkdir()
            paths = _make_tree(root, 0o077)

            target = paths["manifest_ape"]
            target.unlink()
            os.mkfifo(target)

            before = _snapshot_modes(paths)
            with self.assertRaises(_mod.ManifestPermissionError):
                self._run(root)
            after = _snapshot_modes(paths)
            self.assertEqual(before, after)

    def test_hardlinked_manifest_refused_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "install"
            root.mkdir()
            paths = _make_tree(root, 0o077)

            target = paths["manifest_ape"]
            other = target.with_name("other.yaml")
            os.link(target, other)

            before = _snapshot_modes(paths)
            with self.assertRaises(_mod.ManifestPermissionError):
                self._run(root)
            after = _snapshot_modes(paths)
            self.assertEqual(before, after)

    def test_invalid_root_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "install"
            root.mkdir()
            _make_tree(root, 0o077)

            missing = Path(td) / "does-not-exist"
            with self.assertRaises(_mod.ManifestPermissionError):
                self._run(missing)

    def test_root_is_file_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "install"
            root.write_text("not a dir\n", encoding="utf-8")
            with self.assertRaises(_mod.ManifestPermissionError):
                self._run(root)

    def test_missing_manifest_in_child_ok(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "install"
            root.mkdir()
            paths = _make_tree(root, 0o077)

            manifest_count, _ = self._run(root)
            self.assertEqual(manifest_count, 3)
            self.assertTrue(paths["child_empty"].is_dir())

    def test_names_with_spaces_ok(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "install"
            root.mkdir()
            paths = _make_tree(root, 0o077)

            spaced = paths["services"] / "with space"
            spaced.mkdir()
            manifest = spaced / "manifest.yaml"
            manifest.write_text("name: spaced\n", encoding="utf-8")

            manifest_count, _ = self._run(root)
            self.assertEqual(manifest_count, 4)
            self.assertEqual(_mode(manifest), 0o644)

    @unittest.skipUnless(
        os.environ.get("ODS_MIXED_UID_TEST") == "1",
        "set ODS_MIXED_UID_TEST=1 and run as root to exercise mixed-UID read",
    )
    def test_mixed_uid_actual_read(self) -> None:
        """Optional integration: verify a non-owner UID can read manifests.

        Run separately as root, e.g.:
          ODS_MIXED_UID_TEST=1 python3 -m unittest \
            ods/tests/test_public_extension_manifest_permissions.py
        """
        import pwd

        with tempfile.TemporaryDirectory() as td:
            os.chmod(td, 0o755)
            root = Path(td) / "install"
            root.mkdir()
            paths = _make_tree(root, 0o077)
            nobody = pwd.getpwnam("nobody")
            paths.update(_make_dashboard_files(root))

            def can_read(path):
                pid = os.fork()
                if pid == 0:
                    try:
                        os.setgroups([])
                        os.setgid(nobody.pw_gid)
                        os.setuid(nobody.pw_uid)
                        with open(path, "rb") as fh:
                            fh.read(1)
                    except OSError:
                        os._exit(1)
                    os._exit(0)
                _, status = os.waitpid(pid, 0)
                return os.waitstatus_to_exitcode(status) == 0

            self.assertFalse(can_read(paths["manifest_ape"]))
            self._run(root)
            for key in ("manifest_ape", "manifest_beta", "manifest_gamma"):
                self.assertTrue(can_read(paths[key]), key)
            self.assertFalse(can_read(paths["private_ape"]))
            for key, path in paths.items():
                if key.startswith("public_"):
                    self.assertTrue(can_read(path), key)
                if key.startswith("private_") or key == "env":
                    self.assertFalse(can_read(path), key)


if __name__ == "__main__":
    unittest.main()
