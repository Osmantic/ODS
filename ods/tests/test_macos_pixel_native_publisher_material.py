"""The publisher returns the same freshly qualified material it already read."""
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
from pixel_connections import native_executor as executor  # noqa: E402


def test_publisher_returns_its_single_verified_material(tmp_path, monkeypatch):
    helper = tmp_path / 'installers/macos/lib/pixel-native-source-broker.py'
    helper.parent.mkdir(parents=True)
    helper.write_text('calls = 0\ndef _material(selection):\n'
        ' global calls\n calls += 1\n return (selection, calls)\n')
    selected = object()
    monkeypatch.setattr(executor, '_selected', lambda value: {'sourceRoot': str(tmp_path)})
    monkeypatch.setattr(executor, 'protected_bytes', lambda path, **kwargs: path.read_bytes())
    publisher, material = executor._publisher(selected, with_material=True)
    assert material == (selected, 1) and publisher.calls == 1
    normal = executor._publisher(selected)
    assert normal.calls == 1 and normal is not publisher


def test_publisher_never_returns_material_when_the_proof_fails(tmp_path, monkeypatch):
    helper = tmp_path / 'installers/macos/lib/pixel-native-source-broker.py'
    helper.parent.mkdir(parents=True)
    helper.write_text('def _material(selection):\n raise ValueError("invented custody failure")\n')
    monkeypatch.setattr(executor, '_selected', lambda value: {'sourceRoot': str(tmp_path)})
    monkeypatch.setattr(executor, 'protected_bytes', lambda path, **kwargs: path.read_bytes())
    with pytest.raises(ValueError, match='invented custody failure'):
        executor._publisher(object(), with_material=True)


def test_current_publisher_refuses_legacy_environment_before_loading_code(tmp_path, monkeypatch):
    legacy = tmp_path / 'legacy'
    legacy.write_text('preserve')
    selected = SimpleNamespace(deployment=SimpleNamespace(document={'sourceRoot': str(tmp_path)}))
    monkeypatch.setattr(executor.c, 'native_current_source_selection', lambda _bridge: selected)
    monkeypatch.setattr(executor.c, 'NATIVE_LEGACY_ENV', legacy)
    monkeypatch.setattr(executor, 'protected_bytes', lambda *_args, **_kwargs: pytest.fail('must not load code'))
    with pytest.raises(executor.c.CustodyError, match='legacy-env-preserved'):
        executor._publisher_current(object())
    assert legacy.read_text() == 'preserve'
