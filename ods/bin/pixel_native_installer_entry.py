"""Publish and enter one closed, code-only native installer support capsule.

This is separate from runtime deployment custody. No configuration, key,
credential, private journal or owner data is copied into a support capsule.
"""
import ast
import ctypes
import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import threading
import uuid

SUPPORT_ROOT = Path('/usr/local/libexec/ods-pixel-installer-support')
ENTRY = 'bin/pixel_native_installer_entry.py'
MANIFEST = 'support-manifest.json'
MAX_FILE = 4 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024
PEERS = {'access-install': 'installers/macos/lib/pixel-macos-access-install.py',
         'native-uninstall': 'installers/macos/lib/pixel-native-uninstall.py'}
MAC_LIB = (
    'dashboard-private-state-worker.py', 'dashboard-private-state.py', 'native-checkpoint-args.py',
    'native-memory-budget.py', 'native-runtime-audit.py', 'pixel-access-launchd.py',
    'pixel-controller-held-proof-live.py', 'pixel-controller-held-proof.py', 'pixel-controller-proof-child.py',
    'pixel-controller-repair-live.py', 'pixel-controller-repair.py', 'pixel-macos-access-install.py',
    'pixel-native-acceptance.py', 'pixel-native-activate.py', 'pixel-native-bootstrap.py',
    'pixel-native-compose.py', 'pixel-native-config-volume.py', 'pixel-native-config.py',
    'pixel-native-continuation.py', 'pixel-native-docker-migrate.py', 'pixel-native-env.py',
    'pixel-native-finalize.py', 'pixel-native-ingress-config.py', 'pixel-native-ingress-refresh.py',
    'pixel-native-install.py', 'pixel-native-integrations-preservation.py', 'pixel-native-integrations-service.py',
    'pixel-native-layout.py', 'pixel-native-lifecycle-inputs.py', 'pixel-native-lifecycle-stack.py',
    'pixel-native-lifecycle.py', 'pixel-native-manager-service.py', 'pixel-native-migration.py',
    'pixel-native-node.py', 'pixel-native-onboarding.py', 'pixel-native-ops-account.py',
    'pixel-native-ops-service.py', 'pixel-native-ops-state.py', 'pixel-native-prepare.py',
    'pixel-native-promoter-service.py', 'pixel-native-readiness.py', 'pixel-native-recover.py',
    'pixel-native-retirement-reboot.py', 'pixel-native-services.py', 'pixel-native-source-broker.py',
    'pixel-native-stack.py', 'pixel-native-uninstall.py', 'pixel-native-update.py',
    'pixel-runtime-bundle.py', 'pixel-runtime-receipt.py', 'pixel-runtime-upgrade.py',
)
INSTALLER_LIB = ('cloud-config-permissions.py', 'dashboard-lock.py', 'pixel-example-projects.py',
    'pixel-onboarding.py', 'pixel-preview-inspection.py', 'pixel-project-runtime.py',
    'pixel-runtime-budget.py', 'pixel-workspace-guidance.py', 'wsl_stack.py')
HOST_CODE = (
    'access_mode_config.py', 'access_mode_http.mjs', 'access_mode_relay.mjs', 'access_mode_server.py',
    'access_mode_worker.py', 'access_release_transaction.py', 'approval_terminal.py', 'artifact_promoter.py',
    'cancellable-exec.sh', 'chat_history_ledger.mjs', 'chat_image_store.mjs', 'chat_image_transport.mjs',
    'connection_transaction.py', 'extension_manager.py', 'extension_search.py', 'integrations_app_service.py',
    'integrations_apps.py', 'integrations_common.py', 'integrations_mcp.py', 'integrations_notes.py',
    'integrations_ntfy.py', 'integrations_recovery.py', 'integrations_service.py', 'model_transaction.py',
    'native_ingress_config.mjs', 'native_mcp_oauth.mjs', 'native_mcp_owner.py', 'native_search.py',
    'noninteractive-sudo.sh', 'openclaw_tool_recovery.py', 'owner_tool_policy.mjs', 'pixel-wsl-runtime-bridge.sh',
    'pixel_access_mode.py', 'pixel_ingress.mjs', 'preview_inspection.py', 'preview_inspection_capsule.py',
    'preview_inspection_protocol.py', 'project_artifacts.py', 'project_authority.py', 'project_capabilities.py',
    'project_controller.py', 'project_diagnostics.py', 'project_dispatch.py', 'project_jobs.py',
    'project_owner_recovery.py', 'project_owner_resolution.py', 'project_runtime.py', 'project_runtime_protocol.py',
    'project_service.py', 'project_snapshot.py', 'project_storage.py', 'project_transport.py', 'provider_transaction.py',
    'questions_schema.mjs', 'scheduled_tasks_gateway.mjs', 'scheduled_tasks_owner.py', 'settings_transaction.py',
    'system_observe.py', 'task_activity_schema.mjs', 'unix_peer.py', 'workspace_preview.py',
)
_CANONICAL = 'pixel_native_installer_entry'
_SEAL = object()
_ACTIVE = None


