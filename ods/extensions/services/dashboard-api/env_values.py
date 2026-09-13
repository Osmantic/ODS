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


def parse_env_float_clamped(value: str, min_val: float, max_val: float, default: float = 0.0) -> float:
    """Parse env string to float and clamp it between min_val and max_val."""
    try:
        parsed = float(value.strip())
    except (ValueError, TypeError, AttributeError):
        return default
    return max(min_val, min(parsed, max_val))
