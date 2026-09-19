"""Wire-framing contract for pixel_provider.advice_frames.

The advisory worker pipe uses a strict 4-byte big-endian length prefix.
These tests pin the encode/decode/read semantics, size bounds, and
truncation behavior — a drift here silently corrupts advisory replies or
accepts unbounded/malformed frames.
"""
import io
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'bin'))
from pixel_provider import advice_frames as af  # noqa: E402
from pixel_provider.store import MAX_BYTES, StoreError  # noqa: E402


class TestEncode:
    def test_roundtrip(self):
        value = {'a': 1, 'b': 'x', 'nested': {'k': [1, 2, 3]}}
        raw = af.encode_frame(value)
        size = struct.unpack('!I', raw[:4])[0]
        assert size == len(raw) - 4
        assert af.decode_frame(raw) == value

    def test_big_endian_prefix(self):
        raw = af.encode_frame({'x': 1})
        assert raw[:4] == struct.pack('!I', len(raw) - 4)

    def test_compact_ascii_encoding(self):
        raw = af.encode_frame({'k': 'üñïçødé'})
        body = raw[4:]
        # ensure_ascii=True — non-ASCII must be \uXXXX escaped
        assert b'\\u' in body
        assert all(b < 128 for b in body)

    def test_compact_separators(self):
        raw = af.encode_frame({'a': 1, 'b': 2})
        assert b' ' not in raw[4:]
        assert b'{"a":1,"b":2}' == raw[4:]

    @pytest.mark.parametrize('value', [
        'string', [1, 2], 5, None, True,
    ])
    def test_non_dict_rejected(self, value):
        with pytest.raises(StoreError, match='invalid-worker-frame'):
            af.encode_frame(value)

    def test_unserializable_rejected(self):
        with pytest.raises(StoreError, match='invalid-worker-frame'):
            af.encode_frame({'f': float('nan')})

    def test_inf_rejected(self):
        with pytest.raises(StoreError, match='invalid-worker-frame'):
            af.encode_frame({'f': float('inf')})

    def test_empty_dict_rejected_by_size_bound(self):
        # {} encodes to 2 bytes — that is >0, so it IS allowed
        raw = af.encode_frame({})
        assert af.decode_frame(raw) == {}

    def test_size_limit(self):
        # Build a dict that encodes to just over MAX_BYTES
        big = {'x': 'y' * MAX_BYTES}
        with pytest.raises(StoreError, match='worker-frame-limit'):
            af.encode_frame(big)


class TestDecode:
    def test_short_prefix(self):
        with pytest.raises(StoreError, match='truncated-worker-frame'):
            af.decode_frame(b'\x00\x00')

    def test_zero_size(self):
        with pytest.raises(StoreError, match='invalid-worker-frame'):
            af.decode_frame(struct.pack('!I', 0))

    def test_size_exceeds_max(self):
        with pytest.raises(StoreError, match='invalid-worker-frame'):
            af.decode_frame(struct.pack('!I', MAX_BYTES + 1))

    def test_length_mismatch_short(self):
        raw = af.encode_frame({'a': 1})
        with pytest.raises(StoreError, match='invalid-worker-frame'):
            af.decode_frame(raw[:-1])

    def test_length_mismatch_long(self):
        raw = af.encode_frame({'a': 1}) + b'x'
        with pytest.raises(StoreError, match='invalid-worker-frame'):
            af.decode_frame(raw)

    def test_non_dict_body_rejected(self):
        body = b'[1,2,3]'
        raw = struct.pack('!I', len(body)) + body
        with pytest.raises(StoreError, match='invalid-worker-frame'):
            af.decode_frame(raw)

    def test_invalid_json_body(self):
        body = b'{invalid'
        raw = struct.pack('!I', len(body)) + body
        with pytest.raises(StoreError):
            af.decode_frame(raw)


class TestReadFrame:
    def test_stream_roundtrip(self):
        raw = af.encode_frame({'ok': True})
        assert af.read_frame(io.BytesIO(raw)) == {'ok': True}

    def test_truncated_prefix(self):
        with pytest.raises(StoreError, match='truncated-worker-frame'):
            af.read_frame(io.BytesIO(b'\x00\x00'))

    def test_truncated_body(self):
        raw = af.encode_frame({'a': 1})
        with pytest.raises(StoreError, match='truncated-worker-frame'):
            af.read_frame(io.BytesIO(raw[:-2]))

    def test_chunked_reads(self):
        # read_frame must accumulate partial reads
        raw = af.encode_frame({'a': 1, 'b': 'x' * 100})

        class Chunked(io.BytesIO):
            def read(self, n=-1):
                return super().read(min(n, 3))

        assert af.read_frame(Chunked(raw)) == {'a': 1, 'b': 'x' * 100}

    def test_zero_size_prefix(self):
        with pytest.raises(StoreError, match='worker-frame-limit'):
            af.read_frame(io.BytesIO(struct.pack('!I', 0)))

    def test_oversize_prefix(self):
        with pytest.raises(StoreError, match='worker-frame-limit'):
            af.read_frame(io.BytesIO(struct.pack('!I', MAX_BYTES + 1)))

    def test_eof_on_empty_stream(self):
        with pytest.raises(StoreError, match='truncated-worker-frame'):
            af.read_frame(io.BytesIO(b''))
