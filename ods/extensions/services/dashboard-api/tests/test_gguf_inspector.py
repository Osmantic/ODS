"""Unit tests for the GGUF metadata parser in ``gguf_inspector``.

The parser drives model fit/performance reporting in ``performance_oracle`` but
had no direct coverage — existing suites only write placeholder ``.gguf`` files
whose magic bytes fail the header check, so the actual metadata-parsing paths
(scalars, strings, arrays, graceful degradation) were never exercised.

These tests build synthetic-but-spec-accurate GGUF byte streams so the happy
path and every failure mode are pinned down.
"""

import struct

import pytest

from gguf_inspector import inspect_gguf

# GGUF value type ids (see gguf_inspector._GGUF_VALUE_TYPES).
U8, I8, U16, I16, U32, I32, F32, BOOL, STR, ARR, U64, I64, F64 = range(13)

_SCALAR_FMT = {
    U8: "<B", I8: "<b", U16: "<H", I16: "<h", U32: "<I",
    I32: "<i", F32: "<f", BOOL: "<?", U64: "<Q", I64: "<q", F64: "<d",
}


# ── Synthetic GGUF encoder (mirror of the parser's expected layout) ──────────

def _enc_str(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack("<Q", len(b)) + b


def _enc_value(vtype: int, value) -> bytes:
    if vtype in _SCALAR_FMT:
        return struct.pack(_SCALAR_FMT[vtype], value)
    if vtype == STR:
        return _enc_str(value)
    if vtype == ARR:
        item_type, items = value
        out = struct.pack("<I", item_type) + struct.pack("<Q", len(items))
        for item in items:
            out += _enc_value(item_type, item)
        return out
    raise ValueError(f"unencodable type: {vtype}")


def build_gguf(kvs, *, tensor_count=0, version=3, magic=b"GGUF") -> bytes:
    """Encode ``kvs`` (list of ``(key, value_type, value)``) as a GGUF blob."""
    out = magic + struct.pack("<I", version)
    out += struct.pack("<Q", tensor_count) + struct.pack("<Q", len(kvs))
    for key, vtype, value in kvs:
        out += _enc_str(key) + struct.pack("<I", vtype) + _enc_value(vtype, value)
    return out


def _write(tmp_path, name, data: bytes):
    p = tmp_path / name
    p.write_bytes(data)
    return p


def _nested_array(levels: int):
    """Build a value tuple for ``levels`` nested arrays wrapping a single u8."""
    value = (U8, [0])
    for _ in range(levels - 1):
        value = (ARR, [value])
    return value


# ── Happy path ───────────────────────────────────────────────────────────────

def test_full_metadata_is_normalized(tmp_path):
    kvs = [
        ("general.architecture", STR, "llama"),
        ("general.name", STR, "Test Model 7B"),
        ("general.file_type", U32, 15),  # Q4_K_M
        ("llama.context_length", U32, 4096),
        ("llama.block_count", U32, 32),
        ("llama.embedding_length", U32, 4096),
        ("llama.attention.head_count", U32, 32),
        ("llama.attention.head_count_kv", U32, 8),
    ]
    path = _write(tmp_path, "model.gguf", build_gguf(kvs, tensor_count=291))

    result = inspect_gguf(path)

    assert result["readable"] is True
    assert result["format"] == "gguf"
    assert result["version"] == 3
    assert result["tensor_count"] == 291
    assert result["metadata_count"] == len(kvs)
    assert result["architecture"] == "llama"
    assert result["model_name"] == "Test Model 7B"
    assert result["file_type"] == 15
    assert result["quantization"] == "Q4_K_M"
    assert result["context_length"] == 4096
    assert result["block_count"] == 32
    assert result["embedding_length"] == 4096
    assert result["attention_head_count"] == 32
    assert result["attention_head_count_kv"] == 8
    assert result["size_bytes"] == path.stat().st_size


def test_expert_count_accepts_alternate_suffix(tmp_path):
    # Some exporters use ``.expert.count`` instead of ``.expert_count``.
    path = _write(tmp_path, "moe.gguf", build_gguf([
        ("general.architecture", STR, "qwen3moe"),
        ("qwen3moe.expert.count", U32, 128),
        ("qwen3moe.expert.used_count", U32, 8),
    ]))

    result = inspect_gguf(path)

    assert result["expert_count"] == 128
    assert result["expert_used_count"] == 8


@pytest.mark.parametrize("vtype,value", [
    (U8, 200), (I8, -5), (U16, 40000), (I16, -300),
    (U32, 4096), (I32, -70000), (U64, 2 ** 40), (I64, -(2 ** 40)),
])
def test_integer_scalar_types_round_trip(tmp_path, vtype, value):
    path = _write(tmp_path, "scalars.gguf", build_gguf([
        ("general.architecture", STR, "llama"),
        ("llama.context_length", vtype, value),
    ]))

    assert inspect_gguf(path)["context_length"] == value


def test_float_and_bool_scalars_are_preserved_in_metadata(tmp_path):
    path = _write(tmp_path, "misc.gguf", build_gguf([
        ("general.architecture", STR, "llama"),
        ("some.f32", F32, 1.5),
        ("some.f64", F64, 2.25),
        ("some.flag", BOOL, True),
    ]))

    meta = inspect_gguf(path)["metadata"]

    assert meta["some.f32"] == pytest.approx(1.5)
    assert meta["some.f64"] == pytest.approx(2.25)
    assert meta["some.flag"] is True


# ── Arrays ───────────────────────────────────────────────────────────────────

def test_small_array_is_returned_inline(tmp_path):
    path = _write(tmp_path, "arr.gguf", build_gguf([
        ("general.architecture", STR, "llama"),
        ("small.ints", ARR, (U32, [1, 2, 3])),
    ]))

    assert inspect_gguf(path)["metadata"]["small.ints"] == [1, 2, 3]


def test_large_array_is_sampled_not_fully_materialized(tmp_path):
    tokens = [f"tok{i}" for i in range(100)]
    path = _write(tmp_path, "vocab.gguf", build_gguf([
        ("general.architecture", STR, "llama"),
        ("tokenizer.ggml.tokens", ARR, (STR, tokens)),
    ]))

    entry = inspect_gguf(path)["metadata"]["tokenizer.ggml.tokens"]

    assert entry["type"] == "array"
    assert entry["item_type"] == "string"
    assert entry["length"] == 100
    assert entry["sample"] == tokens[:64]


def test_array_boundary_of_sample_limit_stays_inline(tmp_path):
    # Exactly at the 64-element sample limit → still returned as a plain list.
    path = _write(tmp_path, "edge.gguf", build_gguf([
        ("general.architecture", STR, "llama"),
        ("edge.ints", ARR, (U8, list(range(64)))),
    ]))

    assert inspect_gguf(path)["metadata"]["edge.ints"] == list(range(64))


def test_shallow_nested_array_still_parses(tmp_path):
    # Nesting is legal in the GGUF spec; a shallow chain must round-trip.
    path = _write(tmp_path, "nested.gguf", build_gguf([
        ("general.architecture", STR, "llama"),
        ("nested.arr", ARR, _nested_array(4)),
    ]))

    result = inspect_gguf(path)

    assert result["readable"] is True
    assert result["metadata"]["nested.arr"] == [[[[0]]]]


def test_deeply_nested_array_degrades_without_recursion_error(tmp_path):
    # A pathologically nested array must not crash the parser (RecursionError
    # escaping the failure contract) — it degrades to a bounded-depth error.
    path = _write(tmp_path, "deep.gguf", build_gguf([
        ("general.architecture", STR, "llama"),
        ("deep.arr", ARR, _nested_array(500)),
    ]))

    result = inspect_gguf(path)

    assert result["readable"] is False
    assert result["architecture"] == "unknown"
    assert "too deep" in result["error"]


# ── file_type / quantization normalization ──────────────────────────────────

def test_unknown_file_type_falls_back_to_stringified_int(tmp_path):
    path = _write(tmp_path, "unk.gguf", build_gguf([
        ("general.architecture", STR, "gpt2"),
        ("general.file_type", U32, 999),
    ]))

    assert inspect_gguf(path)["quantization"] == "999"


def test_missing_file_type_yields_unknown_quantization(tmp_path):
    path = _write(tmp_path, "noft.gguf", build_gguf([
        ("general.architecture", STR, "llama"),
    ]))

    result = inspect_gguf(path)

    assert result["file_type"] is None
    assert result["quantization"] == "unknown"


def test_non_string_architecture_degrades_to_unknown(tmp_path):
    # A malformed export storing architecture as an int must not leak the int.
    path = _write(tmp_path, "weird.gguf", build_gguf([
        ("general.architecture", U32, 7),
    ]))

    assert inspect_gguf(path)["architecture"] == "unknown"


def test_invalid_utf8_string_is_replaced_not_raised(tmp_path):
    blob = bytearray(build_gguf([("general.name", STR, "ok")]))
    # Corrupt the "ok" bytes (last two) into an invalid UTF-8 sequence.
    blob[-2:] = b"\xff\xfe"
    path = _write(tmp_path, "badutf.gguf", bytes(blob))

    result = inspect_gguf(path)

    assert result["readable"] is True
    assert result["model_name"] == "��"


# ── Graceful degradation ─────────────────────────────────────────────────────

def test_non_gguf_magic_reports_error(tmp_path):
    path = _write(tmp_path, "bad.gguf", b"NOPE" + b"\x00" * 32)

    result = inspect_gguf(path)

    assert result["readable"] is False
    assert result["error"] == "not a GGUF file"


def test_truncated_metadata_degrades_without_raising(tmp_path):
    full = build_gguf([
        ("general.architecture", STR, "llama"),
        ("llama.context_length", U32, 4096),
    ])
    path = _write(tmp_path, "trunc.gguf", full[:20])

    result = inspect_gguf(path)

    assert result["readable"] is False
    assert result["architecture"] == "unknown"
    assert "error" in result


def test_metadata_beyond_read_cap_degrades_gracefully(tmp_path):
    path = _write(tmp_path, "big.gguf", build_gguf([
        ("general.architecture", STR, "llama"),
        ("blob", ARR, (U8, list(range(64)) + [0] * 4096)),
    ]))

    # Cap the read well below the array payload so the parser runs out of bytes.
    result = inspect_gguf(path, max_metadata_bytes=64)

    assert result["readable"] is False
    assert "error" in result


def test_missing_file_reports_not_readable(tmp_path):
    result = inspect_gguf(tmp_path / "nope.gguf")

    assert result["exists"] is False
    assert result["readable"] is False
    assert result["architecture"] == "unknown"


def test_directory_path_is_not_treated_as_file(tmp_path):
    d = tmp_path / "adir.gguf"
    d.mkdir()

    result = inspect_gguf(d)

    assert result["readable"] is False


def test_accepts_str_path_as_well_as_path_object(tmp_path):
    path = _write(tmp_path, "strpath.gguf", build_gguf([
        ("general.architecture", STR, "llama"),
    ]))

    result = inspect_gguf(str(path))

    assert result["readable"] is True
    assert result["path"] == str(path)
