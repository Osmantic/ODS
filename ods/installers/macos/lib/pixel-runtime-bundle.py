"""Stage and publish a reproducible native Pixel runtime without executing it.

Sources are explicitly selected local artifacts, not an upstream provenance
claim. The manifest binds every entry, including internal relative symlinks.
Publication requires a selected digest and root custody; loader compatibility,
configuration activation and runtime behavior need separate gates.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile


MANIFEST = 'bundle.json'
MAX_ENTRIES = 200000
MAX_MANIFEST = 32 * 1024 * 1024
INSTALL_ROOT = Path('/usr/local/libexec/ods-pixel-runtimes')
STREAM_PROGRESS_FILE = 'dist/selection-BEwSQKM-.js'
STREAM_PROGRESS_SOURCE_SHA256 = 'ae83457af1947f3eaf3e08f8fbde869a1c023a80acfbf0f27d3db887af1d36d3'


class BundleError(ValueError):
    pass


def _relative(value):
    if (type(value) is not str or not value or value.startswith('/')
            or any(part in ('', '.', '..') for part in value.split('/'))
            or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
        raise BundleError('invalid-bundle-path')
    return value


def _encode(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n').encode()


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _open_file(root, relative):
    """Never follow an intermediate symlink while reading a selected tree."""
    parts = _relative(relative).split('/')
    parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
        return os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    finally:
        os.close(parent)


def _file(root, relative, output=None):
    fd = _open_file(root, relative)
    with os.fdopen(fd, 'rb') as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_mode & 0o7000:
            raise BundleError('unsupported-bundle-file')
        checksum = hashlib.sha256()
        for block in iter(lambda: source.read(1024 * 1024), b''):
            checksum.update(block)
            if output is not None:
                output.write(block)
        if _signature(before) != _signature(os.fstat(source.fileno())):
            raise BundleError('bundle-source-changed')
        # Modes are deliberately normalized; ACLs and xattrs are not imported.
        return ['file', 0o755 if before.st_mode & 0o111 else 0o644,
                before.st_size, checksum.hexdigest()]


def inventory(root, *, exclude_manifest=False, normalize_modes=True):
    root = Path(root)
    if not stat.S_ISDIR(root.lstat().st_mode):
        raise BundleError('bundle-directory-required')
    entries = {}
    def walk_error(error):
        raise error
    for directory, folders, files in os.walk(root, followlinks=False, onerror=walk_error):
        for name in sorted(folders + files):
            path = Path(directory) / name
            relative = _relative(path.relative_to(root).as_posix())
            if exclude_manifest and relative == MANIFEST:
                continue
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                target = os.readlink(path)
                try:
                    resolved = path.resolve(strict=True)
                except (OSError, RuntimeError):
                    raise BundleError('bundle-link-unavailable') from None
                if (target.startswith('/') or resolved != root and root not in resolved.parents
                        or any(ord(c) < 32 for c in target)):
                    raise BundleError('bundle-link-escapes')
                entries[relative] = ['link', target]
            elif stat.S_ISDIR(info.st_mode):
                if info.st_mode & 0o7000:
                    raise BundleError('unsupported-bundle-directory')
                entries[relative] = ['directory', 0o755 if normalize_modes else stat.S_IMODE(info.st_mode)]
            else:
                entries[relative] = _file(root, relative)
                if not normalize_modes:
                    entries[relative][1] = stat.S_IMODE(info.st_mode)
            if len(entries) > MAX_ENTRIES:
                raise BundleError('bundle-too-many-entries')
    return entries


def _copy_tree(source, target, entries):
    target.mkdir(mode=0o755)
    target.chmod(0o755)
    for relative, record in sorted(entries.items(), key=lambda item: (item[0].count('/'), item[0])):
        path = target / relative
        if record[0] == 'directory':
            path.mkdir(mode=0o755)
            path.chmod(0o755)
        elif record[0] == 'link':
            if os.readlink(source / relative) != record[1]:
                raise BundleError('bundle-source-changed')
            path.symlink_to(record[1])
        else:
            with path.open('xb') as output:
                actual = _file(source, relative, output)
                output.flush()
                os.fsync(output.fileno())
            if actual != record:
                raise BundleError('bundle-source-changed')
            path.chmod(record[1])
    if inventory(target, normalize_modes=False) != entries:
        raise BundleError('bundle-copy-mismatch')


def verify(root, *, expected_digest=None):
    root = Path(root).resolve(strict=True)
    path = root / MANIFEST
    if (not stat.S_ISREG(path.lstat().st_mode) or path.stat().st_size > MAX_MANIFEST):
        raise BundleError('invalid-bundle-manifest')
    with os.fdopen(_open_file(root, MANIFEST), 'rb') as handle:
        body = handle.read(MAX_MANIFEST + 1)
    value = json.loads(body)
    if (len(body) > MAX_MANIFEST or _encode(value) != body or type(value) is not dict
            or set(value) != {'schemaVersion', 'openclawVersion', 'plugins', 'entries'}
            or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
            or type(value['openclawVersion']) is not str or not value['openclawVersion']
            or type(value['entries']) is not dict or len(value['entries']) > MAX_ENTRIES
            or type(value['plugins']) is not list
            or value['plugins'] != ['plugins/' + str(i) for i in range(len(value['plugins']))]):
        raise BundleError('invalid-bundle-manifest')
    checksum = hashlib.sha256(body).hexdigest()
    if expected_digest is not None and checksum != expected_digest:
        raise BundleError('bundle-manifest-changed')
    if inventory(root, exclude_manifest=True, normalize_modes=False) != value['entries']:
        raise BundleError('bundle-content-changed')
    if (value['entries'].get('node', [None])[0] != 'file'
            or value['entries']['node'][1] != 0o755
            or value['entries'].get('runtime/openclaw.mjs', [None])[0] != 'file'
            or value['entries'].get('runtime/package.json', [None])[0] != 'file'
            or any(value['entries'].get(path) != ['directory', 0o755] for path in value['plugins'])):
        raise BundleError('invalid-bundle-layout')
    package = json.loads((root / 'runtime/package.json').read_bytes())
    if package.get('name') != 'openclaw' or package.get('version') != value['openclawVersion']:
        raise BundleError('bundle-version-mismatch')
    return value, checksum


def _patch_stream_progress(runtime):
    """Relocate observers before buffering, only for the reviewed upstream bytes."""
    with os.fdopen(_open_file(runtime, STREAM_PROGRESS_FILE), 'rb') as handle:
        original = handle.read()
    if hashlib.sha256(original).hexdigest() != STREAM_PROGRESS_SOURCE_SHA256:
        raise BundleError('stream-progress-unqualified-source')
    source = original.decode('utf-8')
    start = '\t\t\tconst configuredRunTimeoutMs = resolveAgentTimeoutMs({ cfg: params.config });'
    end = '\n\t\t\ttry {\n\t\t\t\tif (isRawModelRun) {'
    target = '\t\t\tconst innerStreamFn = activeSession.agent.streamFn;'
    if any(source.count(marker) != 1 for marker in (start, end, target)):
        raise BundleError('stream-progress-patch-layout-changed')
    begin, finish, insertion = source.index(start), source.index(end), source.index(target)
    if not insertion < begin < finish:
        raise BundleError('stream-progress-patch-order-changed')
    observers = source[begin:finish]
    patched = (source[:insertion] + observers + '\n' + source[insertion:begin] + source[finish:]).encode('utf-8')
    (runtime / STREAM_PROGRESS_FILE).write_bytes(patched)
    return {'id': 'openclaw-buffered-stream-progress-v1', 'file': STREAM_PROGRESS_FILE,
            'sourceSha256': STREAM_PROGRESS_SOURCE_SHA256,
            'patchedSha256': hashlib.sha256(patched).hexdigest()}


def qualify_stream_progress_upgrade(current, candidate, *, current_digest, candidate_digest):
    """Read-only gate: this upgrade may change neither Node nor plugin code."""
    old, _ = verify(current, expected_digest=current_digest)
    new, _ = verify(candidate, expected_digest=candidate_digest)
    if (old['openclawVersion'] != '2026.6.33'
            or new['openclawVersion'] != old['openclawVersion']
            or new['plugins'] != old['plugins']):
        raise BundleError('runtime-upgrade-version-mismatch')
    changed = {name for name in old['entries'].keys() | new['entries'].keys()
               if old['entries'].get(name) != new['entries'].get(name)}
    expected = {'runtime/' + STREAM_PROGRESS_FILE, 'ods-runtime-patches.json'}
    if changed != expected or 'ods-runtime-patches.json' in old['entries']:
        raise BundleError('runtime-upgrade-unexpected-changes')
    # Reproduce the patch from the current trusted bytes, rather than trusting
    # a candidate's self-declared receipt or package version.
    with tempfile.TemporaryDirectory(prefix='ods-runtime-upgrade-check-') as directory:
        staged = Path(directory)
        target = staged / STREAM_PROGRESS_FILE
        target.parent.mkdir(parents=True)
        target.write_bytes((Path(current) / 'runtime' / STREAM_PROGRESS_FILE).read_bytes())
        receipt = _patch_stream_progress(staged)
        if ((Path(candidate) / 'runtime' / STREAM_PROGRESS_FILE).read_bytes() != target.read_bytes()
                or (Path(candidate) / 'ods-runtime-patches.json').read_bytes() != _encode([receipt])):
            raise BundleError('runtime-upgrade-patch-mismatch')
    return {'currentDigest': current_digest, 'candidateDigest': candidate_digest,
            'changedEntries': sorted(changed), 'scope': 'runtime content only; no activation'}


def qualify_workspace_root_upgrade(current, candidate, *, current_digest, candidate_digest):
    """Accept only the inherited-workspace fix, with all other bytes unchanged."""
    old, _ = verify(current, expected_digest=current_digest)
    new, _ = verify(candidate, expected_digest=candidate_digest)
    entry = 'plugins/0/index.js'
    if (old['openclawVersion'] != '2026.6.33'
            or new['openclawVersion'] != old['openclawVersion']
            or old['plugins'] != ['plugins/0'] or new['plugins'] != old['plugins']):
        raise BundleError('workspace-upgrade-version-mismatch')
    changed = {name for name in old['entries'].keys() | new['entries'].keys()
               if old['entries'].get(name) != new['entries'].get(name)}
    if changed != {entry}:
        raise BundleError('workspace-upgrade-unexpected-changes')
    before = b'      const workspaceRoot = api.config?.agents?.list?.find(agent => agent.id === AGENT_ID)?.workspace;'
    after = before[:-1] + b'\n        ?? api.config?.agents?.defaults?.workspace;'
    source = (Path(current) / entry).read_bytes()
    if source.count(before) != 1 or (Path(candidate) / entry).read_bytes() != source.replace(before, after):
        raise BundleError('workspace-upgrade-patch-mismatch')
    if (Path(current) / entry).stat().st_mode != (Path(candidate) / entry).stat().st_mode:
        raise BundleError('workspace-upgrade-mode-mismatch')
    return {'currentDigest': current_digest, 'candidateDigest': candidate_digest,
            'changedEntries': [entry], 'scope': 'plugin content only; no activation'}


def build(*, node, runtime, destination, plugins=(), expected_version='2026.6.33',
          stream_progress_fix=False):
    node, runtime = Path(node).resolve(strict=True), Path(runtime).resolve(strict=True)
    plugins = [Path(path).resolve(strict=True) for path in plugins]
    destination = Path(destination)
    if not destination.is_absolute() or os.path.lexists(destination):
        raise BundleError('new-absolute-bundle-destination-required')
    parent = destination.parent.resolve(strict=True)
    destination = parent / destination.name
    roots = [runtime, *plugins]
    if any(root == destination or root in destination.parents for root in roots):
        raise BundleError('bundle-destination-inside-source')
    with os.fdopen(_open_file(runtime, 'package.json'), 'rb') as handle:
        package = json.load(handle)
    if package.get('name') != 'openclaw' or package.get('version') != expected_version:
        raise BundleError('unqualified-openclaw-version')
    snapshots = [inventory(root) for root in roots]
    node_record = _file(node.parent, node.name)
    if node_record[1] != 0o755:
        raise BundleError('bundle-node-not-executable')
    staged = Path(tempfile.mkdtemp(prefix='.ods-pixel-bundle-', dir=parent))
    try:
        with (staged / 'node').open('xb') as output:
            if _file(node.parent, node.name, output) != node_record:
                raise BundleError('bundle-source-changed')
            output.flush()
            os.fsync(output.fileno())
        (staged / 'node').chmod(0o755)
        _copy_tree(runtime, staged / 'runtime', snapshots[0])
        if stream_progress_fix:
            receipt = _patch_stream_progress(staged / 'runtime')
            (staged / 'ods-runtime-patches.json').write_bytes(_encode([receipt]))
            (staged / 'ods-runtime-patches.json').chmod(0o644)
        (staged / 'plugins').mkdir(mode=0o755)
        (staged / 'plugins').chmod(0o755)
        for index, (source, entries) in enumerate(zip(plugins, snapshots[1:])):
            _copy_tree(source, staged / 'plugins' / str(index), entries)
        value = {'schemaVersion': 1, 'openclawVersion': expected_version,
                 'plugins': ['plugins/' + str(i) for i in range(len(plugins))],
                 'entries': inventory(staged)}
        body = _encode(value)
        if len(body) > MAX_MANIFEST:
            raise BundleError('bundle-manifest-too-large')
        with (staged / MANIFEST).open('xb') as output:
            output.write(body)
            output.flush()
            os.fsync(output.fileno())
        (staged / MANIFEST).chmod(0o644)
        verify(staged)
        if os.path.lexists(destination):
            raise BundleError('bundle-destination-appeared')
        os.rename(staged, destination)
        return hashlib.sha256(body).hexdigest()
    finally:
        if staged.exists():
            shutil.rmtree(staged)


def publish(source, *, expected_digest, install_root=INSTALL_ROOT):
    """Publish a content-addressed copy as root, without changing any service.

    The independently selected digest is mandatory. Existing versions must
    pass both full custody and content checks; drift is never repaired in place.
    """
    if os.geteuid() != 0:
        raise BundleError('root-bundle-publisher-required')
    if (type(expected_digest) is not str or len(expected_digest) != 64
            or any(c not in '0123456789abcdef' for c in expected_digest)):
        raise BundleError('approved-bundle-digest-required')
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'bin'))
    from pixel_macos_custody import protected_directory, protected_tree_metadata
    source = Path(source).resolve(strict=True)
    value, checksum = verify(source, expected_digest=expected_digest)
    install_root = Path(install_root)
    destination = install_root / checksum
    if source == install_root or source in install_root.parents:
        raise BundleError('bundle-destination-inside-source')
    with protected_directory(install_root, create=True) as parent:
        # Serialize cooperative installers on the verified root directory;
        # no user-writable lock file or user-selected executable is involved.
        fcntl.flock(parent, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if os.path.lexists(destination):
            protected_tree_metadata(destination)
            verify(destination, expected_digest=checksum)
            return destination
        temporary = Path(tempfile.mkdtemp(prefix='.publishing-', dir=install_root))
        staged = temporary / 'bundle'
        try:
            _copy_tree(source, staged, value['entries'])
            with (staged / MANIFEST).open('xb') as output:
                output.write(_encode(value))
                output.flush()
                os.fsync(output.fileno())
            (staged / MANIFEST).chmod(0o644)
            protected_tree_metadata(staged)
            verify(staged, expected_digest=checksum)
            # Flush directories bottom-up before publishing the whole tree.
            for directory, _, _ in os.walk(staged, topdown=False, followlinks=False):
                with protected_directory(directory) as fd:
                    os.fsync(fd)
            if os.path.lexists(destination):
                raise BundleError('bundle-destination-appeared')
            os.rename(staged, destination)
            os.fsync(parent)
            protected_tree_metadata(destination)
            verify(destination, expected_digest=checksum)
            return destination
        finally:
            shutil.rmtree(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify', type=Path)
    parser.add_argument('--publish', type=Path, help='Publish a staged bundle as root; never starts services')
    parser.add_argument('--expected-digest', help='Previously selected manifest SHA256 required for publication')
    parser.add_argument('--node', type=Path)
    parser.add_argument('--runtime', type=Path)
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--plugin', type=Path, action='append', default=[])
    parser.add_argument('--expected-version', default='2026.6.33')
    parser.add_argument('--stream-progress-fix', action='store_true',
                        help='Build-only opt-in patch for reviewed OpenClaw buffered stream observers')
    args = parser.parse_args()
    if args.publish:
        if args.verify or any((args.node, args.runtime, args.destination, args.plugin, args.stream_progress_fix)):
            parser.error('--publish cannot be combined with build or verify')
        published = publish(args.publish, expected_digest=args.expected_digest)
        manifest, checksum = verify(published, expected_digest=args.expected_digest)
    elif args.verify:
        if args.stream_progress_fix:
            parser.error('--stream-progress-fix is build-only')
        manifest, checksum = verify(args.verify)
    else:
        if not all((args.node, args.runtime, args.destination)):
            parser.error('--node, --runtime and --destination are required to build')
        checksum = build(node=args.node, runtime=args.runtime, destination=args.destination,
                         plugins=args.plugin, expected_version=args.expected_version,
                         stream_progress_fix=args.stream_progress_fix)
        manifest, _ = verify(args.destination, expected_digest=checksum)
    print(json.dumps({'manifestSha256': checksum, 'openclawVersion': manifest['openclawVersion'],
                      'entries': len(manifest['entries']), 'plugins': len(manifest['plugins']),
                      'scope': 'content integrity only; not root custody or runtime qualification'}))


if __name__ == '__main__':
    main()
