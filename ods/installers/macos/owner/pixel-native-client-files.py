"""Retain and publish the owner's public Portal code, never private state.

This is an owner-side file transaction, not native runtime custody. Callers must
hold admission while publishing or restoring. Original files are retained by
rename; a retry recognizes only the recorded objects at their three fixed paths.
"""
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile


DIRECTORIES = ('bin', 'lib', 'scripts', 'installers', 'vendor',
    'extensions/services/dashboard', 'extensions/services/dashboard-api',
    'extensions/services/pixel-agent', 'extensions/services/pixel-model-relay')
TOP_LEVEL = ('.env.schema.json', '.dockerignore', 'manifest.json')
SKIP = frozenset(('__pycache__', 'node_modules', 'dist', 'tests', '.git'))
MAX_FILE = 64 * 1024 * 1024
MAX_BYTES = 256 * 1024 * 1024
MAX_FILES = 12000
MAX_NODES = 24000
MAX_DEPTH = 32
MAX_MANIFEST = 16 * 1024 * 1024


def _error():
    raise ValueError('native-client-files-unqualified')


def _canonical(path):
    path = Path(path).absolute()
    if path.resolve(strict=True) != path:
        _error()
    return path


def _acl(path):
    # An owner-code transaction does not interpret or replace Darwin ACLs.
    # Refuse extended ACLs within its selected tree; ancestors such as the
    # owner's home may have macOS's ordinary deny-delete entry.
    if sys.platform != 'darwin':
        return
    libc = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    libc.acl_get_fd_np.argtypes = (ctypes.c_int, ctypes.c_int)
    libc.acl_get_fd_np.restype = ctypes.c_void_p
    libc.acl_get_entry.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))
    libc.acl_valid.argtypes = (ctypes.c_void_p,)
    libc.acl_free.argtypes = (ctypes.c_void_p,)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    acl = None
    try:
        ctypes.set_errno(0)
        acl = libc.acl_get_fd_np(descriptor, 0x100)
        if not acl:
            # Darwin's descriptor query uses ENOENT for an absent ACL, not
            # a missing pathname. Other errors cannot establish absence.
            if ctypes.get_errno() == errno.ENOENT:
                os.fstat(descriptor)
                return
            _error()
        if libc.acl_valid(acl) != 0:
            _error()
        entry = ctypes.c_void_p()
        ctypes.set_errno(0)
        if libc.acl_get_entry(acl, 0, ctypes.byref(entry)) != -1 or ctypes.get_errno() != errno.EINVAL:
            _error()
    finally:
        if acl:
            libc.acl_free(acl)
        os.close(descriptor)


def _directory(path, *, private=False, owner=True, acl=True):
    info = path.lstat()
    mode = stat.S_IMODE(info.st_mode)
    if (not stat.S_ISDIR(info.st_mode) or path.resolve(strict=True) != path
            or (owner and info.st_uid != os.getuid()) or mode & 0o022
            or (private and mode != 0o700)):
        _error()
    if acl:
        _acl(path)
    return {'dev': info.st_dev, 'ino': info.st_ino, 'uid': info.st_uid,
        'gid': info.st_gid, 'mode': mode}


def _root(path, *, private=False):
    path = _canonical(path)
    _directory(path, private=private)
    for parent in path.parents:
        info = parent.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.getuid())
                or (info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX))):
            _error()
    return path



def _file_stamp(info):
    # Reading a file may update atime. It is not evidence that the selected
    # object or its contents changed; ownership and change time still are.
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_uid, info.st_gid,
        info.st_size, info.st_mtime_ns, info.st_ctime_ns, getattr(info, 'st_flags', None))


def _file(path, *, owner=True):
    info = path.lstat()
    mode = stat.S_IMODE(info.st_mode)
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or (owner and info.st_uid != os.getuid()) or mode & 0o7022
            or info.st_size > MAX_FILE or path.resolve(strict=True) != path):
        _error()
    _acl(path)
    value = hashlib.sha256()
    with path.open('rb') as stream:
        if _file_stamp(os.fstat(stream.fileno())) != _file_stamp(info):
            _error()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
        if _file_stamp(os.fstat(stream.fileno())) != _file_stamp(info):
            _error()
    if _file_stamp(path.lstat()) != _file_stamp(info):
        _error()
    return {'sha256': value.hexdigest(), 'size': info.st_size, 'mode': mode,
        'uid': info.st_uid, 'gid': info.st_gid, 'dev': info.st_dev, 'ino': info.st_ino}


