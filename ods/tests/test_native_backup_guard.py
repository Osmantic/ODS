"""Exercise public backup/restore calls against private synthetic native state."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def fixture(tmp_path):
    install = tmp_path / "ODS with spaces"
    (install / "data/open-webui").mkdir(parents=True)
    (install / "data/open-webui/chats.txt").write_text("ordinary chats")
    (install / "config").mkdir()
    (install / "config/settings.json").write_text("{}")
    (install / ".env").write_text("VALUE=original\n")
    (install / "docker-compose.yml").write_text("services: {}\n")
    (install / "lib").mkdir()
    for name in ("rsync.sh", "backup-paths.sh"):
        shutil.copyfile(ROOT / "lib" / name, install / "lib" / name)
    home = tmp_path / "owner"
    home.mkdir()
    commands = tmp_path / "commands"
    commands.mkdir()
    # Patch only process identity/platform in the preflight subprocess; use
    # the real shared validator, file guards, backup and restore entry points.
    launcher = commands / "python3"
    launcher.write_text("#!" + sys.executable + "\n" +
        "import os,pwd,platform,runpy,sys,types\n"
        "if len(sys.argv)>1 and sys.argv[1].endswith('/backup-native-preflight.py'):\n"
        "    pwd.getpwuid=lambda uid:types.SimpleNamespace(pw_name='fixture-owner',pw_dir=os.environ['FIXTURE_OWNER_HOME'],pw_uid=uid)\n"
        "    platform.system=lambda:os.environ.get('FIXTURE_SYSTEM','Linux')\n"
        "    if os.environ.get('FIXTURE_ROOT')=='1':os.geteuid=lambda:0\n"
        "    sys.argv=sys.argv[1:];runpy.run_path(sys.argv[0],run_name='__main__')\n"
        "else:os.execv(" + repr(sys.executable) + ",[" + repr(sys.executable) + ",*sys.argv[1:]])\n")
    launcher.chmod(0o755)
    docker = commands / "docker"
    docker.write_text("#!/bin/sh\nprintf called >> \"$FIXTURE_DOCKER_CALLS\"\nexit 99\n")
    docker.chmod(0o755)
    environment = {**os.environ, "ODS_DIR": str(install), "FIXTURE_OWNER_HOME": str(home),
                   "PATH": str(commands) + os.pathsep + os.environ["PATH"],
                   "FIXTURE_DOCKER_CALLS": str(tmp_path / "docker-calls")}
    return install, home, environment


def call(fixture, program, *arguments):
    return subprocess.run(["bash", str(ROOT / program), *arguments],
                          env=fixture[2], text=True, capture_output=True, timeout=30)


def marker(fixture, *, foreign=False, state="ready"):
    install, home, _ = fixture
    path = home / ".config/ods/pixel-managed.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 2, "manager": "ods", "state": state,
                               "install_dir": str(install.parent / "foreign" if foreign else install)}))
    path.chmod(0o600)
    return path


def tree(root):
    return sorted((str(p.relative_to(root)), "link:" + os.readlink(p) if p.is_symlink()
                   else hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "directory")
                  for p in root.rglob("*"))


@pytest.mark.parametrize("kind", ["full", "user-data"])
def test_native_backup_refuses_before_any_backup_files(fixture, kind):
    marker(fixture)
    before = tree(fixture[0])
    result = call(fixture, "ods-backup.sh", "--type", kind)
    assert result.returncode != 0
    assert "not supported" in result.stderr
    assert tree(fixture[0]) == before
    assert not (fixture[0] / ".backups").exists()


@pytest.mark.parametrize("fault", ["malformed", "symlink", "permissions", "incomplete", "root"])
def test_unsafe_current_owner_state_cannot_be_silently_skipped(fixture, fault):
    path = marker(fixture, state="activating" if fault == "incomplete" else "ready")
    if fault == "malformed":
        path.write_text("not json")
    elif fault == "symlink":
        original = path.with_suffix(".original")
        path.rename(original)
        path.symlink_to(original)
    elif fault == "permissions":
        path.chmod(0o644)
    elif fault == "root":
        fixture[2]["FIXTURE_ROOT"] = "1"
    result = call(fixture, "ods-backup.sh", "--type", "full")
    assert result.returncode != 0
    assert not (fixture[0] / ".backups").exists()


@pytest.mark.parametrize("selected", ["PIXEL_AGENT_MODE=pixel", "PIXEL_NATIVE_UID=501"])
def test_selected_pixel_with_missing_receipt_refuses(fixture, selected):
    (fixture[0] / ".env").write_text(selected + "\n")
    assert call(fixture, "ods-backup.sh", "--type", "full").returncode != 0
    assert not (fixture[0] / ".backups").exists()


@pytest.mark.parametrize("state", ["empty", "home", "symlink", "malformed"])
def test_mac_incomplete_native_state_refuses(fixture, state):
    fixture[2]["FIXTURE_SYSTEM"] = "Darwin"
    native = fixture[0] / "data/pixel-native"
    if state == "symlink":
        native.symlink_to(native.parent / "missing")
    else:
        native.mkdir()
        if state == "home":
            (native / "home/.openclaw/agents/pixel/sessions").mkdir(parents=True)
            (native / "home/.openclaw/agents/pixel/sessions/retained.jsonl").write_text("private fixture")
        elif state == "malformed":
            (native / "preparation").mkdir()
            (native / "preparation/activation.json").write_text("not json")
    before = tree(fixture[0])
    result = call(fixture, "ods-backup.sh", "--type", "full")
    assert result.returncode != 0
    assert tree(fixture[0]) == before


@pytest.mark.parametrize("kind", [[], ["--config-only"], ["--data-only"]])
def test_restore_native_refusal_precedes_extract_stop_and_replace(fixture, kind):
    marker(fixture)
    before = tree(fixture[0])
    # There is deliberately no archive. Native refusal must precede extraction
    # and the requested docker stop, as well as all target file publication.
    result = call(fixture, "ods-restore.sh", "--force", "--stop-containers", *kind, "20260101-000000")
    assert result.returncode != 0
    assert "Native Pixel backup/restore is not supported" in result.stderr
    assert "Backup not found" not in result.stdout + result.stderr
    assert not Path(fixture[2]["FIXTURE_DOCKER_CALLS"]).exists()
    assert tree(fixture[0]) == before


def config_archive(fixture):
    result = call(fixture, "ods-backup.sh", "--type", "config")
    assert result.returncode == 0, result.stdout + result.stderr
    archives = list((fixture[0] / ".backups").iterdir())
    assert len(archives) == 1
    return archives[0], result


def test_native_config_archive_is_explicitly_incomplete_and_inspectable(fixture):
    marker(fixture)
    archive, result = config_archive(fixture)
    manifest = json.loads((archive / "manifest.json").read_text())
    assert manifest["native_pixel"] == {"included": False, "reason": "unsupported-native-state"}
    assert "not a recovery backup" in result.stderr
    assert "ods-restore.sh " not in result.stdout
    assert call(fixture, "ods-backup.sh", "verify", archive.name).returncode == 0
    assert call(fixture, "ods-backup.sh", "--list").returncode == 0
    before = tree(fixture[0])
    preview = call(fixture, "ods-restore.sh", "--dry-run", archive.name)
    assert preview.returncode == 0, preview.stdout + preview.stderr
    assert "Native Pixel state is excluded" in preview.stderr
    assert tree(fixture[0]) == before


@pytest.mark.parametrize("legacy_receipt", [False, True])
def test_native_config_archive_cannot_be_applied_to_ordinary_target(fixture, legacy_receipt):
    marker_path = marker(fixture)
    (fixture[0] / ".env").write_text("PIXEL_AGENT_MODE=pixel\n")
    archive, _ = config_archive(fixture)
    marker_path.unlink()
    (fixture[0] / ".env").write_text("VALUE=ordinary-target\n")
    if legacy_receipt:
        manifest = json.loads((archive / "manifest.json").read_text())
        del manifest["native_pixel"]
        (archive / "manifest.json").write_text(json.dumps(manifest))
    before = tree(fixture[0])
    result = call(fixture, "ods-restore.sh", "--force", "--skip-verify", "--stop-containers", archive.name)
    assert result.returncode != 0
    assert "Native Pixel backup/restore is not supported" in result.stderr
    assert not Path(fixture[2]["FIXTURE_DOCKER_CALLS"]).exists()
    assert tree(fixture[0]) == before


@pytest.mark.parametrize("foreign", [False, True])
def test_ordinary_backup_restore_roundtrip_and_empty_disabled_directory(fixture, foreign):
    if foreign:
        marker(fixture, foreign=True)
    (fixture[0] / "data/pixel").mkdir()
    (fixture[0] / ".env").write_text("PIXEL_AGENT_MODE=hermes\n")
    result = call(fixture, "ods-backup.sh", "--type", "full")
    assert result.returncode == 0, result.stdout + result.stderr
    archive = next((fixture[0] / ".backups").iterdir())
    assert "native_pixel" not in json.loads((archive / "manifest.json").read_text())
    (fixture[0] / "data/open-webui/chats.txt").write_text("changed")
    restored = call(fixture, "ods-restore.sh", "--force", archive.name)
    assert restored.returncode == 0, restored.stdout + restored.stderr
    assert (fixture[0] / "data/open-webui/chats.txt").read_text() == "ordinary chats"
