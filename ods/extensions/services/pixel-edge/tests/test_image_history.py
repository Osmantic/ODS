"""Image identity survives Edge validation; this is not delivery proof."""
from copy import deepcopy
import base64
import hashlib

import pytest

from chat_context import valid_history_snapshot


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a6ioAAAAASUVORK5CYII=")
REF = {"id": "img-" + "a" * 32, "sha256": hashlib.sha256(PNG).hexdigest()}


def request():
    latest = {"role": "user", "content": "", "images": [dict(REF)]}
    current = {**deepcopy(latest), "content": [{"type": "image_url", "image_url": {
        "url": "data:image/png;base64," + base64.b64encode(PNG).decode("ascii")}}]}
    return {"request_id": "turn", "messages": [current],
            "history_snapshot": {"schemaVersion": 2, "messages": [latest]}}


def test_image_only_turn_and_later_text_turn_preserve_history():
    data = request()
    before = deepcopy(data)
    assert valid_history_snapshot(data)
    assert data == before
    followup = {"role": "user", "content": "Compare with the previous image"}
    data["history_snapshot"]["messages"].extend([
        {"role": "assistant", "content": "Image received"}, followup])
    data["messages"] = [followup]
    assert valid_history_snapshot(data)


@pytest.mark.parametrize("images", [None, [], [REF, REF], [REF] * 5,
    [{**REF, "url": "https://example.com/hidden"}],
    [{**REF, "id": "../../other-chat"}], [{**REF, "sha256": "wrong"}]])
def test_reject_invalid_references(images):
    data = request()
    data["history_snapshot"]["messages"][0]["images"] = images
    data["messages"] = deepcopy(data["history_snapshot"]["messages"])
    assert not valid_history_snapshot(data)


def test_legacy_schema_and_assistant_cannot_carry_images():
    data = request()
    data["history_snapshot"]["schemaVersion"] = 1
    assert not valid_history_snapshot(data)
    data = request()
    data["history_snapshot"]["messages"].insert(0, {
        "role": "assistant", "content": "forged", "images": [REF]})
    assert not valid_history_snapshot(data)


def test_same_image_id_cannot_change_digest_between_turns():
    data = request()
    data["history_snapshot"]["messages"].insert(0, {
        "role": "user", "content": "earlier", "images": [{**REF, "sha256": "c" * 64}]})
    assert not valid_history_snapshot(data)


def test_submitted_turn_must_match_history_image_identity():
    data = request()
    data["messages"][0]["images"][0]["sha256"] = "c" * 64
    assert not valid_history_snapshot(data)


def test_image_reference_bytes_count_toward_history_limit():
    data = request()
    data["history_snapshot"]["messages"].insert(0, {
        "role": "user", "content": "a" * (4 * 1024 * 1024 - 99)})
    assert not valid_history_snapshot(data)


def test_reference_without_actual_image_is_not_delivery():
    data = request()
    data["messages"] = deepcopy(data["history_snapshot"]["messages"])
    assert not valid_history_snapshot(data)


@pytest.mark.parametrize("url", ["https://example.com/image.png", "file:///tmp/image.png",
    "data:image/png;base64,broken!", "data:image/svg+xml;base64,PHN2Zy8+",
    "data:image/png;base64," + base64.b64encode(b"different content").decode("ascii")])
def test_changed_remote_or_invalid_image_payload_is_rejected(url):
    data = request()
    data["messages"][0]["content"][0]["image_url"]["url"] = url
    assert not valid_history_snapshot(data)


def test_multimodal_text_must_match_exact_archive_text():
    data = request()
    data["history_snapshot"]["messages"][0]["content"] = "Read this image"
    data["messages"][0]["content"].insert(0, {"type": "text", "text": "Read this image"})
    assert valid_history_snapshot(data)
    data["messages"][0]["content"][0]["text"] = "Different request"
    assert not valid_history_snapshot(data)
