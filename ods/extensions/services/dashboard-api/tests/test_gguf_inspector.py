from pathlib import Path
import struct

from gguf_inspector import _first_int, inspect_gguf


def _pack_string(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack("<Q", len(b)) + b


def _build_minimal_gguf(metadata_uint32s: dict[str, int]) -> bytes:
    data = b"GGUF"
    data += struct.pack("<I", 3)  # version
    data += struct.pack("<Q", 0)  # tensor count
    data += struct.pack("<Q", len(metadata_uint32s))  # metadata count

    for key, value in metadata_uint32s.items():
        data += _pack_string(key)
        data += struct.pack("<I", 4)  # value_type = 4 (uint32)
        data += struct.pack("<I", value)

    return data


def test_gguf_sliding_window_extraction(tmp_path: Path):
    gguf_path = tmp_path / "test.gguf"
    
    # Build minimal valid GGUF with uint32 values
    content = _build_minimal_gguf({
        "llama.block_count": 32,
        "llama.attention.head_count": 32,
        "llama.attention.sliding_window": 4096,
    })
    
    gguf_path.write_bytes(content)
    
    result = inspect_gguf(gguf_path)
    
    assert result["readable"] is True
    assert result["attention_sliding_window"] == 4096


def test_first_int_suffix_matching():
    # Test valid extraction
    dict_with_swa = {
        "some.other.key": 123,
        "llama.attention.sliding_window": 8192,
    }
    assert _first_int(dict_with_swa, (".attention.sliding_window",)) == 8192

    # Test regression guard: absent key must yield None
    dict_without_swa = {
        "llama.block_count": 32,
        "llama.attention.head_count": 32,
    }
    assert _first_int(dict_without_swa, (".attention.sliding_window",)) is None
