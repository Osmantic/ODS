#!/usr/bin/env python3
"""
M3: API Privacy Shield - Core PII Scrubber
Detects and replaces PII with tokens, restores on reverse.
"""

import codecs
import hashlib
import ipaddress
import json
import os
import re
import secrets
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Tuple

# Upper bound on distinct PII values one detector remembers. The least
# recently used mapping is evicted past this, so hostile input cannot grow
# memory (or per-request cost) without limit.
MAX_PII_ENTRIES = int(os.getenv("SHIELD_MAX_PII_ENTRIES", "4096"))

# Every generated token matches this; restore resolves tokens in one pass
# with a dict lookup instead of one str.replace per known token.
TOKEN_RE = re.compile(r"<PII_[a-z_]+_[0-9a-f]{12}>")


def _luhn_ok(number_str: str) -> bool:
    """Luhn checksum over the digits of *number_str* (13-19 digits)."""
    digits = [int(d) for d in number_str if d.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    checksum = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


# Issuer prefixes: Amex 34/37, Diners/JCB 3x, Visa 4, Mastercard 2/5,
# Discover/UnionPay 6. Bare digit runs starting with 0, 1, 7, 8 or 9 (for
# example 13-digit millisecond timestamps) are never treated as cards.
_CARD_ISSUER_DIGITS = frozenset("23456")


def _card_ok(candidate: str) -> bool:
    """Card candidates need a known issuer leading digit and a valid Luhn."""
    return candidate[0] in _CARD_ISSUER_DIGITS and _luhn_ok(candidate)


def _ip_ok(candidate: str) -> bool:
    """IPv4 dotted quads pass; IPv6 candidates must parse as an address.

    At least two non-empty groups are required so code such as ``a::`` or
    a bare ``::`` is not mistaken for an address.
    """
    if ":" not in candidate:
        return True
    if sum(1 for group in candidate.split(":") if group) < 2:
        return False
    try:
        ipaddress.IPv6Address(candidate)
    except ValueError:
        return False
    return True


def _e164_ok(candidate: str) -> bool:
    """E.164 allows at most 15 digits; require 8+ to skip short noise."""
    return 8 <= sum(c.isdigit() for c in candidate) <= 15


# Ordered (type, regex, validator). Specific detectors run before generic
# digit runs so e.g. an Amex number is not split into a phone fragment.
_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]", Optional[Callable[[str], bool]]], ...] = (
    ('email', re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b'), None),
    # Prefix-identified secrets that need no "api_key=" label.
    ('api_key', re.compile(
        r'(?<![A-Za-z0-9_-])(?:sk-[A-Za-z0-9_-]{20,}'
        r'|gh[pousr]_[A-Za-z0-9]{36}'
        r'|AKIA[0-9A-Z]{16})(?![A-Za-z0-9_-])'), None),
    ('api_key', re.compile(
        r'\b(?:api[_-]?key|apikey|token)[\s]*[=:]\s*["\']?[a-zA-Z0-9_\-]{16,}["\']?\b',
        re.IGNORECASE), None),
    # 13-19 digit cards (Visa/MC 4-4-4-4, Amex 4-6-5, ...), Luhn-validated.
    ('credit_card', re.compile(r'\b\d(?:[ -]?\d){12,18}\b'), _card_ok),
    # E.164 international numbers: +CC followed by digit groups.
    ('phone', re.compile(r'(?<![\w+])\+[1-9]\d{0,2}(?:[ .-]?\(?\d{1,5}\)?){2,5}\b'), _e164_ok),
    ('phone', re.compile(r'\b(?:\+?1[-.\s]?)?\(?[0-9]{3}\)?[-.\s]?[0-9]{3}[-.\s]?[0-9]{4}\b'), None),
    ('ssn', re.compile(r'\b(?!(?:19|20)\d{2}[-.\s]?\d{2}[-.\s]?\d{4}\b)\d{3}[-.\s]?\d{2}[-.\s]?\d{4}\b'), None),
    ('ip_address', re.compile(
        r'\b(?:\d{1,3}\.){3}\d{1,3}\b'  # IPv4
        r'|'
        # IPv6 candidate (full or compressed), validated with ipaddress.
        r'(?<![\w:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![\w:])'
    ), _ip_ok),
)


