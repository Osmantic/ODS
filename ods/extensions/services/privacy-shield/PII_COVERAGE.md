# Privacy Shield - PII Detection Coverage

## Overview

Privacy Shield provides PII (Personally Identifiable Information) detection and redaction for voice agent conversations.

Detected values are replaced with a stable `<PII_<type>_<hash>>` token before the
request leaves the host, and restored from the response on the way back. This
page describes what the shipped patterns actually match. The source of truth is
`PIIDetector.PATTERNS` in [`pii_scrubber.py`](pii_scrubber.py); the cases below
are covered by [`tests/test_pii_scrubber.py`](tests/test_pii_scrubber.py).

## Current Implementation: Regex-Only Detection

**Status:** The current implementation uses regex-based pattern matching for PII detection.

### Detected PII Types

| Type | Matches | Does not match |
|------|---------|----------------|
| Email addresses | `user@example.com`, `user+tag@example.com`, `john.doe.jr@sub.example.co.uk` | — |
| Phone numbers (US) | `555-123-4567`, `555.123.4567`, `555 123 4567`, `(555) 123-4567`, `+1 555 123 4567` | International formats other than a leading `+1` |
| Social Security Numbers | `123-45-6789`, `123.45.6789`, `123 45 6789` | A leading `19xx`/`20xx` group, so `2019-05-1234` is treated as a date range, not an SSN |
| Credit card numbers | 16-digit numbers that pass a Luhn check: `4111 1111 1111 1111`, `4111-1111-1111-1111`, `4111111111111111` | Any other length — 15-digit Amex, 14-digit Diners, 13-digit Visa. 16-digit numbers that fail Luhn are left alone on purpose |
| IP addresses | IPv4 `192.168.1.100`; IPv6 full, leading `::`, trailing `::` and middle `::` forms | — |
| API keys and tokens | A `api_key` / `api-key` / `apikey` / `token` label, then `=` or `:`, then 16+ characters of `[A-Za-z0-9_-]` — e.g. `api_key=sk-abc123xyz789abcdef`, `X-Api-Key: sk-abc123xyz789abcdef` | Values shorter than 16 characters. `Authorization: Bearer <token>` — the label is `Bearer`, not `token` |

The whole match is replaced, label included, so `api_key=sk-...` becomes a single
`<PII_api_key_...>` token rather than `api_key=<PII_api_key_...>`.

### Limitations

The following PII types are **not** currently detected:

- Person names (e.g. "John Smith")
- Physical addresses
- Dates of birth
- Passport numbers
- Driver's license numbers
- Bank account numbers
- Medical record numbers
- `Authorization: Bearer <token>` headers
- Payment cards that are not exactly 16 digits

Two behaviours worth knowing about:

- **Patterns are applied in order and can overlap.** `email`, `phone`, `ssn`,
  `ip_address`, `api_key`, `credit_card` — in that order. A 14-digit card such as
  `3056 930902 5904` has its trailing ten digits consumed by the phone pattern
  first, so it is redacted as a phone number and the leading `3056` is left in
  the text. Treat non-16-digit cards as unprotected.
- **Detection is per-conversation, not per-line.** The token for a given value is
  stable within a `PIIDetector` instance, so the same email in a later turn gets
  the same token and restores correctly.

## Future Enhancement: Presidio Integration

**Planned:** Integration with Microsoft Presidio for comprehensive NER-based PII detection.

### Benefits of Presidio Integration

- Named entity recognition for person names
- Address detection and normalization
- Context-aware PII detection
- Customizable PII recognizers
- Support for multiple languages

### Implementation Timeline

- **Current:** Regex-only detection (ship-ready)
- **Post-ship:** Presidio integration for enhanced coverage

## Configuration

No configuration required. Privacy Shield operates automatically when enabled.

## Error Handling

Error responses return generic messages to prevent information leakage:

```json
{"error": "Privacy check failed", "shield": "active"}
```

Detailed errors are logged server-side.
