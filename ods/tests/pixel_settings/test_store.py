"""Settings persistence evidence, not runtime Apply or user acceptance."""
import copy
import json
import os
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin"))
from pixel_provider.store import ProviderStore, StoreError
from pixel_settings.contract import SettingsError
from pixel_settings.store import (MAX_REVISION, SettingsStore, WindowsSettingsStore,
                                  create_settings_store, default_document, normalize_document)


def test_default_and_normalization_are_independent_and_preserve_reset():
    doc = {"schemaVersion": 1, "revision": MAX_REVISION, "preferences": {"thinking": None}}
    normalized = normalize_document(doc)
    assert normalized == doc and normalized is not doc
    normalized["preferences"]["thinking"] = "off"
    assert doc["preferences"] == {"thinking": None}
    first = default_document()
    first["preferences"]["verbosity"] = "full"
    assert default_document() == {"schemaVersion": 1, "revision": 0, "preferences": {}}


@pytest.mark.parametrize("document", [
    None, [], {}, {"schemaVersion": 1, "revision": 0},
    dict(default_document(), schemaVersion=True), dict(default_document(), schemaVersion=1.0),
    dict(default_document(), revision=True), dict(default_document(), revision=-1),
    dict(default_document(), revision=1.0), dict(default_document(), revision=MAX_REVISION + 1),
    dict(default_document(), unknown=1), dict(default_document(), preferences=None),
    dict(default_document(), preferences={"apiKey": "secret"}),
    dict(default_document(), preferences={"contextTokens": True}),
])
def test_malformed_documents_rejected(document):
    with pytest.raises((StoreError, SettingsError)):
        normalize_document(document)


@pytest.mark.parametrize("revision", [True, -1, 1.0, "1", MAX_REVISION, MAX_REVISION + 1])
def test_invalid_revision_fails_before_io(revision):
    store = Mock()
    with pytest.raises(StoreError, match="invalid-request"):
        SettingsStore.save_changes(store, {}, expected_revision=revision)
    store.load.assert_not_called()
    store.save.assert_not_called()


def test_invalid_changes_fail_before_io():
    store = Mock()
    with pytest.raises(SettingsError):
        SettingsStore.save_changes(store, {"unowned": True}, expected_revision=0)
    store.load.assert_not_called()
    store.save.assert_not_called()


def test_sparse_merge_preserves_reset_and_calls_cas_without_mutating_inputs():
    current = dict(default_document(), revision=3, preferences={"thinking": "low", "verbosity": "on"})
    changes = {"thinking": None}
    before = copy.deepcopy((current, changes))
    store = Mock()
    store.load.return_value = current
    store.save.return_value = "saved-result"
    assert SettingsStore.save_changes(store, changes, expected_revision=3) == "saved-result"
    store.save.assert_called_once_with(dict(current, preferences={"thinking": None, "verbosity": "on"}),
                                       expected_revision=3)
    assert (current, changes) == before


def test_stale_and_second_cas_failures_are_not_retried():
    store = Mock()
    store.load.return_value = dict(default_document(), revision=2)
    with pytest.raises(StoreError, match="stale-revision"):
        SettingsStore.save_changes(store, {}, expected_revision=1)
    store.save.assert_not_called()
    store.save.side_effect = StoreError("stale-revision")
    with pytest.raises(StoreError, match="stale-revision"):
        SettingsStore.save_changes(store, {}, expected_revision=2)
    assert store.save.call_count == 1


@pytest.mark.parametrize("platform,kind", [("linux", SettingsStore), ("darwin", SettingsStore),
                                          ("win32", WindowsSettingsStore)])
def test_constructor_wiring_without_provisioning(tmp_path, platform, kind):
    missing = tmp_path / "not-provisioned"
    store = create_settings_store(missing, platform=platform)
    assert isinstance(store, kind)
    assert store.config_name == "pixel-settings.json"
    assert store.validator is normalize_document and store.default_factory is default_document
    assert not missing.exists()
    if platform == "win32":
        assert store.directory == os.fspath(missing)  # raw spelling, native custody owns validation
    with pytest.raises(StoreError, match="unsupported-platform"):
        create_settings_store(missing, platform="unknown")


@pytest.mark.skipif(os.name != "posix", reason="Requires actual POSIX file custody")
def test_posix_reopen_sparse_reset_stale_and_provider_filename_separation(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    provider = ProviderStore(directory)
    provider_before = provider.save(provider.load(), expected_revision=0)
    provider_bytes = (directory / "provider-config.json").read_bytes()
    store = SettingsStore(directory)
    assert store.load() == default_document()
    saved = store.save_changes({"thinking": "low", "verbosity": "on"}, expected_revision=0)
    assert saved["revision"] == 1
    reset = SettingsStore(directory).save_changes({"thinking": None}, expected_revision=1)
    assert reset["preferences"] == {"thinking": None, "verbosity": "on"}
    assert SettingsStore(directory).load() == reset
    assert SettingsStore(directory).read_snapshot() == reset
    assert json.loads((directory / "pixel-settings.json").read_bytes()) == reset
    assert (directory / "pixel-settings.json").stat().st_mode & 0o777 == 0o600
    with pytest.raises(StoreError, match="stale-revision"):
        store.save_changes({"verbosity": "full"}, expected_revision=1)
    assert provider.load() == provider_before
    assert (directory / "provider-config.json").read_bytes() == provider_bytes


@pytest.mark.skipif(os.name != "posix", reason="Requires actual POSIX file custody")
def test_store_does_not_provision_or_accept_unsafe_directory(tmp_path):
    missing = tmp_path / "missing"
    with pytest.raises(StoreError):
        SettingsStore(missing).load()
    assert not missing.exists()
    unsafe = tmp_path / "unsafe"
    unsafe.mkdir(mode=0o755)
    unsafe.chmod(0o755)
    with pytest.raises(StoreError, match="unsafe-directory"):
        SettingsStore(unsafe).save_changes({}, expected_revision=0)
    assert not (unsafe / "pixel-settings.json").exists()
