"""Inspect large tokenizer headers without allocating their discarded tail."""
import io
import struct

import pytest
from test_gguf_inspector import ARR, STR, U32, _enc_str, build_gguf

from gguf_inspector import inspect_gguf


def test_large_tokenizer_header_retains_following_architecture_with_bounded_reads(tmp_path, monkeypatch):
    path = tmp_path / "large-tokenizer.gguf"
    token = _enc_str("a" * 100)
    count = 100_000
    prefix = build_gguf([])[:-8] + struct.pack("<Q", 3)
    prefix += _enc_str("tokenizer.ggml.tokens") + struct.pack("<IIQ", ARR, STR, count)
    suffix = build_gguf([
        ("general.architecture", STR, "gpt-oss"),
        ("gpt-oss.context_length", U32, 131072),
    ])[24:]
    header = prefix + token * count + suffix
    path.write_bytes(header + b"TENSOR-PAYLOAD")
    opened = []
    original_open = type(path).open

    class TrackingFile(io.BufferedReader):
        total_read = 0
        furthest_read = 0

        def read(self, size=-1):
            data = super().read(size)
            self.total_read += len(data)
            self.furthest_read = max(self.furthest_read, self.tell())
            return data

    def tracked_open(self, *args, **kwargs):
        if self == path and args == ("rb",):
            handle = TrackingFile(io.FileIO(path, "r"))
            opened.append(handle)
            return handle
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(type(path), "open", tracked_open)
    result = inspect_gguf(path)

    assert result["readable"] is True
    assert result["architecture"] == "gpt-oss"
    assert result["context_length"] == 131072
    assert result["metadata"]["tokenizer.ggml.tokens"]["sample"] == ["a" * 100] * 64
    assert opened[0].total_read < 1024 * 1024
    assert opened[0].furthest_read == len(header)
    assert opened[0].closed


@pytest.mark.parametrize("kind", [STR, U32])
def test_truncated_unsampled_tail_is_rejected(tmp_path, kind):
    values = ["word"] * 70 if kind == STR else [1] * 70
    blob = build_gguf([("tokenizer.values", ARR, (kind, values))])
    path = tmp_path / "truncated.gguf"
    path.write_bytes(blob[:-1])
    result = inspect_gguf(path)
    assert result["readable"] is False
    assert "ended unexpectedly" in result["error"]


def test_sampled_value_cannot_exceed_the_read_budget(tmp_path):
    path = tmp_path / "long-value.gguf"
    path.write_bytes(build_gguf([("general.name", STR, "x" * 1024)]))
    assert inspect_gguf(path, max_metadata_bytes=128)["readable"] is False
