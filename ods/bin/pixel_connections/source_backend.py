"""Request-local Source custody. Business decisions stay in the coordinator.

The native capability is sealed to the actual Root bridge; it never changes a
module's paths, Run adapter, identity lookup or platform on another request.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import os
import re
import stat

from pixel_access_bridge import LaunchdAccessBridge
from . import custody as c

_SEAL = object()
PATH = frozenset(('discard_flow', 'discard_flows', 'write_handoff', 'connection_env', 'install_env',
    'direct_enabled', 'sign_in_identity', 'holds_sign_in', 'archive_google_actions', 'clear_google_data',
    'forget_sign_in', 'erase', 'remove_env'))
PATH_RUN = frozenset(('prepare', 'exchange', 'watch_direct', 'pause_direct', 'stop', 'quiesce_for_sign_in', 'revoke'))
RUN = frozenset(('start', 'sync'))


def protected_directory(path):
    # Linux ships only the shared Source backend. Native custody is loaded only
    # after the request-local sealed NativeSource path is selected.
    from pixel_macos_custody import protected_directory as native_directory
    return native_directory(path)


@dataclass(frozen=True)
class LinuxSource:
    paths: c.Paths
    run: object

    def broker_ids(self):
        return c.broker_ids()

    def recovering_environment(self):
        return False

    def __getattr__(self, name):
        if name in PATH:
            return lambda *args, **kwargs: getattr(c, name)(self.paths, *args, **kwargs)
        if name in PATH_RUN:
            return lambda *args, **kwargs: getattr(c, name)(self.paths, self.run, *args, **kwargs)
        if name in RUN:
            return lambda *args, **kwargs: getattr(c, name)(self.run, *args, **kwargs)
        raise AttributeError(name)


@dataclass(frozen=True)
class NativeSource:
    bridge: object
    seal: object

    def __post_init__(self):
        if type(self.bridge) is not LaunchdAccessBridge or self.seal is not _SEAL:
            raise c.CustodyError('native-source-backend-required')

    @property
    def selection(self):
        if type(self) is not NativeSource or self.seal is not _SEAL or type(self.bridge) is not LaunchdAccessBridge:
            raise c.CustodyError('native-source-backend-required')
        if LaunchdAccessBridge._native_runtime_lease(self.bridge) is None:
            raise c.CustodyError('native-source-backend-scope-required')
        LaunchdAccessBridge._native_source_deadline_ns(self.bridge)
        return c.native_source_selection(self.bridge, self.bridge.connection_deployment())

    def _view(self):
        from .native_executor import _view
        return _view(self.selection)

    def _files(self):
        """Root file custody only, including during withdrawal or after reboot.

        This validates the same sealed deployment, kit, identity and immutable
        gate/child directory inodes. It intentionally grants no invocation,
        process, environment publication or availability: only the executor's
        separate current-boot view can authorize those paths.
        """
        selection = self.selection
        _manifest, kit_sha = c.native_verify_kit(selection, _publication=True)
        with protected_directory(c.NATIVE_META) as metadata, protected_directory(c.NATIVE_STATE.parent) as parent:
            gate = os.open(c.NATIVE_STATE.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                identity, binding, _expected = c._native_environment_binding(selection, metadata, gate,
                    kit_sha, _publication=True)
                return selection, identity, binding
            finally:
                os.close(gate)

    @property
    def paths(self):
        self._files()
        return c.NativeSourcePaths(install=c.NATIVE_KIT, state=c.NATIVE_STATE, env=c.NATIVE_ENV, kit=c.NATIVE_KIT)

    @property
    def run(self):
        from .native_executor import NativeSourceExecutor
        return NativeSourceExecutor(self.selection)

    def broker_ids(self):
        if not os.path.lexists(c.NATIVE_STATE):
            raise KeyError(c.NATIVE_BROKER)
        _selection, identity, _binding = self._files()
        return identity['id'], identity['id']

    @contextmanager
    def _directory(self, relative):
        if relative not in ('private', 'private/oauth', 'projection', 'results', 'proposals'):
            raise c.CustodyError('native-source-directory-invalid')
        _selection, _identity, binding = self._files()
        with protected_directory(c.NATIVE_META) as metadata, protected_directory(c.NATIVE_STATE.parent) as parent:
            intent, _raw = c._native_json_at(metadata, 'source-custody-prepare.json')
            gate = os.open(c.NATIVE_STATE.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            directory = None
            try:
                directory = c._native_directory_fd(gate, relative)
                expected = intent['children'][relative]['inode']
                if (list((os.fstat(gate).st_dev, os.fstat(gate).st_ino)) != intent['gate']
                        or intent['gate'] != binding['gate']
                        or list((os.fstat(directory).st_dev, os.fstat(directory).st_ino)) != expected):
                    raise c.CustodyError('native-source-directory-changed')
                yield directory
                current = c._native_directory_fd(gate, relative)
                try:
                    if [os.fstat(current).st_dev, os.fstat(current).st_ino] != expected:
                        raise c.CustodyError('native-source-directory-changed')
                finally:
                    os.close(current)
                self._files()
            finally:
                if directory is not None:
                    os.close(directory)
                os.close(gate)

    @staticmethod
    def _file(directory, name, uid, *, limit=c.MAX_RESULT_BYTES, metadata_only=False):
        """Metadata-only for a token; a credential is never opened/read by Root."""
        if metadata_only:
            try:
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                return None, None
        else:
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            except FileNotFoundError:
                return None, None
            try:
                info = os.fstat(fd)
                NativeSource._safe_file(info, uid, limit)
                c._native_no_acl(fd)
                raw = bytearray()
                while len(raw) <= limit:
                    chunk = os.read(fd, min(65536, limit + 1 - len(raw)))
                    if not chunk:
                        break
                    raw.extend(chunk)
                if (NativeSource._stamp(os.fstat(fd)) != NativeSource._stamp(info) or len(raw) != info.st_size
                        or c._native_at_inode(directory, name) != [info.st_dev, info.st_ino]):
                    raise c.CustodyError('native-source-file-changed')
                return bytes(raw), info
            finally:
                os.close(fd)
        NativeSource._safe_file(info, uid, limit)
        return None, info

    @staticmethod
    def _stamp(info):
        return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    @staticmethod
    def _safe_file(info, uid, limit):
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != uid
                or info.st_gid != uid or stat.S_IMODE(info.st_mode) != 0o600 or not 0 <= info.st_size <= limit):
            raise c.CustodyError('native-source-private-file-unsafe')

    def prepare(self, *, reader):
        if reader != self.bridge.owner.pw_name:
            raise c.CustodyError('native-source-owner-changed')
        selection = self.selection
        from .native_executor import _publisher
        publisher = _publisher(selection)
        with protected_directory(c.NATIVE_META) as metadata:
            initial, _raw = c._native_json_at(metadata, 'source-initial-generation.json')
            initial_boot, _raw = c._native_json_at(metadata, 'source-initial-boot.json')
        if (initial is not None and (type(initial) is not dict or initial.get('phase') != 'committed')
                or c._native_initial_boot_pending(initial_boot)):
            from .native_executor import initial_quiesce_snapshot, quiesce_previous_boot
            snapshot = initial_quiesce_snapshot(selection)
            changed_boot = snapshot is not None and snapshot['boot'] != c._native_boot_uuid()
            if changed_boot:
                quiesce_previous_boot(selection)
            if changed_boot or c._native_initial_boot_pending(initial_boot):
                resume = getattr(publisher, 'resume_initial_generation_boot', None)
                if resume is None:
                    raise c.CustodyError('native-source-initial-boot-unavailable')
                resume(selection)
            initialize = getattr(publisher, 'publish_initial_generation', None)
            if initialize is None:
                raise c.CustodyError('native-source-initial-generation-unavailable')
            initialize(selection)
            self._view()
            return
        if (os.path.lexists(c.NATIVE_META / 'source-jobs-prepare.json')
                or os.path.lexists(c.NATIVE_META / 'source-environment-generation.json')):
            self._files()
            # Preparing another OAuth attempt also retires terminal/abandoned
            # one-shot admissions. Existing generation authority is never reused
            # merely because its environment text stayed the same.
            retire = any(os.path.lexists(c.NATIVE_META / name) for name in ('source-jobs-active.json', 'source-execution.json'))
            self.install_env(self._environment_text(), _retire=retire)
            return
        if not os.path.lexists(c.NATIVE_META / 'source-jobs-prepare.json'):
            manifest, _kit_sha = c.native_verify_kit(selection, _publication=True)
            if manifest['schemaVersion'] != 2 or not os.path.lexists(c.NATIVE_META / 'source-publication.json'):
                publisher.publish_kit(selection)
            identity = c.native_prepare_identity(selection)
            c.native_prepare_state(selection, identity)
            c.native_publish_environment(selection, c.native_environment_text(selection, preauth=True))
            c.native_prepare_logs(selection)
        initialize = getattr(publisher, 'publish_initial_generation', None)
        if initialize is None:
            raise c.CustodyError('native-source-initial-generation-unavailable')
        initialize(selection)
        self._view()

    def write_handoff(self, name, value):
        if type(name) is not str or not re.fullmatch('[a-f0-9]{32}\\.(?:flow|code)\\.json', name):
            raise c.CustodyError('invalid-flow')
        body = c._native_encoded(value)
        if len(body) > c.MAX_RESULT_BYTES:
            raise c.CustodyError('source-broker-result-invalid')
        uid, gid = self.broker_ids()
        with self._directory('private/oauth') as directory:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            try:
                os.fchown(fd, uid, gid)
                os.fchmod(fd, 0o600)
                with os.fdopen(os.dup(fd), 'wb') as stream:
                    stream.write(body)
                    stream.flush()
                    os.fsync(fd)
                self._safe_file(os.fstat(fd), uid, c.MAX_RESULT_BYTES)
                os.fsync(directory)
            finally:
                os.close(fd)

    def _discard(self, names):
        if not os.path.lexists(c.NATIVE_STATE):
            return
        uid, _gid = self.broker_ids()
        with self._directory('private/oauth') as directory:
            chosen = os.listdir(directory) if names is None else names
            for name in chosen:
                if not c.FLOW_FILE.fullmatch(name):
                    continue
                _raw, info = self._file(directory, name, uid, metadata_only=True)
                if info is not None:
                    if c._native_at_inode(directory, name) != [info.st_dev, info.st_ino]:
                        raise c.CustodyError('native-source-file-changed')
                    os.unlink(name, dir_fd=directory)
            os.fsync(directory)

    def discard_flow(self, flow_id):
        if type(flow_id) is not str or not c.FLOW_ID.fullmatch(flow_id):
            raise c.CustodyError('invalid-flow')
        self._discard([flow_id + '.' + suffix + '.json' for suffix in ('flow', 'code', 'result')])

    def discard_flows(self):
        self._discard(None)

    def sign_in_identity(self):
        if not os.path.lexists(c.NATIVE_STATE):
            return None
        uid, _gid = self.broker_ids()
        with self._directory('private') as directory:
            _raw, info = self._file(directory, 'google-token.json', uid, metadata_only=True)
            if info is None:
                return None
            return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
                info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode), info.st_nlink)

    def holds_sign_in(self):
        return self.sign_in_identity() is not None

    def forget_sign_in(self):
        if not os.path.lexists(c.NATIVE_STATE):
            return
        self._no_actors()
        uid, _gid = self.broker_ids()
        with self._directory('private') as directory:
            _raw, info = self._file(directory, 'google-token.json', uid, metadata_only=True)
            if info is not None:
                if c._native_at_inode(directory, 'google-token.json') != [info.st_dev, info.st_ino]:
                    raise c.CustodyError('native-source-file-changed')
                os.unlink('google-token.json', dir_fd=directory)
                os.fsync(directory)

    def _result(self, name, keys, kind):
        uid, _gid = self.broker_ids()
        with self._directory('private/oauth') as directory:
            raw, info = self._file(directory, name, uid)
            if raw is None:
                return None
            value = c.decode_oauth_result(raw, keys, kind)
            if c._native_at_inode(directory, name) != [info.st_dev, info.st_ino]:
                raise c.CustodyError('native-source-file-changed')
            os.unlink(name, dir_fd=directory)
            os.fsync(directory)
            return value

    def exchange(self, flow_id):
        if type(flow_id) is not str or not c.FLOW_ID.fullmatch(flow_id):
            raise c.CustodyError('invalid-flow')
        self.run(['systemctl', 'start', 'pixel-source-exchange@' + flow_id + '.service'], 120)
        result = self._result(flow_id + '.result.json', c.EXCHANGE_KEYS, 'pixel-source-oauth-result')
        self.discard_flow(flow_id)
        return c.exchange_result(result, flow_id)

    def start(self, *, direct):
        c.start(self.run, direct=direct)

    def sync(self):
        c.sync(self.run)

    def watch_direct(self, *, enabled):
        self.run(['systemctl', 'enable' if enabled else 'disable', '--now', 'pixel-source-direct.path'], 30)

    def stop(self):
        if not os.path.lexists(c.NATIVE_STATE):
            return
        from .native_executor import _boot_origin, quiesce_previous_boot
        snapshot, _doc, _descriptor, _definitions = _boot_origin(self.selection)
        if snapshot['previousBoot'] != c._native_boot_uuid() or snapshot['withdrawn']:
            quiesce_previous_boot(self.selection)
            return
        run = self.run
        for unit in ('pixel-source-broker.timer', 'pixel-source-direct.path'):
            c._check(run, ['systemctl', 'disable', '--now', unit])
        for unit in ('pixel-source-broker.service', 'pixel-source-direct.service'):
            c._check(run, ['systemctl', 'stop', unit])

    def quiesce_for_sign_in(self):
        if not os.path.lexists(c.NATIVE_STATE):
            return
        from .native_executor import _boot_origin, quiesce_previous_boot
        snapshot, _doc, _descriptor, _definitions = _boot_origin(self.selection)
        if snapshot['previousBoot'] != c._native_boot_uuid() or snapshot['withdrawn']:
            quiesce_previous_boot(self.selection)
            return
        self.stop()
        executor = self.run
        for job in tuple(executor._read()[0]['jobs'].values()):
            executor.stop(job['role'], job['identifier'])
        self._no_actors()

    def _no_actors(self):
        selection = self.selection
        from .native_executor import _publisher
        self._files()
        with protected_directory(c.NATIVE_META) as metadata:
            return _publisher(selection)._no_actors(selection, c, metadata, _generation=True)

    def direct_enabled(self):
        if not os.path.lexists(c.NATIVE_STATE):
            return False
        return dict(c.parse_env(self._environment_text())).get(c.DIRECT_FLAG) == '1'

    def connection_env(self, **options):
        selection = self.selection
        lines, _checksum = c._native_environment_template(selection, _publication=True)
        return c.native_environment_text(selection, c.connection_overrides(lines, **options), _publication=True)

    def _environment_text(self):
        _selection, identity, _binding = self._files()
        with protected_directory(c.NATIVE_STATE.parent) as parent:
            gate = os.open(c.NATIVE_STATE.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                before = c._native_environment_snapshot(gate, c.NATIVE_ENV.name, identity['id'])
                fd = os.open(c.NATIVE_ENV.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=gate)
                try:
                    c.native_verify_env_fd(fd, identity['id'])
                    raw = os.read(fd, c.MAX_RESULT_BYTES + 1)
                finally:
                    os.close(fd)
                if (before is None or len(raw) != before['size'] or hashlib.sha256(raw).hexdigest() != before['sha256']
                        or c._native_environment_snapshot(gate, c.NATIVE_ENV.name, identity['id']) != before):
                    raise c.CustodyError('native-source-env-changed')
            finally:
                os.close(gate)
        return raw.decode('utf-8')

    def recovering_environment(self):
        from .native_executor import _publisher
        return _publisher(self.selection).environment_quiesce_snapshot(self.selection) is not None

    def install_env(self, text, *, _retire=False):
        data, _kit_sha = c._native_environment_bytes(self.selection, text, _publication=True)
        from .native_executor import _boot_origin, _publisher
        snapshot, _doc, _descriptor, _definitions = _boot_origin(self.selection)
        current_boot = snapshot['previousBoot'] == c._native_boot_uuid() and not snapshot['withdrawn']
        generation = snapshot['generation']
        if current_boot and not _retire:
            _doc, public = self._view()
            if hashlib.sha256(data).hexdigest() == public['launch']['environment']['sha256']:
                return dict(public['launch']['environment'])
        self.quiesce_for_sign_in()
        options = {'expected_generation': generation} if _retire and current_boot else {}
        return _publisher(self.selection).publish_environment_generation(self.selection, text, **options)

    def pause_direct(self):
        if not os.path.lexists(c.NATIVE_STATE):
            return
        if self.direct_enabled():
            self.install_env(c.env_text(c.parse_env(self._environment_text()), {c.DIRECT_FLAG: '0'}))
        else:
            self.quiesce_for_sign_in()

    def revoke(self):
        if not self.holds_sign_in():
            return 'not-connected'
        self.install_env(self._environment_text(), _retire=True)
        self.quiesce_for_sign_in()
        self.run(['systemctl', 'start', 'pixel-source-revoke.service'], 60)
        result = self._result('revoke.result.json', c.REVOKE_KEYS, 'pixel-source-oauth-revoke-result')
        outcome = 'revoked' if result is not None and result['outcome'] == 'revoked' else 'local-only'
        if self.holds_sign_in():
            self._no_actors()
            self.forget_sign_in()
            outcome = 'local-only'
        return outcome

    def archive_google_actions(self, flow_id=None):
        self._no_actors()
        # Derived old-account data belongs in Root metadata, outside the live
        # broker-owned private tree. No token or OAuth handoff is archived.
        paths = self.paths
        class Retired:
            private = c.NATIVE_META
            proposals, results = paths.proposals, paths.results
        c.archive_google_actions(Retired(), flow_id)
        self._files()

    def clear_google_data(self):
        self._no_actors()
        c.clear_google_data(self.paths)
        self._files()

    def erase(self):
        self.quiesce_for_sign_in()
        c.erase(self.paths)
        self._files()

    def remove_env(self):
        # The literal sealed environment is a generation input. Disconnect
        # withdraws grants rather than leaving a missing/unbound environment.
        self.install_env(c.native_environment_text(self.selection, preauth=True, _publication=True))


def for_bridge(bridge, *, linux_paths, linux_run):
    if isinstance(bridge, LaunchdAccessBridge):
        return NativeSource(bridge, _SEAL)
    return LinuxSource(linux_paths, linux_run)


def native_status_view(bridge):
    """Fixed read-only Root interface; does not provision or grant dispatch.

    Paths/token metadata are private caller inputs to the shared status reader,
    not a wire response. Native MCP-only requests do not need this Google view.
    """
    source = NativeSource(bridge, _SEAL)
    selection = source.selection
    from .native_executor import _publisher
    publisher = _publisher(selection)
    _module, doc, _component, _ref = publisher._material(selection)
    paths = c.NativeSourcePaths(install=c.NATIVE_KIT, state=c.NATIVE_STATE, env=c.NATIVE_ENV, kit=c.NATIVE_KIT)
    if not os.path.lexists(c.NATIVE_STATE):
        if os.path.lexists(c.NATIVE_META / 'source-installer-code-only.json'):
            c.native_code_only_status_absence(selection)
        else:
            c._native_installer_absence_state(bridge, qualified_runtime={'sourceRoot': doc['sourceRoot']})
        return {'paths': paths, 'brokerUid': None, 'tokenIdentity': None,
            'statePresent': False, 'dispatchReady': False}
    _selection, identity, _binding = source._files()
    token = source.sign_in_identity()
    try:
        _doc, public = source._view()
    except c.CustodyError:
        ready = False  # Private staging/recovery is not current runtime proof.
    except FileNotFoundError:
        # A known journal may temporarily hold the Gate under its preserved
        # name. Only its fixed producer may qualify that prefix; an unexplained
        # missing file remains unavailable.
        pending = publisher.environment_quiesce_snapshot(selection)
        if pending is None:
            with protected_directory(c.NATIVE_META) as metadata:
                initial, _raw = c._native_json_at(metadata, 'source-initial-generation.json')
            if type(initial) is not dict or initial.get('phase') != 'generation':
                raise
            publisher.verify_initial_generation_child(selection)
        ready = False
    else:
        ready = public.get('schemaVersion') == 3
    return {'paths': paths, 'brokerUid': identity['id'], 'tokenIdentity': token,
        'statePresent': True, 'dispatchReady': ready}


def native_status_projection(bridge):
    """Bounded broker status bytes from the sealed projection directory FD."""
    view = native_status_view(bridge)
    answer = {**view, 'rawStatus': None, 'reason': None}
    if not view['statePresent'] or not view['dispatchReady']:
        if view['statePresent'] and view['tokenIdentity'] is not None:
            answer['reason'] = 'connection-admission-held'
        return answer
    source = NativeSource(bridge, _SEAL)
    doc, _public = source._view()
    with source._directory('projection') as directory:
        try:
            fd = os.open('status.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            fd = None
        if fd is not None:
            try:
                before = os.fstat(fd)
                if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_uid != view['brokerUid']
                        or before.st_gid != doc['ownerGid'] or stat.S_IMODE(before.st_mode) not in (0o600, 0o640)
                        or not 0 <= before.st_size <= c.MAX_RESULT_BYTES):
                    raise c.CustodyError('native-source-status-unsafe')
                c._native_no_acl(fd)
                raw = bytearray()
                while len(raw) <= c.MAX_RESULT_BYTES:
                    part = os.read(fd, min(65536, c.MAX_RESULT_BYTES + 1 - len(raw)))
                    if not part:
                        break
                    raw.extend(part)
                if (source._stamp(before) != source._stamp(os.fstat(fd)) or len(raw) != before.st_size
                        or c._native_at_inode(directory, 'status.json') != [before.st_dev, before.st_ino]):
                    raise c.CustodyError('native-source-status-changed')
                answer['rawStatus'] = bytes(raw)
            finally:
                os.close(fd)
    if source.sign_in_identity() != view['tokenIdentity']:
        raise c.CustodyError('native-source-status-token-changed')
    _doc, public = source._view()
    if public.get('schemaVersion') != 3:
        raise c.CustodyError('native-source-status-generation-changed')
    return answer
