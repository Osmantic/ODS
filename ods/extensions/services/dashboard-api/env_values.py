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


def parse_env_version_tuple(value: str) -> tuple[int, ...]:
    """Parse a version string (e.g. '1.2.3') into a tuple of integers for easy comparison."""
    if not value:
        return ()
    try:
        return tuple(int(part) for part in value.strip().split(".") if part.strip())
    except ValueError:
        return ()
