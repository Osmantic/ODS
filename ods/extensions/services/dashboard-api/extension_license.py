"""Conservative, commit-scoped evidence for SPDX license expressions.

This is a preparation gate, not a legal opinion or a general license detector.
An unfamiliar license document is left for review instead of being guessed.
"""
import hashlib
import re
import tomllib


RECOGNIZED_OPEN_SOURCE_LICENSES = frozenset({
    'MIT', 'Apache-2.0', 'BSD-2-Clause', 'BSD-3-Clause', 'ISC', 'Zlib', 'Unlicense',
    'MPL-2.0', 'EPL-1.0', 'EPL-2.0', 'BSL-1.0', '0BSD', 'Artistic-2.0',
    'GPL-2.0', 'GPL-3.0', 'AGPL-3.0', 'LGPL-2.1', 'LGPL-3.0',
    'GPL-2.0-only', 'GPL-2.0-or-later', 'GPL-3.0-only', 'GPL-3.0-or-later',
    'AGPL-3.0-only', 'AGPL-3.0-or-later', 'LGPL-2.1-only', 'LGPL-2.1-or-later',
    'LGPL-3.0-only', 'LGPL-3.0-or-later',
})

_TOKEN = re.compile(r'AND|OR|\(|\)|[A-Za-z0-9][A-Za-z0-9.+-]*')
_LICENSE_PATH = re.compile(r'(?:LICEN[CS]E|COPYING)(?:[._-][A-Za-z0-9._-]+)?', re.I)


def license_document_path(path):
    return isinstance(path, str) and len(path) <= 128 and bool(_LICENSE_PATH.fullmatch(path))


def spdx_identifiers(expression):
    """Parse the supported SPDX AND/OR grammar and return its allowed IDs.

    WITH exceptions and LicenseRef IDs require manual review. No free-form
    substring match may turn an unrecognized expression into approval.
    """
    if not isinstance(expression, str) or not 1 <= len(expression) <= 256:
        raise ValueError('Unsupported SPDX license expression')
    tokens = []
    offset = 0
    while offset < len(expression):
        while offset < len(expression) and expression[offset].isspace():
            offset += 1
        if offset == len(expression):
            break
        match = _TOKEN.match(expression, offset)
        if match is None:
            raise ValueError('Unsupported SPDX license expression')
        tokens.append(match.group())
        offset = match.end()
        if len(tokens) > 32:
            raise ValueError('Unsupported SPDX license expression')
    position = 0

    def atom():
        nonlocal position
        if position >= len(tokens):
            raise ValueError('Unsupported SPDX license expression')
        token = tokens[position]
        position += 1
        if token == '(':
            ids = alternatives()
            if position >= len(tokens) or tokens[position] != ')':
                raise ValueError('Unsupported SPDX license expression')
            position += 1
            return ids
        if token not in RECOGNIZED_OPEN_SOURCE_LICENSES:
            raise ValueError('Unsupported SPDX license expression')
        return {token}

    def conjunction():
        nonlocal position
        ids = atom()
        while position < len(tokens) and tokens[position] == 'AND':
            position += 1
            ids.update(atom())
        return ids

    def alternatives():
        nonlocal position
        ids = conjunction()
        while position < len(tokens) and tokens[position] == 'OR':
            position += 1
            ids.update(conjunction())
        return ids

    identifiers = alternatives()
    if position != len(tokens):
        raise ValueError('Unsupported SPDX license expression')
    return identifiers


def project_license_expression(pyproject):
    if not isinstance(pyproject, str) or len(pyproject) > 256000:
        raise ValueError('Invalid project license metadata')
    try:
        project = tomllib.loads(pyproject).get('project')
    except tomllib.TOMLDecodeError as exc:
        raise ValueError('Invalid project license metadata') from exc
    expression = project.get('license') if isinstance(project, dict) else None
    if not isinstance(expression, str):
        raise ValueError('Explicit SPDX project license required')
    expression = expression.strip()
    spdx_identifiers(expression)
    return expression


def _blob(content):
    raw = content.encode('utf-8')
    return hashlib.sha1(b'blob ' + str(len(raw)).encode('ascii') + b'\x00' + raw).hexdigest()


def license_text_matches(identifier, content):
    """Recognize complete conventional texts conservatively, never pointers."""
    if not isinstance(content, str) or not 200 <= len(content) <= 256000:
        return False
    text = re.sub(r'\s+', ' ', content).lower()
    if identifier == 'Apache-2.0':
        return ('apache license version 2.0' in text
                and 'terms and conditions for use, reproduction, and distribution' in text
                and 'grant of copyright license' in text)
    if identifier in {'BSD-2-Clause', 'BSD-3-Clause'}:
        common = ('redistribution and use in source and binary forms' in text
                  and 'redistributions of source code must retain' in text
                  and 'redistributions in binary form must reproduce' in text
                  and 'this software is provided' in text)
        third = 'neither the name of' in text
        return common and (not third if identifier == 'BSD-2-Clause' else third)
    if identifier == 'MIT':
        return ('permission is hereby granted, free of charge' in text
                and 'to deal in the software without restriction' in text
                and 'the software is provided "as is"' in text)
    if identifier == 'ISC':
        return ('permission to use, copy, modify, and/or distribute this software' in text
                and 'the software is provided "as is"' in text)
    # Other allowlisted IDs remain eligible through the existing single-license
    # GitHub evidence path. Composite expressions need recognizable full texts.
    return False


def verified_expression_evidence(value):
    """Return the SPDX expression only if each license has pinned file proof."""
    if not isinstance(value, dict):
        raise ValueError('Repository license requires review')
    metadata = value.get('metadata')
    documents = value.get('documents')
    if (not isinstance(metadata, dict) or metadata.get('path') != 'pyproject.toml'
            or not isinstance(metadata.get('content'), str)
            or metadata.get('blob') != _blob(metadata['content'])
            or not isinstance(documents, list) or not 1 <= len(documents) <= 12):
        raise ValueError('Repository license requires review')
    expression = project_license_expression(metadata['content'])
    identifiers = spdx_identifiers(expression)
    if len(identifiers) < 2:
        raise ValueError('Composite SPDX license expression required')
    covered = set()
    for document in documents:
        if (not isinstance(document, dict) or not license_document_path(document.get('path'))
                or not isinstance(document.get('content'), str)
                or document.get('blob') != _blob(document['content'])):
            raise ValueError('Repository license requires review')
        for identifier in identifiers:
            if license_text_matches(identifier, document['content']):
                covered.add(identifier)
    if covered != identifiers:
        raise ValueError('Repository license requires review')
    return expression
