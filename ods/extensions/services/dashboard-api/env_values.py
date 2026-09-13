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


def env_require_non_empty(value: str, var_name: str) -> str:
    """Ensure the env value is not empty or exclusively whitespace, raising ValueError if it is."""
    if not value or not value.strip():
        raise ValueError(f"Environment variable {var_name} cannot be empty or whitespace-only")
    return value.strip()
