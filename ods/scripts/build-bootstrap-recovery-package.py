#!/usr/bin/env python3
"""Build the separate, deterministic root recovery artifact; install nothing."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile


def build(destination):
    source = Path(__file__).resolve().parents[1] / 'recovery/bootstrap_current'
    files = {path.name: path.read_bytes() for path in sorted(source.iterdir()) if path.suffix in ('.py', '.json')}
    expected = {'loader.py', 'custody.py', 'preflight.py', 'scope.py', 'socket_custody.py', 'adapters.py',
                'controller.py', 'owner.py', 'quarantine.py', 'compatibility.json'}
    if set(files) != expected or any(path.is_symlink() for path in source.iterdir()):
        raise ValueError('recovery package source set changed')
    manifest = dict(version=1, files={name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()})
    files['manifest.json'] = (json.dumps(manifest, sort_keys=True, separators=(',', ':')) + '\n').encode()
    with Path(destination).open('xb') as stream:
        with tarfile.open(fileobj=stream, mode='w', format=tarfile.USTAR_FORMAT) as archive:
            for name, raw in sorted(files.items()):
                item = tarfile.TarInfo('ods-bootstrap-recovery-v1/' + name)
                item.size, item.mode, item.uid, item.gid, item.mtime = len(raw), 0o644, 0, 0, 0
                archive.addfile(item, io.BytesIO(raw))
    return dict(artifact=str(Path(destination).absolute()), sha256=hashlib.sha256(Path(destination).read_bytes()).hexdigest(),
                manifestSha256=hashlib.sha256(files['manifest.json']).hexdigest(), installed=False)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('usage: build-bootstrap-recovery-package.py OUTPUT.tar')
    print(json.dumps(build(sys.argv[1]), sort_keys=True))
