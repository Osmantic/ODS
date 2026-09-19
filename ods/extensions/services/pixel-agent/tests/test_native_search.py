"""Contract tests for host/native_search.py.

native_search provisions the pinned OpenClaw parallel-search plugin: a fixed
package@version with a baked-in SHA-512 integrity value, extracted through a
strict tarball allowlist (package/ root only, no node_modules, no traversal,
bounded sizes) into an immutable, byte-verified tree.

These tests pin the offline-verifiable surface: custody checks, archive
validation, tree verification, and the onboarding provider selection that
preserves legacy SearXNG installs. Network fetch itself is out of scope.
"""
import base64
import hashlib
import importlib.util
import io
import json
import os
import tarfile
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "host" / "native_search.py"
SPEC = importlib.util.spec_from_file_location("native_search", MODULE)
ns = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ns)


def make_tgz(files, dirs=("package",)):
    """Build an in-memory .tgz. files: {name: bytes}; dirs: dir names."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as archive:
        for name in dirs:
            info = tarfile.TarInfo(name)
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            archive.addfile(info)
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def valid_files(**kw):
    files = {
        "package/package.json": json.dumps(
            {"name": ns.PACKAGE, "version": ns.VERSION}).encode(),
        "package/openclaw.plugin.json": json.dumps({
            "id": "parallel",
            "contracts": {"webSearchProviders": ["parallel-free"]},
        }).encode(),
        "package/index.mjs": b"export {}",
    }
    files.update(kw)
    return files


class TestChecked:
    def test_regular_owner_file_ok(self, tmp_path):
        f = tmp_path / "f"
        f.write_text("x")
        assert ns.checked(f).st_size == 1

    @pytest.mark.parametrize("mode", [0o620, 0o602, 0o777, 0o622])
    def test_public_file_group_other_write_rejected(self, tmp_path, mode):
        # Non-private files may be group/other-readable but never writable.
        f = tmp_path / "f"
        f.write_text("x")
        os.chmod(f, mode)
        with pytest.raises(ValueError, match="unsafe search plugin path"):
            ns.checked(f)

    def test_owner_only_mode_accepted(self, tmp_path):
        f = tmp_path / "f"
        f.write_text("x")
        os.chmod(f, 0o600)
        assert ns.checked(f).st_size == 1

    def test_private_requires_0700_bound(self, tmp_path):
        f = tmp_path / "f"
        f.write_text("x")
        os.chmod(f, 0o644)  # group/other read allowed for public files
        ns.checked(f)       # 0644 passes non-private check
        with pytest.raises(ValueError):
            ns.checked(f, private=True)

    def test_directory_requires_flag(self, tmp_path):
        with pytest.raises(ValueError):
            ns.checked(tmp_path)
        assert ns.checked(tmp_path, directory=True).st_mode

    def test_symlink_rejected(self, tmp_path):
        f = tmp_path / "f"
        f.write_text("x")
        link = tmp_path / "l"
        link.symlink_to(f)
        with pytest.raises(ValueError):
            ns.checked(link)


class TestReadPrivate:
    def test_round_trip(self, tmp_path):
        f = tmp_path / "a.tgz"
        f.write_bytes(b"data")
        os.chmod(f, 0o600)
        assert ns.read_private(f) == b"data"

    @pytest.mark.parametrize("mode", [0o644, 0o640, 0o777])
    def test_non_private_mode_rejected(self, tmp_path, mode):
        f = tmp_path / "a.tgz"
        f.write_bytes(b"data")
        os.chmod(f, mode)
        with pytest.raises(ValueError, match="owner-private"):
            ns.read_private(f)

    def test_oversize_rejected(self, tmp_path):
        f = tmp_path / "a.tgz"
        f.write_bytes(b"x" * (ns.MAX_ARCHIVE + 1))
        os.chmod(f, 0o600)
        with pytest.raises(ValueError):
            ns.read_private(f)


class TestVerifyArchive:
    def test_wrong_bytes_rejected(self):
        with pytest.raises(ValueError, match="pinned release"):
            ns.verify_archive(b"not the release")

    def test_oversize_rejected(self):
        with pytest.raises(ValueError, match="pinned release"):
            ns.verify_archive(b"x" * (ns.MAX_ARCHIVE + 1))


class TestArchiveFiles:
    def test_valid_package(self):
        files = ns.archive_files(make_tgz(valid_files()))
        assert set(files) == {
            "package.json", "openclaw.plugin.json", "index.mjs"}

    def _expect(self, files, match, dirs=("package",)):
        with pytest.raises(ValueError, match=match):
            ns.archive_files(make_tgz(files, dirs=dirs))

    def test_missing_package_root(self):
        self._expect({"other/x.txt": b"x"}, "entry", dirs=("other",))

    @pytest.mark.parametrize("name", [
        "package/../evil", "package/./x", "package//x", "package/a\\b",
        "package/node_modules/x.js", "package/node_modules",
    ])
    def test_traversal_and_node_modules_rejected(self, name):
        files = valid_files()
        files[name] = b"x"
        self._expect(files, "archive entry|node_modules|package root")

    def test_duplicate_entry_rejected(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as archive:
            info = tarfile.TarInfo("package")
            info.type = tarfile.DIRTYPE
            archive.addfile(info)
            for _ in range(2):
                info = tarfile.TarInfo("package/dup.txt")
                info.size = 1
                archive.addfile(info, io.BytesIO(b"x"))
        with pytest.raises(ValueError, match="entry"):
            ns.archive_files(buf.getvalue())

    def test_symlink_entry_rejected(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as archive:
            info = tarfile.TarInfo("package")
            info.type = tarfile.DIRTYPE
            archive.addfile(info)
            link = tarfile.TarInfo("package/evil")
            link.type = tarfile.SYMTYPE
            link.linkname = "/etc/passwd"
            archive.addfile(link)
        with pytest.raises(ValueError, match="entry"):
            ns.archive_files(buf.getvalue())

    def test_more_than_64_entries_rejected(self):
        files = valid_files()
        for i in range(70):
            files[f"package/f{i}.txt"] = b"x"
        self._expect(files, "entry")

    def test_package_identity_required(self):
        bad = dict(valid_files())
        bad["package/package.json"] = json.dumps(
            {"name": "other/pkg", "version": ns.VERSION}).encode()
        self._expect(bad, "identity or keyless provider")

    def test_version_must_match_pin(self):
        bad = dict(valid_files())
        bad["package/package.json"] = json.dumps(
            {"name": ns.PACKAGE, "version": "0.0.0"}).encode()
        self._expect(bad, "identity or keyless provider")

    def test_manifest_contract_required(self):
        bad = dict(valid_files())
        bad["package/openclaw.plugin.json"] = json.dumps({
            "id": "parallel",
            "contracts": {"webSearchProviders": ["other"]},
        }).encode()
        self._expect(bad, "identity or keyless provider")

    def test_manifest_id_required(self):
        bad = dict(valid_files())
        bad["package/openclaw.plugin.json"] = json.dumps({
            "id": "other",
            "contracts": {"webSearchProviders": ["parallel-free"]},
        }).encode()
        self._expect(bad, "identity or keyless provider")


class TestExpectedDirectories:
    def test_collects_parents(self):
        files = {"a/b/c.txt": b"x", "a/d.txt": b"y", "e.txt": b"z"}
        assert ns.expected_directories(files) == {"a", "a/b"}

    def test_empty(self):
        assert ns.expected_directories({}) == set()


class TestVerifyTree:
    def _install(self, target, files):
        target.mkdir(parents=True)
        for name, data in files.items():
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            os.chmod(path, 0o644)

    def test_matching_tree_passes(self, tmp_path):
        files = valid_files()
        files = {k[len("package/"):]: v for k, v in files.items()}
        target = tmp_path / "parallel-x"
        self._install(target, files)
        ns.verify_tree(target, files)

    def test_extra_file_rejected(self, tmp_path):
        files = {"a.txt": b"x"}
        target = tmp_path / "t"
        self._install(target, dict(files, extra=b"y"))
        with pytest.raises(ValueError, match="tree differs|bytes differ"):
            ns.verify_tree(target, files)

    def test_missing_file_rejected(self, tmp_path):
        files = {"a.txt": b"x", "b.txt": b"y"}
        target = tmp_path / "t"
        self._install(target, {"a.txt": b"x"})
        with pytest.raises(ValueError, match="differs"):
            ns.verify_tree(target, files)

    def test_byte_mismatch_rejected(self, tmp_path):
        files = {"a.txt": b"expected"}
        target = tmp_path / "t"
        self._install(target, {"a.txt": b"tampered"})
        with pytest.raises(ValueError, match="bytes differ"):
            ns.verify_tree(target, files)

    def test_world_writable_member_rejected(self, tmp_path):
        files = {"a.txt": b"x"}
        target = tmp_path / "t"
        self._install(target, files)
        os.chmod(target / "a.txt", 0o666)
        with pytest.raises(ValueError, match="unsafe search plugin"):
            ns.verify_tree(target, files)


class TestSelectProvider:
    def test_missing_answers_defaults_parallel(self, tmp_path):
        assert ns.select_provider(tmp_path / "absent.json") == "parallel-free"

    def test_explicit_provider_wins(self, tmp_path):
        answers = tmp_path / "answers.json"
        answers.write_text(json.dumps({"webSearchProvider": "searxng"}))
        os.chmod(answers, 0o600)
        assert ns.select_provider(answers, "parallel-free") == "parallel-free"

    def test_legacy_searxng_preserved(self, tmp_path):
        answers = tmp_path / "answers.json"
        answers.write_text(json.dumps({"webSearchProvider": "searxng"}))
        os.chmod(answers, 0o600)
        assert ns.select_provider(answers) == "searxng"

    def test_answers_without_key_default_searxng(self, tmp_path):
        # Old installers always wrote SearXNG — absence preserves it.
        answers = tmp_path / "answers.json"
        answers.write_text(json.dumps({}))
        os.chmod(answers, 0o600)
        assert ns.select_provider(answers) == "searxng"

    @pytest.mark.parametrize("provider", ["evil", "", "searxng ", 5])
    def test_invalid_provider_rejected(self, tmp_path, provider):
        answers = tmp_path / "answers.json"
        answers.write_text(json.dumps({"webSearchProvider": provider}))
        os.chmod(answers, 0o600)
        with pytest.raises(ValueError, match="must be searxng"):
            ns.select_provider(answers)

    def test_non_dict_answers_rejected(self, tmp_path):
        answers = tmp_path / "answers.json"
        answers.write_text(json.dumps(["x"]))
        os.chmod(answers, 0o600)
        with pytest.raises(ValueError, match="invalid"):
            ns.select_provider(answers)


class TestPrepareBase:
    @pytest.mark.parametrize("base", [
        "relative", "/", "..", "/a/../b", "/tmp/with\ttab",
    ])
    def test_invalid_base(self, base):
        with pytest.raises(ValueError, match="absolute directory|symbolic"):
            ns.prepare(base)

    def test_symlink_in_base_rejected(self, tmp_path):
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real)
        with pytest.raises(ValueError, match="symbolic links"):
            ns.prepare(link)
