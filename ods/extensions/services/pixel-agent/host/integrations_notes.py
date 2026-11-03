"""Bounded Markdown operations anchored to the owner's opened vault directory."""
from contextlib import contextmanager
import os
import pathlib
import re
import stat
from integrations_common import IntegrationError, clip, digest

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
def _parent(root_fd, parts, *, create=False, expected=None, identities=None):
    fd = os.dup(root_fd)
    try:
        for index, part in enumerate(parts[:-1]):
            previous = expected['parents'][index] if expected is not None and index < len(expected['parents']) else None
            if create and previous is None:
                if expected is not None and index == len(expected['parents']):
                    _check_approval_identity(fd, expected)
                try:
                    os.mkdir(part, mode=0o755, dir_fd=fd)
                except FileExistsError:
                    if expected is not None:
                        raise IntegrationError('notes-changed', 'The note destination changed. Request a new approval.') from None
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            if identities is not None:
                info = os.fstat(following)
                identities.append([part, _review_identity(info)])
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


def _review_identity(info, *, file=False):
    if not isinstance(getattr(info, 'st_ctime_ns', None), int):
        raise IntegrationError('review-only-context-unavailable', 'The note identity freshness could not be verified.')
    result = {'device': info.st_dev, 'inode': info.st_ino, 'ctimeNs': info.st_ctime_ns}
    for field in ('st_birthtime_ns', 'st_birthtime', 'st_gen'):
        if hasattr(info, field):
            result[field] = getattr(info, field)
    if file:
        if not isinstance(getattr(info, 'st_mtime_ns', None), int):
            raise IntegrationError('review-only-context-unavailable', 'The note identity freshness could not be verified.')
        result.update(size=info.st_size, mtimeNs=info.st_mtime_ns)
    return result


def _approval_identity(identity):
    # For a new note the destination is the deepest existing directory.
    # For append it is the file itself. Inodes can be reused after unlink.
    if identity['target'] is not None:
        return identity['target']
    return identity['parents'][-1][1] if identity['parents'] else identity['root']


def _check_approval_identity(fd, snapshot, *, file=False):
    stamp = snapshot.get('approvalIdentity')
    if stamp is not None and _review_identity(os.fstat(fd), file=file) != stamp:
        raise IntegrationError('notes-changed', 'The note destination changed. Request a new approval.')


def _snapshot(root_fd, parts, *, _review=False):
    info = os.fstat(root_fd)
    result = {'root': [info.st_dev, info.st_ino], 'parents': [], 'missing': [], 'target': None}
    if _review:
        result['_reviewIdentity'] = {'root': _review_identity(info), 'parents': [], 'target': None}
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
            if _review:
                result['_reviewIdentity']['parents'].append([part, _review_identity(info)])
        try:
            info = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return result
        _regular(info)
        result['target'] = [info.st_dev, info.st_ino]
        if _review:
            result['_reviewIdentity']['target'] = _review_identity(info, file=True)
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


def write_binding(config, relative, mode, *, reference_context=None, for_approval=False):
    validate_write('binding', mode)
    root = vault(config)
    parts, relative = _parts(relative)
    with _root(root) as fd:
        _check_review_source(config, fd, reference_context)
        snapshot = _snapshot(fd, parts, _review=reference_context is not None or for_approval)
        identity = snapshot.pop('_reviewIdentity', None)
        _check_review_target(relative, {'vault': str(root), **snapshot}, reference_context, identity=identity)
        if for_approval:
            snapshot['approvalIdentity'] = _approval_identity(identity)
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


def _review_source(config, root_fd, *, info=None):
    epoch = config.get('approvalBindings', {}).get('notes')
    if not isinstance(epoch, str) or not re.fullmatch(r'[a-f0-9]{32}', epoch):
        raise IntegrationError('review-only-context-unavailable', 'Review and save the notes connection before retrying.')
    info = os.fstat(root_fd) if info is None else info
    return digest('review-obsidian-source-v1', config['notes'], epoch, _review_identity(info))


def _check_review_source(config, root_fd, context):
    if context is not None and _review_source(config, root_fd) != context['binding']:
        raise IntegrationError('stale-review-reference', 'The notes connection or vault changed. No note was read or proposed.')


def _check_review_target(relative, binding, context, *, identity=None):
    expected = next((entry['binding'] for entry in context['entries'] if entry['path'] == relative), None) if context else None
    if expected is not None and expected != digest('review-note-target-v1', binding, identity):
        raise IntegrationError('stale-review-reference', 'The referenced note destination changed. No note was read or proposed.')


