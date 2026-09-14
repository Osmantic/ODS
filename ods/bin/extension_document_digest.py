"""Canonical YAML/JSON document digests for extension provenance.

Catalog generation and future host-side artifact verification must agree on
the semantic bytes behind manifest and Compose digests.  This module owns that
one transformation.  It reads no paths and has no import-time effects; callers
retain filesystem custody and size-limit responsibility.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

import yaml


class CanonicalDocumentError(ValueError):
    """A stable, value-free canonicalization failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects ambiguous duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found duplicate mapping key",
                key_node.start_mark,
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _fail(code: str, message: str) -> None:
    raise CanonicalDocumentError(code, message) from None


def _source_text(source: bytes | str) -> str:
    if isinstance(source, bytes):
        try:
            return source.decode("utf-8", errors="strict")
        except UnicodeError:
            _fail("canonical-document-parse-error", "document is not valid UTF-8")
    if isinstance(source, str):
        return source
    _fail("canonical-document-input", "document input must be bytes or text")


def _validate_unicode(value: str) -> None:
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        _fail(
            "canonical-document-invalid-unicode",
            "document contains invalid Unicode",
        )


def _validate_json_document(value: object, active: set[int] | None = None) -> None:
    """Reject YAML-only values without one portable JSON identity."""

    if active is None:
        active = set()
    if value is None or type(value) in {str, bool, int}:
        if isinstance(value, str):
            _validate_unicode(value)
        return
    if type(value) is float:
        if not math.isfinite(value):
            _fail(
                "canonical-document-non-finite-number",
                "document contains a non-finite number",
            )
        return
    if isinstance(value, (list, dict)):
        identity = id(value)
        if identity in active:
            _fail(
                "canonical-document-cyclic-alias",
                "document contains a cyclic YAML alias",
            )
        active.add(identity)
        if isinstance(value, dict):
            if any(not isinstance(key, str) for key in value):
                _fail(
                    "canonical-document-non-string-key",
                    "document contains a non-string object key",
                )
            for key in value:
                _validate_unicode(key)
            children = value.values()
        else:
            children = value
        for child in children:
            _validate_json_document(child, active)
        active.remove(identity)
        return
    _fail(
        "canonical-document-unsupported-value",
        f"document contains unsupported YAML value: {type(value).__name__}",
    )


def canonical_document_bytes(source: bytes | str) -> bytes:
    """Return the exact semantic bytes used by extension provenance hashes."""

    try:
        value = yaml.load(_source_text(source), Loader=_UniqueKeyLoader)
    except CanonicalDocumentError:
        raise
    except RecursionError as exc:
        raise CanonicalDocumentError(
            "canonical-document-too-deep",
            "document nesting is too deep",
        ) from exc
    except yaml.YAMLError as exc:
        raise CanonicalDocumentError(
            "canonical-document-parse-error",
            "document cannot be parsed as YAML or JSON",
        ) from exc
    try:
        _validate_json_document(value)
    except RecursionError as exc:
        raise CanonicalDocumentError(
            "canonical-document-too-deep",
            "document nesting is too deep",
        ) from exc
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return (serialized + "\n").encode("utf-8", errors="strict")
    except RecursionError as exc:
        raise CanonicalDocumentError(
            "canonical-document-too-deep",
            "document nesting is too deep",
        ) from exc
    except (TypeError, ValueError, UnicodeError) as exc:
        raise CanonicalDocumentError(
            "canonical-document-encode-error",
            "document cannot be encoded canonically",
        ) from exc


def canonical_document_sha256(source: bytes | str) -> str:
    """Return a prefixed SHA-256 over canonical document semantics."""

    digest = hashlib.sha256(canonical_document_bytes(source)).hexdigest()
    return f"sha256:{digest}"


__all__ = [
    "CanonicalDocumentError",
    "canonical_document_bytes",
    "canonical_document_sha256",
]
