"""Small, dependency-free helpers for values read from ODS ``.env`` files."""

import re


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


def quote_env_value(value: str) -> str:
    """Serialize one value for the single-line ``.env`` grammar shared by
    Docker Compose and ODS's own readers (``lib/safe-env.sh``,
    ``strip_matching_quotes``).

    Bare values are left alone whenever every reader agrees on them. Compose
    interpolates ``$NAME`` in unquoted and double-quoted values, cuts an
    unquoted value at the first `` #``, trims surrounding whitespace, and
    treats a leading quote as the start of a quoted value; ODS readers do
    none of that. Such values are single-quoted, which is literal for every
    reader. A value that already contains a single quote or a backslash uses
    the double-quoted escape set from ``lib/dotenv-quote.sh`` instead.
    """
    if "\n" in value or "\r" in value:
        raise ValueError("newlines cannot be represented in a .env value")
    if value == "" or (
        value == value.strip()
        and value[0] not in {"'", '"'}
        and "$" not in value
        and "#" not in value
    ):
        return value
    if "'" not in value and "\\" not in value:
        return f"'{value}'"
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
    )
    return f'"{escaped}"'


_DOUBLE_QUOTED_RE = re.compile(r'^"((?:\\.|[^"\\])*)"(?:\s+#.*)?$')
_SINGLE_QUOTED_RE = re.compile(r"^'([^']*)'(?:\s+#.*)?$")


def parse_env_value(raw: str) -> str:
    """Read one ``.env`` value the way Docker Compose does.

    Compose trims surrounding whitespace, ends an unquoted value at the first
    `` #`` (a ``#`` at the start of the trimmed value is not a comment), lets a
    comment follow a closing quote, treats single quotes literally, and
    decodes the ``\\\\`` ``\\"`` ``\\$`` escapes inside double quotes -- the set
    ``quote_env_value`` emits and ``lib/safe-env.sh`` decodes. Values that
    start with a quote but do not parse as a quoted value fall back to
    ``strip_matching_quotes`` so mismatched quotes stay data.
    """
    value = raw.strip()
    match = _DOUBLE_QUOTED_RE.match(value)
    if match:
        return (
            match.group(1)
            .replace('\\"', '"')
            .replace("\\$", "$")
            .replace("\\\\", "\\")
        )
    match = _SINGLE_QUOTED_RE.match(value)
    if match:
        return match.group(1)
    if value[:1] in {"'", '"'}:
        return strip_matching_quotes(value)
    return value.split(" #", 1)[0].rstrip()
