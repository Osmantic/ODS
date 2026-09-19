"""Contract tests for scripts/check-version-consistency.py.

The release gate fails when the ODS version authorities drift across
manifest.json, the FastAPI app, installer constants, the CLI, and the
changelog. These tests run main() against a fixture tree (module-level ROOT
is patched) and pin which drift the gate catches — and which it tolerates.
"""
import importlib.util
import json
from pathlib import Path

import pytest

MODULE = (Path(__file__).resolve().parents[1] / "scripts"
          / "check-version-consistency.py")
SPEC = importlib.util.spec_from_file_location(
    "check_version_consistency", MODULE)
vc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vc)

VERSION = "1.2.3"


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """Minimal fixture tree mirroring every version authority."""
    root = tmp_path / "ods"
    root.mkdir()

    def write(rel, content):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    write("manifest.json", json.dumps({
        "ods_version": VERSION,
        "release": {"version": VERSION, "date": "2026-09-17"},
    }))
    write("extensions/services/dashboard-api/main.py",
          f'app = FastAPI(title="ODS", version="{VERSION}")')
    write("installers/lib/constants.sh", f'VERSION="{VERSION}"')
    write("ods-cli", f'#!/bin/sh\nVERSION="{VERSION}"')
    write("installers/macos/lib/constants.sh", f'ODS_VERSION="{VERSION}"')
    write("installers/windows/lib/constants.ps1",
          f'$script:ODS_VERSION = "{VERSION}"')
    write("installers/phases/06-directories.sh",
          f'ODS_VERSION=${{VERSION:-{VERSION}}}')
    write("CHANGELOG.md",
          "# Changelog\n\n## [Unreleased]\n\n"
          f"## [{VERSION}] - 2026-09-17\n\n### Added\n- x\n\n"
          "## [1.0.0] - 2026-01-01\n")
    write("../ARCHITECTURE.md", f"# Arch\n> Version {VERSION} | rest\n")
    monkeypatch.setattr(vc, "ROOT", root)
    return root, write


class TestPassingTree:
    def test_consistent_tree_passes(self, tree, capsys):
        assert vc.main() == 0
        assert "[PASS] version consistency (1.2.3)" in capsys.readouterr().out

    def test_missing_optional_version_file_ok(self, tree):
        assert vc.optional_version_file() is None
        assert vc.main() == 0

    def test_version_file_raw_and_json(self, tree):
        root, write = tree
        write(".version", "1.2.3")
        assert vc.optional_version_file() == "1.2.3"
        assert vc.main() == 0
        write(".version", json.dumps({"version": "1.2.3"}))
        assert vc.optional_version_file() == "1.2.3"
        assert vc.main() == 0


class TestDriftDetection:
    @pytest.mark.parametrize("rel,content", [
        ("manifest.json", '{"ods_version":"9.9.9"}'),
        ("extensions/services/dashboard-api/main.py",
         'app = FastAPI(version="9.9.9")'),
        ("installers/lib/constants.sh", 'VERSION="9.9.9"'),
        ("ods-cli", 'VERSION="9.9.9"'),
        ("installers/macos/lib/constants.sh", 'ODS_VERSION="9.9.9"'),
        ("installers/windows/lib/constants.ps1",
         '$script:ODS_VERSION = "9.9.9"'),
        ("installers/phases/06-directories.sh",
         'ODS_VERSION=${VERSION:-9.9.9}'),
        ("../ARCHITECTURE.md", "> Version 9.9.9 |"),
        ("CHANGELOG.md", "## [9.9.9] - 2026-09-17\n"),
    ])
    def test_single_source_drift_fails(self, tree, rel, content, capsys):
        _, write = tree
        write(rel, content)
        assert vc.main() == 1
        assert "[FAIL] version consistency" in capsys.readouterr().out

    def test_manifest_drift_fails_all_others(self, tree, capsys):
        # When manifest is the only authority that moved, every check fails.
        _, write = tree
        write("manifest.json", json.dumps({
            "ods_version": "9.9.9",
            "release": {"version": "9.9.9", "date": "2026-09-17"}}))
        # changelog still at 1.2.3 → drift reported against manifest
        assert vc.main() == 1

    def test_release_date_mismatch_fails(self, tree, capsys):
        _, write = tree
        write("manifest.json", json.dumps({
            "ods_version": VERSION,
            "release": {"version": VERSION, "date": "2026-01-01"}}))
        assert vc.main() == 1
        assert "release.date" in capsys.readouterr().out

    def test_release_version_mismatch_fails(self, tree):
        _, write = tree
        write("manifest.json", json.dumps({
            "ods_version": VERSION,
            "release": {"version": "0.0.0", "date": "2026-09-17"}}))
        assert vc.main() == 1


class TestManifestValidation:
    def test_missing_manifest(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(vc, "ROOT", tmp_path / "absent")
        assert vc.main() == 1
        assert "cannot read manifest.json" in capsys.readouterr().out

    def test_empty_version_rejected(self, tree, capsys):
        _, write = tree
        write("manifest.json", json.dumps({
            "ods_version": "",
            "release": {"version": "", "date": ""}}))
        assert vc.main() == 1

    @pytest.mark.parametrize("version", ["1.2", "v1.2.3", "1.2.3-beta", "x"])
    def test_non_semver_rejected(self, tree, capsys, version):
        _, write = tree
        write("manifest.json", json.dumps({
            "ods_version": version,
            "release": {"version": version, "date": "2026-09-17"}}))
        assert vc.main() == 1
        assert "x.y.z" in capsys.readouterr().out


class TestHelpers:
    def test_latest_changelog_skips_unreleased(self, tree):
        assert vc.latest_changelog_release() == ("1.2.3", "2026-09-17")

    def test_latest_changelog_no_release(self, tree):
        _, write = tree
        write("CHANGELOG.md", "# Changelog\n\n## [Unreleased]\n")
        with pytest.raises(ValueError, match="released version"):
            vc.latest_changelog_release()

    def test_first_match_missing_pattern(self, tree):
        _, write = tree
        path = write("installers/lib/constants.sh", 'OTHER="x"')
        with pytest.raises(ValueError, match="could not find version"):
            vc.first_match(path, r'^VERSION="([^"]+)"', "constants")

    def test_version_file_empty_returns_none(self, tree):
        _, write = tree
        write(".version", "   \n")
        assert vc.optional_version_file() is None

    def test_version_file_json_without_version(self, tree):
        _, write = tree
        write(".version", json.dumps({"other": "x"}))
        assert vc.optional_version_file() == '{"other": "x"}'
