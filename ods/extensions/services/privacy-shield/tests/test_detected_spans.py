"""Scrub detected spans without substituting earlier non-matching substrings."""
from .test_pii_scrubber import PrivacyShield

import pytest


@pytest.mark.parametrize("value,nonmatch", [
    ("2125550199", "9992125550199888"),
    ("123-45-6789", "x123-45-6789y"),
    ("192.168.1.1", "x192.168.1.1y"),
])
def test_request_scrubs_the_detected_occurrence_not_an_earlier_substring(value, nonmatch):
    shield = PrivacyShield()
    text = f"Reference: {nonmatch}; contact: {value}."
    scrubbed, metadata = shield.process_request(text)
    assert scrubbed.startswith(f"Reference: {nonmatch}; contact: <PII_")
    assert not scrubbed.endswith(f"contact: {value}.")
    assert metadata["scrubbed"] is True
    assert metadata["pii_count"] == 1
    assert shield.process_response(scrubbed) == text

    # Reusing a session mapping must use the same span-aware replacement.
    again, repeated_metadata = shield.process_request(text)
    assert again == scrubbed
    assert repeated_metadata["pii_count"] == 1


def test_repeated_real_matches_share_one_token_and_restore_exactly():
    shield = PrivacyShield()
    text = "Call 2125550199 or 2125550199."
    scrubbed, metadata = shield.process_request(text)
    token = next(iter(shield.detector.pii_map))
    assert scrubbed == f"Call {token} or {token}."
    assert metadata["pii_count"] == 1
    assert shield.process_response(scrubbed) == text


def test_invalid_luhn_candidates_stay_unchanged_alongside_valid_cards():
    shield = PrivacyShield()
    text = "Invalid 4111 1111 1111 1112; valid 4111 1111 1111 1111."
    scrubbed, metadata = shield.process_request(text)
    assert scrubbed.startswith("Invalid 4111 1111 1111 1112; valid <PII_credit_card_")
    assert metadata["pii_count"] == 1
    assert shield.process_response(scrubbed) == text
