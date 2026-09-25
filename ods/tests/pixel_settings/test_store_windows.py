"""Real native Windows persistence; never a Full Access/runtime Apply claim."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin"))
from pixel_provider.store import StoreError
from pixel_provider.windows_bootstrap import create_private_root
from pixel_provider.windows_custody import read_private
from pixel_provider.windows_store import WindowsProviderStore
from pixel_settings.store import WindowsSettingsStore, default_document

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Requires actual native Windows APIs")


def test_native_persistence_and_new_process_readback_preserve_provider(tmp_path):
    directory = tmp_path / "settings"
    create_private_root(directory)
    provider = WindowsProviderStore(directory)
    provider.save(provider.load(), expected_revision=0)
    before = read_private(directory / "provider-config.json")
    store = WindowsSettingsStore(directory)
    assert store.load() == default_document()
    store.save_changes({"contextTokens": 65536, "verbosity": "on"}, expected_revision=0)
    saved = store.save_changes({"contextTokens": None}, expected_revision=1)
    assert saved == {"schemaVersion": 1, "revision": 2,
                     "preferences": {"contextTokens": None, "verbosity": "on"}}
    assert json.loads(read_private(directory / "pixel-settings.json")) == saved
    child = subprocess.run([sys.executable, "-c",
        "import json,sys;sys.path.insert(0,sys.argv[1]);"
        "from pixel_settings.store import WindowsSettingsStore;"
        "print(json.dumps(WindowsSettingsStore(sys.argv[2]).load()))",
        str(Path(__file__).resolve().parents[2] / "bin"), str(directory)],
        capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
    assert child.returncode == 0, child.stderr
    assert json.loads(child.stdout) == saved
    assert read_private(directory / "provider-config.json") == before


def test_native_stale_update_and_invalid_request_do_not_change_bytes(tmp_path):
    directory = tmp_path / "settings"
    create_private_root(directory)
    store = WindowsSettingsStore(directory)
    store.save_changes({"verbosity": "full"}, expected_revision=0)
    before = read_private(directory / "pixel-settings.json")
    with pytest.raises(StoreError, match="stale-revision"):
        store.save_changes({"verbosity": "off"}, expected_revision=0)
    with pytest.raises(StoreError, match="invalid-request"):
        store.save_changes({"verbosity": "off"}, expected_revision=True)
    assert read_private(directory / "pixel-settings.json") == before


def test_native_unqualified_root_is_not_adopted_or_repaired(tmp_path):
    missing = tmp_path / "missing"
    with pytest.raises(StoreError):
        WindowsSettingsStore(missing).load()
    assert not missing.exists()
    with pytest.raises(StoreError):
        WindowsSettingsStore(tmp_path).save_changes({}, expected_revision=0)
    assert not (tmp_path / "pixel-settings.json").exists()
