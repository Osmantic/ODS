"""The lock the Desktop profile installs from stays hash-pinned and gateway-free.

The Desktop installer runs pip with --require-hashes against this file, and the
profile talks to llama-server directly, so the lock must never pull in an LLM
gateway such as LiteLLM.
"""

import re
from pathlib import Path

LOCK = Path(__file__).resolve().parent.parent / "requirements.lock"
_REQUIREMENT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==\S+")


def _hash_counts() -> dict[str, int]:
    """Locked package name -> number of --hash lines pinned to it."""
    counts: dict[str, int] = {}
    current: str | None = None
    for line in LOCK.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _REQUIREMENT.match(stripped)
        if match:
            current = match.group(1).lower()
            counts[current] = 0
        assert current is not None, f"unpinned requirement line: {line!r}"
        counts[current] += stripped.count("--hash=sha256:")
    return counts


def test_every_locked_requirement_is_pinned_and_hashed():
    counts = _hash_counts()
    assert counts
    assert [name for name, hashes in counts.items() if hashes == 0] == []


def test_lock_has_no_llm_gateway():
    assert not _hash_counts().keys() & {"litellm", "litellm-proxy-extras", "litellm-enterprise"}
