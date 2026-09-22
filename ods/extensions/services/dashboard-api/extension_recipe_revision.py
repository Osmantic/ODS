"""Recoverable replacement of recipe files, under coordinator/lifecycle locks.

The caller verifies the owner, terminal operation and both recipes. This module
does not grant revision authority, stop processes, change settings or install.
Only definition files are touched; application data stays in its directory.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile

FILES = frozenset({'manifest.yaml', 'compose.yaml', 'upstream.json', '.ods-library-receipt.json'})
LIMIT = 524288


def _read(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > LIMIT:
        raise ValueError('Recipe file requires inspection')
    return path.read_bytes()


def _atomic(path, content):
    descriptor, temporary = tempfile.mkstemp(prefix='.revision-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            if path.exists():
                os.fchmod(stream.fileno(), path.stat().st_mode & 0o777)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _digest(content):
    return hashlib.sha256(content).hexdigest()


def stage_revision(journal, directories, replacements, *, context=None):
    """Persist old and new bytes before touching a live definition.

    directories is a caller-owned mapping such as library/user. replacements
    maps each location to existing definition filenames and desired bytes.
    The journal must be private to the checked request/operation.
    """
    journal = Path(journal)
    if journal.exists() or journal.is_symlink() or journal.parent.is_symlink():
        raise ValueError('Revision journal already exists or is unsafe')
    entries = []
    for location, files in replacements.items():
        directory = Path(directories[location])
        if directory.is_symlink() or not directory.is_dir() or not files or not set(files) <= FILES:
            raise ValueError('Invalid recipe revision target')
        for name, desired in files.items():
            if not isinstance(desired, bytes) or len(desired) > LIMIT:
                raise ValueError('Invalid replacement recipe file')
            previous = _read(directory / name)
            entries.append({'location': location, 'name': name,
                            'old': base64.b64encode(previous).decode('ascii'),
                            'new': base64.b64encode(desired).decode('ascii')})
    if not entries or len(entries) > 8:
        raise ValueError('Invalid revision size')
    record = {'schemaVersion': 1, 'entries': entries}
    if context is not None:
        record['context'] = context
    _atomic(journal, json.dumps(record).encode())


def read_revision_context(journal):
    """Read caller-owned identities from the same durable file transaction."""
    journal = Path(journal)
    if journal.is_symlink() or not journal.is_file() or journal.stat().st_size > 12 * 1024 * 1024:
        raise ValueError('Invalid revision journal')
    record = json.loads(journal.read_bytes())
    if (not isinstance(record, dict) or record.get('schemaVersion') != 1
            or set(record) != {'schemaVersion', 'entries', 'context'}
            or not isinstance(record['context'], dict)):
        raise ValueError('Invalid revision context')
    return record['context']


def recover_revision(journal, directories, *, rollback=False):
    """Idempotently apply or roll back a persisted revision before binding it.

    A file may contain exactly its saved old or new bytes. Anything else means
    another writer changed it: abort before writing any file. Keep the journal
    for reconciliation until the request binding and operation journal commit.
    """
    journal = Path(journal)
    if journal.is_symlink() or not journal.is_file() or journal.stat().st_size > 12 * 1024 * 1024:
        raise ValueError('Invalid revision journal')
    record = json.loads(journal.read_bytes())
    if (not isinstance(record, dict) or set(record) not in
            ({'schemaVersion', 'entries'}, {'schemaVersion', 'entries', 'context'})
            or record['schemaVersion'] != 1 or not isinstance(record['entries'], list)
            or not 1 <= len(record['entries']) <= 8):
        raise ValueError('Invalid revision journal')
    changes, seen = [], set()
    for entry in record['entries']:
        if (not isinstance(entry, dict) or set(entry) != {'location', 'name', 'old', 'new'}
                or entry['location'] not in directories or entry['name'] not in FILES):
            raise ValueError('Invalid revision entry')
        directory = Path(directories[entry['location']])
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError('Invalid revision directory')
        path = directory / entry['name']
        if path in seen:
            raise ValueError('Duplicate revision entry')
        seen.add(path)
        old = base64.b64decode(entry['old'], validate=True)
        new = base64.b64decode(entry['new'], validate=True)
        if max(len(old), len(new)) > LIMIT:
            raise ValueError('Oversized revision entry')
        current = _read(path)
        if _digest(current) not in {_digest(old), _digest(new)}:
            raise ValueError('Recipe changed outside this revision')
        changes.append((path, current, old if rollback else new))
    for path, expected, desired in changes:
        if _read(path) != expected:
            raise ValueError('Recipe changed during revision')
        if desired != expected:
            _atomic(path, desired)


def commit_bound_revision(file_journal, directories, installation_journal,
                          service_id, expected_attempt, old_binding, new_binding,
                          read_binding, bind_new, observe):
    """Finish an already validated/staged revision, including crash recovery.

    The caller holds coordinator, service and request locks and preserves this
    transaction's identities with its journal. No new installation is dispatched.
    Binding reads must reject expired/cancelled/wrong-owner requests.
    """
    from extension_installation import verify_failed_attempt, retire_failed_attempt
    if (not isinstance(old_binding, dict) or not isinstance(new_binding, dict)
            or old_binding == new_binding
            or old_binding.get('extensionId') != service_id
            or new_binding.get('extensionId') != service_id):
        raise ValueError('Invalid revision bindings')
    current = read_binding()
    if current not in (old_binding, new_binding):
        raise ValueError('Request changed during revision')
    attempt = installation_journal.records.get(service_id)
    if attempt is None:
        # A lost reply after retirement may be replayed. Only the committed
        # binding can reconcile it, and the files must still match the journal.
        if current != new_binding:
            raise ValueError('Old request has no failed installation attempt')
        recover_revision(file_journal, directories)
        return new_binding
    if verify_failed_attempt(installation_journal, service_id, observe) != expected_attempt:
        raise ValueError('Installation attempt changed during revision')
    try:
        recover_revision(file_journal, directories)
        if current == old_binding:
            bind_new()
        if read_binding() != new_binding:
            raise ValueError('Revised binding was not committed')
    except Exception:
        # A response lost after a successful binding must not revert its files.
        # If request observation is unavailable, keep the journal for recovery.
        if read_binding() == old_binding:
            recover_revision(file_journal, directories, rollback=True)
        raise
    retire_failed_attempt(installation_journal, service_id, expected_attempt, observe)
    return new_binding
