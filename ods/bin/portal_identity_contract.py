"""Pure Portal display-name contract: shared, copy-ready, no I/O, no side effects.

Display names are inert strings only: never instructions, prompts, or machine
identifiers. The 2048-byte HTTP request envelope boundary is enforced by the
caller before these functions are reached.
"""
from __future__ import annotations

import unicodedata

DEFAULT_NAME = "Portal"
MAX_REVISION = 2**53 - 1
MAX_NAME_CODEPOINTS = 60
MAX_RAW_NAME_CODEPOINTS = 240
REQUEST_ENVELOPE_MAX_BYTES = 2048  # caller-enforced

_FORBIDDEN_CATEGORIES = {"Cc", "Cs", "Zl", "Zp"}
_ALLOWED_FORMAT = "\u200c\u200d"  # ZWNJ/ZWJ joiners; every other Cf code is rejected


def normalize_name(value):
    if type(value) is not str:
        raise ValueError("invalid-name")
    if len(value) > MAX_RAW_NAME_CODEPOINTS:
        raise ValueError("name-too-long")
    name = unicodedata.normalize("NFC", value)
    for char in name:
        category = unicodedata.category(char)
        if category in _FORBIDDEN_CATEGORIES or (category == "Cf" and char not in _ALLOWED_FORMAT):
            raise ValueError("invalid-name-character")
    name = name.strip() or DEFAULT_NAME
    if len(name) > MAX_NAME_CODEPOINTS:
        raise ValueError("name-too-long")
    return name


def default_document():
    return {"schemaVersion": 1, "revision": 0, "displayName": DEFAULT_NAME}


def normalize_document(value):
    if (type(value) is not dict or set(value) != {"schemaVersion", "revision", "displayName"}
            or type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1
            or type(value["revision"]) is not int
            or not 0 <= value["revision"] <= MAX_REVISION):
        raise ValueError("invalid-document")
    name = normalize_name(value["displayName"])
    # Stored data must already be canonical; corrupt names are rejected, never rewritten.
    if name != value["displayName"]:
        raise ValueError("non-canonical-display-name")
    return {"schemaVersion": 1, "revision": value["revision"], "displayName": name}


def normalize_edit(value):
    if (type(value) is not dict or set(value) != {"expectedRevision", "displayName"}
            or type(value["expectedRevision"]) is not int
            or not 0 <= value["expectedRevision"] < MAX_REVISION):
        raise ValueError("invalid-edit")
    return {"expectedRevision": value["expectedRevision"],
            "displayName": normalize_name(value["displayName"])}