@dataclass
class PIIDetector:
    """Detects and manages PII in text."""

    # Token prefix for PII placeholders
    token_prefix: str = "<PII_"
    token_suffix: str = ">"

    # token -> original, in LRU order, bounded by max_entries.
    pii_map: "OrderedDict[str, str]" = field(default_factory=OrderedDict)
    counter: int = field(default=0)
    # None = unbounded (request-local maps, which die with the request).
    max_entries: Optional[int] = field(default_factory=lambda: MAX_PII_ENTRIES)

    # Stable session token (persisted, doesn't change on restart)
    session_token: str = field(default_factory=lambda: secrets.token_hex(16))

    # original -> token reverse index: O(1) reuse of an existing token.
    _reverse: Dict[str, str] = field(default_factory=dict, repr=False)
    _max_token_len: int = field(default=0, repr=False)

    PATTERNS = _PATTERNS

    @staticmethod
    def _luhn_check(number_str: str) -> bool:
        """Validate a credit card number using the Luhn algorithm."""
        return _luhn_ok(number_str)

    def _generate_token(self, pii_type: str, original: str) -> str:
        """Generate a unique token for PII."""
        # Create deterministic hash for same PII = same token within session
        # Use stable session_token instead of id(self) which changes on restart
        hash_input = f"{pii_type}:{original}:{self.session_token}"
        short_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:12]
        return f"{self.token_prefix}{pii_type}_{short_hash}{self.token_suffix}"

    def _token_for(self, pii_type: str, match: str) -> str:
        """Return the token for *match*, creating it and enforcing the cap."""
        token = self._reverse.get(match)
        if token is not None:
            self.pii_map.move_to_end(token)
            return token
        token = self._generate_token(pii_type, match)
        self.pii_map[token] = match
        self._reverse[match] = token
        self._max_token_len = max(self._max_token_len, len(token))
        while self.max_entries is not None and len(self.pii_map) > self.max_entries:
            _, evicted = self.pii_map.popitem(last=False)
            del self._reverse[evicted]
        return token

    def scrub(self, text: str) -> str:
        """
        Scrub PII from text, replace with tokens.
        Returns scrubbed text.
        """
        if not isinstance(text, str):
            raise TypeError("PII scrub requires text")
        scrubbed = text

        for pii_type, pattern, validator in self.PATTERNS:
            def replace_match(found, pii_type=pii_type, validator=validator):
                match = found.group(0)
                if validator is not None and not validator(match):
                    return match
                return self._token_for(pii_type, match)

            # Replace exactly the regex match. A same-valued substring earlier
            # in the text may not satisfy the pattern's boundary conditions.
            scrubbed = pattern.sub(replace_match, scrubbed)

        return scrubbed

    def restore(self, text: str, *, json_escape: bool = False) -> str:
        """
        Restore PII from tokens in text.

        With *json_escape*, each original is inserted JSON-string escaped.
        Tokens in a JSON (or SSE/NDJSON) body sit inside string literals, and
        an original such as ``api_key="..."`` would otherwise break the JSON.
        """
        if not isinstance(text, str):
            raise TypeError("PII restore requires text")
        if not self.pii_map:
            return text

        def original(match: "re.Match[str]") -> str:
            value = self.pii_map.get(match.group(0))
            if value is None:
                return match.group(0)
            return json.dumps(value, ensure_ascii=False)[1:-1] if json_escape else value

        return TOKEN_RE.sub(original, text)

    def max_token_len(self) -> int:
        """Length of the longest PII token issued (0 if none active).

        The streaming restorer uses this to bound the hold-back buffer so a
        token straddling a chunk boundary is never emitted half-restored,
        while the buffer can never grow without limit on hostile input.
        """
        return self._max_token_len if self.pii_map else 0

    def get_stats(self) -> Dict:
        """Return statistics about detected PII."""
        return {
            'unique_pii_count': len(self.pii_map),
            'pii_types': list(set(
                # A type may itself contain underscores (ip_address, credit_card),
                # so take everything between the prefix and the trailing _<hash>
                # rather than split('_')[1], which would yield "ip" / "credit".
                token[len(self.token_prefix):-len(self.token_suffix)].rsplit('_', 1)[0]
                for token in self.pii_map.keys()
            ))
        }


