"""Privileged adapter for the one reviewed, retained-hold controller repair.

Loaded only by the explicit repair-controller command. It never updates the
original runtime archive, releases admission, switches a model, or invokes the
access reproof ceremony. Completion still requires ordinary runtime recovery.
"""
import json
import os
from pathlib import Path
import socket
import stat


def controller_status(installer, service, *, owner_gid):
    pid = installer._upgrade_service_identity(service)
    address = installer._launchd.ACCESS_SOCKET
    info = address.lstat()
    if (type(owner_gid) is not int or owner_gid < 0 or not stat.S_ISSOCK(info.st_mode)
            or info.st_uid != 0 or info.st_gid != owner_gid or stat.S_IMODE(info.st_mode) != 0o660):
        raise installer.InstallError('controller-repair-socket-unavailable')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(20)
        connection.connect(str(address))
        connection.sendall(b'{"operation":"status"}\n')
        with connection.makefile('rb') as stream:
            raw = stream.readline(65537)
    if len(raw) > 65536 or not raw.endswith(b'\n'):
        raise installer.InstallError('controller-repair-status-invalid')
    value = installer._repair._object(raw, 65536)
    if (set(value) != {'status', 'body'} or type(value['status']) is not int or value['status'] != 200
            or type(value['body']) is not dict or installer._upgrade_service_identity(service) != pid):
        raise installer.InstallError('controller-repair-status-invalid')
    return value['body']


