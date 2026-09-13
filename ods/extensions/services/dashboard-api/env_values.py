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


def parse_env_port_safe(value: str, default: int = 8080) -> int:
    """Parse a port number from env, ensuring it falls in the valid 1-65535 range."""
    try:
        port = int(value.strip())
        if 1 <= port <= 65535:
            return port
    except (ValueError, TypeError, AttributeError):
        pass
    return default
