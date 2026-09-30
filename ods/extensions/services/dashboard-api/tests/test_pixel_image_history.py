import pytest
from pydantic import ValidationError

from pixel_chat_context import HistorySnapshot


REF = {"id": "img-" + "a" * 32, "sha256": "b" * 64}


def test_legacy_history_serialization_is_byte_shape_compatible():
    value = {"schemaVersion": 1, "messages": [{"role": "user", "content": "hello"}]}
    assert HistorySnapshot.model_validate(value).model_dump() == value


def test_image_only_and_followup_history_preserve_reference_identity():
    value = {"schemaVersion": 2, "messages": [
        {"role": "user", "content": "", "images": [REF]},
        {"role": "assistant", "content": "An image arrived"},
        {"role": "user", "content": "What was in that image?"},
    ]}
    parsed = HistorySnapshot.model_validate(value)
    assert parsed.model_dump() == value
    assert HistorySnapshot.model_validate_json(parsed.model_dump_json()).model_dump() == value


@pytest.mark.parametrize("version,role,images", [
    (1, "user", [REF]), (2, "assistant", [REF]), (2, "user", []),
    (2, "user", [REF, REF]), (2, "user", [{**REF, "url": "https://example.com/image"}]),
])
def test_unversioned_forged_or_ambiguous_image_history_rejected(version, role, images):
    with pytest.raises(ValidationError):
        HistorySnapshot.model_validate({"schemaVersion": version, "messages": [
            {"role": role, "content": "text", "images": images},
        ]})
