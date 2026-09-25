"""Machine-readable formats for extension settings (manifest ``env_vars``).

A declaration may constrain its value with a named ``format``, an anchored
``pattern`` (explained by ``format_description``), ``min_length`` (counted in
characters) and ``max_length`` (counted in UTF-8 bytes), may require it to
differ from other settings of the same extension (``distinct_from``), and may
name a ``generate`` kind the dashboard can fill with a random value. Counting
the minimum in characters and the maximum in bytes keeps both bounds safe
whichever unit the service itself measures.

Only declarations are described here. Callers check values and report the
setting names with the expected format; a value is never part of a result.
"""
import re

# Plain dot-atom addresses only: a subset of what PHP's FILTER_VALIDATE_EMAIL,
# Django and the recipes' own start-up checks accept.
_EMAIL = (r'^(?=.{3,254}$)(?=[^@]{1,64}@)[A-Za-z0-9_%+-]+(\.[A-Za-z0-9_%+-]+)*'
          r'@([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$')
NAMED_FORMATS = {
    # Either case, as the recipes' own checks accept; generated values are
    # lowercase. A recipe that only accepts lowercase adds a pattern.
    'hex32': ('^[0-9a-fA-F]{32}$', '32 hexadecimal characters (0-9, a-f)'),
    'hex64': ('^[0-9a-fA-F]{64}$', '64 hexadecimal characters (0-9, a-f)'),
    'hex128': ('^[0-9a-fA-F]{128}$', '128 hexadecimal characters (0-9, a-f)'),
    'email': (_EMAIL, 'a plain email address such as name@example.com'),
    'url': ('^https?://[^\\s/?#@]+([/?#][^\\s]*)?$', 'an http:// or https:// URL without credentials'),
    'integer': ('^[1-9][0-9]*$', 'a positive whole number (digits only, no leading zeros)'),
}
GENERATORS = ('hex32', 'hex64', 'hex128', 'password', 'token')
FIELDS = ('format', 'pattern', 'format_description', 'min_length', 'max_length', 'generate', 'distinct_from')
KEY = re.compile(r'[A-Z][A-Z0-9_]{0,127}')
MAX_VALUE_BYTES = 4096
# Patterns run in Python here and as JavaScript RegExps in the dashboard.
# These constructs differ between the two engines (or do not exist in one):
# Unicode-dependent classes and anchors, property escapes, and every group
# opener other than (?: (?= (?!. The manifest schema's `pattern` rule rejects
# the same set (UNPORTABLE_PATTERN_RULE; a test keeps the two identical).
_UNPORTABLE = re.compile(r'\\[dDwWbBAZzGQEpP]|\(\?(?![=!:])')
UNPORTABLE_PATTERN_RULE = r'^\^(?!.*\\[dDwWbBAZzGQEpP])(?!.*\(\?(?![=!:])).*\$$'


class SettingFormatError(ValueError):
    pass


def _length(item, name):
    value = item.get(name)
    if value is None:
        return None
    if type(value) is not int or not 1 <= value <= MAX_VALUE_BYTES:
        raise SettingFormatError(f'Invalid {name}')
    return value


def _pattern(item):
    pattern, description = item.get('pattern'), item.get('format_description')
    if pattern is None:
        if description is not None:
            raise SettingFormatError('format_description only explains a pattern')
        return None, None
    if (not isinstance(pattern, str) or not 2 <= len(pattern) <= 512 or not pattern.startswith('^')
            or not pattern.endswith('$') or _UNPORTABLE.search(pattern)):
        raise SettingFormatError('Invalid pattern')
    try:
        re.compile(pattern)
    except re.error:
        raise SettingFormatError('Invalid pattern') from None
    if not isinstance(description, str) or not 1 <= len(description.strip()) <= 160:
        raise SettingFormatError('A pattern needs a format_description')
    return pattern, description


