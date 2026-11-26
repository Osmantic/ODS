"""register-model-store.py refuses Windows removable drives (#8518).

Docker Desktop does not mount removable drives into its VM, so a store on a
USB pen drive registered fine but the Dashboard API saw it empty.
"""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('register_store', ROOT/'scripts/register-model-store.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def layout(tmp_path):
    install = tmp_path/'installation'
    (install/'data').mkdir(parents=True)
    models = tmp_path/'models'
    models.mkdir()
    (models/'model.gguf').write_bytes(b'GGUF')
    return install, models


def test_removable_drive_is_refused_without_writing_config(layout, monkeypatch):
    install, models = layout
    monkeypatch.setattr(module, 'windows_drive_type', lambda directory: module.DRIVE_REMOVABLE)
    with pytest.raises(ValueError, match='removable drive; Docker Desktop cannot read it'):
        module.register(install, 'usb-models', models)
    assert not (install/'data/model-stores.json').exists()
    assert not (install/'.model-stores.compose.json').exists()


@pytest.mark.parametrize('drive_type', [None, 3, 4])  # not Windows, fixed, network
def test_other_drive_types_still_register(layout, monkeypatch, drive_type):
    install, models = layout
    monkeypatch.setattr(module, 'windows_drive_type', lambda directory: drive_type)
    result = module.register(install, 'ssd', models)
    assert result['registered'] == 'ssd'
    registry = json.loads((install/'data/model-stores.json').read_text())
    assert [store['id'] for store in registry['stores']] == ['ssd']


def test_drive_type_is_none_off_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(module.os, 'name', 'posix')
    assert module.windows_drive_type(tmp_path) is None
