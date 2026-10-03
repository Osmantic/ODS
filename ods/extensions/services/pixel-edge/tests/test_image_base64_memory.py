"""Preserve the previous full-reencode acceptance without its large copies."""
import base64
import binascii
import hashlib
import random
import re

import pytest

from image_message import matches_archived_message


def accepted(encoded, digest=None):
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raw = b""
    reference = {"id": "img-" + "a" * 32,
                 "sha256": digest or hashlib.sha256(raw).hexdigest()}
    archived = {"role": "user", "content": "", "images": [reference]}
    current = {**archived, "content": [{"type": "image_url", "image_url": {
        "url": "data:image/png;base64," + encoded}}]}
    return matches_archived_message(current, archived)


@pytest.mark.parametrize("encoded", ["eB==", "eHh=", "eA===", "eHh4=", "eHh4==",
                                     "e=A=", "eA==eA==", "eA", "eHh", "e", "", "eA==\n"])
def test_noncanonical_padding_bits_lengths_and_interior_padding_rejected(encoded):
    assert not accepted(encoded)


@pytest.mark.parametrize("raw", [b"x", b"xx", b"xxx", b"xxxx", b"\0\xff\0"])
def test_canonical_lengths_and_exact_hash_still_required(raw):
    encoded = base64.b64encode(raw).decode("ascii")
    assert accepted(encoded)
    assert not accepted(encoded, "f" * 64)


def test_tail_check_matches_previous_whole_reencode_over_varied_corpus():
    rng = random.Random(93817)
    for size in [*range(130), 1024, 4097, 65536]:
        canonical = base64.b64encode(rng.randbytes(size)).decode("ascii")
        variants = [canonical, canonical + "=", canonical + "==", canonical.rstrip("="),
                    canonical[:3] + "=" + canonical[3:], canonical + "\n"]
        if len(canonical) >= 4:
            for tail in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/":
                position = -3 if canonical.endswith("==") else -2 if canonical.endswith("=") else -1
                variants.append(canonical[:position] + tail + canonical[position + 1:] if position < -1
                                else canonical[:-1] + tail)
        for encoded in variants:
            try:
                decoded = base64.b64decode(encoded, validate=True)
                previous = bool(decoded) and re.fullmatch(r"[A-Za-z0-9+/]*={0,2}", encoded) is not None \
                    and base64.b64encode(decoded).decode("ascii") == encoded
            except (ValueError, binascii.Error):
                previous = False
            assert accepted(encoded) == previous, (size, encoded[-12:])
