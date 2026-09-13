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


def parse_env_byte_size(value: str, default: int = 0) -> int:
    """Parse a size string (e.g. '1K', '2M', '3G') into integer bytes."""
    if not value:
        return default
    val = value.strip().upper()
    multiplier = 1
    if val.endswith("K") or val.endswith("KB"):
        val = val.rstrip("KB")
        multiplier = 1024
    elif val.endswith("M") or val.endswith("MB"):
        val = val.rstrip("MB")
        multiplier = 1024 * 1024
    elif val.endswith("G") or val.endswith("GB"):
        val = val.rstrip("GB")
        multiplier = 1024 * 1024 * 1024
    try:
        return int(val) * multiplier
    except ValueError:
        return default
