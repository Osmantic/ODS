"""Remove only an explicitly reviewed, settled probe manifest; never scan a root.

The supervisor/root reviewer establishes settlement and disarm from the bound
receipts. This helper checks custody, not runtime quiescence. Recheck + unlink is
not atomic against a same-UID external writer. Keep the physical lane held.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat

NAME = re.compile(r'(?:authorize-[a-f0-9]{64}\.json(?:\.claimed)?|transfer-[a-f0-9]{32}\.json(?:\.consumed)?|run-[a-f0-9]{64}\.json(?:\.events\.jsonl)?)\Z')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _root(root):
    root = Path(root)
    if os.name != 'posix' or not root.is_absolute() or root.resolve(strict=True) != root:
        raise ValueError('canonical absolute POSIX root required')
    s = root.stat()
    if s.st_uid != os.getuid() or stat.S_IMODE(s.st_mode) & 0o077:
        raise ValueError('owner-private root required')
    return root, {'uid': s.st_uid, 'device': s.st_dev, 'inode': s.st_ino}


def _read(root, name, scope):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError('unrecognized explicit probe basename')
    try:
        fd = os.open(root / name, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    try:
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or stat.S_IMODE(s.st_mode) != 0o600 or s.st_nlink not in (1, 2) or s.st_size > 131072:
            raise ValueError('unsafe probe file custody')
        data = b''
        while chunk := os.read(fd, 131073 - len(data)):
            data += chunk
            if len(data) > 131072:
                raise ValueError('probe file exceeds bound')
        after = os.fstat(fd)
        if (s.st_size, s.st_mtime_ns, s.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError('probe file changed during read')
        rows = [json.loads(line) for line in data.splitlines() if line.strip()] if name.endswith('.jsonl') else [json.loads(data)]
        if not rows or len(rows) > 256 or any(not isinstance(row, dict) or row.get('scopeId') != scope for row in rows):
            raise ValueError('foreign or unrecognized probe scope')
        return {'name': name, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data), 'mode': stat.S_IMODE(s.st_mode), 'uid': s.st_uid, 'device': s.st_dev, 'inode': s.st_ino, 'links': s.st_nlink}
    finally:
        os.close(fd)


def create_manifest(root, scope_id, names, evidence):
    root, custody = _root(root)
    if not isinstance(scope_id, str) or not re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', scope_id):
        raise ValueError('exact probe scope required')
    if not isinstance(names, list) or not 1 <= len(names) <= 128 or len(set(names)) != len(names):
        raise ValueError('bounded unique explicit probe files required')
    if set(evidence) != {'settlement', 'routerDisarm'} or any(not isinstance(v, str) or not re.fullmatch('[a-f0-9]{64}', v) for v in evidence.values()):
        raise ValueError('exact settlement and disarm receipt hashes required')
    files = [_read(root, name, scope_id) for name in names]
    if any(row is None for row in files):
        raise ValueError('initial manifest requires observed files')
    for row in files:
        siblings = [f for f in files if (f['device'], f['inode']) == (row['device'], row['inode'])]
        if len(siblings) != row['links']:
            raise ValueError('all names of a hard-link election must be explicit')
    return {'schema': 'ods-owned-probe-cleanup.v1', 'root': str(root), 'rootCustody': custody, 'scopeId': scope_id, 'evidence': evidence, 'files': files}


def cleanup(manifest, *, approved_manifest_sha256, evidence, after_unlink=None):
    if manifest.get('schema') != 'ods-owned-probe-cleanup.v1' or digest(manifest) != approved_manifest_sha256:
        raise ValueError('exact reviewed manifest required')
    if evidence != manifest.get('evidence'):
        raise ValueError('settlement/disarm receipt binding changed')
    root, custody = _root(manifest['root'])
    if custody != manifest['rootCustody']:
        raise ValueError('probe root custody changed')
    files = manifest['files']
    if not isinstance(files, list) or not 1 <= len(files) <= 128 or len({f['name'] for f in files}) != len(files):
        raise ValueError('invalid explicit manifest')
    # Validate all present names before the first mutation. Absence is not proof
    # that a previous invocation removed the file; record it honestly.
    present = {}
    for row in files:
        current = _read(root, row['name'], manifest['scopeId'])
        if current is not None:
            if any(current[k] != row[k] for k in ('sha256', 'bytes', 'mode', 'uid', 'device', 'inode')):
                raise ValueError('probe file identity/content changed')
            present[row['name']] = current
    for row in present.values():
        siblings = [f for f in present.values() if (f['device'], f['inode']) == (row['device'], row['inode'])]
        if len(siblings) != row['links']:
            raise ValueError('unlisted remaining hard link')
    outcomes = []
    for row in files:
        current = _read(root, row['name'], manifest['scopeId'])
        if current is None:
            outcomes.append({'name': row['name'], 'state': 'absent-unattributed'})
            continue
        if any(current[k] != row[k] for k in ('sha256', 'bytes', 'mode', 'uid', 'device', 'inode')):
            raise ValueError('probe file changed before unlink')
        os.unlink(root / row['name'])
        outcomes.append({'name': row['name'], 'state': 'removed-by-this-invocation'})
        if after_unlink:
            after_unlink(row['name'])
    remaining = [row['name'] for row in files if os.path.lexists(root / row['name'])]
    return {'schema': 'ods-owned-probe-cleanup-result.v1', 'manifestSha256': approved_manifest_sha256, 'files': outcomes, 'remainingManifestNames': remaining, 'allManifestNamesAbsent': not remaining, 'boundary': 'explicit manifest only; no directory-wide absence claim'}
