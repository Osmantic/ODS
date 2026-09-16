"""Run from dashboard-api: python tests/benchmark_gguf_arrays.py.

Measure metadata inspection only; these fixtures do not measure model loading
or inference. Compare the same command on the base and candidate commits.
"""
import json
import statistics
import struct
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gguf_inspector import inspect_gguf


def string(value):
    raw = value.encode()
    return struct.pack('<Q', len(raw)) + raw


def main():
    receipts = []
    with tempfile.TemporaryDirectory() as directory:
        for count in [131072, 1000000]:
            data = b'GGUF' + struct.pack('<IQQ', 3, 0, 3)
            for key, kind, value in [
                ('tokenizer.ggml.scores', 6, struct.pack('<f', 1.0)),
                ('tokenizer.ggml.token_type', 5, struct.pack('<i', 1)),
            ]:
                data += string(key) + struct.pack('<IIQ', 9, kind, count) + value * count
            data += string('general.architecture') + struct.pack('<I', 8) + string('llama')
            model = Path(directory) / 'model.gguf'
            model.write_bytes(data)
            durations = []
            for _ in range(5):
                start = time.perf_counter()
                result = inspect_gguf(model)
                durations.append(time.perf_counter() - start)
                assert result['readable'] and result['architecture'] == 'llama'
                assert result['metadata']['tokenizer.ggml.token_type']['sample'] == [1] * 64
            receipts.append({'entries_per_array': count, 'median_seconds': statistics.median(durations)})
    print(json.dumps(receipts, indent=2))


if __name__ == '__main__':
    main()