class Adapter:
    def __init__(self, installer, selection, plan, journal, records):
        from pixel_macos_custody import protected_bytes, replace_protected_bytes
        self.i, self.r = installer, installer._repair
        self.selection, self.plan, self.journal, self.records = selection, plan, journal, records
        self.path = installer._controller_repair_path(selection['candidate_digest'])
        self.read, self.cas = protected_bytes, replace_protected_bytes
        _, self.services = installer._upgrade_services(plan, records)
        if set(self.services) != {'gateway', 'access', 'relay', 'operations', 'manager', 'promoter'}:
            raise installer.InstallError('controller-repair-native-services-required')
        self.access = self.services['access']
        # Do not reuse or overwrite the original deployment's stop witnesses.
        self.witness = self.path.with_suffix('.access-stop.json')
        self.access.save_stop = self.save_stop
        self.access.load_stop = self.load_stop

    def document(self):
        raw = self.i._controller_private_bytes(self.path, self.r.LIMIT)
        return raw, self.r._object(raw, self.r.LIMIT)

    def snapshots(self):
        return self.i._controller_repair_snapshots(self.selection['candidate_digest'])

    def _write(self, path, document, *, expected=None):
        from pixel_macos_custody import protected_directory, _verify_fd
        body = (json.dumps(document, sort_keys=True, separators=(',', ':')) + '\n').encode()
        if expected is not None:
            self.cas(path, expected=expected, replacement=body, mode=0o600)
            return
        # The caller holds the controller flock. This protects against competing
        # cooperating installers, not an arbitrary root process. A partial temp
        # file is never authority; after rename, even an fsync error must leave
        # the published intent for explicit resume, not overwrite it on retry.
        with protected_directory(path.parent) as directory:
            if stat.S_IMODE(os.fstat(directory).st_mode) != 0o700:
                raise self.i.InstallError('controller-repair-state-unsafe')
            name = '.controller-repair-' + os.urandom(16).hex()
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    os.fchown(stream.fileno(), 0, 0)
                    _verify_fd(stream.fileno(), directory=False)
                    stream.write(body)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.stat(path.name, dir_fd=directory, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise self.i.InstallError('controller-repair-record-already-exists')
                os.rename(name, path.name, src_dir_fd=directory, dst_dir_fd=directory)
                name = None
                os.fsync(directory)
            finally:
                if name is not None:
                    try:
                        os.unlink(name, dir_fd=directory)
                    except FileNotFoundError:
                        pass
                    else:
                        os.fsync(directory)

    def save_stop(self, value):
        raw = self.i._controller_private_bytes(self.witness, 1024 * 1024) if os.path.lexists(self.witness) else None
        self._write(self.witness, value, expected=raw)

    def load_stop(self):
        return json.loads(self.i._controller_private_bytes(self.witness, 1024 * 1024))

    def persist(self, previous, updated):
        raw, actual = self.document()
        if json.dumps(actual, sort_keys=True) != json.dumps(previous, sort_keys=True):
            raise self.i.InstallError('controller-repair-journal-changed')
        self._write(self.path, updated, expected=raw)

    def hold(self, snapshots):
        hold = self.r._hold(snapshots['hold'])
        if hold['phase'] != 'held':
            raise self.i.InstallError('controller-repair-held-admission-required')
        context = self.i._migration_edge_context(self.plan)
        result = self.i.subprocess.run(['docker', 'inspect', hold['container'], '--format',
            '{{.Id}} {{.State.Running}} {{index .Config.Labels "com.docker.compose.service"}}'],
            check=True, stdout=self.i.subprocess.PIPE, stderr=self.i.subprocess.DEVNULL,
            text=True, timeout=10, **context)
        if result.stdout.strip().split() != [hold['container'], 'true', 'pixel-edge']:
            raise self.i.InstallError('controller-repair-edge-changed')
        # Read only: no acquire/release request, including on replay.
        if self.i._migration_edge_request(self.plan, hold['container']) != hold['status']:
            raise self.i.InstallError('controller-repair-hold-changed')

    def verify(self, value, allowed, *, terminal=False, unpublished=False):
        if any(os.path.lexists(self.i._launchd.ACCESS_STATE / name) for name in
               ('runtime-upgrade.json', 'transition.json', 'policy-activation.json')):
            raise self.i.InstallError('controller-repair-other-transition')
        plan, journal, records = self.i._load_upgrade_recovery_base(**self.selection, completed=True)
        self.i._verify_recovery_bindings(plan, records)
        if plan != self.plan or journal.value != self.journal.value or records != self.records:
            raise self.i.InstallError('controller-repair-authority-changed')
        snapshots = self.snapshots()
        self.r.validate_intent(value, current=self.selection['current_digest'],
            candidate=self.selection['candidate_digest'], owner=self.i._controller_repair_owner(plan),
            snapshots=snapshots)
        if not unpublished and json.dumps(self.document()[1], sort_keys=True) != json.dumps(value, sort_keys=True):
            raise self.i.InstallError('controller-repair-journal-changed')
        for item in records:
            body = self.read(item['path'], limit=8 * 1024 * 1024)
            self.i._check_existing(Path(item['path']), body, mode=item['mode'], gid=item['gid'])
            if (self.r.sha(body) not in allowed if item['path'] == self.r.TARGET else body != item['after']):
                raise self.i.InstallError('controller-repair-deployment-drift')
        self.i._policy.policy_state(plan['access_settings']['gateway_policy'])
        for role, service in self.services.items():
            if self.i._job_disabled(service.target):
                raise self.i.InstallError('controller-repair-disabled-service')
            if role != 'access' and list(self.i._upgrade_service_identity(service)) != value['identities'][role]:
                raise self.i.InstallError('controller-repair-unrelated-process-changed')
        self.hold(snapshots)
        if terminal:
            identity = list(self.i._upgrade_service_identity(self.access))
            if identity == value['identities']['access']:
                raise self.i.InstallError('controller-repair-restart-unconfirmed')
            if value['phase'] not in ('rollback-starting', 'rolled-back'):
                status = controller_status(self.i, self.access, owner_gid=plan['owner'].pw_gid)
                if (status.get('available') is not True or status.get('surface') != 'darwin'
                        or status.get('scope') != 'owner-host' or status.get('pending') is not False
                        or status.get('busy') is not False or status.get('reason') not in (None, 'runtime-proof-required')):
                    raise self.i.InstallError('controller-repair-inspection-unconfirmed')
            self.i._verify_new_services(plan)
            self.i._ready_gateway(self.services['gateway'], plan['access_settings']['gateway_port'])
            if self.snapshots() != snapshots:
                raise self.i.InstallError('controller-repair-authority-changed')

    def loaded(self):
        try:
            self.i._command(['/bin/launchctl', 'print', self.access.target])
        except self.i.InstallError as error:
            if error.code == 'host-command-failed' and error.returncode == 113:
                return False
            raise
        self.i._upgrade_service_identity(self.access)
        return True

    def stop(self, value):
        if self.loaded():
            if value['phase'] == 'stopping' and list(self.i._upgrade_service_identity(self.access)) != value['identities']['access']:
                raise self.i.InstallError('controller-repair-access-process-changed')
            self.i._stop_upgrade_service(self.access)
        else:
            self.access.assert_stopped()

    def replace(self, value, *, rollback=False):
        self.access.assert_stopped()
        before = self.r._bridge_record(self.snapshots()['archive'],
            self.selection['current_digest'], self.selection['candidate_digest'])
        after = self.r.replacement(before)
        expected, replacement = (after, before) if rollback else (before, after)
        current = self.read(self.r.TARGET)
        if current != replacement:
            self.cas(self.r.TARGET, expected=expected, replacement=replacement, mode=0o644)

    def start(self, value):
        if self.loaded():
            if list(self.i._upgrade_service_identity(self.access)) == value['identities']['access']:
                raise self.i.InstallError('controller-repair-restart-unconfirmed')
            return
        # A missing label is not proof that a possibly started process tree died.
        # A crash after bootstrap intent cleared this witness stays held.
        self.access.assert_stopped()
        self.i._start_upgrade_service(self.access)


def run(installer, *, selection, apply=False, resume=False, rollback=False):
    if installer.sys.platform != 'darwin' or os.geteuid() != 0:
        raise installer.InstallError('macos-root-install-required')
    bridge = installer._recovery_bridge(**selection)
    with bridge.recovery_locked(completed_digest=selection['candidate_digest']):
        plan, journal, records = installer._load_upgrade_recovery_base(**selection, completed=True)
        installer._verify_recovery_bindings(plan, records)
        if journal.value['phase'] != 'active':
            raise installer.InstallError('controller-repair-active-archive-required')
        adapter = Adapter(installer, selection, plan, journal, records)
        exists = os.path.lexists(adapter.path)
        if exists:
            value = adapter.document()[1]
            if apply:
                raise installer.InstallError('controller-repair-explicit-resume-required')
            installer._repair.validate_intent(value, current=selection['current_digest'],
                candidate=selection['candidate_digest'], owner=installer._controller_repair_owner(plan),
                snapshots=adapter.snapshots())
        else:
            if resume or rollback or os.path.lexists(adapter.witness):
                raise installer.InstallError('controller-repair-intent-required')
            identities = {role: list(installer._upgrade_service_identity(service))
                          for role, service in adapter.services.items()}
            value = installer._repair.make_intent(current=selection['current_digest'],
                candidate=selection['candidate_digest'], owner=installer._controller_repair_owner(plan),
                snapshots=adapter.snapshots(), identities=identities)
            adapter.verify(value, {installer._repair.BEFORE}, unpublished=True)
            status = controller_status(installer, adapter.access, owner_gid=plan['owner'].pw_gid)
            if status.get('available') is not False or status.get('reason') != 'inspection-failed':
                raise installer.InstallError('controller-repair-observed-failure-required')
            installer._verify_new_services(plan)
            installer._ready_gateway(adapter.services['gateway'], plan['access_settings']['gateway_port'])
        if not (apply or resume or rollback):
            if exists:
                adapter.verify(value, installer._repair.phase_hashes(value['phase']))
            return {'operation': 'controller-repair', 'status': 'planned', 'repair': installer._repair.REPAIR,
                    'candidateDigest': selection['candidate_digest'], 'admission': 'held'}
        if not exists:
            adapter._write(adapter.path, value)
        outcome = installer._repair.execute(value, adapter, rollback=rollback)
        return {'operation': 'controller-repair', 'status': outcome['phase'],
                'repair': installer._repair.REPAIR, 'candidateDigest': selection['candidate_digest'],
                'admission': 'held', 'requiresRuntimeRecovery': True}
