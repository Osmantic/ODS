"""Remove an exact shipped fleet assumption, preserving owner workspace edits.

This ODS overlay does not modify the pinned Pixel source or release identity.
Run as the workspace owner, after configure and before plan for generated files,
and before the gateway restart for an existing owner workspace.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import sys

MAX_BYTES = 2 * 1024 * 1024
LEGACY_HEADING = b'## Dream Fleet Local-First Operating Contract (canonical)\n'
LEGACY_SECTION_SHA256 = 'f4ae5ee982981b26ec0b4130772d4ed6e7519eec0ae77650a777325bfa79b818'
LEGACY_MEMORY_SHA256 = '1f1c2722d83c4cd9a91a63c87a6bca94cea1f0d112c8aed36233d72ddaa89ec1'
MARKER = b'Dream Fleet Local-First Operating Contract'
GUIDANCE = b'''## Model routing and execution evidence

Use the owner's currently selected model and the capabilities actually available
in this ODS installation. A workspace template is not evidence that any particular
machine, model, fleet, or supervisory agent is installed or participating.
Do not assign work to imagined machines or report invented delegation or ratios.

Distinguish model inference from tool execution. A program running locally, a
local workspace, or a localhost model gateway does not establish that the model
itself runs locally: the gateway may route to a cloud provider. Describe local or
remote inference only when current runtime evidence establishes it. If unknown,
say so when relevant instead of claiming that no remote model was used.

Report work and tests actually performed. Existing owner authorization and
permission boundaries still apply; this guidance grants no new capabilities.

'''


def transform(name, body):
    """Replace only a byte-known default span, never an arbitrary named section."""
    body.decode('utf-8')  # Invalid text is not a safe migration candidate.
    newline = b'\r\n' if b'\r\n' in body else b'\n'
    if newline == b'\r\n' and b'\n' in body.replace(b'\r\n', b''):
        return body, 'manual-review-required' if MARKER in body else 'unchanged'
    normalized = body.replace(b'\r\n', b'\n')
    if MARKER not in normalized:
        return body, 'current' if GUIDANCE in normalized else 'unchanged'
    if name == 'AGENTS.md':
        if normalized.count(MARKER) != 1 or normalized.count(LEGACY_HEADING) != 1:
            return body, 'manual-review-required'
        start = normalized.index(LEGACY_HEADING)
        if start and normalized[start - 1:start] != b'\n':
            return body, 'manual-review-required'
        following = normalized.find(b'\n## ', start + len(LEGACY_HEADING))
        end = following + 1 if following >= 0 else len(normalized)
        legacy = normalized[start:end]
        if hashlib.sha256(legacy).hexdigest() != LEGACY_SECTION_SHA256:
            return body, 'manual-review-required'
        # Locate the exact bytes in the original document; prefix/suffix are
        # retained byte-for-byte, including the owner's line endings.
        old = legacy.replace(b'\n', newline)
        replacement = GUIDANCE.replace(b'\n', newline)
    else:
        lines = [line for line in normalized.splitlines(keepends=True) if MARKER in line]
        if len(lines) != 1 or hashlib.sha256(lines[0]).hexdigest() != LEGACY_MEMORY_SHA256:
            return body, 'manual-review-required'
        old, replacement = lines[0].replace(b'\n', newline), b''
    if body.count(old) != 1:
        return body, 'manual-review-required'
    return body.replace(old, replacement, 1), 'migrated'


def _identity(info):
    return info.st_dev, info.st_ino, info.st_uid, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _directory(path, *, generated=False):
    path = Path(path)
    if not path.is_absolute() or any(part in ('.', '..') for part in path.parts):
        raise ValueError('absolute workspace path required')
    descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        parent = None
        for part in path.parts[1:]:
            parent = os.fstat(descriptor)
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        info = os.fstat(descriptor)
        if info.st_uid != os.getuid():
            raise ValueError('private owner workspace required')
        if generated and (path.name != 'workspace' or path.parent.name != '.generated'
                          or parent is None or parent.st_uid != os.getuid() or parent.st_mode & 0o077):
            raise ValueError('private generated workspace parent required')
        if info.st_mode & 0o022:
            # Node's recursive cp preserves the template directory mode. Only
            # a generated workspace under its already private owner directory
            # may be tightened here; never chmod an existing owner workspace.
            if not generated:
                raise ValueError('private owner workspace required')
            os.fchmod(descriptor, 0o700)
        result, descriptor = descriptor, None
        return result
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _read(directory, name, *, generated=False):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                or info.st_size > MAX_BYTES):
            raise ValueError('unsafe workspace guidance file')
        if info.st_mode & 0o022:
            if not generated:
                raise ValueError('unsafe workspace guidance file')
            # Recursive cp can also preserve 0664 from a clone under umask 002.
            # _directory verified the private generated parent. This descriptor
            # is an owner-only, single regular file, never a followed link.
            os.fchmod(fd, 0o600)
            info = os.fstat(fd)
        chunks = bytearray()
        while len(chunks) <= MAX_BYTES:
            part = os.read(fd, min(65536, MAX_BYTES + 1 - len(chunks)))
            if not part:
                break
            chunks.extend(part)
        if len(chunks) > MAX_BYTES or _identity(info) != _identity(os.fstat(fd)):
            raise ValueError('workspace guidance changed during read')
        return bytes(chunks), info
    finally:
        os.close(fd)


def _revalidate(directory, path, name, body, info):
    current, observed = _read(directory, name)
    if current != body or _identity(info) != _identity(observed):
        raise ValueError('workspace guidance changed before replacement')
    check = _directory(path)
    try:
        original, resolved = os.fstat(directory), os.fstat(check)
        if (original.st_dev, original.st_ino) != (resolved.st_dev, resolved.st_ino):
            raise ValueError('workspace directory changed before replacement')
    finally:
        os.close(check)


def migrate_workspace(path, *, generated=False):
    if os.geteuid() == 0:
        raise ValueError('run workspace guidance migration as its non-root owner')
    directory = _directory(path, generated=generated)
    try:
        pending, statuses = [], {}
        # Validate both files before any mutation, including unchanged owner text.
        for name in ('AGENTS.md', 'MEMORY.md'):
            try:
                body, info = _read(directory, name, generated=generated)
            except FileNotFoundError:
                statuses[name] = 'absent'
                continue
            replacement, statuses[name] = transform(name, body)
            if replacement != body:
                pending.append((name, body, replacement, info))
        for name, body, replacement, info in pending:
            temporary = '.ods-guidance-' + secrets.token_hex(16)
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(replacement)
                    os.fchmod(stream.fileno(), stat.S_IMODE(info.st_mode))
                    stream.flush()
                    os.fsync(stream.fileno())
                _revalidate(directory, path, name, body, info)
                os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
                os.fsync(directory)
            finally:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass
        return statuses
    finally:
        os.close(directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--generated', action='store_true', help='prepare a private .generated/workspace only')
    args = parser.parse_args()
    result = migrate_workspace(args.workspace, generated=args.generated)
    print(json.dumps(result, sort_keys=True))
    if 'manual-review-required' in result.values():
        print('ODS preserved customized fleet guidance; review it against the selected model route.', file=sys.stderr)


if __name__ == '__main__':
    main()
