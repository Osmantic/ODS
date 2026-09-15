"""Bulk requests preserve session identities and exact restoration."""
from .test_pii_scrubber import PrivacyShield


def test_bulk_contacts_reuse_tokens_across_requests_and_restore_exactly():
    shield = PrivacyShield()
    contacts = [f"contact-{index}@example.test" for index in range(2000)]
    original = "\n".join(contacts + contacts[::2])
    scrubbed, metadata = shield.process_request(original)
    assert "@example.test" not in scrubbed
    assert metadata["pii_count"] == len(contacts)
    assert shield.process_response(scrubbed) == original
    repeat, repeated_metadata = shield.process_request(original)
    assert repeat == scrubbed
    assert repeated_metadata == metadata


def test_reloaded_session_preserves_existing_token_and_first_duplicate():
    shield = PrivacyShield()
    original = "Contact person@example.test"
    scrubbed, _ = shield.process_request(original)
    saved_map = dict(shield.detector.pii_map)
    saved_map["<PII_email_second>"] = "person@example.test"
    reloaded = PrivacyShield()
    reloaded.detector.pii_map = saved_map
    repeat, _ = reloaded.process_request(original)
    assert repeat == scrubbed
    assert reloaded.process_response(repeat) == original


def test_changes_to_the_session_map_are_seen_on_the_next_request():
    shield = PrivacyShield()
    original = "person@example.test"
    first, _ = shield.process_request(original)
    shield.detector.pii_map.clear()
    second, metadata = shield.process_request(original)
    assert second == first
    assert metadata["pii_count"] == 1
    assert shield.process_response(second) == original