class StreamRestorer:
    """Incremental, boundary-safe PII restore for streamed responses.

    A PII token (e.g. ``<PII_email_a1b2c3d4e5f6>``) can be split across two
    network chunks. Restoring each chunk independently would miss any token
    that straddles the boundary. This holds back a minimal trailing slice
    that could still be the start of an in-flight token, releasing (and
    restoring) everything else immediately so streaming latency stays low.

    It also owns an incremental decoder so a multi-byte character split
    across chunks decodes correctly; undecodable bytes are replaced rather
    than raising, so a mislabelled body degrades instead of killing the
    whole stream.
    """

    # Every generated token starts with this sentinel. Nothing before the
    # last occurrence of an *unterminated* sentinel needs to be held.
    _PREFIX = "<PII_"
    _SUFFIX = ">"

    def __init__(self, detector: "PIIDetector", encoding: str = "utf-8",
                 *, json_escape: bool = False):
        self._detector = detector
        self._json_escape = json_escape
        try:
            self._decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
        except (LookupError, TypeError):
            self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._carry = ""

    def _holdback_len(self, text: str) -> int:
        """Number of trailing chars of *text* that must be withheld.

        We must withhold the shortest suffix S such that S could still grow
        into a complete token once more bytes arrive. Two cases:

        * An unterminated full prefix ``<PII_`` is present with no closing
          ``>`` after it -> hold from that prefix onward (a token is open).
        * No full prefix, but the tail is itself a proper prefix of
          ``<PII_`` (the sentinel was split) -> hold that partial prefix.

        The hold-back is capped at ``max_token_len`` so adversarial input
        that never closes a token cannot grow the buffer unbounded.
        """
        max_tok = self._detector.max_token_len()
        if max_tok == 0 or not text:
            return 0

        idx = text.rfind(self._PREFIX)
        if idx != -1 and text.find(self._SUFFIX, idx) == -1:
            # Open, unterminated token starting at idx.
            hold = len(text) - idx
            return min(hold, max_tok)

        # No open full prefix: only worry about a split sentinel at the end.
        max_partial = min(len(self._PREFIX) - 1, len(text))
        for n in range(max_partial, 0, -1):
            if text[-n:] == self._PREFIX[:n]:
                return n
        return 0

    def feed(self, chunk: bytes, *, restore: bool = True) -> str:
        """Decode + restore a chunk; return text safe to emit now."""
        text = self._carry + self._decoder.decode(chunk)
        if not restore:
            # Retain decoder state across the restoration budget boundary.
            # Decoding still needs bytes held from the previous transport chunk.
            self._carry = ""
            return text
        hold = self._holdback_len(text)
        if hold:
            releasable, self._carry = text[:-hold], text[-hold:]
        else:
            releasable, self._carry = text, ""
        if not releasable:
            return ""
        return self._detector.restore(releasable, json_escape=self._json_escape)

    def finalize(self, *, restore: bool = True) -> str:
        """Flush the decoder and carry buffer at end of stream."""
        tail = self._decoder.decode(b"", True)
        remaining = self._carry + tail
        self._carry = ""
        if not remaining:
            return ""
        if not restore:
            return remaining
        return self._detector.restore(remaining, json_escape=self._json_escape)


class PrivacyShield:
    """
    Main API Privacy Shield wrapper.
    Wraps API calls to scrub/restore PII transparently.
    """

    def __init__(self, backend_client=None, max_entries: Optional[int] = MAX_PII_ENTRIES):
        self.detector = PIIDetector(max_entries=max_entries)
        self.backend = backend_client  # e.g., OpenAI client

    def process_request(self, prompt: str) -> Tuple[str, Dict]:
        """
        Process outgoing request - scrub PII.
        Returns (scrubbed_prompt, metadata for restore).
        """
        scrubbed = self.detector.scrub(prompt)
        stats = self.detector.get_stats()

        metadata = {
            'scrubbed': scrubbed != prompt,
            'pii_count': stats['unique_pii_count'],
            'pii_types': stats['pii_types']
        }

        return scrubbed, metadata

    def process_response(self, response_text: str) -> str:
        """
        Process incoming response - restore PII.
        """
        return self.detector.restore(response_text)


# Simple CLI for testing
if __name__ == "__main__":

    shield = PrivacyShield()

    # Test input
    test_text = """
    Contact John Doe at john.doe@example.com or call 555-123-4567.
    API Key: sk-abc123xyz789abcdef
    Server IP: 192.168.1.100
    SSN: 123-45-6789
    """

    print("=== PII Scrubber Test ===")
    print(f"\nOriginal:\n{test_text}")

    scrubbed, meta = shield.process_request(test_text)
    print(f"\nScrubbed:\n{scrubbed}")
    print(f"\nMetadata: {meta}")

    restored = shield.process_response(scrubbed)
    print(f"\nRestored:\n{restored}")

    # Verify round-trip
    if restored.strip() == test_text.strip():
        print("\n✅ Round-trip successful!")
    else:
        print("\n❌ Round-trip failed!")
        print(f"Diff: {set(restored.split()) ^ set(test_text.split())}")
