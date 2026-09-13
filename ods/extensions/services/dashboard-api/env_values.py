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


def env_is_truthy_loose(value: str) -> bool:
    """Return True if the env string loosely represents a positive affirmation (1, y, yes, true, on, enable)."""
    if not value:
        return False
    return value.strip().lower() in {"1", "y", "yes", "true", "on", "enable", "enabled"}
