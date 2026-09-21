#!/usr/bin/env python3
"""Owner-only receipt relocation while the installer proves services stopped."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys


def relocate(value, controller):
    if (type(value) is not dict or set(value) != {'source', 'target', 'sourceHash', 'targetHash'}
            or any(type(value[key]) is not str for key in value)
            or any(not value[key].startswith('/') or any(part in ('', '.', '..')
                for part in value[key].split('/')[1:]) for key in ('source', 'target'))
            or Path(value['source']).parent != Path(value['target']).parent):
        raise ValueError('invalid-relocation-selection')
    for key in ('sourceHash', 'targetHash'):
        if len(value[key]) != 64 or any(c not in '0123456789abcdef' for c in value[key]):
            raise ValueError('invalid-relocation-selection')
    legacy = controller._default_state_dir()
    state = legacy if os.path.lexists(legacy) else str(Path(value['target']).parent / '.ods-access-mode')
    # No receipt is normal for a never-enabled sandbox deployment. Do not
    # invent a baseline or silently skip an unmanaged full-access config.
    if not os.path.lexists(Path(state) / controller.RECEIPT_NAME):
        for path_key, hash_key in (('source', 'sourceHash'), ('target', 'targetHash')):
            _, _, body, _ = controller._load_config(value[path_key])
            if hashlib.sha256(body).hexdigest() != value[hash_key]:
                raise ValueError('configuration-changed')
            status = controller.get_status(value[path_key], state_dir=state)
            if status.get('configured_status') != 'sandboxed':
                raise ValueError('full-access-receipt-required')
        return False
    return controller.relocate_receipt(value['source'], value['target'], state,
        old_sha256=value['sourceHash'], new_sha256=value['targetHash'],
        # This helper is not a public API. Its parent holds the controller
        # lock and independently proves all three service process trees dead.
        check_no_active_run=lambda: False)


def main():
    try:
        if os.geteuid() == 0 or sys.platform != 'darwin':
            raise ValueError('macos-owner-required')
        raw = sys.stdin.buffer.readline(16385)
        if len(raw) > 16384 or not raw.endswith(b'\n'):
            raise ValueError('invalid-frame')
        source = Path(__file__).resolve().parents[3] / 'extensions/services/pixel-agent/host/pixel_access_mode.py'
        spec = importlib.util.spec_from_file_location('receipt_controller', source)
        controller = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(controller)
        changed = relocate(json.loads(raw), controller)
        print(json.dumps({'relocated': changed}))
        return 0
    except Exception:
        print('runtime-receipt-relocation-failed', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
