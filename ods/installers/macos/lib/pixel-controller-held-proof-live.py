"""Privileged retained-hold proof adapter, called under recovery_locked only."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time

HERE = Path(__file__).resolve().parent
CONFIG = '/private/etc/ods/pixel-access.json'
PROGRAM = '/usr/local/libexec/ods-pixel-access/'


def helper(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Adapter:
    def __init__(self, installer, selection, plan, journal, records, services):
        from pixel_macos_custody import protected_bytes, replace_protected_bytes
        self.i, self.repair = installer, installer._repair
        self.pure, self.child = helper('pixel-controller-held-proof'), helper('pixel-controller-proof-child')
        self.selection, self.plan, self.journal, self.records, self.services = selection, plan, journal, records, services
        if set(services) != {'gateway', 'access', 'relay', 'operations', 'manager', 'promoter'}:
            raise installer.InstallError('controller-held-proof-services-required')
        self.path = installer._launchd.ACCESS_STATE / ('runtime-controller-proof-' + selection['candidate_digest'] + '.json')
        self.repair_path = installer._controller_repair_path(selection['candidate_digest'])
        self.read = protected_bytes
        writer_type = helper('pixel-controller-repair-live').Adapter
        self.writer = object.__new__(writer_type)
        self.writer.i, self.writer.cas = installer, replace_protected_bytes
        self.deadline = time.monotonic() + 360
        by_path = {item['path']: item['after'] for item in records}
        self.modules = {name: self.repair.sha(by_path[PROGRAM + name]) for name in self.child.MODULES}
        self.settings_hash = self.repair.sha(by_path[CONFIG])
        self.mode = installer._policy.policy_state(plan['access_settings']['gateway_policy'])['activeMode']

    def budget(self):
        result = self.deadline - time.monotonic()
        if result <= 0:
            raise self.i.InstallError('controller-held-proof-timeout')
        return result

    def authority(self):
        return dict(current=self.selection['current_digest'], candidate=self.selection['candidate_digest'],
            owner=self.i._controller_repair_owner(self.plan),
            snapshots=self.i._controller_repair_snapshots(self.selection['candidate_digest']),
            repair_body=self.i._controller_private_bytes(self.repair_path, self.repair.LIMIT),
            settings_hash=self.settings_hash, mode=self.mode)

    def document(self):
        raw = self.i._controller_private_bytes(self.path, self.pure.LIMIT)
        return raw, self.repair._object(raw, self.pure.LIMIT)

    def persist(self, previous, updated):
        self.budget()
        raw, actual = self.document()
        if json.dumps(actual, sort_keys=True) != json.dumps(previous, sort_keys=True):
            raise self.i.InstallError('controller-held-proof-journal-changed')
        self.pure.validate(self.repair, updated, **self.authority())
        self.writer._write(self.path, updated, expected=raw)

    def edge(self, value):
        hold = self.repair._hold(self.authority()['snapshots']['hold'])
        context = self.i._migration_edge_context(self.plan)
        result = self.i.subprocess.run(['docker', 'inspect', hold['container'], '--format',
            '{{.Id}} {{.State.Running}} {{index .Config.Labels "com.docker.compose.service"}}'],
            check=True, stdout=self.i.subprocess.PIPE, stderr=self.i.subprocess.DEVNULL,
            text=True, timeout=min(10, self.budget()), **context)
        if result.stdout.strip().split() != [hold['container'], 'true', 'pixel-edge']:
            raise self.i.InstallError('controller-held-proof-edge-changed')
        # Same-token acquire is read-only for an already held edge. It proves
        # ownership, not merely that somebody holds a matching revision.
        operation = 'acquire' if hold['phase'] == 'held' else None
        observed = self.i._migration_edge_request(self.plan, hold['container'], operation,
            hold['binding'] if operation else None)
        if hold['phase'] == 'held':
            if observed != hold['status']:
                raise self.i.InstallError('controller-held-proof-hold-changed')
        elif value['phase'] not in ('releasing-edge', 'complete') or (
                observed != hold['status'] and not self.idle_edge(observed)):
            raise self.i.InstallError('controller-held-proof-release-unconfirmed')
        return hold

    @staticmethod
    def idle_edge(value):
        return (type(value) is dict and value.get('capability') == 'available' and value.get('phase') == 'idle'
            and type(value.get('streams')) is int and value['streams'] == 0 and value.get('admission_blocked') is False)

    def verify(self, value, *, unpublished=False):
        self.budget()
        if any(os.path.lexists(self.i._launchd.ACCESS_STATE / name) for name in
               ('runtime-upgrade.json', 'transition.json', 'policy-activation.json')):
            raise self.i.InstallError('controller-held-proof-other-transition')
        plan, journal, records = self.i._load_upgrade_recovery(**self.selection, completed=True)
        self.i._verify_recovery_bindings(plan, records)
        if (plan != self.plan or journal.value != self.journal.value or records != self.records
                or journal.value['phase'] != 'active'):
            raise self.i.InstallError('controller-held-proof-authority-changed')
        self.pure.validate(self.repair, value, **self.authority())
        if not unpublished and json.dumps(self.document()[1], sort_keys=True) != json.dumps(value, sort_keys=True):
            raise self.i.InstallError('controller-held-proof-journal-changed')
        for item in records:
            body = self.read(item['path'], limit=8 * 1024 * 1024)
            self.i._check_existing(Path(item['path']), body, mode=item['mode'], gid=item['gid'])
            if body != item['after']:
                raise self.i.InstallError('controller-held-proof-file-drift')
        if self.i._policy.policy_state(plan['access_settings']['gateway_policy'])['activeMode'] != self.mode:
            raise self.i.InstallError('controller-held-proof-policy-changed')
        for name, service in self.services.items():
            if (self.i._job_disabled(service.target)
                    or list(self.i._upgrade_service_identity(service)) != value['identities'][name]):
                raise self.i.InstallError('controller-held-proof-process-changed')
        self.edge(value)
        self.budget()

    def invoke(self, value, action):
        self.verify(value)
        reply = self.child_sample(value, action)
        self.verify(value)
        return reply

    def child_sample(self, value, action):
        """Fixed child; caller supplies the enclosing authority checks."""
        hold = self.repair._hold(self.authority()['snapshots']['hold'])
        request = dict(action=action, owner=value['owner'], settingsSha256=self.settings_hash,
            modules=self.modules, token=hold['binding']['token'], mode=self.mode,
            gatewayIdentity=value['identities']['gateway'])
        body = self.child.request_bytes(request)
        # The fixed child bounds its one JSON response before writing stdout;
        # independently enforce the protocol bound here. No stdin credential,
        # environment-selected executable, checkout import, or public stderr.
        result = subprocess.run(self.child.command(), input=body,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd='/',
            env={'PATH': '/usr/bin:/bin'}, timeout=min(160, self.budget()), check=False)
        self.budget()
        if result.returncode != 0 or type(result.stdout) is not bytes or len(result.stdout) > self.child.MAX_REPLY:
            raise self.i.InstallError('controller-held-proof-child-failed')
        reply = self.repair._object(result.stdout, self.child.MAX_REPLY)
        if (set(reply) != {'native', 'owned', 'ready'} or type(reply['native']) is not dict
                or type(reply['owned']) is not bool or type(reply['ready']) is not bool):
            raise self.i.InstallError('controller-held-proof-child-invalid')
        return reply

    def snapshot(self, value):
        return self.invoke(value, 'snapshot')

    def native(self, value, operation):
        if (value['phase'], operation) not in (('acquiring-native', 'acquire'), ('probing', 'probe'), ('releasing-native', 'release')):
            raise self.i.InstallError('controller-held-proof-operation-invalid')
        return self.invoke(value, operation)

    def release_edge(self, value):
        self.verify(value)
        hold = self.edge(value)
        def final_proof():
            # The ordinary release helper calls this AFTER its own slow edge
            # ownership/journal checks, immediately before the release request.
            # No parent service/archive checks may follow this final sample.
            self.pure.check_native(self.child_sample(value, 'snapshot'), value,
                                   held=False, ready=True, exact=True)
            self.budget()
        if hold['phase'] != 'released':
            self.i._finish_migration_hold(self.plan, hold, verify_before_release=final_proof)
        else:
            final_proof()
            result = self.i._migration_edge_request(self.plan, hold['container'], 'release', hold['binding'])
            if not self.idle_edge(result):
                raise self.i.InstallError('controller-held-proof-release-unconfirmed')

    def verify_released(self, value):
        hold = self.edge(value)
        if hold['phase'] != 'released':
            raise self.i.InstallError('controller-held-proof-release-unconfirmed')


def run_locked(installer, *, selection, plan, journal, records, services):
    """Caller must retain recovery_locked; only its archived-repaired path calls this."""
    adapter = Adapter(installer, selection, plan, journal, records, services)
    if os.path.lexists(adapter.path):
        value = adapter.document()[1]
    else:
        identities = {name: list(installer._upgrade_service_identity(service)) for name, service in services.items()}
        value = adapter.pure.make_intent(adapter.repair, identities=identities, **adapter.authority())
        adapter.verify(value, unpublished=True)
        adapter.writer._write(adapter.path, value)
    return adapter.pure.execute(value, adapter)
