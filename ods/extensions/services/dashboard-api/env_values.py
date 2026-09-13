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


def parse_env_duration_seconds(value: str, default: int = 0) -> int:
    """Parse a duration string (e.g. '10s', '5m', '2h') into integer seconds."""
    if not value:
        return default
    val = value.strip().lower()
    multiplier = 1
    if val.endswith("s"):
        val = val[:-1]
    elif val.endswith("m"):
        val = val[:-1]
        multiplier = 60
    elif val.endswith("h"):
        val = val[:-1]
        multiplier = 3600
    elif val.endswith("d"):
        val = val[:-1]
        multiplier = 86400
    try:
        return int(val) * multiplier
    except ValueError:
        return default
