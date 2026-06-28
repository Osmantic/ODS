"""Read-only GGUF metadata inspection.

The dashboard only needs lightweight model metadata for fit/performance
reporting. This parser intentionally stops after the GGUF metadata header and
never reads tensor data, so it is safe to run against very large model files.
"""

from __future__ import annotations

import logging
import struct
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_GGUF_VALUE_TYPES = {
    0: "uint8",
    1: "int8",
    2: "uint16",
    3: "int16",
    4: "uint32",
    5: "int32",
    6: "float32",
    7: "bool",
    8: "string",
    9: "array",
    10: "uint64",
    11: "int64",
    12: "float64",
}

_STRUCTS = {
    0: "<B",
    1: "<b",
    2: "<H",
    3: "<h",
    4: "<I",
    5: "<i",
    6: "<f",
    7: "<?",
    10: "<Q",
    11: "<q",
    12: "<d",
}

_FILE_TYPE_LABELS = {
    0: "F32",
    1: "F16",
    2: "Q4_0",
    3: "Q4_1",
    6: "Q5_0",
    7: "Q5_1",
    8: "Q8_0",
    10: "Q2_K",
    11: "Q3_K_S",
    12: "Q3_K_M",
    13: "Q3_K_L",
    14: "Q4_K_S",
    15: "Q4_K_M",
    16: "Q5_K_S",
    17: "Q5_K_M",
    18: "Q6_K",
    19: "IQ2_XXS",
    20: "IQ2_XS",
    21: "Q2_K_S",
    22: "IQ3_XS",
    23: "IQ3_XXS",
    24: "IQ1_S",
    25: "IQ4_NL",
    26: "IQ3_S",
    27: "IQ3_M",
    28: "IQ2_S",
    29: "IQ2_M",
    30: "IQ4_XS",
    31: "IQ1_M",
    32: "BF16",
    33: "TQ1_0",
    34: "TQ2_0",
}


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0

    def read(self, size: int) -> bytes:
        if self.offset + size > len(self.data):
            raise ValueError("GGUF metadata ended unexpectedly")
        chunk = self.data[self.offset:self.offset + size]
        self.offset += size
        return chunk

    def skip(self, size: int) -> None:
        self.read(size)

    def unpack(self, fmt: str):
        size = struct.calcsize(fmt)
        return struct.unpack(fmt, self.read(size))[0]

    def string(self) -> str:
        length = self.unpack("<Q")
        return self.read(length).decode("utf-8", errors="replace")


def _read_value(reader: _Reader, value_type: int) -> Any:
    if value_type in _STRUCTS:
        return reader.unpack(_STRUCTS[value_type])
    if value_type == 8:
        return reader.string()
    if value_type == 9:
        return _read_array(reader)
    raise ValueError(f"unsupported GGUF value type: {value_type}")


def _skip_value(reader: _Reader, value_type: int) -> None:
    if value_type in _STRUCTS:
        reader.skip(struct.calcsize(_STRUCTS[value_type]))
        return
    if value_type == 8:
        length = reader.unpack("<Q")
        reader.skip(length)
        return
    if value_type == 9:
        item_type = reader.unpack("<I")
        length = reader.unpack("<Q")
        for _ in range(length):
            _skip_value(reader, item_type)
        return
    raise ValueError(f"unsupported GGUF value type: {value_type}")


def _read_array(reader: _Reader) -> Any:
    item_type = reader.unpack("<I")
    length = reader.unpack("<Q")
    if item_type not in _STRUCTS and item_type not in (8, 9):
        raise ValueError(f"unsupported GGUF array type: {item_type}")

    sample_limit = 64
    sample = [_read_value(reader, item_type) for _ in range(min(length, sample_limit))]
    for _ in range(max(length - sample_limit, 0)):
        _skip_value(reader, item_type)

    if length <= sample_limit:
        return sample
    return {
        "type": "array",
        "item_type": _GGUF_VALUE_TYPES.get(item_type, str(item_type)),
        "length": length,
        "sample": sample,
    }


def _first_int(metadata: dict[str, Any], suffixes: tuple[str, ...]) -> int | None:
    for key, value in metadata.items():
        if key.endswith(suffixes) and isinstance(value, int):
            return value
    return None


def _first_value(metadata: dict[str, Any], suffixes: tuple[str, ...]) -> Any:
    for key, value in metadata.items():
        if key.endswith(suffixes):
            return value
    return None


def inspect_gguf(path: Path | str, max_metadata_bytes: int = 8 * 1024 * 1024) -> dict[str, Any]:
    """Return normalized GGUF metadata, degrading to ``unknown`` on failure."""
    p = Path(path)
    result: dict[str, Any] = {
        "path": str(p),
        "exists": p.exists(),
        "format": "gguf",
        "readable": False,
        "architecture": "unknown",
        "quantization": "unknown",
        "metadata": {},
    }
    if not p.exists() or not p.is_file():
        return result

    try:
        result["size_bytes"] = p.stat().st_size
        with p.open("rb") as f:
            data = f.read(max_metadata_bytes)
        reader = _Reader(data)
        if reader.read(4) != b"GGUF":
            result["error"] = "not a GGUF file"
            return result
        version = reader.unpack("<I")
        tensor_count = reader.unpack("<Q")
        metadata_count = reader.unpack("<Q")
        metadata: dict[str, Any] = {}
        for _ in range(metadata_count):
            key = reader.string()
            value_type = reader.unpack("<I")
            metadata[key] = _read_value(reader, value_type)

        file_type = metadata.get("general.file_type")
        architecture = metadata.get("general.architecture", "unknown")
        result.update({
            "readable": True,
            "version": version,
            "tensor_count": tensor_count,
            "metadata_count": metadata_count,
            "architecture": architecture if isinstance(architecture, str) else "unknown",
            "file_type": file_type,
            "quantization": _FILE_TYPE_LABELS.get(file_type, str(file_type) if file_type is not None else "unknown"),
            "context_length": _first_int(metadata, (".context_length",)),
            "block_count": _first_int(metadata, (".block_count",)),
            "embedding_length": _first_int(metadata, (".embedding_length",)),
            "attention_head_count": _first_int(metadata, (".attention.head_count",)),
            "attention_head_count_kv": _first_int(metadata, (".attention.head_count_kv",)),
            "expert_count": _first_int(metadata, (".expert_count", ".expert.count")),
            "expert_used_count": _first_int(metadata, (".expert_used_count", ".expert.used_count")),
            "model_name": _first_value(metadata, ("general.name",)),
            "metadata": metadata,
        })
    except (OSError, UnicodeDecodeError, ValueError, struct.error) as exc:
        logger.debug("Failed to inspect GGUF %s: %s", p, exc)
        result["error"] = str(exc)
    return result
