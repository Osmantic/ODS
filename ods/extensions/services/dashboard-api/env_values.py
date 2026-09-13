"""Small, dependency-free helpers for values read from ODS ``.env`` files."""


def strip_matching_quotes(value: str) -> str:
    """Trim whitespace and remove exactly one matching outer quote pair.

    ODS writes shell-compatible values that may be wrapped in single or
    double quotes. Unmatched or mixed quotes are data, not delimiters, and
    must survive reads unchanged.
    """
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value

from urllib.parse import urlparse

def parse_env_url_host(value: str) -> str:
    """Extract just the hostname from a full URL env string."""
    if not value or not value.strip():
        return ""
    try:
        parsed = urlparse(value.strip())
        return parsed.hostname or ""
    except Exception:
        return ""
