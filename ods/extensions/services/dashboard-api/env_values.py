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

import re

def parse_env_hex_color(value: str, default: str = "#000000") -> str:
    """Parse and validate a hex color string from the environment, returning default if invalid."""
    if not value:
        return default
    val = value.strip().upper()
    if not val.startswith("#"):
        val = "#" + val
    if re.match(r"^#([0-9A-F]{3}|[0-9A-F]{6})$", val):
        return val
    return default