def _read(root, relative, *, config=None, reference_context=None, _review=False):
    parts, relative = _parts(relative)
    parents = []
    with _root(root) as root_fd:
        _check_review_source(config, root_fd, reference_context)
        review_target = _review or reference_context is not None
        root_info = os.fstat(root_fd) if review_target else None
        source_binding = _review_source(config, root_fd, info=root_info) if _review else None
        with _parent(root_fd, parts, identities=parents if review_target else None) as parent:
            try:
                fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=parent)
            except FileNotFoundError:
                raise IntegrationError('note-missing', f'There is no note {relative}.') from None
            except OSError:
                raise _invalid() from None
            try:
                if review_target:
                    info = os.fstat(fd)
                    # The parent descriptors are still open while traversing;
                    # _parent records their identity stamps, not a later stat.
                    legacy_parents = [[part, stamp['device'], stamp['inode']] for part, stamp in parents]
                    binding = {'vault': str(root), 'root': [root_info.st_dev, root_info.st_ino],
                               'parents': legacy_parents, 'missing': [], 'target': [info.st_dev, info.st_ino]}
                    identity = {'root': _review_identity(root_info), 'parents': parents, 'target': _review_identity(info, file=True)}
                    _check_review_target(relative, binding, reference_context, identity=identity)
                    entry = {'path': relative, 'binding': digest('review-note-target-v1', binding, identity)}
                text = _read_fd(fd)
                # This receipt describes the descriptors admitted before this
                # successful bounded read, never a later path replacement.
                return (relative, text, source_binding, entry) if _review else (relative, text)
            finally:
                os.close(fd)


def search(config, query, *, _review=False, reference_context=None):
    root = vault(config)
    terms = [term for term in re.findall(r'\w+', query.lower()) if len(term) > 1][:12]
    if not terms:
        raise IntegrationError('invalid-query', 'Give one or more words to search for.')
    hits, count, limited = [], 0, False

    def walk(directory, prefix='', depth=0, parents=(), stamps=()):
        nonlocal count, limited
        if depth > 64 or count >= MAX_NOTE_FILES:
            limited = True
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
                        next_parents = parents
                        next_stamps = stamps
                        if _review:
                            child_info = os.fstat(child)
                            next_parents = (*parents, [name, child_info.st_dev, child_info.st_ino])
                            next_stamps = (*stamps, [name, _review_identity(child_info)])
                        walk(child, relative + '/', depth + 1, next_parents, next_stamps)
                    finally:
                        os.close(child)
                    continue
                if not name.lower().endswith('.md') or not stat.S_ISREG(info.st_mode):
                    continue
                count += 1
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                             dir_fd=directory)
                try:
                    target_info = os.fstat(fd) if _review else None
                    target_stamp = _review_identity(target_info, file=True) if _review else None
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
                hit = {'path': relative, 'title': title, 'line': best + 1,
                       'snippet': lines[best].strip()[:240], 'matched': len(matched), 'of': len(terms)}
                if _review:
                    hit['_referenceBinding'] = digest('review-note-target-v1', {'vault': str(root), 'root': root_identity,
                        'parents': list(parents), 'missing': [], 'target': [target_info.st_dev, target_info.st_ino]},
                        {'root': root_stamp, 'parents': list(stamps), 'target': target_stamp})
                hits.append((score, hit))
            except (OSError, IntegrationError):
                if _review:
                    limited = True
                continue

    # One opened vault anchors the complete traversal, including renamed parents.
    with _root(root) as root_fd:
        _check_review_source(config, root_fd, reference_context)
        root_info = os.fstat(root_fd) if _review else None
        root_identity = [root_info.st_dev, root_info.st_ino] if _review else None
        root_stamp = _review_identity(root_info) if _review else None
        source_binding = _review_source(config, root_fd) if _review else None
        walk(root_fd)
    hits.sort(key=lambda item: (-item[0], item[1]['path']))
    results = [item for _score, item in hits[:8]]
    if _review:
        entries = [{'path': item['path'], 'binding': item.pop('_referenceBinding')} for item in results]
        return results, source_binding, entries, limited or count >= MAX_NOTE_FILES or len(hits) > 8
    return results


def read(config, relative, *, reference_context=None):
    relative, text = _read(vault(config), relative, config=config, reference_context=reference_context)
    return relative, clip(text, MAX_NOTE_READ_CHARS)


def read_review(config, relative, *, reference_context=None):
    relative, text, binding, entry = _read(vault(config), relative, config=config,
                                         reference_context=reference_context, _review=True)
    return relative, clip(text, MAX_NOTE_READ_CHARS), binding, [entry]


def write(config, relative, text, mode, *, expected_binding=None):
    validate_write(text, mode)
    root = vault(config)
    parts, relative = _parts(relative)
    with _root(root) as root_fd:
        for_approval = expected_binding is not None and 'approvalIdentity' in expected_binding
        snapshot = _snapshot(root_fd, parts, _review=for_approval)
        if for_approval:
            snapshot['approvalIdentity'] = _approval_identity(snapshot.pop('_reviewIdentity'))
        binding = {'vault': str(root), **snapshot}
        if expected_binding is not None and binding != expected_binding:
            raise IntegrationError('notes-changed', 'The note destination changed. Request a new approval.')
        if mode == 'append' and snapshot['target'] is None:
            raise IntegrationError('note-missing', f'There is no note {relative}.')
        with _parent(root_fd, parts, create=mode == 'create', expected=snapshot) as parent:
            if mode == 'create' and not snapshot['missing']:
                _check_approval_identity(parent, snapshot)
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
                if mode == 'append':
                    _check_approval_identity(fd, snapshot, file=True)
                existing = _read_fd(fd) if mode == 'append' else ''
                separator = '' if existing.endswith('\n\n') or not existing else '\n' if existing.endswith('\n') else '\n\n'
                payload = (separator + text.rstrip() + '\n').encode()
                while payload:
                    payload = payload[os.write(fd, payload):]
            finally:
                os.close(fd)
    return relative
