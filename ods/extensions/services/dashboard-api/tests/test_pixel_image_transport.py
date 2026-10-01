import base64
from io import BytesIO

from PIL import Image
from pydantic import ValidationError
import pytest

import pixel_image_transport as transport
from pixel_image_store import ImageStore
from pixel_image_transport import ImageReference, ImageResolutionError, resolve_image_parts


def png(color):
    output = BytesIO()
    Image.new("RGB", (4, 4), color).save(output, format="PNG")
    return output.getvalue()


@pytest.fixture
def stored(tmp_path):
    store = ImageStore(tmp_path / "images")
    references = []
    for color in ("red", "blue"):
        receipt = store.put("a" * 64, "chat", png(color), "image/png")
        references.append({key: receipt[key] for key in ("id", "sha256")})
    yield store, references
    store.close()


def test_resolves_actual_images_in_order_without_mutating_references(stored):
    store, references = stored
    before = [value.copy() for value in references]
    parts = resolve_image_parts(store, "a" * 64, "chat", "Compare these", references)
    assert parts[0] == {"type": "text", "text": "Compare these"}
    for part, color in zip(parts[1:], ("red", "blue")):
        prefix, encoded = part["image_url"]["url"].split(",")
        assert prefix == "data:image/png;base64"
        assert base64.b64decode(encoded, validate=True) == png(color)
    assert references == before
    assert resolve_image_parts(store, "a" * 64, "chat", "", references)[0]["type"] == "image_url"


@pytest.mark.parametrize("owner,chat", [("b" * 64, "chat"), ("a" * 64, "other")])
def test_wrong_scope_never_falls_back_to_text(stored, owner, chat):
    store, references = stored
    with pytest.raises(ImageResolutionError, match="unavailable"):
        resolve_image_parts(store, owner, chat, "Please inspect", references)


def test_missing_or_changed_reference_rejects_whole_message(stored):
    store, references = stored
    references[1]["sha256"] = "0" * 64
    with pytest.raises(ImageResolutionError, match="identity"):
        resolve_image_parts(store, "a" * 64, "chat", "Inspect both", references)
    references[1]["id"] = "img-" + "0" * 32
    with pytest.raises(ImageResolutionError, match="unavailable"):
        resolve_image_parts(store, "a" * 64, "chat", "Inspect both", references)


def test_combined_byte_limit_and_duplicate_rejection(stored, monkeypatch):
    store, references = stored
    monkeypatch.setattr(transport, "MAX_TURN_IMAGE_BYTES", len(png("red")))
    with pytest.raises(ImageResolutionError, match="combined"):
        resolve_image_parts(store, "a" * 64, "chat", "", references)
    with pytest.raises(ImageResolutionError, match="more than once"):
        resolve_image_parts(store, "a" * 64, "chat", "", [references[0]] * 2)
    with pytest.raises(ImageResolutionError, match="four"):
        resolve_image_parts(store, "a" * 64, "chat", "", references * 3)


@pytest.mark.parametrize("extra", ["url", "path", "data", "owner", "chat"])
def test_reference_cannot_inject_storage_scope_or_content(extra):
    with pytest.raises(ValidationError):
        ImageReference.model_validate({"id": "img-" + "a" * 32, "sha256": "b" * 64, extra: "untrusted"})


@pytest.mark.parametrize("field", ["id", "sha256"])
@pytest.mark.parametrize("suffix", ["\n", "\r\n", " "])
def test_reference_identity_has_exact_length_without_trailing_delimiters(field, suffix):
    reference = {"id": "img-" + "a" * 32, "sha256": "b" * 64}
    reference[field] += suffix
    with pytest.raises(ValidationError):
        ImageReference.model_validate(reference)
