"""Verify that a multimodal turn carries the exact archived image identities."""
import base64
import binascii
import hashlib
import re


MAX_IMAGE_BYTES = 8 * 1024 * 1024
_DATA = re.compile(r"data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/]*={0,2})", re.ASCII)


def matches_archived_message(message, archived):
    if not isinstance(message, dict) or not isinstance(archived, dict):
        return False
    references = archived.get("images")
    if references is None:
        return message == archived
    if (set(message) != {"role", "content", "images"} or message["role"] != "user"
            or message["images"] != references or not isinstance(message["content"], list)):
        return False
    parts = message["content"]
    text = archived.get("content")
    if not isinstance(text, str):
        return False
    if text:
        if not parts or parts[0] != {"type": "text", "text": text}:
            return False
        parts = parts[1:]
    if not isinstance(references, list) or not 1 <= len(references) <= 4 or len(parts) != len(references):
        return False
    total = 0
    for part, reference in zip(parts, references):
        if (not isinstance(part, dict) or set(part) != {"type", "image_url"}
                or part["type"] != "image_url" or not isinstance(part["image_url"], dict)
                or set(part["image_url"]) != {"url"} or not isinstance(part["image_url"]["url"], str)):
            return False
        url = part["image_url"]["url"]
        if len(url) > 4 * ((MAX_IMAGE_BYTES + 2) // 3) + 32:
            return False
        match = _DATA.fullmatch(url)
        if match is None:
            return False
        try:
            data = base64.b64decode(match[2], validate=True)
        except (ValueError, binascii.Error):
            return False
        total += len(data)
        if not data or total > MAX_IMAGE_BYTES or base64.b64encode(data).decode("ascii") != match[2]:
            return False
        if not isinstance(reference, dict) or hashlib.sha256(data).hexdigest() != reference.get("sha256"):
            return False
    return True
