"""Strict bounded, single-document framing for a private advisory pipe."""
import json
import struct

from .store import MAX_BYTES,StoreError,decode_document


def encode_frame(value):
    if not isinstance(value,dict):
        raise StoreError('invalid-worker-frame')
    try:
        raw = json.dumps(value,ensure_ascii=True,allow_nan=False,separators=(',',':')).encode('utf-8')
    except (ValueError,TypeError,RecursionError):
        raise StoreError('invalid-worker-frame') from None
    if not 0 < len(raw) <= MAX_BYTES:
        raise StoreError('worker-frame-limit')
    return struct.pack('!I',len(raw))+raw


def decode_frame(raw):
    if len(raw) < 4:
        raise StoreError('truncated-worker-frame')
    size = struct.unpack('!I',raw[:4])[0]
    if not 0 < size <= MAX_BYTES or len(raw) != size+4:
        raise StoreError('invalid-worker-frame')
    value = decode_document(raw[4:])
    if not isinstance(value,dict):
        raise StoreError('invalid-worker-frame')
    return value


def read_frame(stream):
    def exact(size):
        value = bytearray()
        while len(value) < size:
            part = stream.read(size-len(value))
            if not part:
                raise StoreError('truncated-worker-frame')
            value.extend(part)
        return bytes(value)
    prefix = exact(4)
    size = struct.unpack('!I',prefix)[0]
    if not 0 < size <= MAX_BYTES:
        raise StoreError('worker-frame-limit')
    return decode_frame(prefix+exact(size))
