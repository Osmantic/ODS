import math


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


def parse_env_float_safe(
    val: object, default: float = 0.0, min_val: float | None = None, max_val: float | None = None
) -> float:
    """Parse environment float values safely with min/max bounds clamping.

    Returns default on None, non-numeric strings, NaN, or Inf values.
    """
    if val is None:
        return default
    try:
        parsed = float(str(val).strip())
        if math.isnan(parsed) or math.isinf(parsed):
            return default
    except (ValueError, TypeError):
        return default

    if min_val is not None and parsed < min_val:
        return min_val
    if max_val is not None and parsed > max_val:
        return max_val
    return parsed

