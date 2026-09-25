"""An unsuccessful second update must retain the first update's rollback point."""

import errno
from pathlib import Path
from unittest.mock import Mock

import pytest

from test_extensions import TestUpdateExtension as _UpdateFixtures


@pytest.fixture
def two_versions(test_client, monkeypatch, tmp_path):
    ext, library, user, installed = _UpdateFixtures()._prepare(monkeypatch, tmp_path)
    original = (installed / "manifest.yaml").read_bytes()
    source = library / "my-ext" / "manifest.yaml"
    source.write_text("release: second\n")
    result = test_client.post("/api/extensions/my-ext/update", headers=test_client.auth_headers)
    assert result.status_code == 200
    source.write_text("release: third\n")
    agent = Mock(return_value=True)
    monkeypatch.setattr(ext, "_call_agent", agent)
    sync = Mock(return_value=True)
    monkeypatch.setattr(ext, "_sync_extension_config", sync)
    return ext, user, installed, original, agent, sync


@pytest.mark.parametrize("failure_point", ["live_to_backup", "staged_to_live"])
def test_failed_update_keeps_both_live_definition_and_usable_rollback(
    test_client, monkeypatch, two_versions, failure_point,
):
    ext, user, installed, original, agent, sync = two_versions
    backup = user / ".backups" / "my-ext"
    prior_receipt = (backup / ".ods-library-receipt.json").read_bytes()
    replace = ext.os.replace

    def fail_update(source, destination):
        source, destination = Path(source), Path(destination)
        if failure_point == "live_to_backup" and source == installed and destination == backup:
            raise OSError(errno.EBUSY, "live definition is busy")
        if failure_point == "staged_to_live" and destination == installed and source != backup:
            raise OSError(errno.ENOSPC, "staged rename failed")
        return replace(source, destination)

    with monkeypatch.context() as mutation:
        mutation.setattr(ext.os, "replace", fail_update)
        with pytest.raises(OSError):
            test_client.post("/api/extensions/my-ext/update", headers=test_client.auth_headers)

    assert (installed / "manifest.yaml").read_text() == "release: second\n"
    assert (backup / "manifest.yaml").read_bytes() == original
    assert (backup / ".ods-library-receipt.json").read_bytes() == prior_receipt
    assert not list((user / ".tmp").glob(".my-ext-retired-backup-*"))
    agent.assert_not_called()
    sync.assert_not_called()

    rollback = test_client.post("/api/extensions/my-ext/rollback", headers=test_client.auth_headers)
    assert rollback.status_code == 200
    assert (installed / "manifest.yaml").read_bytes() == original
    agent.assert_called_once_with("start", "my-ext")


def test_failed_backup_reseat_keeps_the_only_prior_copy_and_reports_its_path(
    test_client, monkeypatch, two_versions, caplog,
):
    ext, user, installed, original, agent, sync = two_versions
    backup = user / ".backups" / "my-ext"
    replace = ext.os.replace

    def fail_update_and_recovery(source, destination):
        source, destination = Path(source), Path(destination)
        if destination == backup:
            raise OSError(errno.EACCES, "backup rename denied")
        return replace(source, destination)

    monkeypatch.setattr(ext.os, "replace", fail_update_and_recovery)
    with pytest.raises(OSError):
        test_client.post("/api/extensions/my-ext/update", headers=test_client.auth_headers)

    parked = list((user / ".tmp").glob(".my-ext-retired-backup-*/my-ext"))
    assert len(parked) == 1
    assert (parked[0] / "manifest.yaml").read_bytes() == original
    assert str(parked[0]) in caplog.text
    assert "prior backup could not be re-seated" in caplog.text
    assert (installed / "manifest.yaml").read_text() == "release: second\n"
    agent.assert_not_called()
    sync.assert_not_called()
