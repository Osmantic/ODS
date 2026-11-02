"""Bounded Markdown operations anchored to the owner's opened vault directory."""
from contextlib import contextmanager
import os
import pathlib
import re
import stat
from integrations_common import IntegrationError, clip

MAX_NOTE_BYTES = 1024 * 1024
MAX_NOTE_FILES = 5000
MAX_NOTE_READ_CHARS = 20000
MAX_WRITE_CHARS = 20000


def _invalid():
    return IntegrationError('invalid-note', 'Notes outside the vault, symbolic links, shared files and hidden folders cannot be used.')


def vault(config):
    notes = config['notes']
    if not notes['enabled']:
        raise IntegrationError('notes-off', 'Notes are not set up. The owner can connect an Obsidian vault in Settings → Portal integrations.')
    root = pathlib.Path(notes['vaultPath'])
    if root.is_symlink() or not root.is_dir():
        raise IntegrationError('notes-unavailable', 'The notes folder is not available.')
    return root


def _parts(relative):
    if not isinstance(relative, str) or not relative.strip() or len(relative) > 512 or '\0' in relative:
        raise _invalid()
    relative = relative.strip()
    if relative.startswith('/') or '\\' in relative:
        raise _invalid()
    if not relative.lower().endswith('.md'):
        relative += '.md'
    parts = relative.split('/')
    if any(not part or part.startswith('.') for part in parts):
        raise _invalid()
    return parts, '/'.join(parts)


@contextmanager
def _root(root):
    root = pathlib.Path(root)
    if not root.is_absolute() or '..' in root.parts:
        raise _invalid()
    fd = None
    try:
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        for part in root.parts[1:]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = following
    except OSError:
        if fd is not None:
            os.close(fd)
        raise _invalid() from None
    try:
        yield fd
    finally:
        os.close(fd)


@contextmanager
def _parent(root_fd, parts, *, create=False, expected=None):
    fd = os.dup(root_fd)
    try:
        for index, part in enumerate(parts[:-1]):
            previous = expected['parents'][index] if expected is not None and index < len(expected['parents']) else None
            if create and previous is None:
                try:
                    os.mkdir(part, mode=0o755, dir_fd=fd)
                except FileExistsError:
                    if expected is not None:
                        raise IntegrationError('notes-changed', 'The note destination changed. Request a new approval.') from None
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            if previous is not None:
                info = os.fstat(following)
                if previous != [part, info.st_dev, info.st_ino]:
                    os.close(following)
                    raise IntegrationError('notes-changed', 'The note destination changed. Request a new approval.')
            os.close(fd)
            fd = following
        yield fd
    except OSError:
        raise _invalid() from None
    finally:
        os.close(fd)


def _regular(info):
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise _invalid()
    if info.st_size > MAX_NOTE_BYTES:
        raise IntegrationError('note-too-large', 'That note is too large to read or append to.')


def _snapshot(root_fd, parts):
    info = os.fstat(root_fd)
    result = {'root': [info.st_dev, info.st_ino], 'parents': [], 'missing': [], 'target': None}
    fd = os.dup(root_fd)
    try:
        for index, part in enumerate(parts[:-1]):
            try:
                following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            except FileNotFoundError:
                result['missing'] = parts[index:-1]
                return result
            os.close(fd)
            fd = following
            info = os.fstat(fd)
            result['parents'].append([part, info.st_dev, info.st_ino])
        try:
            info = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return result
        _regular(info)
        result['target'] = [info.st_dev, info.st_ino]
        return result
    except OSError:
        raise _invalid() from None
    finally:
        os.close(fd)


def note_path(root, relative, *, must_exist):
    parts, relative = _parts(relative)
    with _root(root) as fd:
        snapshot = _snapshot(fd, parts)
    if must_exist and snapshot['target'] is None:
        raise IntegrationError('note-missing', f'There is no note {relative}.')
    return pathlib.Path(root).joinpath(*parts), relative


def write_binding(config, relative, mode):
    validate_write('binding', mode)
    root = vault(config)
    parts, relative = _parts(relative)
    with _root(root) as fd:
        snapshot = _snapshot(fd, parts)
    if mode == 'append' and snapshot['target'] is None:
        raise IntegrationError('note-missing', f'There is no note {relative}.')
    if mode == 'create' and snapshot['target'] is not None:
        raise IntegrationError('note-exists', f'{relative} already exists; add to it instead.')
    return relative, {'vault': str(root), **snapshot}


def validate_write(text, mode):
    if mode not in ('append', 'create') or not isinstance(text, str) or not text.strip() or len(text) > MAX_WRITE_CHARS:
        raise IntegrationError('invalid-note', 'Give the text to add, up to 20,000 characters.')


