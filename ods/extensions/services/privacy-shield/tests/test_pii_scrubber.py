"""Unit tests for the deployed PII scrubber regex patterns."""

import sys
from pathlib import Path

import pytest

# Add parent dir so pii_scrubber can be imported
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pii_scrubber import PIIDetector, PrivacyShield


@pytest.fixture
def detector():
    return PIIDetector()


@pytest.fixture
def shield():
    return PrivacyShield()


# ── Email detection ──────────────────────────────────────────────────────────

class TestEmailDetection:
    def test_basic_email(self, detector):
        text = "Contact me at user@example.com please"
        result = detector.scrub(text)
        assert "user@example.com" not in result
        assert "<PII_email_" in result

    def test_email_with_plus(self, detector):
        text = "Send to user+tag@example.com"
        result = detector.scrub(text)
        assert "user+tag@example.com" not in result

    def test_email_with_dots(self, detector):
        text = "john.doe.jr@subdomain.example.co.uk"
        result = detector.scrub(text)
        assert "john.doe.jr@subdomain.example.co.uk" not in result

    def test_no_false_positive_at_sign(self, detector):
        text = "Use @mentions in chat"
        result = detector.scrub(text)
        assert result == text


# ── Phone detection ──────────────────────────────────────────────────────────

class TestPhoneDetection:
    def test_us_phone_dashes(self, detector):
        text = "Call 555-123-4567"
        result = detector.scrub(text)
        assert "555-123-4567" not in result
        assert "<PII_phone_" in result

    def test_us_phone_dots(self, detector):
        text = "Call 555.123.4567"
        result = detector.scrub(text)
        assert "555.123.4567" not in result

    def test_us_phone_with_country_code(self, detector):
        text = "Call +1-555-123-4567"
        result = detector.scrub(text)
        assert "555-123-4567" not in result

    def test_us_phone_parens(self, detector):
        text = "Call (555) 123-4567"
        result = detector.scrub(text)
        assert "123-4567" not in result


# ── SSN detection ────────────────────────────────────────────────────────────

class TestSSNDetection:
    def test_ssn_with_dashes(self, detector):
        text = "SSN: 123-45-6789"
        result = detector.scrub(text)
        assert "123-45-6789" not in result
        assert "<PII_ssn_" in result

    def test_ssn_with_dots(self, detector):
        text = "SSN: 123.45.6789"
        result = detector.scrub(text)
        assert "123.45.6789" not in result

    def test_ssn_no_separators(self, detector):
        text = "SSN: 123456789"
        result = detector.scrub(text)
        assert "123456789" not in result

    def test_ssn_no_false_positive_date(self, detector):
        """Dates starting with 19xx or 20xx should not match as SSNs."""
        text = "Date: 2024-01-2345 is not an SSN"
        result = detector.scrub(text)
        # The date-like string should NOT be scrubbed as an SSN
        assert "<PII_ssn_" not in result


# ── IP address detection ─────────────────────────────────────────────────────

class TestIPDetection:
    def test_ipv4(self, detector):
        text = "Server at 192.168.1.100"
        result = detector.scrub(text)
        assert "192.168.1.100" not in result
        assert "<PII_ip_address_" in result

    def test_ipv4_localhost(self, detector):
        text = "Listening on 127.0.0.1"
        result = detector.scrub(text)
        assert "127.0.0.1" not in result

    def test_full_ipv6(self, detector):
        text = "IPv6: 2001:0db8:85a3:0000:0000:8a2e:0370:7334"
        result = detector.scrub(text)
        assert "2001:0db8:85a3:0000:0000:8a2e:0370:7334" not in result


# ── API key detection ────────────────────────────────────────────────────────

class TestAPIKeyDetection:
    def test_api_key_equals(self, detector):
        text = "api_key=sk-abc123xyz789abcdef"
        result = detector.scrub(text)
        assert "sk-abc123xyz789abcdef" not in result
        assert "<PII_api_key_" in result

    def test_api_key_colon(self, detector):
        text = "token: abcdef1234567890abcdef"
        result = detector.scrub(text)
        assert "abcdef1234567890abcdef" not in result

    def test_short_value_not_matched(self, detector):
        text = "api_key=short"
        result = detector.scrub(text)
        assert result == text


# ── Credit card detection ────────────────────────────────────────────────────

