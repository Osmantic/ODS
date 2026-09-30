from io import BytesIO
import os

import pytest
from PIL import Image

import pixel_image_store as store_module
from pixel_image_store import ImageStore, ImageStoreCapacity


def png(color="red"):
    output = BytesIO()
    Image.new("RGB", (4, 4), color).save(output, format="PNG")
    return output.getvalue()


def test_scope_persistence_and_conversation_deletion(tmp_path):
    directory = tmp_path / "private"
    store = ImageStore(directory)
    receipt = store.put("a" * 64, "chat-1", png(), "image/png")
    assert store.put("a" * 64, "chat-1", png(), "image/png") == receipt
    other = store.put("a" * 64, "chat-2", png(), "image/png")
    assert other["id"] != receipt["id"]
    store.close()
    store = ImageStore(directory)
    assert store.get("a" * 64, "chat-1", receipt["id"])["data"] == png()
    assert store.get("b" * 64, "chat-1", receipt["id"]) is None
    assert store.get("a" * 64, "chat-2", receipt["id"]) is None
    store.delete_conversation("b" * 64, "chat-1")
    assert store.get("a" * 64, "chat-1", receipt["id"])
    store.delete_conversation("a" * 64, "chat-1")
    assert store.get("a" * 64, "chat-1", receipt["id"]) is None
    assert store.get("a" * 64, "chat-2", other["id"])
    store.close()


def test_capacity_preserves_existing_and_dedup(tmp_path, monkeypatch):
    store = ImageStore(tmp_path / "private")
    monkeypatch.setattr(store_module, "MAX_STORE_IMAGES", 1)
    receipt = store.put("a" * 64, "chat", png(), "image/png")
    assert store.put("a" * 64, "chat", png(), "image/png") == receipt
    with pytest.raises(ImageStoreCapacity):
        store.put("a" * 64, "chat", png("blue"), "image/png")
    assert store.get("a" * 64, "chat", receipt["id"])["data"] == png()
    store.close()


def test_corruption_is_not_silently_forwarded(tmp_path):
    store = ImageStore(tmp_path / "private")
    receipt = store.put("a" * 64, "chat", png(), "image/png")
    with store.db:
        store.db.execute("UPDATE images SET data=?", (b"corrupt",))
    with pytest.raises(ValueError, match="integrity"):
        store.get("a" * 64, "chat", receipt["id"])
    with pytest.raises(ValueError, match="integrity"):
        store.put("a" * 64, "chat", png(), "image/png")
    store.close()


def test_discard_reclaims_draft_quota_but_preserves_retained_history(tmp_path, monkeypatch):
    store = ImageStore(tmp_path / "private")
    owner = "a" * 64
    monkeypatch.setattr(store_module, "MAX_STORE_IMAGES", 1)
    receipt = store.put(owner, "chat", png(), "image/png")
    assert store.discard_draft("b" * 64, "chat", receipt["id"])["discarded"]
    assert store.get(owner, "chat", receipt["id"])
    assert store.discard_draft(owner, "chat", receipt["id"])["discarded"]
    assert store.get(owner, "chat", receipt["id"]) is None
    assert store.discard_draft(owner, "chat", receipt["id"])["discarded"]
    replacement = store.put(owner, "chat", png("blue"), "image/png")
    store.retain(owner, "chat", [replacement])
    assert store.discard_draft(owner, "chat", replacement["id"]) == {"retained": True, "discarded": False}
    assert store.get(owner, "chat", replacement["id"])["data"] == png("blue")
    store.close()


def test_failed_retention_rolls_back_all_references(tmp_path):
    store = ImageStore(tmp_path / "private")
    receipt = store.put("a" * 64, "chat", png(), "image/png")
    with pytest.raises(ValueError, match="disappeared"):
        store.retain("a" * 64, "chat", [receipt, {**receipt, "id": "img-" + "0" * 32}])
    assert store.discard_draft("a" * 64, "chat", receipt["id"])["discarded"]
    store.close()


def test_schema_upgrade_does_not_treat_existing_images_as_disposable(tmp_path):
    directory = tmp_path / "private"
    store = ImageStore(directory)
    receipt = store.put("a" * 64, "chat", png(), "image/png")
    store.db.execute("ALTER TABLE images DROP COLUMN retained")
    store.db.commit()
    store.close()
    store = ImageStore(directory)
    assert store.discard_draft("a" * 64, "chat", receipt["id"])["retained"]
    assert store.get("a" * 64, "chat", receipt["id"])["data"] == png()
    store.close()


def test_byte_quota_and_transaction_recovery(tmp_path, monkeypatch):
    store = ImageStore(tmp_path / "private")
    monkeypatch.setattr(store_module, "MAX_STORE_BYTES", len(png()) - 1)
    with pytest.raises(ImageStoreCapacity):
        store.put("a" * 64, "chat", png(), "image/png")
    assert store.db.execute("SELECT COUNT(*) FROM images").fetchone()[0] == 0
    monkeypatch.setattr(store_module, "MAX_STORE_BYTES", len(png()))
    receipt = store.put("a" * 64, "chat", png(), "image/png")
    assert store.get("a" * 64, "chat", receipt["id"])
    store.close()


@pytest.mark.parametrize("identity", ["../../outside", "img-" + "a" * 33, "https://example.com/image", ""])
def test_invalid_identifiers_never_become_paths(tmp_path, identity):
    store = ImageStore(tmp_path / "private")
    with pytest.raises(ValueError, match="identity"):
        store.get("a" * 64, "chat", identity)
    store.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlinks")
def test_symlinked_database_rejected(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    outside = tmp_path / "outside"
    outside.write_bytes(b"untouched")
    (directory / "images.sqlite3").symlink_to(outside)
    with pytest.raises(ValueError, match="regular"):
        ImageStore(directory)
    assert outside.read_bytes() == b"untouched"


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory mode")
def test_nonprivate_directory_rejected(tmp_path):
    directory = tmp_path / "public"
    directory.mkdir(mode=0o755)
    with pytest.raises(ValueError, match="private"):
        ImageStore(directory)
