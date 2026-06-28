# Privacy Shield - PII Detection Coverage

## Overview

Privacy Shield provides PII (Personally Identifiable Information) detection and redaction for voice agent conversations.

## Current Implementation: Regex-Only Detection

**Status:** The current implementation uses regex-based pattern matching for PII detection.

### Detected PII Types

| Type | Pattern | Example |
|------|---------|---------|
| Email addresses | `\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b` | user@example.com |
| Phone numbers (US) | `\b\d{3}[-.]?\d{3}[-.]?\d{4}\b` | 555-123-4567 |
| Social Security Numbers | `\b\d{3}-\d{2}-\d{4}\b` | 123-45-6789 |
| Credit card numbers | `\b(?:\d[ -]*?){13,16}\b` | 4111 1111 1111 1111 |
| IP addresses | `\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b` | 192.168.1.1 |

### Limitations

The following PII types are **NOT currently detected**:

- Person names (e.g., "John Smith")
- Physical addresses
- Dates of birth
- Passport numbers
- Driver's license numbers
- Bank account numbers
- Medical record numbers

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
