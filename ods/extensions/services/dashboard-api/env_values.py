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


def parse_env_boolean_flags(value: str, allowed_flags: set[str]) -> set[str]:
    """Parse a comma-separated string of flags, retaining only those in the allowed set."""
    if not value:
        return set()
    flags = {f.strip().lower() for f in value.split(",")}
    return flags.intersection(allowed_flags)
