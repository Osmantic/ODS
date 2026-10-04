#!/usr/bin/env python3
"""The README's model tables must match what the catalog selector picks.

Each README row is checked at the ends of its memory range with the arguments
the installers pass to scripts/select-model.py. When the catalog changes a
pick, update the README row and the expectation here together.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ODS = Path(__file__).resolve().parents[1]
# ODS_README_UNDER_TEST lets the negative self-test point at a drifted copy.
README = Path(os.environ.get('ODS_README_UNDER_TEST', ODS.parent / 'README.md')).read_text(encoding='utf-8')

# (README row prefix, expected catalog id, expected context, selector inputs:
#  (backend, memory type, VRAM MB, RAM GB, tier, host arch))
ROWS = [
    ('| 8–16 GB VRAM | Qwen3.5 9B (Q4_K_M) | 64K |', 'qwen3.5-9b-q4', 65536,
     [('nvidia', 'discrete', 8192, 32, '1', 'amd64'), ('nvidia', 'discrete', 16384, 32, '2', 'amd64')]),
    ('| 24–32 GB VRAM | Qwen3.5 27B (Q4_K_M) | 64K |', 'qwen3.5-27b-q4', 65536,
     [('nvidia', 'discrete', 24576, 64, '3', 'amd64'), ('nvidia', 'discrete', 32768, 64, '3', 'amd64')]),
    ('| 48 GB VRAM | Qwen3.6 35B-A3B (UD-Q4_K_M) | 128K |', 'qwen3.6-35b-a3b-ud-q4', 131072,
     [('nvidia', 'discrete', 49152, 128, '4', 'amd64')]),
    ('| 96 GB+ VRAM (x86_64) | Qwen3 Coder Next (Q4_K_M) | 128K |', 'qwen3-coder-next-q4', 131072,
     [('nvidia', 'discrete', 98304, 256, 'NV_ULTRA', 'amd64')]),
    ('| 128 GB unified (arm64) | Qwen3.6 35B-A3B (UD-Q4_K_M) | 128K |', 'qwen3.6-35b-a3b-ud-q4', 131072,
     [('nvidia', 'unified', 131072, 128, 'NV_ULTRA', 'arm64')]),
    ('| 64–128 GB unified | Qwen3.6 35B-A3B (UD-Q4_K_M) | 128K |', 'qwen3.6-35b-a3b-ud-q4', 131072,
     [('amd', 'unified', 65536, 64, 'SH_COMPACT', 'amd64'), ('amd', 'unified', 131072, 128, 'SH_LARGE', 'amd64')]),
    ('| 8 GB | NVIDIA Nemotron3 Nano 4B (Q4_K_M) | 64K |', 'nvidia-nemotron3-nano-4b-q4', 65536,
     [('apple', 'unified', 8192, 8, '1', 'arm64')]),
    ('| 16–36 GB | Qwen3.5 9B (Q4_K_M) | 64K |', 'qwen3.5-9b-q4', 65536,
     [('apple', 'unified', 16384, 16, '1', 'arm64'), ('apple', 'unified', 36864, 36, '3', 'arm64')]),
    ('| 48 GB+ | Qwen3.6 35B-A3B (UD-Q4_K_M) | 128K |', 'qwen3.6-35b-a3b-ud-q4', 131072,
     [('apple', 'unified', 49152, 48, '4', 'arm64')]),
    ('| 8–12 GB | Qwen3.5 2B (Q4_K_M) | 64K |', 'qwen3.5-2b-q4', 65536,
     [('cpu', 'none', 0, 8, '1', 'amd64'), ('cpu', 'none', 0, 12, '1', 'amd64')]),
    ('| 16–20 GB | Qwen3.5 4B (Q4_K_M) | 64K |', 'qwen3.5-4b-q4', 65536,
     [('cpu', 'none', 0, 16, '1', 'amd64'), ('cpu', 'none', 0, 20, '1', 'amd64')]),
    ('| 24 GB+ | Qwen3.5 9B (Q4_K_M) | 64K |', 'qwen3.5-9b-q4', 65536,
     [('cpu', 'none', 0, 24, '1', 'amd64')]),
]


def select(backend, memory_type, vram_mb, ram_gb, tier, host_arch):
    result = subprocess.run(
        [sys.executable, str(ODS / 'scripts/select-model.py'), '--catalog', str(ODS / 'config/model-library.json'),
         '--backend', backend, '--memory-type', memory_type, '--vram-mb', str(vram_mb), '--ram-gb', str(ram_gb),
         '--profile', 'qwen', '--tier', tier, '--host-arch', host_arch, '--installable-only',
         '--min-context', '65536'],
        capture_output=True, text=True, check=True)
    selected = json.loads(result.stdout)['selected']
    return selected['id'], selected['context_length']


def readme_table_rows():
    section = README.split('## Hardware Auto-Detection', 1)[1].split('\n## ', 1)[0]
    return [line for line in section.splitlines()
            if line.startswith('| ') and ' GB' in line.split('|')[1]]


def main():
    failures = []
    covered = {prefix for prefix, *_ in ROWS}
    for line in readme_table_rows():
        if not any(line.startswith(prefix) for prefix in covered):
            failures.append(f'README row has no selector check here: {line}')
    for prefix, expected_id, expected_context, cases in ROWS:
        if prefix not in README:
            failures.append(f'README is missing the expected row: {prefix}')
        for case in cases:
            model_id, context = select(*case)
            if (model_id, context) != (expected_id, expected_context):
                failures.append(f'{prefix} {case}: selector picks {model_id} at {context}, '
                                f'README says {expected_id} at {expected_context}')
    if failures:
        print('\n'.join('[FAIL] ' + failure for failure in failures))
        print('Update the README hardware tables and this test together.')
        sys.exit(1)
    print(f'[PASS] {len(ROWS)} README model-table rows match the catalog selector')


if __name__ == '__main__':
    main()
