"""Inspect real file boundaries while bounding work on unsampled numeric arrays."""
import struct

import pytest
from test_gguf_inspector import ARR, STR, U32, _enc_str, build_gguf

import gguf_inspector


@pytest.mark.parametrize('item_type,fmt,value', [
    (0, '<B', 3), (1, '<b', -3), (2, '<H', 300), (3, '<h', -300),
    (4, '<I', 1000), (5, '<i', -1000), (6, '<f', 1.5), (7, '<?', True),
    (10, '<Q', 2**40), (11, '<q', -(2**40)), (12, '<d', 1.5),
])
def test_fixed_width_tail_preserves_sample_and_following_metadata(tmp_path, monkeypatch, item_type, fmt, value):
    count = 131072
    data = b'GGUF' + struct.pack('<IQQ', 3, 0, 2)
    data += _enc_str('tokenizer.values') + struct.pack('<IIQ', ARR, item_type, count)
    data += struct.pack(fmt, value) * count
    data += _enc_str('general.architecture') + struct.pack('<I', STR) + _enc_str('llama')
    path = tmp_path / 'model.gguf'
    path.write_bytes(data)
    # The public inspector must not visit every value that it does not return.
    original = gguf_inspector._skip_value
    calls = []
    def counted(*args):
        calls.append(1)
        return original(*args)
    monkeypatch.setattr(gguf_inspector, '_skip_value', counted)
    result = gguf_inspector.inspect_gguf(path)
    assert result['readable'] and result['architecture'] == 'llama'
    assert result['metadata']['tokenizer.values']['sample'] == [value] * 64
    assert result['metadata']['tokenizer.values']['length'] == count
    assert len(calls) <= 64


@pytest.mark.parametrize('nested', [False, True])
def test_truncated_unsampled_array_still_fails(tmp_path, nested):
    values = (ARR, [(U32, [1, 2, 3])] * 65) if nested else (U32, list(range(100)))
    path = tmp_path / 'truncated.gguf'
    path.write_bytes(build_gguf([('values', ARR, values)])[:-1])
    result = gguf_inspector.inspect_gguf(path)
    assert not result['readable']
    assert result['error'] == 'GGUF metadata ended unexpectedly'


def test_skipped_nested_fixed_width_arrays_leave_next_key_aligned(tmp_path):
    path = tmp_path / 'nested.gguf'
    path.write_bytes(build_gguf([
        ('values', ARR, (ARR, [(U32, list(range(100)))] * 65)),
        ('general.architecture', STR, 'llama'),
    ]))
    result = gguf_inspector.inspect_gguf(path)
    assert result['readable'] and result['architecture'] == 'llama'
    assert result['metadata']['values']['length'] == 65
