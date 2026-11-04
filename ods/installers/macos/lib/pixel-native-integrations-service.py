"""Root-published native owner integrations, with a separately authenticated owner channel.

The owner selected arbitrary local MCP programs, so this owner service is not
put in the gateway's filesystem sandbox. The gateway gets only the agent
endpoint; its policy explicitly seals the private owner state and credential.
Physical launchd/Seatbelt qualification remains required on Apple Silicon.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import pwd
import re
import stat
import subprocess
import sys


SOURCES = frozenset({"integrations_common.py", "integrations_mcp.py", "integrations_notes.py",
    "integrations_service.py", "unix_peer.py", "integrations_app_service.py",
    "integrations_apps.py", "integrations_ntfy.py", "integrations_recovery.py"})
RUNTIME = Path('/private/var/lib/ods-pixel-integrations')
AUTH_ROOT = Path('/private/var/lib/ods-pixel-integrations-auth')
LOGS = Path('/private/var/log/ods-pixel-integrations')
LABEL = 'com.ods.pixel-native-integrations'


def operations_helpers():
    spec = importlib.util.spec_from_file_location('integrations_publication_helpers',
        Path(__file__).with_name('pixel-native-ops-service.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def manager_helpers():
    spec = importlib.util.spec_from_file_location('integrations_log_helpers',
        Path(__file__).with_name('pixel-native-manager-service.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def owner_entry(owner):
    if not isinstance(owner, str) or not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', owner) or owner == 'root':
        raise ValueError('invalid-native-integrations-owner')
    entry = pwd.getpwnam(owner)
    if entry.pw_uid <= 0 or entry.pw_gid < 0:
        raise ValueError('invalid-native-integrations-owner')
    home = Path(entry.pw_dir)
    if not home.is_absolute() or home == Path('/') or home.resolve() != home:
        raise ValueError('canonical-native-integrations-home-required')
    return entry


def state_directory(entry):
    state = Path(entry.pw_dir) / '.config/ods/pixel-integrations'
    # sockaddr_un.sun_path is104 bytes on Darwin, including the trailing NUL.
    if len(os.fsencode(state / 'owner.sock')) >= 104:
        raise ValueError('native-integrations-owner-socket-path-too-long')
    return state


def render(*, python, program_root, owner, runtime=RUNTIME, logs=LOGS, label=LABEL):
    entry = owner_entry(owner)
    state = state_directory(entry)
    paths = [Path(value) for value in (python, program_root, runtime, logs)]
    if label != LABEL or any(not p.is_absolute() or '..' in p.parts or p == Path('/') for p in paths):
        raise ValueError('invalid-native-integrations-path')
    python, program_root, runtime, logs = paths
    if any(a == b or a in b.parents or b in a.parents
           for a in (runtime, logs, state, AUTH_ROOT) for b in (python, program_root)):
        raise ValueError('native-integrations-boundary-overlap')
    if runtime == logs or runtime in logs.parents or logs in runtime.parents:
        raise ValueError('native-integrations-boundary-overlap')
    document = {'Label': label, 'UserName': owner,
        'ProgramArguments': ['/usr/bin/env', '-i', 'PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin',
            'HOME=' + entry.pw_dir, 'USER=' + owner, 'LOGNAME=' + owner,
            'TMPDIR=' + str(runtime), str(python), '-I', '-B',
            str(program_root / 'integrations_service.py'), 'serve', str(runtime / 'control.sock'), str(state)],
        'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 10, 'ExitTimeOut': 300,
        'Umask': 0o077, 'WorkingDirectory': '/', 'ProcessType': 'Background',
        'StandardInPath': '/dev/null', 'StandardOutPath': str(logs / 'stdout.log'),
        'StandardErrorPath': str(logs / 'stderr.log'),
        'SoftResourceLimits': {'NumberOfFiles': 1024}, 'HardResourceLimits': {'NumberOfFiles': 4096}}
    return {'plist': plistlib.dumps(document, sort_keys=True)}


def publication_files(*, sources, expected_sha256, python, program_root, owner, definition,
                      runtime=RUNTIME, logs=LOGS, label=LABEL):
    if sys.platform != 'darwin' or os.geteuid() != 0:
        raise ValueError('macos-root-required')
    if type(sources) is not dict or type(expected_sha256) is not dict or set(sources) != SOURCES or set(expected_sha256) != SOURCES:
        raise ValueError('approved-integrations-source-set-required')
    for name in sorted(SOURCES):
        body = sources[name]
        if type(body) is not bytes or not 0 < len(body) <= 1024 * 1024 or hashlib.sha256(body).hexdigest() != expected_sha256[name]:
            raise ValueError('approved-integrations-snapshot-required')
        compile(body, name, 'exec')
    rendered = render(python=python, program_root=program_root, owner=owner, runtime=runtime, logs=logs, label=label)
    if Path(definition).name != label + '.plist':
        raise ValueError('integrations-definition-label-mismatch')
    operations_helpers().custody.protected_bytes(str(python), limit=128 * 1024 * 1024)
    files = [(Path(program_root) / name, sources[name], 0o644, 0) for name in sorted(SOURCES)]
    files.append((Path(definition), rendered['plist'], 0o644, 0))
    return files


def publish(**options):
    files = publication_files(**options)
    installer = operations_helpers().installer_helpers()
    for path, body, mode, gid in files:
        installer._preflight_file(path, body, mode=mode, uid=0, gid=gid)
    entry = owner_entry(options['owner'])
    manager_helpers().prepare_logs(options.get('logs', LOGS), uid=entry.pw_uid, gid=entry.pw_gid)
    for path, body, mode, gid in files:
        installer._write_exact(path, body, mode=mode, uid=0, gid=gid)
    return Path(options['definition'])


def _acl(path, entry):
    subprocess.run(['/bin/chmod', '+a', entry, str(path)], check=True,
        stdin=subprocess.DEVNULL, capture_output=True, timeout=10)


def _owner_state(entry, python):
    """The owner creates its directories with the published fixed helper.

    A crash between root mkdir/chown must never leave a root-owned .config in
    the real home. No owner command or mutable source is executed by root.
    """
    state = state_directory(entry)
    source = '/usr/local/libexec/ods-pixel-services/integrations/integrations_common.py'
    custody = operations_helpers().custody
    custody.protected_bytes(source, limit=1024 * 1024)
    custody.protected_bytes(str(python), limit=128 * 1024 * 1024)
    result = subprocess.run([str(python), '-I', '-B', source, '--prepare-native-state', str(state)],
        user=entry.pw_uid, group=entry.pw_gid, extra_groups=[], cwd='/',
        env={'PATH': '/usr/bin:/bin', 'HOME': entry.pw_dir}, stdin=subprocess.DEVNULL,
        capture_output=True, timeout=15)
    if result.returncode != 0 or result.stdout.strip() != b'{"schemaVersion":1,"status":"prepared"}':
        raise ValueError('native-integrations-private-state-preparation-failed')
    return state


def provision(owner, *, reuse=False, python=None, resume_partial=False):
    """Provision fixed runtime/auth and preserve all previously saved owner data.

    Called with the deployment lock and stopped services. Existing runtime/auth
    is accepted only for an explicitly journaled upgrade/recovery, never adopted
    by a new install. Credentials are never included in publication snapshots.
    """
    if sys.platform != 'darwin' or os.geteuid() != 0:
        raise ValueError('macos-root-required')
    if type(resume_partial) is not bool or resume_partial and not reuse:
        raise ValueError('native-integrations-preservation-intent-required')
    entry = owner_entry(owner)
    state_directory(entry)  # Nonmutating pathname preflight first.
    python = operations_helpers().select_python() if python is None else python
    custody = operations_helpers().custody
    if resume_partial:
        # Only the controller's exact legacy-preservation transaction may opt
        # into this. Active new-service deployments never repair drift this way.
        _resume_private_preparation(entry, custody)
    # Managed replacement applies publication_files through its journal rather
    # than publish(). Provision must therefore prepare launchd's logs too.
    manager_helpers().prepare_logs(LOGS, uid=entry.pw_uid, gid=entry.pw_gid)
    _validate_logs(entry, custody)
    with custody.protected_directory(LOGS) as logs:
        for name in ('stdout.log', 'stderr.log'):
            fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=logs)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        os.fsync(logs)
    with custody.protected_directory(RUNTIME.parent) as runtime_parent:
        if os.path.lexists(RUNTIME):
            info = RUNTIME.lstat()
            if not reuse or not stat.S_ISDIR(info.st_mode) or info.st_uid != entry.pw_uid or stat.S_IMODE(info.st_mode) != 0o700:
                raise ValueError('new-native-integrations-runtime-required')
            _validate_runtime(entry, custody)
        else:
            RUNTIME.mkdir(mode=0o700)
            os.chown(RUNTIME, entry.pw_uid, entry.pw_gid)
        os.fsync(runtime_parent)
    with custody.protected_directory(AUTH_ROOT, create=True) as auth_parent:
        folder = AUTH_ROOT / str(entry.pw_uid)
        if set(os.listdir(AUTH_ROOT)) - {str(entry.pw_uid)}:
            raise ValueError('unsafe-native-integrations-auth')
        if os.path.lexists(folder):
            if not reuse:
                raise ValueError('new-native-integrations-auth-required')
            # Verify without emitting or rewriting the established credential.
            _published_common().native_owner_key(entry.pw_uid)
            fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                if set(os.listdir(fd)) != {'owner.key'}:
                    raise ValueError('unsafe-native-integrations-auth')
            finally:
                os.close(fd)
        else:
            folder.mkdir(mode=0o700)
            try:
                key = folder / 'owner.key'
                fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(os.urandom(32))
                    stream.flush()
                    os.fsync(stream.fileno())
                _acl(key, 'user:' + owner + ' allow read,readattr,readextattr,readsecurity')
                _acl(folder, 'user:' + owner + ' allow list,search,readattr,readsecurity')
                _published_common().native_owner_key(entry.pw_uid)
                fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            except BaseException:
                # Preserve any partial credential for explicit recovery, rather
                # than silently rotating authority on the next attempt.
                raise
        os.fsync(auth_parent)
    for parent in {AUTH_ROOT.parent, LOGS.parent}:
        with custody.protected_directory(parent) as directory:
            os.fsync(directory)
    _owner_state(entry, python)
    _published_common().validate_native_state(state_directory(entry), uid=entry.pw_uid)


def _validate_discovery(directory, entry, custody):
    """Preserve the service's bounded hint; it grants no execution authority.

    A stopped service's snapshot may be expired. Custody and the closed
    producer format still apply; freshness belongs to the prompt consumer.
    """
    def invalid(): raise ValueError('unsafe-native-integrations-discovery')
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value: invalid()
            value[key] = item
        return value
    def stamp(info):
        return (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    fd = os.open('discovery.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        before = os.fstat(fd)
        custody._require_no_acl(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or (before.st_uid, before.st_gid, stat.S_IMODE(before.st_mode)) != (entry.pw_uid, entry.pw_gid, 0o600)
                or not 0 < before.st_size <= 8192): invalid()
        with os.fdopen(fd, 'rb', closefd=False) as stream: raw = stream.read(8193)
        after = os.fstat(fd)
        named = os.stat('discovery.json', dir_fd=directory, follow_symlinks=False)
        custody._require_no_acl(fd)
        if len(raw) != before.st_size or stamp(before) != stamp(after) or stamp(before) != stamp(named): invalid()
        try:
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=lambda _text: invalid())
        except (ValueError, UnicodeError, RecursionError):
            invalid()
        if (type(value) is not dict or set(value) != {'version', 'revision', 'validUntil', 'servers', 'truncated'}
                or type(value['version']) is not int or value['version'] != 1
                or any(type(value[key]) is not int or value[key] < 0 for key in ('revision', 'validUntil'))
                or type(value['truncated']) is not bool or type(value['servers']) is not list
                or len(value['servers']) > 20): invalid()
        identities = set()
        for server in value['servers']:
            if (type(server) is not dict or set(server) != {'id', 'name', 'tools'}
                    or type(server['id']) is not str or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,31}', server['id'])
                    or server['id'] in identities or type(server['name']) is not str or len(server['name']) > 96
                    or re.search(r'[/\\\x00-\x1f\x7f]', server['name'])
                    or type(server['tools']) is not list or len(server['tools']) > 200): invalid()
            identities.add(server['id'])
            tools = set()
            for tool in server['tools']:
                if (type(tool) is not dict or set(tool) != {'name', 'ask'} or type(tool['name']) is not str
                        or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', tool['name'])
                        or tool['name'] in tools or type(tool['ask']) is not bool): invalid()
                tools.add(tool['name'])
    finally:
        os.close(fd)


def _validate_runtime(entry, custody):
    fd = os.open(RUNTIME, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        names = set(os.listdir(fd))
        if (info.st_uid != entry.pw_uid or info.st_gid != entry.pw_gid
                or stat.S_IMODE(info.st_mode) != 0o700 or names - {'control.sock', 'discovery.json'}):
            raise ValueError('unsafe-native-integrations-runtime')
        custody._require_no_acl(fd)
        if 'discovery.json' in names:
            _validate_discovery(fd, entry, custody)
        try:
            sock = os.stat('control.sock', dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if (not stat.S_ISSOCK(sock.st_mode) or sock.st_uid != entry.pw_uid or sock.st_gid != entry.pw_gid
                or stat.S_IMODE(sock.st_mode) != 0o600 or sock.st_nlink != 1):
            raise ValueError('unsafe-native-integrations-socket')
    finally:
        os.close(fd)


def validate_retirement(owner):
    """Read-only custody checks before stop/quarantine; no repair or deletion."""
    if sys.platform != 'darwin' or os.geteuid() != 0:
        raise ValueError('macos-root-required')
    entry = owner_entry(owner)
    custody = operations_helpers().custody
    with custody.protected_directory(RUNTIME.parent):
        _validate_runtime(entry, custody)
    with custody.protected_directory(AUTH_ROOT):
        if set(os.listdir(AUTH_ROOT)) != {str(entry.pw_uid)}:
            raise ValueError('unsafe-native-integrations-auth')
        common = _published_common()
        common.native_owner_key(entry.pw_uid)
        fd = os.open(AUTH_ROOT / str(entry.pw_uid), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            if set(os.listdir(fd)) != {'owner.key'}:
                raise ValueError('unsafe-native-integrations-auth')
        finally:
            os.close(fd)
    _validate_logs(entry, custody)
    return True


def _validate_logs(entry, custody):
    with custody.protected_directory(LOGS) as directory:
        if set(os.listdir(directory)) != {'stdout.log', 'stderr.log'}:
            raise ValueError('unsafe-native-integrations-logs')
        for name in ('stdout.log', 'stderr.log'):
            fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
            try:
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != entry.pw_uid or info.st_gid != entry.pw_gid
                        or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
                    raise ValueError('unsafe-native-integrations-log')
                custody._require_no_acl(fd)
            finally:
                os.close(fd)


def _published_common(program_root='/usr/local/libexec/ods-pixel-services/integrations'):
    path = Path(program_root) / 'integrations_common.py'
    body = operations_helpers().custody.protected_bytes(str(path), limit=1024 * 1024)
    return _common_from_body(body, path)


def _common_from_body(body, path='/usr/local/libexec/ods-pixel-services/integrations/integrations_common.py'):
    if type(body) is not bytes or not 0 < len(body) <= 1024 * 1024:
        raise ValueError('approved-integrations-common-required')
    spec = importlib.util.spec_from_loader('native_integrations_common', loader=None)
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(path)
    exec(compile(body, str(path), 'exec'), module.__dict__)
    return module


def _acl_preparation_state(common, fd, uid, *, directory=False):
    """Only the two exact states produced by our ordered ACL preparation."""
    try:
        common.verify_native_no_acl(fd)
        return False
    except ValueError:
        common.verify_native_owner_acl(fd, uid, directory=directory)
        return True


def _resume_private_preparation(entry, custody):
    source = '/usr/local/libexec/ods-pixel-services/integrations/integrations_common.py'
    body = custody.protected_bytes(source, limit=1024 * 1024)
    validate_preserved(entry.pw_name, common_body=body, allow_partial=True)
    common = _common_from_body(body)
    if os.path.lexists(LOGS):
        with custody.protected_directory(LOGS) as directory:
            for name in os.listdir(directory):
                fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
                try:
                    if os.fstat(fd).st_uid == 0:
                        # Prefix validation proved this is our empty file,
                        # interrupted before its atomic ownership transfer.
                        os.fchown(fd, entry.pw_uid, entry.pw_gid)
                        os.fsync(fd)
                finally:
                    os.close(fd)
            os.fsync(directory)
    if os.path.lexists(RUNTIME):
        fd = os.open(RUNTIME, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            if os.fstat(fd).st_uid == 0:
                os.fchown(fd, entry.pw_uid, entry.pw_gid)
                os.fsync(fd)
        finally:
            os.close(fd)
    folder = AUTH_ROOT / str(entry.pw_uid)
    if not os.path.lexists(folder):
        return
    directory = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        path = folder / 'owner.key'
        fd = os.open('owner.key', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 0
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or not 0 <= info.st_size <= 32):
                raise ValueError('unsafe-native-integrations-auth')
            # An incomplete key was never activated. Preserve every existing
            # byte and append only the missing random suffix; never truncate,
            # replace or rotate a completed credential.
            remaining = os.urandom(32 - info.st_size)
            os.lseek(fd, info.st_size, os.SEEK_SET)
            while remaining:
                written = os.write(fd, remaining)
                if written <= 0:
                    raise OSError('native-integrations-key-write-incomplete')
                remaining = remaining[written:]
            os.fsync(fd)
            if not _acl_preparation_state(common, fd, entry.pw_uid):
                _acl(path, 'user:' + entry.pw_name + ' allow read,readattr,readextattr,readsecurity')
                common.verify_native_owner_acl(fd, entry.pw_uid)
                os.fsync(fd)
        finally:
            os.close(fd)
        if not _acl_preparation_state(common, directory, entry.pw_uid, directory=True):
            _acl(folder, 'user:' + entry.pw_name + ' allow list,search,readattr,readsecurity')
            common.verify_native_owner_acl(directory, entry.pw_uid, directory=True)
        os.fsync(directory)
    finally:
        os.close(directory)
    common.native_owner_key(entry.pw_uid)


def validate_preserved(owner, *, common_body, allow_partial=True):
    """Read-only validation under an independently verified preservation record.

    The caller must bind common_body to the exact approved candidate source,
    prior deployment/owner/install identity and all three fixed private roots.
    No paths or source bytes may come from an owner/agent request. Missing roots
    support interrupted preparation/quarantine; present roots accept only our
    explicit preparation prefixes, never arbitrary contents or ACL grants.
    """
    if sys.platform != 'darwin' or os.geteuid() != 0 or type(allow_partial) is not bool:
        raise ValueError('macos-root-required')
    entry, custody = owner_entry(owner), operations_helpers().custody
    common = _common_from_body(common_body)
    if not allow_partial:
        _validate_runtime(entry, custody)
        common.native_owner_key(entry.pw_uid)
        _validate_logs(entry, custody)
        return True
    with custody.protected_directory(RUNTIME.parent):
        if os.path.lexists(RUNTIME):
            fd = os.open(RUNTIME, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                info, names = os.fstat(fd), set(os.listdir(fd))
                if (info.st_uid, info.st_gid) == (0, 0):
                    if names or stat.S_IMODE(info.st_mode) != 0o700:
                        raise ValueError('unsafe-native-integrations-runtime')
                    custody._require_no_acl(fd)
                else:
                    _validate_runtime(entry, custody)
            finally:
                os.close(fd)
    if os.path.lexists(LOGS):
        with custody.protected_directory(LOGS) as fd:
            info, names = os.fstat(fd), set(os.listdir(fd))
            if stat.S_IMODE(info.st_mode) != 0o755 or names not in (set(), {'stdout.log'}, {'stdout.log', 'stderr.log'}):
                raise ValueError('unsafe-native-integrations-logs')
            for name in sorted(names):
                child = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    item = os.fstat(child)
                    if (not stat.S_ISREG(item.st_mode) or item.st_nlink != 1 or stat.S_IMODE(item.st_mode) != 0o600
                            or (item.st_uid, item.st_gid) not in ((0, 0), (entry.pw_uid, entry.pw_gid))
                            or item.st_uid == 0 and item.st_size != 0):
                        raise ValueError('unsafe-native-integrations-log')
                    common.verify_native_no_acl(child)
                finally:
                    os.close(child)
    if os.path.lexists(AUTH_ROOT):
        with custody.protected_directory(AUTH_ROOT) as root:
            info, names = os.fstat(root), set(os.listdir(root))
            if stat.S_IMODE(info.st_mode) != 0o755 or names not in (set(), {str(entry.pw_uid)}):
                raise ValueError('unsafe-native-integrations-auth')
            if names:
                folder = os.open(str(entry.pw_uid), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
                try:
                    item, files = os.fstat(folder), set(os.listdir(folder))
                    if (item.st_uid != 0 or item.st_gid != 0 or stat.S_IMODE(item.st_mode) != 0o700
                            or files not in (set(), {'owner.key'})):
                        raise ValueError('unsafe-native-integrations-auth')
                    folder_ready = _acl_preparation_state(common, folder, entry.pw_uid, directory=True)
                    if not files:
                        if folder_ready:
                            raise ValueError('unsafe-native-integrations-auth-prefix')
                    else:
                        key = os.open('owner.key', os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=folder)
                        try:
                            item = os.fstat(key)
                            if (not stat.S_ISREG(item.st_mode) or item.st_uid != 0 or item.st_gid != 0
                                    or item.st_nlink != 1 or stat.S_IMODE(item.st_mode) != 0o600 or not 0 <= item.st_size <= 32):
                                raise ValueError('unsafe-native-integrations-auth')
                            key_ready = _acl_preparation_state(common, key, entry.pw_uid)
                            if (key_ready and item.st_size != 32) or (folder_ready and not key_ready):
                                raise ValueError('unsafe-native-integrations-auth-prefix')
                        finally:
                            os.close(key)
                finally:
                    os.close(folder)
    return True


def readiness(*, owner, python, program_root='/usr/local/libexec/ods-pixel-services/integrations'):
    """Prove both roles answer; never test providers, approve, notify or start MCP."""
    entry = owner_entry(owner)
    script = Path(program_root) / 'integrations_service.py'
    operations_helpers().custody.protected_bytes(str(script))
    result = subprocess.run([str(python), '-I', '-B', str(script), 'health',
        str(RUNTIME / 'control.sock'), str(state_directory(entry))],
        user=entry.pw_uid, group=entry.pw_gid, extra_groups=[], cwd='/',
        env={'PATH': '/usr/bin:/bin', 'HOME': entry.pw_dir}, stdin=subprocess.DEVNULL,
        capture_output=True, timeout=15)
    try:
        value = json.loads(result.stdout) if result.returncode == 0 else None
    except ValueError:
        value = None
    if value != {'schemaVersion': 1, 'status': 'ready', 'owner': True, 'agent': True}:
        raise ValueError('native-integrations-readiness-failed')
    return True