def _bytes(document):
    return (json.dumps(document, sort_keys=True, separators=(',', ':')) + '\n').encode()


def _sync(directory):
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _rename(source, target):
    """A competing owner write must never be overwritten after the precheck."""
    if sys.platform == 'darwin':
        libc = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
        operation = libc.renamex_np
        operation.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
        arguments = (os.fsencode(source), os.fsencode(target), 0x4)  # RENAME_EXCL.
    elif sys.platform.startswith('linux'):
        libc = ctypes.CDLL(None, use_errno=True)
        operation = getattr(libc, 'renameat2', None)
        if operation is None:
            _error()
        operation.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
        arguments = (-100, os.fsencode(source), -100, os.fsencode(target), 1)  # RENAME_NOREPLACE.
    else:
        _error()
    operation.restype = ctypes.c_int
    if operation(*arguments) != 0:
        raise OSError(ctypes.get_errno(), 'native-client-files-rename-refused')


def _write(path, document, *, replace=False):
    body = _bytes(document)
    if len(body) > MAX_MANIFEST:
        _error()
    if replace and os.path.lexists(path):
        _file(path)
    with tempfile.NamedTemporaryFile(prefix='.client-record-', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        os.fchmod(stream.fileno(), 0o600)
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    if not replace and os.path.lexists(path):
        _error()
    os.replace(temporary, path)
    _sync(path.parent)


def _relative(value):
    if (type(value) is not str or not value or value.startswith('/')
            or any(part in ('', '.', '..') for part in value.split('/'))):
        _error()
    return Path(value)


def _allowed(value):
    if value in TOP_LEVEL or value == 'ods-macos.sh':
        return True
    return any(value.startswith(root + '/') for root in DIRECTORIES)


def _skip(relative):
    return any(part in SKIP for part in relative.parts) or relative.suffix == '.pyc'


def _sources(source):
    found = {}
    nodes, size = 0, 0
    def visit(path):
        nonlocal nodes, size
        relative = path.relative_to(source)
        nodes += 1
        if nodes > MAX_NODES or len(relative.parts) > MAX_DEPTH:
            _error()
        if _skip(relative):
            return
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            _directory(path, owner=False)
            for child in sorted(path.iterdir()):
                visit(child)
        else:
            if relative.name in ('compose.yaml', 'compose.yaml.disabled') and any(
                    str(relative).startswith(root + '/') for root in DIRECTORIES[-2:]):
                return
            entry = _file(path, owner=False)
            size += entry['size']
            if len(found) >= MAX_FILES or size > MAX_BYTES:
                _error()
            found[str(relative)] = (path, entry)
    for name in DIRECTORIES:
        path = source / name
        if os.path.lexists(path):
            visit(path)
    for name in TOP_LEVEL:
        path = source / name
        if os.path.lexists(path):
            found[name] = (path, _file(path, owner=False))
    # These aliases are how the macOS installer publishes its CLI libraries.
    cli = source / 'installers/macos/ods-macos.sh'
    if cli.is_file():
        found['ods-macos.sh'] = (cli, _file(cli, owner=False))
    libraries = source / 'installers/macos/lib'
    if libraries.is_dir():
        for path in sorted(libraries.glob('*.sh')):
            found['lib/' + path.name] = (path, _file(path, owner=False))
    if not found or len(found) > MAX_FILES or sum(item[1]['size'] for item in found.values()) > MAX_BYTES:
        _error()
    return found


def stage(source, install_dir, destination):
    """Copy bounded public code and record originals without changing install."""
    source, install_dir = _root(source), _root(install_dir)
    destination = Path(destination).absolute()
    if source == install_dir or source in install_dir.parents or install_dir in source.parents:
        _error()
    _root(destination.parent)
    if not os.path.lexists(destination):
        destination.mkdir(mode=0o700)
    destination = _root(destination, private=True)
    if any(destination.iterdir()) or source == destination or source in destination.parents:
        _error()
    selected = _sources(source)
    for folder in ('candidate', 'previous', 'directories'):
        (destination / folder).mkdir(mode=0o700)
    manifest = {'schemaVersion': 1, 'installDir': str(install_dir), 'source': str(source),
        'installIdentity': _directory(install_dir), 'files': {}, 'directories': {}}
    parents = set()
    for name in selected:
        parent = Path(name).parent
        while parent != Path('.'):
            parents.add(str(parent))
            parent = parent.parent
    for index, name in enumerate(sorted(parents, key=lambda item: (item.count('/'), item))):
        path = install_dir / name
        if os.path.lexists(path):
            manifest['directories'][name] = {'previous': _directory(path)}
        else:
            staged = destination / 'directories' / str(index)
            staged.mkdir(mode=0o755)
            manifest['directories'][name] = {'previous': None, 'slot': str(index),
                'candidate': _directory(staged)}
    for name, (path, expected) in sorted(selected.items()):
        _relative(name)
        if not _allowed(name):
            _error()
        target = install_dir / name
        before = _file(target) if os.path.lexists(target) else None
        staged = destination / 'candidate' / name
        staged.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        (destination / 'previous' / name).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with path.open('rb') as reader, staged.open('xb') as writer:
            os.fchmod(writer.fileno(), expected['mode'])
            for chunk in iter(lambda: reader.read(1024 * 1024), b''):
                writer.write(chunk)
            writer.flush()
            os.fsync(writer.fileno())
        if _file(path, owner=False) != expected:
            _error()
        candidate = _file(staged)
        if any(candidate[key] != expected[key] for key in ('sha256', 'mode', 'size')):
            _error()
        manifest['files'][name] = {'candidate': candidate, 'previous': before}
    _write(destination / 'manifest.json', manifest)
    _write(destination / 'journal.json', {'schemaVersion': 1, 'manifestSHA256':
        hashlib.sha256(_bytes(manifest)).hexdigest(), 'phase': 'staged', 'operation': None})
    verify_staged(destination)
    return manifest


def _load(destination):
    destination = _root(destination, private=True)
    documents = []
    for name in ('manifest.json', 'journal.json'):
        path = destination / name
        identity = _file(path)
        if identity['mode'] != 0o600 or identity['size'] > MAX_MANIFEST:
            _error()
        documents.append(json.loads(path.read_bytes()))
    manifest, journal = documents
    if (manifest.get('schemaVersion') != 1 or journal.get('schemaVersion') != 1
            or journal.get('manifestSHA256') != hashlib.sha256(_bytes(manifest)).hexdigest()
            or type(manifest.get('files')) is not dict or not manifest['files']
            or len(manifest['files']) > MAX_FILES or type(manifest.get('directories')) is not dict):
        _error()
    install_dir = _root(manifest['installDir'])
    if _directory(install_dir) != manifest['installIdentity']:
        _error()
    for name in manifest['files']:
        _relative(name)
        if not _allowed(name) or _skip(Path(name)):
            _error()
    for name in manifest['directories']:
        _relative(name)
    for folder in ('candidate', 'previous', 'directories'):
        _directory(destination / folder, private=True)
    return destination, install_dir, manifest, journal


def identity(destination):
    """Immutable binding for the calling admission transaction."""
    return _load(destination)[3]['manifestSHA256']


def _observe(path):
    return _file(path) if os.path.lexists(path) else None


def _parents(destination, install_dir, manifest, *, publish=False, journal=None):
    for name, value in sorted(manifest['directories'].items(), key=lambda item: (item[0].count('/'), item[0])):
        path = install_dir / name
        previous = value['previous']
        if previous is not None:
            if not os.path.lexists(path) or _directory(path) != previous:
                _error()
            continue
        slot = destination / 'directories' / value['slot']
        actual = _directory(path) if os.path.lexists(path) else None
        staged = _directory(slot) if os.path.lexists(slot) else None
        expected = value['candidate']
        if (actual, staged) == (expected, None):
            continue
        if (actual, staged) != (None, expected):
            _error()
        if publish:
            journal.update(phase='publishing', operation={'kind': 'directory', 'path': name})
            _write(destination / 'journal.json', journal, replace=True)
            _rename(slot, path)
            _sync(slot.parent)
            _sync(path.parent)


def _state(destination, install_dir, name, entry):
    paths = (install_dir / name, destination / 'previous' / name, destination / 'candidate' / name)
    actual = tuple(_observe(path) for path in paths)
    previous, candidate = entry['previous'], entry['candidate']
    if actual == (previous, None, candidate):
        return 'previous', paths
    if previous is not None and actual == (None, previous, candidate):
        return 'retained', paths
    if actual == (candidate, previous, None):
        return 'candidate', paths
    _error()


def _move(destination, journal, source, target, *, phase, name, kind):
    journal.update(phase=phase, operation={'kind': kind, 'path': name})
    _write(destination / 'journal.json', journal, replace=True)
    # The caller has just verified both fixed locations, including absence.
    if os.path.lexists(target):
        _error()
    _rename(source, target)
    _sync(source.parent)
    _sync(target.parent)


def publish(destination):
    destination, install_dir, manifest, journal = _load(destination)
    _parents(destination, install_dir, manifest)
    # Refuse drift anywhere before starting another file mutation.
    for name, entry in manifest['files'].items():
        _state(destination, install_dir, name, entry)
    _parents(destination, install_dir, manifest, publish=True, journal=journal)
    for name, entry in manifest['files'].items():
        state, (target, backup, staged) = _state(destination, install_dir, name, entry)
        if state == 'candidate':
            continue
        if state == 'previous' and entry['previous'] is not None:
            _move(destination, journal, target, backup, phase='publishing', name=name, kind='retain')
        _state(destination, install_dir, name, entry)
        _move(destination, journal, staged, target, phase='publishing', name=name, kind='publish')
    verify(destination, selected='candidate')
    journal.update(phase='published', operation=None)
    _write(destination / 'journal.json', journal, replace=True)
    return {'status': 'published', 'files': len(manifest['files'])}


def restore(destination):
    destination, install_dir, manifest, journal = _load(destination)
    _parents(destination, install_dir, manifest)
    for name, entry in manifest['files'].items():
        _state(destination, install_dir, name, entry)
    for name, entry in reversed(list(manifest['files'].items())):
        state, (target, backup, staged) = _state(destination, install_dir, name, entry)
        if state == 'previous':
            continue
        if state == 'candidate':
            _move(destination, journal, target, staged, phase='restoring', name=name, kind='unpublish')
        _state(destination, install_dir, name, entry)
        if entry['previous'] is not None:
            _move(destination, journal, backup, target, phase='restoring', name=name, kind='restore')
    # Empty directories can return to their original staging slot. Foreign
    # content is preserved in place; no recursive removal is ever performed.
    for name, value in sorted(manifest['directories'].items(), key=lambda item: (-item[0].count('/'), item[0])):
        if value['previous'] is not None:
            continue
        path = install_dir / name
        slot = destination / 'directories' / value['slot']
        if os.path.lexists(path) and not any(path.iterdir()):
            if _directory(path) != value['candidate'] or os.path.lexists(slot):
                _error()
            _move(destination, journal, path, slot, phase='restoring', name=name, kind='directory-restore')
    verify(destination, selected='previous')
    journal.update(phase='restored', operation=None)
    _write(destination / 'journal.json', journal, replace=True)
    return {'status': 'restored', 'files': len(manifest['files'])}


def verify(destination, selected='candidate'):
    if selected not in ('candidate', 'previous'):
        _error()
    destination, install_dir, manifest, _journal = _load(destination)
    _parents(destination, install_dir, manifest)
    for name, entry in manifest['files'].items():
        if _state(destination, install_dir, name, entry)[0] != selected:
            _error()
    return True


def verify_staged(destination):
    destination, install_dir, manifest, _journal = _load(destination)
    _parents(destination, install_dir, manifest)
    expected = set(manifest['files'])
    expected_directories = {'.'}
    for name in expected:
        expected_directories.update(str(parent) for parent in Path(name).parents)
    observed = set()
    def visit(path):
        if path.is_dir() and not path.is_symlink():
            if str(path.relative_to(destination / 'candidate')) not in expected_directories:
                _error()
            _directory(path)
            for child in path.iterdir():
                visit(child)
        else:
            name = str(path.relative_to(destination / 'candidate'))
            if name not in expected or _file(path) != manifest['files'][name]['candidate']:
                _error()
            observed.add(name)
    visit(destination / 'candidate')
    if observed != expected:
        _error()
    for name, entry in manifest['files'].items():
        if _state(destination, install_dir, name, entry)[0] != 'previous':
            _error()
    return True
