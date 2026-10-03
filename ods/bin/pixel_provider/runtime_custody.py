"""Read-only qualification of an operator-installed protected provider runtime.

The fixed private root descriptor is installation data, never a public request.
It pins every runtime/source entry plus the interpreter and launcher. Creating a
descriptor is NOT qualification: every use checks the actual files. This module
does not install software, change services or confer root execution on Node.
"""
import hashlib
import os
import platform
import plistlib
import re
import stat
from pathlib import Path

from pixel_access_bridge import UNIT, AccessError, digest, remaining
from pixel_access_protocol import HEX
from pixel_settings.coordinator import _read

from .managed_deployment import deployment, required_policy
from .service_environment import _parents as _posix_parents

DESCRIPTOR = Path('/etc/ods/pixel-provider-runtime.json')
MAX_MANIFEST = 32 * 1024 * 1024
ROOT_UID = 0


def _descriptor_path():
    # /etc is Apple's OS alias. Only canonicalize our fixed descriptor, never
    # a path supplied by a descriptor or a public request.
    if platform.system() == 'Darwin' and DESCRIPTOR == Path('/etc/ods/pixel-provider-runtime.json'):
        return Path('/private/etc/ods/pixel-provider-runtime.json')
    return DESCRIPTOR


def _parents(path):
    _posix_parents(path)
    if platform.system() == 'Darwin':
        from pixel_macos_custody import CustodyError, protected_directory
        try:
            with protected_directory(path.parent):
                pass
        except (CustodyError, OSError):
            raise AccessError('provider-runtime-custody-unqualified') from None


def _acl(fd):
    if platform.system() == 'Darwin':
        from pixel_macos_custody import CustodyError, _require_no_acl
        try:
            _require_no_acl(fd)
        except CustodyError:
            raise AccessError('provider-runtime-custody-unqualified') from None


def _directory_acl(path, expected):
    if platform.system() != 'Darwin':
        return
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if os.fstat(fd) != expected:
            raise AccessError('provider-runtime-file-changed')
        _acl(fd)
    finally:
        os.close(fd)


