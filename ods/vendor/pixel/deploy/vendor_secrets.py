"""Shared high-precision vendor credential detection for OUTBOUND content in the owner-confirmed
external-action brokers (GitHub, Calendar).

Only specific, near-zero-false-positive vendor secret FORMATS are matched -- NOT generic
credential assignments, long hexadecimal (commit SHAs), or word-like values -- so legitimate prose,
credential documentation, commit references, and placeholders (e.g. "Set your API_KEY in .env",
"Bearer YOUR_TOKEN_HERE", "rotate DEPLOY_TOKEN") are never flagged. These brokers submit
owner-hash-confirmed proposals that cannot be redacted, so a match must REJECT the proposal (the
caller raises), keeping an injected or accidental credential off the (possibly public) destination.

Single source of truth so the detection cannot drift between the two brokers.
"""

from __future__ import annotations

import re

VENDOR_SECRET_PATTERNS = (
    ("private key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", re.I)),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("OpenAI-style key", re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("GitLab token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b")),
    ("Google API key", re.compile(r"\bAIza[A-Za-z0-9_-]{35}\b")),
    ("npm token", re.compile(r"\bnpm_[A-Za-z0-9]{20,}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{16,}\b", re.I)),
    ("Stripe live key", re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{16,}\b")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("bearer token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}\b")),
)


def find_vendor_secret(*texts: str) -> str | None:
    """Return the label of the first vendor credential found across the given texts, else None."""
    joined = "\n".join(text for text in texts if isinstance(text, str))
    for name, pattern in VENDOR_SECRET_PATTERNS:
        if pattern.search(joined):
            return name
    return None
