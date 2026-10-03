import json
from pathlib import Path

import pytest


from model_stores import scan_model_files, resolve_model_file, active_store
from test_wsl_managed_model_activation import managed  # noqa: F401


def _write_registry(data: Path, stores):
    (data / "model-stores.json").write_text(json.dumps({"schemaVersion": 1, "stores": stores}))


def test_preferred_store_id_resolves_duplicate_same_basename(tmp_path):
    data = tmp_path / "data"
    (data / "models").mkdir(parents=True)
    external = tmp_path / "ssd-models"
    external.mkdir()
    (data / "models" / "dup.gguf").write_bytes(b"default-copy")
    (external / "dup.gguf").write_bytes(b"external-copy")
    _write_registry(data, [
        {"id": "ssd", "hostPath": str(external), "containerPath": "/model-stores/ssd"},
    ])
    # Generic scan remains ambiguous.
    assert "dup.gguf" not in scan_model_files(data)
    # Explicit preferred store resolves the correct listing.
    resolved = scan_model_files(data, preferred_store_id="ssd")
    assert resolved["dup.gguf"] == external / "dup.gguf"


def test_preferred_store_id_absent_does_not_relax_ambiguity(tmp_path):
    data = tmp_path / "data"
    (data / "models").mkdir(parents=True)
    external = tmp_path / "ssd-models"
    external.mkdir()
    (data / "models" / "dup.gguf").write_bytes(b"default-copy")
    (external / "dup.gguf").write_bytes(b"external-copy")
    _write_registry(data, [
        {"id": "ssd", "hostPath": str(external), "containerPath": "/model-stores/ssd"},
    ])
    # Missing preferred store id must not resolve the duplicate.
    assert "dup.gguf" not in scan_model_files(data, preferred_store_id="missing")
    # Unrelated preferred store id must not resolve the duplicate.
    other = tmp_path / "other-models"
    other.mkdir()
    _write_registry(data, [
        {"id": "ssd", "hostPath": str(external), "containerPath": "/model-stores/ssd"},
        {"id": "other", "hostPath": str(other), "containerPath": "/model-stores/other"},
    ])
    assert "dup.gguf" not in scan_model_files(data, preferred_store_id="other")


def test_preferred_store_id_does_not_resolve_multiple_same_casefold_files(tmp_path):
    data = tmp_path / "data"
    (data / "models").mkdir(parents=True)
    external = tmp_path / "ssd-models"
    external.mkdir()
    (external / "dup.gguf").write_bytes(b"lower")
    (external / "DUP.GGUF").write_bytes(b"upper")
    if (external / "dup.gguf").samefile(external / "DUP.GGUF"):
        pytest.skip("filesystem does not support distinct casefold-colliding names")
    _write_registry(data, [
        {"id": "ssd", "hostPath": str(external), "containerPath": "/model-stores/ssd"},
    ])
    # Two same-casefold files within the preferred directory remain ambiguous.
    assert "dup.gguf" not in scan_model_files(data, preferred_store_id="ssd")
    assert "DUP.GGUF" not in scan_model_files(data, preferred_store_id="ssd")


def test_preferred_store_id_does_not_weaken_generic_resolve(tmp_path):
    data = tmp_path / "data"
    (data / "models").mkdir(parents=True)
    external = tmp_path / "ssd-models"
    external.mkdir()
    (data / "models" / "dup.gguf").write_bytes(b"default-copy")
    (external / "dup.gguf").write_bytes(b"external-copy")
    _write_registry(data, [
        {"id": "ssd", "hostPath": str(external), "containerPath": "/model-stores/ssd"},
    ])
    # Generic resolve_model_file must still refuse the ambiguous basename.
    assert resolve_model_file(data, "dup.gguf") is None
    # active_store without id still returns default.
    assert active_store(data)["path"] == data / "models"


def test_management_response_preserves_verified_storeid(managed, monkeypatch):  # noqa: F811
    handler = managed["status"]()
    assert handler["managed"] is True
    # The managed fixture binds the runtime to windows-lemonade; the verified
    # store id must be surfaced by the management snapshot.
    import test_wsl_managed_model_activation as mod
    monkeypatch.setattr(mod.host, "_model_management_cache", None)
    code, value = mod.host._model_management_snapshot()
    assert code == 200
    assert value.get("modelStoreId") == "windows-lemonade"