def _regular(path, cache=None, *, maximum=None):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != ROOT_UID or info.st_nlink != 1
            or info.st_mode & 0o022):
        raise AccessError('provider-runtime-custody-unqualified')
    if maximum is not None and info.st_size > maximum:
        raise AccessError('provider-runtime-file-too-large')
    signature = (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
                 info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    cached = cache.get(str(path)) if cache is not None else None
    if cached is not None and cached[0] == signature and platform.system() != 'Darwin':
        return cached[1]
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        if opened != info:
            raise AccessError('provider-runtime-file-changed')
        # Cached bytes do not exempt a Darwin file from current ACL custody.
        _acl(fd)
        if cached is not None and cached[0] == signature:
            return cached[1]
        checksum = hashlib.sha256()
        consumed = 0
        with os.fdopen(fd, 'rb', closefd=False) as handle:
            while True:
                size = 1024 * 1024 if maximum is None else min(1024 * 1024, maximum + 1 - consumed)
                block = handle.read(size)
                if not block:
                    break
                consumed += len(block)
                if maximum is not None and consumed > maximum:
                    raise AccessError('provider-runtime-file-too-large')
                remaining(30)
                checksum.update(block)
        after = os.fstat(fd)
        current = path.lstat()
        if ((opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) !=
                (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)):
            raise AccessError('provider-runtime-file-changed')
        result = ['file', stat.S_IMODE(opened.st_mode), opened.st_size, checksum.hexdigest()]
        if cache is not None:
            cache[str(path)] = signature, result
        return result
    finally:
        os.close(fd)


def tree_manifest(root, cache=None):
    """All entries, including directories and resolved internal-only symlinks."""
    root = Path(root)
    _parents(root / 'entry')
    entries = {}
    def walk_error(error):
        raise error
    for directory, folders, files in os.walk(root, followlinks=False, onerror=walk_error):
        remaining(30)
        for name in sorted(folders + files):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            if info.st_uid != ROOT_UID or (not stat.S_ISLNK(info.st_mode) and info.st_mode & 0o022):
                raise AccessError('provider-runtime-custody-unqualified')
            if stat.S_ISLNK(info.st_mode):
                try:
                    resolved = path.resolve(strict=True)
                except (OSError, RuntimeError):
                    raise AccessError('provider-runtime-link-unqualified') from None
                if resolved != root and root not in resolved.parents:
                    raise AccessError('provider-runtime-link-unqualified')
                entries[relative] = ['link', os.readlink(path)]
            elif stat.S_ISDIR(info.st_mode):
                _directory_acl(path, info)
                entries[relative] = ['directory', stat.S_IMODE(info.st_mode)]
            else:
                entries[relative] = _regular(path, cache)
            if len(entries) > 200000:
                raise AccessError('provider-runtime-manifest-too-large')
    return entries


class RuntimeCustody:
    def __init__(self, bridge):
        self.bridge = bridge
        # Per-operation only; never persist or share across request handlers.
        # Every use still checks the complete entry set, links and file metadata.
        # Only root-protected, unchanged files may reuse their verified hash.
        self._cache = {}

    def inspect(self):
        descriptor = _descriptor_path()
        _parents(descriptor)
        descriptor_record = _regular(descriptor, self._cache, maximum=16384)
        value, descriptor_sha = _read(descriptor, 0, 16384)
        if descriptor_sha != descriptor_record[-1]:
            raise AccessError('provider-runtime-file-changed')
        keys = {'schemaVersion', 'runtimeRoot', 'sourceRoot', 'node', 'launcher',
                'hostPython', 'manifest', 'manifestSha256', 'policy'}
        if (type(value) is not dict or set(value) != keys
                or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
                or type(value['manifestSha256']) is not str or not HEX.fullmatch(value['manifestSha256'])):
            raise AccessError('provider-runtime-descriptor-unqualified')
        for key in ('runtimeRoot', 'sourceRoot', 'node', 'launcher', 'hostPython', 'manifest'):
            if type(value[key]) is not str:
                raise AccessError('provider-runtime-descriptor-unqualified')
            _parents(Path(value[key]))
        manifest_record = _regular(Path(value['manifest']), self._cache, maximum=MAX_MANIFEST)
        manifest, checksum = _read(Path(value['manifest']), 0, MAX_MANIFEST)
        if checksum != manifest_record[-1]:
            raise AccessError('provider-runtime-file-changed')
        if checksum != value['manifestSha256'] or type(manifest) is not dict:
            raise AccessError('provider-runtime-manifest-changed')
        actual = {'runtime': tree_manifest(value['runtimeRoot'], self._cache), 'source': tree_manifest(value['sourceRoot'], self._cache),
                  'node': _regular(Path(value['node']), self._cache), 'launcher': _regular(Path(value['launcher']), self._cache),
                  'hostPython': _regular(Path(value['hostPython']), self._cache)}
        if actual != manifest or value['launcher'] != self.bridge.binary:
            raise AccessError('provider-runtime-custody-unqualified')
        # A root-selected launcher must select this exact protected Node/entry,
        # clear Node injection variables, and preserve all owner CLI arguments.
        expected = ('#!/bin/sh\nexec /usr/bin/env -u NODE_OPTIONS -u NODE_PATH '
                    + value['node'] + ' ' + value['runtimeRoot'] + '/openclaw.mjs "$@"\n').encode()
        if Path(value['launcher']).read_bytes() != expected:
            raise AccessError('provider-runtime-launcher-unqualified')
        # Restrict paths used in this fixed launcher grammar; no shell expansion.
        if any(not re.fullmatch(r'/[A-Za-z0-9_./-]+', value[key]) for key in ('node', 'runtimeRoot')):
            raise AccessError('provider-runtime-launcher-unqualified')
        if required_policy(value['policy']) != value['policy']:
            raise AccessError('provider-runtime-policy-unqualified')
        return value, digest([value, manifest])

    def qualify(self):
        return self.inspect()[1]

    def require_worker(self, directory, expected_custody):
        """Owner-only source/runtime verification while root holds store lock."""
        value, checksum = self.inspect()
        if checksum != expected_custody:
            raise AccessError('provider-runtime-custody-changed')
        try:
            receipt, _ = _read(Path(directory) / 'advice-runtime.json', self.bridge.owner.pw_uid, 8192)
        except FileNotFoundError:
            raise AccessError('provider-worker-runtime-not-ready') from None
        result = self.bridge.worker('provider-worker-status', provider_probe={
            'python': value['hostPython'],
            'launcher': str(Path(value['sourceRoot']) / 'bin/ods-pixel-route-lease'),
            'providerDirectory': str(directory), 'receipt': receipt})
        if result != {'ready': True}:
            raise AccessError('provider-worker-runtime-not-ready')

    def verify_process(self):
        from pixel_settings.coordinator import _identity
        identity = _identity(self.bridge)
        value, _ = self.inspect()
        if platform.system() == 'Darwin':
            # OpenClaw deliberately overwrites argv with its process title, so
            # the launchd plist and libproc identity are the source of truth.
            # The launcher is still a protected, exact wrapper; its shebang
            # and exec line bind the selected Node/runtime entrypoint.
            process = getattr(self.bridge, 'gateway_process', None)
            if (type(process) is not dict or set(process) != {'uid', 'gid', 'executable'}
                    or process['executable'] != value['node']):
                raise AccessError('provider-runtime-process-unqualified')
            try:
                from pixel_macos_process import ProcessIdentityError, process_identity
                observed = process_identity(identity['pid'], **process)
            except ProcessIdentityError as error:
                raise AccessError(str(error)) from None
            if observed[-1] != value['node']:
                raise AccessError('provider-runtime-process-unqualified')
            plist = getattr(self.bridge.gateway_service, 'plist', None)
            if not isinstance(plist, (str, Path)):
                raise AccessError('provider-runtime-process-unqualified')
            try:
                from pixel_macos_custody import CustodyError, protected_bytes
                document = plistlib.loads(protected_bytes(plist))
            except (CustodyError, OSError, ValueError, TypeError, plistlib.InvalidFileException):
                raise AccessError('provider-runtime-process-unqualified') from None
            arguments = document.get('ProgramArguments') if isinstance(document, dict) else None
            if (not isinstance(arguments, list) or len(arguments) < 4
                    or arguments[:2] != ['/usr/bin/env', '-i']
                    or any(not isinstance(argument, str) or not argument for argument in arguments)):
                raise AccessError('provider-runtime-process-unqualified')
            command = []
            for argument in arguments[2:]:
                if not command and '=' in argument and argument.partition('=')[0].isidentifier():
                    continue
                command.append(argument)
            # The fixed Seatbelt profile is part of the root-owned launchd
            # boundary. The protected launcher remains the first executable
            # inside that boundary and still selects the exact Node/runtime.
            if (len(command) < 6 or command[:2] != ['/usr/bin/sandbox-exec', '-f']
                    or not re.fullmatch(r'/etc/ods/[A-Za-z0-9_./-]+', command[2])
                    or command[3:5] != [value['launcher'], 'gateway']):
                raise AccessError('provider-runtime-process-unqualified')
        else:
            process = Path('/proc') / str(identity['pid'])
            # Bind the systemd command to the protected launcher; MainPID/start/
            # boot and the enclosing transaction's unit fingerprint bind the
            # actual process. systemd can clear ExecStart's historical pid.
            command = self.bridge.command(['systemctl', 'show', UNIT, '--property=ExecStart', '--value'])
            prefix = '{ path=' + value['launcher'] + ' ; argv[]=' + value['launcher'] + ' gateway '
            if (not command.startswith(prefix) or command.count('{ path=') != 1
                    or (process / 'exe').resolve() != Path(value['node'])):
                raise AccessError('provider-runtime-process-unqualified')
        if _identity(self.bridge) != identity:
            raise AccessError('provider-process-changed')

    def deployment(self, binding, directory):
        value, checksum = self.inspect()
        return (deployment(binding, value['sourceRoot'], value['hostPython'], str(directory), True),
                value['policy'], checksum)
