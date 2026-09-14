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


def parse_env_list_safe(val: object, delimiter: str = ",") -> list:
    """Parse delimited environment string into stripped item list safely.

    Handles None, empty strings, non-string inputs, and custom delimiters without exception.
    """
    if val is None:
        return []
    val_str = str(val).strip()
    if not val_str:
        return []
    delim = delimiter if delimiter else ","
    return [item.strip() for item in val_str.split(delim) if item.strip()]

