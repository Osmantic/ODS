"""Small, dependency-free helpers for values read from ODS ``.env`` files."""


def strip_matching_quotes(value: str) -> str:
    """Trim whitespace and remove exactly one matching outer quote pair.

    ODS writes shell-compatible values that may be wrapped in single or
    double quotes. Unmatched or mixed quotes are data, not delimiters, and
    must survive reads unchanged.
    """
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def parse_bool_env(value: object, default: bool = False) -> bool:
    """Safely parse boolean environment variable values."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    val_str = strip_matching_quotes(str(value)).lower()
    if val_str in {"1", "true", "yes", "on", "enabled"}:
        return True
    if val_str in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def sanitize_env_key(key: str) -> str:
    """Sanitize environment key name to valid C/POSIX identifier."""
    if not isinstance(key, str):
        return ""
    key = key.strip()
    if not key or not (key[0].isalpha() or key[0] == "_"):
        return ""
    return "".join(c if c.isalnum() or c == "_" else "_" for c in key)