class TestCreditCardDetection:
    def test_visa_with_spaces(self, detector):
        text = "Card: 4111 1111 1111 1111"
        result = detector.scrub(text)
        assert "4111 1111 1111 1111" not in result
        assert "<PII_credit_card_" in result

    def test_visa_with_dashes(self, detector):
        text = "Card: 4111-1111-1111-1111"
        result = detector.scrub(text)
        assert "4111-1111-1111-1111" not in result

    def test_visa_no_separators(self, detector):
        text = "Card: 4111111111111111"
        result = detector.scrub(text)
        assert "4111111111111111" not in result

    def test_luhn_invalid_not_matched(self, detector):
        """A 16-digit number that fails Luhn should not be scrubbed."""
        text = "Number: 1234567890123456"
        result = detector.scrub(text)
        # 1234567890123456 fails Luhn — should NOT be treated as credit card
        assert "<PII_credit_card_" not in result

    def test_luhn_check_static(self):
        """Verify the Luhn algorithm implementation directly."""
        assert PIIDetector._luhn_check("4111111111111111") is True  # Valid Visa test number
        assert PIIDetector._luhn_check("5500000000000004") is True  # Valid MC test number
        assert PIIDetector._luhn_check("1234567890123456") is False  # Invalid

    # A PAN is 13-19 digits, not 16. Amex is 15 and Diners is 14, so a
    # 16-digit-only detector hands those card numbers to the upstream provider
    # in cleartext — the exact leak the shield exists to stop.
    @pytest.mark.parametrize(
        "label,number",
        [
            ("amex grouped", "3782 822463 10005"),
            ("amex plain", "378282246310005"),
            ("amex dashed", "3782-822463-10005"),
            ("diners grouped", "3056 930902 5904"),
            ("diners plain", "30569309025904"),
            ("visa 13", "4222222222222"),
            ("visa 19", "4111111111111111110"),
        ],
    )
    def test_non_sixteen_digit_pans_are_scrubbed(self, detector, label, number):
        result = detector.scrub(f"Card: {number} on file")
        assert number not in result, label
        assert "<PII_credit_card_" in result, label

    def test_luhn_accepts_the_whole_pan_length_range(self):
        assert PIIDetector._luhn_check("378282246310005") is True  # 15, Amex
        assert PIIDetector._luhn_check("30569309025904") is True  # 14, Diners
        assert PIIDetector._luhn_check("4222222222222") is True  # 13, Visa
        assert PIIDetector._luhn_check("4111111111111111110") is True  # 19
        assert PIIDetector._luhn_check("378282246310004") is False  # bad checksum
        assert PIIDetector._luhn_check("123456789012") is False  # 12, too short
        assert PIIDetector._luhn_check("12345678901234567890") is False  # 20, too long

    def test_short_digit_runs_are_not_cards(self, detector):
        """Phone numbers and SSNs sit below the PAN length floor."""
        for text in ("Call 555-123-4567", "SSN 123-45-6789", "Ref 1234 5678"):
            assert "<PII_credit_card_" not in detector.scrub(text), text

    def test_card_wins_over_phone_on_overlap(self, detector):
        """A Diners PAN contains a phone-shaped run; the card must claim it."""
        result = detector.scrub("Card: 3056 930902 5904")
        assert "<PII_phone_" not in result
        assert "3056" not in result


# ── Round-trip (scrub + restore) ─────────────────────────────────────────────

class TestRoundTrip:
    def test_full_round_trip(self, shield):
        original = (
            "Contact john@example.com or call 555-123-4567. "
            "SSN: 123-45-6789. Server: 192.168.1.100. "
            "api_key=sk-abc123xyz789abcdef"
        )
        scrubbed, meta = shield.process_request(original)
        assert meta["scrubbed"] is True
        assert meta["pii_count"] > 0
        restored = shield.process_response(scrubbed)
        assert restored == original

    def test_no_pii_unchanged(self, shield):
        text = "Hello, this is a normal message with no PII."
        scrubbed, meta = shield.process_request(text)
        assert meta["scrubbed"] is False
        assert scrubbed == text

    def test_deterministic_tokens(self, detector):
        """Same PII should produce same token within a session."""
        text1 = "Email: test@example.com"
        text2 = "Also: test@example.com"
        result1 = detector.scrub(text1)
        result2 = detector.scrub(text2)
        # Extract the token from result1
        import re
        tokens1 = re.findall(r'<PII_email_[a-f0-9]+>', result1)
        tokens2 = re.findall(r'<PII_email_[a-f0-9]+>', result2)
        assert len(tokens1) == 1
        assert len(tokens2) == 1
        assert tokens1[0] == tokens2[0]


# ── Stats ────────────────────────────────────────────────────────────────────

class TestStats:
    def test_stats_after_scrub(self, detector):
        detector.scrub("Email: a@b.com, Phone: 555-123-4567")
        stats = detector.get_stats()
        assert stats["unique_pii_count"] >= 2
        assert "email" in stats["pii_types"]
        assert "phone" in stats["pii_types"]

    def test_stats_empty(self, detector):
        stats = detector.get_stats()
        assert stats["unique_pii_count"] == 0
        assert stats["pii_types"] == []

    def test_stats_reports_multiword_type(self, detector):
        # Regression: a multi-word PII type (ip_address) must be reported in
        # full, not truncated to "ip" by a naive token.split('_')[1].
        detector.scrub("Server IP 192.168.1.100")
        types = detector.get_stats()["pii_types"]
        assert "ip_address" in types
        assert "ip" not in types