def _encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n').encode()


def _sha(body):
    return hashlib.sha256(body).hexdigest()


def _relative(value):
    if (type(value) is not str or not value or value.startswith('/')
            or any(part in ('', '.', '..') for part in value.split('/'))
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError('native-installer-support-path-invalid')
    return value


def _code_relative(value):
    value = _relative(value)
    if not (value.endswith(('.py', '.mjs', '.sh')) or value == 'bin/ods-pixel-route-lease'):
        raise ValueError('native-installer-support-code-path-required')
    return value


def _acl(fd):
    libc = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    libc.acl_get_fd_np.argtypes = [ctypes.c_int, ctypes.c_int]
    libc.acl_get_fd_np.restype = ctypes.c_void_p
    libc.acl_get_entry.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
    libc.acl_get_entry.restype = ctypes.c_int
    libc.acl_valid.argtypes = [ctypes.c_void_p]
    libc.acl_valid.restype = ctypes.c_int
    libc.acl_free.argtypes = [ctypes.c_void_p]
    ctypes.set_errno(0)
    acl = libc.acl_get_fd_np(fd, 0x100)
    if not acl:
        if ctypes.get_errno() == errno.ENOENT:
            os.fstat(fd)
            return
        raise ValueError('native-installer-support-acl-unavailable')
    try:
        if libc.acl_valid(acl) != 0:
            raise ValueError('native-installer-support-acl-invalid')
        item = ctypes.c_void_p()
        ctypes.set_errno(0)
        if libc.acl_get_entry(acl, 0, ctypes.byref(item)) != -1 or ctypes.get_errno() != errno.EINVAL:
            raise ValueError('native-installer-support-acl-present')
    finally:
        libc.acl_free(acl)


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _mode(relative):
    return 0o755 if relative.endswith('.sh') or relative == 'bin/ods-pixel-route-lease' else 0o644


def _directory(path, *, protected):
    path = Path(path)
    if not path.is_absolute() or any(part in ('.', '..') for part in path.parts):
        raise ValueError('native-installer-support-path-invalid')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
            info = os.fstat(fd)
            if not stat.S_ISDIR(info.st_mode) or protected and (
                    info.st_uid != 0 or info.st_mode & 0o022):
                raise ValueError('native-installer-support-directory-unsafe')
            if protected:
                _acl(fd)
        result, fd = fd, None
        return result
    finally:
        if fd is not None:
            os.close(fd)


def _read(root, relative, *, protected=False, maximum=MAX_FILE, mode=0o644):
    relative = _relative(relative)
    directory = _directory(Path(root) / Path(relative).parent, protected=protected)
    try:
        fd = os.open(Path(relative).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > maximum
                    or protected and (before.st_uid != 0 or before.st_gid != 0 or stat.S_IMODE(before.st_mode) != mode)):
                raise ValueError('native-installer-support-file-unsafe')
            if protected:
                _acl(fd)
            body = bytearray()
            while len(body) <= maximum:
                chunk = os.read(fd, min(65536, maximum + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
            if (len(body) > maximum or _stamp(before) != _stamp(os.fstat(fd))
                    or _stamp(before) != _stamp(os.stat(Path(relative).name, dir_fd=directory, follow_symlinks=False))):
                raise ValueError('native-installer-support-source-changed')
            return bytes(body), _stamp(before)
        finally:
            os.close(fd)
    finally:
        os.close(directory)


def _sources(root):
    body, _info = _read(root, 'installers/macos/lib/pixel-native-source-broker.py')
    tree = ast.parse(body)
    values = [ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == 'ROOT_SOURCE_FILES' for target in node.targets)]
    if len(values) != 1 or type(values[0]) is not dict or len(values[0]) != 111:
        raise ValueError('native-installer-support-runtime-closure-changed')
    mapping = {_code_relative(target): _code_relative(origin) for target, origin in values[0].items()}
    for origin in tuple(mapping.values()):
        mapping.setdefault(origin, origin)
    for prefix, names in (('installers/macos/lib/', MAC_LIB), ('installers/lib/', INSTALLER_LIB),
            ('extensions/services/pixel-agent/host/', HOST_CODE)):
        for name in names:
            mapping[prefix + name] = prefix + name
    for name in (ENTRY, 'extensions/services/dashboard-api/env_values.py'):
        mapping[name] = name
    return dict(sorted(mapping.items()))


def _snapshot(source):
    mapping = _sources(source)
    bodies, images, records = {}, {}, {}
    for target, origin in mapping.items():
        body, image = _read(source, origin)
        bodies[target], images[target] = body, image
        records[target] = {'mode': _mode(target), 'bytes': len(body), 'sha256': _sha(body)}
    if sum(map(len, bodies.values())) > MAX_TOTAL:
        raise ValueError('native-installer-support-code-too-large')
    return mapping, bodies, images, records


def _root_directory(path):
    parent = _directory(path.parent, protected=True)
    try:
        try:
            os.mkdir(path.name, 0o755, dir_fd=parent)
            created = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                if os.fstat(created).st_uid != 0:
                    raise ValueError('native-installer-support-directory-unsafe')
                os.fchown(created, 0, 0)
                os.fchmod(created, 0o755)  # Only the directory just created here.
                _acl(created)
                os.fsync(created)
            finally:
                os.close(created)
            os.fsync(parent)
        except FileExistsError:
            pass
    finally:
        os.close(parent)
    fd = _directory(path, protected=True)
    os.close(fd)


def _verify_tree(root, code, *, expected=None):
    root = Path(root)
    body, _image = _read(root, MANIFEST, protected=True, maximum=1024 * 1024)
    manifest = json.loads(body)
    if (type(manifest) is not dict or set(manifest) != {'schemaVersion', 'kind', 'codeSha256', 'files'}
            or type(manifest['schemaVersion']) is not int or manifest['schemaVersion'] != 1
            or manifest['kind'] != 'ods-native-installer-support' or manifest['codeSha256'] != code
            or type(manifest['files']) is not dict or set(manifest['files']) != set(_sources(root))
            or _sha(_encoded(manifest['files'])) != code or _encoded(manifest) != body
            or expected is not None and manifest['files'] != expected):
        raise ValueError('native-installer-support-manifest-invalid')
    for name, record in manifest['files'].items():
        if (type(record) is not dict or set(record) != {'mode', 'bytes', 'sha256'}
                or type(record['mode']) is not int or record['mode'] != _mode(name)
                or type(record['bytes']) is not int or not 0 <= record['bytes'] <= MAX_FILE
                or type(record['sha256']) is not str or not re.fullmatch('[a-f0-9]{64}', record['sha256'])):
            raise ValueError('native-installer-support-manifest-invalid')
    if sum(record['bytes'] for record in manifest['files'].values()) > MAX_TOTAL:
        raise ValueError('native-installer-support-code-too-large')
    expected_names = {MANIFEST, *manifest['files']}
    directories = {''}
    for name in expected_names:
        directories.update(str(parent) for parent in Path(name).parents if str(parent) != '.')
    observed = set()
    def walk(path, prefix):
        directory = _directory(path, protected=True)
        try:
            if (os.fstat(directory).st_gid, stat.S_IMODE(os.fstat(directory).st_mode)) != (0, 0o755):
                raise ValueError('native-installer-support-directory-unsafe')
            for name in os.listdir(directory):
                relative = prefix + name
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    if relative not in directories:
                        raise ValueError('native-installer-support-unknown-entry')
                    walk(path / name, relative + '/')
                else:
                    if relative not in expected_names:
                        raise ValueError('native-installer-support-unknown-entry')
                    actual, _stamp_value = _read(root, relative, protected=True, mode=_mode(relative))
                    if relative != MANIFEST and manifest['files'][relative] != {
                            'mode': _mode(relative), 'bytes': len(actual), 'sha256': _sha(actual)}:
                        raise ValueError('native-installer-support-code-changed')
                    observed.add(relative)
        finally:
            os.close(directory)
    walk(root, '')
    if observed != expected_names or _read(root, MANIFEST, protected=True, maximum=1024 * 1024)[0] != body:
        raise ValueError('native-installer-support-code-changed')
    return manifest


def _verify(root, *, expected=None):
    root = Path(root)
    if root.parent != SUPPORT_ROOT or not re.fullmatch('[a-f0-9]{64}', root.name):
        raise ValueError('native-installer-support-location-invalid')
    return _verify_tree(root, root.name, expected=expected)


def stage_support(source_root, *, python):
    """Called by the authorized Root installer with its qualified interpreter."""
    if sys.platform != 'darwin' or os.geteuid() != 0:
        raise ValueError('macos-root-required')
    python = Path(python)
    if not python.is_absolute() or not os.access(python, os.X_OK):
        raise ValueError('native-installer-support-python-unqualified')
    # select_python owns interpreter capability qualification. This boundary
    # additionally rejects a caller-owned replacement or writable ancestor.
    directory = _directory(python.parent, protected=True)
    try:
        info = os.stat(python.name, dir_fd=directory, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or info.st_nlink != 1:
            raise ValueError('native-installer-support-python-unqualified')
    finally:
        os.close(directory)
    source = Path(source_root)
    mapping, bodies, images, records = _snapshot(source)
    code = _sha(_encoded(records))
    _root_directory(SUPPORT_ROOT.parent)
    _root_directory(SUPPORT_ROOT)
    root = SUPPORT_ROOT / code
    if os.path.lexists(root):
        _verify(root, expected=records)
    else:
        temporary = SUPPORT_ROOT / ('.' + code + '-' + uuid.uuid4().hex + '.pending')
        os.mkdir(temporary, 0o700)
        for relative, body in bodies.items():
            path = temporary / relative
            path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                os.fchown(fd, 0, 0)
                offset = 0
                while offset < len(body):
                    count = os.write(fd, body[offset:])
                    if count <= 0: raise ValueError('native-installer-support-write-failed')
                    offset += count
                os.fchmod(fd, _mode(relative))
                _acl(fd)
                os.fsync(fd)
            finally:
                os.close(fd)
        manifest = {'schemaVersion': 1, 'kind': 'ods-native-installer-support', 'codeSha256': code, 'files': records}
        fd = os.open(temporary / MANIFEST, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        try:
            body = _encoded(manifest)
            offset = 0
            while offset < len(body):
                count = os.write(fd, body[offset:])
                if count <= 0: raise ValueError('native-installer-support-write-failed')
                offset += count
            os.fchown(fd, 0, 0); os.fchmod(fd, 0o644)
            os.fsync(fd); _acl(fd)
        finally:
            os.close(fd)
        # Source contents and original inode/stamps must still be exact before
        # publishing any captured code as a Root support entry.
        if _sources(source) != mapping:
            raise ValueError('native-installer-support-source-changed')
        for relative, origin in mapping.items():
            if _read(source, origin) != (bodies[relative], images[relative]):
                raise ValueError('native-installer-support-source-changed')
        for directory, _folders, _files in os.walk(temporary, topdown=False, followlinks=False):
            os.chown(directory, 0, 0); os.chmod(directory, 0o755)
            fd = _directory(Path(directory), protected=True)
            try: os.fsync(fd)
            finally: os.close(fd)
        _verify_tree(temporary, code, expected=records)
        os.rename(temporary, root)
        fd = _directory(SUPPORT_ROOT, protected=True)
        try: os.fsync(fd)
        finally: os.close(fd)
        _verify(root, expected=records)
    if _sources(source) != mapping or any(_read(source, origin) != (bodies[target], images[target])
            for target, origin in mapping.items()):
        raise ValueError('native-installer-support-source-changed')
    return {'root': str(root), 'entry': str(root / ENTRY), 'codeSha256': code, 'python': str(python)}


def active_support():
    value = _ACTIVE
    if (sys.platform != 'darwin' or os.geteuid() != 0 or not sys.flags.isolated or not sys.dont_write_bytecode
            or sys.modules.get(_CANONICAL) is not sys.modules.get(__name__) or type(value) is not tuple
            or len(value) != 6 or value[0] is not _SEAL or value[3] != os.getpid()
            or value[4] is not threading.current_thread() or value[2] not in PEERS
            or value[1].parent != SUPPORT_ROOT or not re.fullmatch('[a-f0-9]{64}', value[1].name)
            or Path(__file__) != value[1] / ENTRY):
        return None
    return {'root': str(value[1]), 'codeSha256': value[1].name, 'peer': value[2]}


def _foreign_modules(root, manifest, *, initial=False):
    admitted = {str(root / name) for name in manifest['files']}
    prefixes = ('pixel_', 'access_mode_', 'access_release_', 'settings_transaction', 'provider_transaction',
        'model_transaction', 'connection_transaction', 'scheduled_tasks_', 'native_', 'unix_peer')
    for name, module in tuple(sys.modules.items()):
        if name.startswith(prefixes) and module is not sys.modules.get(__name__):
            if initial or getattr(module, '__file__', None) not in admitted:
                raise ValueError('native-installer-support-foreign-module')


def main(argv=None):
    global _ACTIVE
    if sys.platform != 'darwin' or os.geteuid() != 0 or not sys.flags.isolated or not sys.dont_write_bytecode:
        raise ValueError('native-installer-support-isolated-root-required')
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 3 or args[:1] != ['--peer'] or args[1] not in PEERS or args[2] != '--':
        raise ValueError('native-installer-support-peer-invalid')
    peer, arguments = args[1], args[3:]
    entry = Path(__file__)
    root = entry.parent.parent
    if entry != root / ENTRY:
        raise ValueError('native-installer-support-location-invalid')
    manifest = _verify(root)
    _foreign_modules(root, manifest, initial=True)
    current = sys.modules[__name__]
    previous = sys.modules.get(_CANONICAL)
    if previous is not None and previous is not current:
        raise ValueError('native-installer-support-foreign-module')
    sys.modules[_CANONICAL] = current  # One canonical identity, no peer replacement.
    binary = str(root / 'bin')
    if binary in sys.path:
        raise ValueError('native-installer-support-path-already-loaded')
    sys.path.insert(0, binary)
    if peer == 'access-install':
        positions = [index for index, value in enumerate(arguments) if value == '--source' or value.startswith('--source=')]
        if len(positions) > 1:
            raise ValueError('native-installer-support-source-argument-invalid')
        if positions:
            index = positions[0]
            if arguments[index] == '--source':
                if index + 1 >= len(arguments): raise ValueError('native-installer-support-source-argument-invalid')
                arguments[index + 1] = str(root)
            else:
                arguments[index] = '--source=' + str(root)
    _ACTIVE = (_SEAL, root, peer, os.getpid(), threading.current_thread(), object())
    old_argv = sys.argv
    try:
        _verify(root)
        spec = importlib.util.spec_from_file_location('_ods_native_installer_peer', root / PEERS[peer])
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _foreign_modules(root, manifest)
        sys.argv = [str(root / PEERS[peer]), *arguments]
        return module.main()
    finally:
        _ACTIVE = None
        sys.argv = old_argv


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, TypeError):
        print('error: native-installer-support-custody-failed', file=sys.stderr)
        raise SystemExit(1)
