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


def env_mask_secret(value: str, visible_chars: int = 4) -> str:
    """Partially mask a secret string, leaving only the last few characters visible."""
    if not value:
        return ""
    val = value.strip()
    if len(val) <= visible_chars:
        return "*" * len(val)
    return "*" * (len(val) - visible_chars) + val[-visible_chars:]