def _read_fd(fd):
    _regular(os.fstat(fd))
    chunks, size = [], 0
    while chunk := os.read(fd, min(65536, MAX_NOTE_BYTES + 1 - size)):
        chunks.append(chunk)
        size += len(chunk)
        if size > MAX_NOTE_BYTES:
            raise IntegrationError('note-too-large', 'That note is too large to read.')
    return b''.join(chunks).decode('utf-8', errors='replace')


def _read(root, relative):
    parts, relative = _parts(relative)
    with _root(root) as root_fd, _parent(root_fd, parts) as parent:
        try:
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=parent)
        except FileNotFoundError:
            raise IntegrationError('note-missing', f'There is no note {relative}.') from None
        except OSError:
            raise _invalid() from None
        try:
            return relative, _read_fd(fd)
        finally:
            os.close(fd)


def search(config, query):
    root = vault(config)
    terms = [term for term in re.findall(r'\w+', query.lower()) if len(term) > 1][:12]
    if not terms:
        raise IntegrationError('invalid-query', 'Give one or more words to search for.')
    hits, count = [], 0

    def walk(directory, prefix='', depth=0):
        nonlocal count
        if depth > 64 or count >= MAX_NOTE_FILES:
            return
        for name in sorted(os.listdir(directory)):
            if name.startswith('.') or count >= MAX_NOTE_FILES:
                continue
            relative = prefix + name
            try:
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                    dir_fd=directory)
                    try:
                        walk(child, relative + '/', depth + 1)
                    finally:
                        os.close(child)
                    continue
                if not name.lower().endswith('.md') or not stat.S_ISREG(info.st_mode):
                    continue
                count += 1
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                             dir_fd=directory)
                try:
                    text = _read_fd(fd)
                finally:
                    os.close(fd)
                title = pathlib.PurePosixPath(name).stem
                lowered = text.lower()
                matched = [term for term in terms if term in lowered or term in title.lower()]
                if not matched:
                    continue
                score = len(matched) * 100 + sum(lowered.count(term) for term in matched) + 20 * sum(term in title.lower() for term in matched)
                lines = text.splitlines() or ['']
                best = max(range(len(lines)), key=lambda index: (sum(term in lines[index].lower() for term in terms),
                                                                 not lines[index].lstrip().startswith('#')))
                hits.append((score, {'path': relative, 'title': title, 'line': best + 1,
                                    'snippet': lines[best].strip()[:240], 'matched': len(matched), 'of': len(terms)}))
            except (OSError, IntegrationError):
                continue

    # One opened vault anchors the complete traversal, including renamed parents.
    with _root(root) as root_fd:
        walk(root_fd)
    hits.sort(key=lambda item: (-item[0], item[1]['path']))
    return [item for _score, item in hits[:8]]


def read(config, relative):
    relative, text = _read(vault(config), relative)
    return relative, clip(text, MAX_NOTE_READ_CHARS)


def write(config, relative, text, mode, *, expected_binding=None):
    validate_write(text, mode)
    root = vault(config)
    parts, relative = _parts(relative)
    with _root(root) as root_fd:
        snapshot = _snapshot(root_fd, parts)
        binding = {'vault': str(root), **snapshot}
        if expected_binding is not None and binding != expected_binding:
            raise IntegrationError('notes-changed', 'The note destination changed. Request a new approval.')
        if mode == 'append' and snapshot['target'] is None:
            raise IntegrationError('note-missing', f'There is no note {relative}.')
        with _parent(root_fd, parts, create=mode == 'create', expected=snapshot) as parent:
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
            flags |= os.O_CREAT | os.O_EXCL if mode == 'create' else os.O_APPEND
            try:
                fd = os.open(parts[-1], flags, 0o644, dir_fd=parent)
            except FileExistsError:
                raise IntegrationError('note-exists', f'{relative} already exists; add to it instead.') from None
            except OSError:
                raise _invalid() from None
            try:
                info = os.fstat(fd)
                _regular(info)
                if mode == 'append' and [info.st_dev, info.st_ino] != snapshot['target']:
                    raise IntegrationError('notes-changed', 'The note destination changed. Request a new approval.')
                existing = _read_fd(fd) if mode == 'append' else ''
                separator = '' if existing.endswith('\n\n') or not existing else '\n' if existing.endswith('\n') else '\n\n'
                payload = (separator + text.rstrip() + '\n').encode()
                while payload:
                    payload = payload[os.write(fd, payload):]
            finally:
                os.close(fd)
    return relative