def parse_setting_format(item):
    """Return the normalized format of a declaration, or None if unconstrained.

    Raises ``SettingFormatError`` for a declaration that cannot be enforced
    identically by the API and the dashboard.
    """
    if not any(name in item for name in FIELDS):
        return None
    named = item.get('format')
    if named is not None and named not in NAMED_FORMATS:
        raise SettingFormatError('Unknown format')
    pattern, description = _pattern(item)
    min_length, max_length = _length(item, 'min_length'), _length(item, 'max_length')
    if min_length and max_length and min_length > max_length:
        raise SettingFormatError('min_length exceeds max_length')
    generate = item.get('generate')
    if generate is not None and generate not in GENERATORS:
        raise SettingFormatError('Unknown generator')
    distinct = item.get('distinct_from', [])
    if (not isinstance(distinct, list) or len(distinct) > 8 or len(set(map(str, distinct))) != len(distinct)
            or any(not isinstance(key, str) or not KEY.fullmatch(key) or key == item.get('key') for key in distinct)):
        raise SettingFormatError('Invalid distinct_from')
    patterns = [NAMED_FORMATS[named][0]] if named else []
    patterns += [pattern] if pattern else []
    return {
        'name': named,
        'patterns': patterns,
        'minLength': min_length,
        'maxLength': max_length,
        'generate': generate,
        'distinctFrom': distinct,
        'hint': format_hint(named, description, min_length, max_length),
    }


def format_hint(named, description, min_length, max_length):
    """Owner-facing description of the expected value, read after "must be".

    Empty when only ``distinct_from`` or ``generate`` is declared.
    """
    parts = [NAMED_FORMATS[named][1]] if named else []
    if description:
        parts.append(description.strip().rstrip('.'))
    # A named hex format already fixes the length.
    if not (named or '').startswith('hex'):
        if min_length:
            parts.append(f'at least {min_length} characters')
        if max_length:
            parts.append(f'no more than {max_length} bytes')
    return ', '.join(parts)


def conforms(value, spec):
    """Whether ``value`` satisfies a parsed format (None means unconstrained)."""
    if not isinstance(value, str):
        return False
    if spec is None:
        return True
    if spec['minLength'] and len(value) < spec['minLength']:
        return False
    try:
        size = len(value.encode('utf-8'))
    except UnicodeEncodeError:  # A lone surrogate is not a value any service can use.
        return False
    if spec['maxLength'] and size > spec['maxLength']:
        return False
    return all(re.fullmatch(pattern, value) for pattern in spec['patterns'])


def format_problem(key, spec):
    """Name the setting and the expected format; never include the value."""
    return f'{key} must be {spec["hint"]}.'


def distinct_conflicts(fields, value_of):
    """Declared-distinct pairs ``(key, other)`` whose values are equal.

    ``value_of`` returns a setting's value (submitted or saved); empty values
    never conflict. Only the pair is returned, never the value.
    """
    conflicts = []
    for field in fields:
        for other in (field['format'] or {}).get('distinctFrom', []):
            value = value_of(field['key'])
            if value and value == value_of(other):
                conflicts.append((field['key'], other))
    return conflicts


def setting_problems(fields, value_of, keys):
    """What is wrong with the settings named in ``keys``, value-free.

    ``fields`` are the install plan's configuration fields and ``value_of``
    returns a setting's value (submitted or saved). Each problem names the
    setting, the expected format and an owner-facing sentence.
    """
    by_key = {field['key']: field for field in fields}
    problems = []
    for key in sorted(keys):
        field = by_key.get(key)
        if field and not conforms(value_of(key), field['format']):
            problems.append({'key': key, 'expected': field['format']['hint'],
                             'message': format_problem(key, field['format'])})
    for key, other in distinct_conflicts(fields, value_of):
        if key in keys or other in keys:
            problems.append({'key': key, 'expected': f'a value different from {other}',
                             'message': f'{key} must differ from {other}.'})
    return problems
