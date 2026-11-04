"""Fixed native Source Root executor; no public availability or provider planner.

The caller holds the existing Root admission lock and bounded operation. Every
mutating launchctl call has durable intent. Lost start replies are observed,
never replayed. The broker owns its existing READY/GO pipes and child cleanup.
"""
import copy
import errno
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import re
import stat
import threading
import time

from pixel_access_bridge import AccessError, LaunchdAccessBridge, remaining
from pixel_macos_custody import protected_bytes, protected_directory, verify_loaded_launchd_definition
from pixel_macos_process import ProcessIdentityError, process_birth, process_identity, process_tree_snapshot
from . import custody as c

RECORD = 'source-execution.json'
PHASES = ('bootstrap-intent', 'loaded', 'start-intent', 'queued', 'observed', 'quiescing', 'stopped', 'unknown')
JOB_KEYS = {'role', 'identifier', 'nonce', 'invocation', 'definition', 'phase', 'baselineRuns',
    'runs', 'identity', 'tree', 'exitCode', 'bootoutIntent', 'uncertain'}


def _fail(code):
    raise c.CustodyError('native-source-' + code)


def _selected(selection):
    if type(selection) is not c._NativeSourceSelection or selection.seal is not c._NATIVE_SEAL:
        _fail('deployment-required')
    return c._native_selected(selection, _publication=True)


def _publisher(selection):
    doc = _selected(selection)
    path = Path(doc['sourceRoot']) / 'installers/macos/lib/pixel-native-source-broker.py'
    protected_bytes(path, limit=2 * 1024 * 1024)
    spec = importlib.util.spec_from_file_location('qualified_native_source_executor_publisher', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._material(selection)  # Independent complete bundle/source authority.
    return module


def _view(selection):
    doc = _selected(selection)
    _manifest, kit_sha = c.native_verify_kit(selection, require_profiles=True, _publication=True)
    with protected_directory(c.NATIVE_META) as metadata, protected_directory(c.NATIVE_STATE.parent) as parent:
        gate = os.open(c.NATIVE_STATE.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            identity, binding, expected = c._native_environment_binding(selection, metadata, gate, kit_sha, _publication=True)
            raw, _inode = c._native_root_file(gate, 'custody.json', mode=0o640, gid=identity['id'], limit=16384)
            value = c._native_public_json(raw)
            if value.get('schemaVersion') == 2:
                c._native_validate_inactive_generation(selection, metadata, gate, identity, binding, expected, raw, _inode)
                origin, _raw = c._native_json_at(metadata, 'source-jobs-prepare.json')
                value['launch'] = dict(value['launch'], boot=origin['witness']['boot'], invocations={})
                snapshot = {'boot': origin['witness']['boot']}
            else:
                snapshot = c._native_broker_projection(value)
            common = {key: item for key, item in value.items() if key not in ('launch', 'runtime', 'schemaVersion', 'phase')}
            if (common != {key: item for key, item in json.loads(expected).items() if key not in ('schemaVersion', 'phase')}
                    or value.get('runtime') is not None and (value['runtime']['custodySha256'] != doc['runtimeCustodySha256']
                        or value['runtime']['bundleSha256'] != doc['bundleSha256'])
                    or snapshot['boot'] != c._native_boot_uuid()
                    or c._native_at_inode(parent, c.NATIVE_STATE.name) != binding['gate']):
                _fail('execution-binding-changed')
            env, _raw = c._native_json_at(metadata, 'source-environment.json')
            c._native_environment_record(env, binding)
            if (env['pending'] is not None or env['current'] is None
                    or {key: env['current'][key] for key in ('inode', 'sha256')} != value['launch']['environment']
                    or c._native_environment_snapshot(gate, c.NATIVE_ENV.name, identity['id']) != env['current']):
                _fail('execution-environment-changed')
            c._native_verify_prepared_logs(metadata, identity, binding)
            return doc, value
        finally:
            os.close(gate)


def _label(role, identifier):
    c.native_child_arguments(role, identifier)
    return c.NATIVE_LABEL + role + ('.' + identifier if identifier is not None else '')


def _tree(value):
    if (type(value) is not list or len(value) > 256
            or any(type(row) is not list or len(row) != 3 or any(type(item) is not int for item in row)
                or not 0 < row[0] <= 2147483647 or row[1] <= 0 or not 0 <= row[2] < 1000000 for row in value)
            or len({row[0] for row in value}) != len(value)):
        _fail('execution-tree-invalid')
    return value


def _validate(value, doc, public):
    if (type(value) is not dict or set(value) != {'schemaVersion', 'kind', 'deploymentSha256', 'boot', 'jobs', 'triggers', 'admission'}
            or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
            or value['kind'] != 'ods-native-source-execution'
            or value['deploymentSha256'] != public['deploymentSha256'] or value['boot'] != public['launch']['boot']
            or type(value['jobs']) is not dict or len(value['jobs']) > 64):
        _fail('execution-record-invalid')
    admission = value['admission']
    if admission is not None:
        if (type(admission) is not dict or set(admission) != {'label', 'role', 'identifier', 'beforeNonce', 'ceilingNs'}
                or admission['label'] != _label(admission['role'], admission['identifier'])
                or type(admission['ceilingNs']) is not str or not re.fullmatch('[1-9][0-9]{0,18}', admission['ceilingNs'])
                or int(admission['ceilingNs']) >= 2**63
                or admission['beforeNonce'] is not None and (type(admission['beforeNonce']) is not str
                    or not c.FLOW_ID.fullmatch(admission['beforeNonce']))):
            _fail('execution-record-invalid')
    trigger = value['triggers']
    if (type(trigger) is not dict or set(trigger) != {'sync', 'direct', 'nextSyncNs', 'directSnapshot'}
            or any(type(trigger[key]) is not bool for key in ('sync', 'direct'))
            or trigger['nextSyncNs'] is not None and (type(trigger['nextSyncNs']) is not str or not re.fullmatch('[1-9][0-9]{0,18}', trigger['nextSyncNs']))
            or trigger['directSnapshot'] is not None and (type(trigger['directSnapshot']) is not str or not c._NATIVE_SHA.fullmatch(trigger['directSnapshot']))
            or trigger['sync'] != (trigger['nextSyncNs'] is not None)
            or trigger['direct'] != (trigger['directSnapshot'] is not None)):
        _fail('execution-record-invalid')
    for label, job in value['jobs'].items():
        if (type(job) is not dict or set(job) != JOB_KEYS or label != _label(job['role'], job['identifier'])
                or job['phase'] not in PHASES or type(job['uncertain']) is not bool or type(job['bootoutIntent']) is not bool):
            _fail('execution-record-invalid')
        invocation = c._native_invocation(job['invocation']).document
        if ((invocation['role'], invocation['identifier'], invocation['nonce'], invocation['boot']) !=
                (job['role'], job['identifier'], job['nonce'], value['boot'])
                or type(job['definition']) is not dict or set(job['definition']) != {'inode', 'sha256'}
                or not c._native_inode(job['definition']['inode']) or not c._NATIVE_SHA.fullmatch(job['definition']['sha256'])
                or any(job[key] is not None and (type(job[key]) is not int or not 0 <= job[key] < 2**31) for key in ('baselineRuns', 'runs'))
                or job['exitCode'] is not None and (type(job['exitCode']) is not int or not 0 <= job['exitCode'] <= 255)):
            _fail('execution-record-invalid')
        identity = job['identity']
        if identity is not None and (type(identity) is not list or len(identity) != 10
                or identity[3:9] != [public['broker']['id']] * 6 or identity[-1] != doc['hostPythonPath']
                or any(type(item) is not int for item in identity[:9])):
            _fail('execution-record-invalid')
        _tree(job['tree'])
    return value


class NativeSourceExecutor:
    """Request-local adapter for the existing Source Run grammar and six roles."""

    def __init__(self, selection):
        _selected(selection)
        self.selection, self.bridge = selection, selection.bridge
        self.publisher = _publisher(selection)

    def _read(self):
        doc, public = _view(self.selection)
        with protected_directory(c.NATIVE_META) as directory:
            value, raw = c._native_json_at(directory, RECORD)
        if value is None:
            value = {'schemaVersion': 1, 'kind': 'ods-native-source-execution',
                'deploymentSha256': public['deploymentSha256'], 'boot': public['launch']['boot'], 'jobs': {}, 'admission': None,
                'triggers': {'sync': False, 'direct': False, 'nextSyncNs': None, 'directSnapshot': None}}
        return _validate(value, doc, public), raw, doc, public

    def _save(self, value, expected):
        doc, public = _view(self.selection)
        _validate(value, doc, public)
        with protected_directory(c.NATIVE_META) as directory:
            return c._native_save_at(self.selection, directory, RECORD, value, previous=expected, _publication=True)

    def _definition(self, public, role, identifier):
        label = _label(role, identifier)
        expected = public['launch']['definitions'].get(label)
        if expected is None:
            _fail('execution-slot-missing')
        with protected_directory(c.NATIVE_DEFINITIONS) as directory:
            body, inode = c._native_root_file(directory, label + '.plist', mode=0o644)
        path, fixed, _mode = c.native_disabled_definition(role, identity=public['broker'], python=public['hostPython'], identifier=identifier)
        document = plistlib.loads(fixed)
        document['Disabled'] = False
        if (body != plistlib.dumps(document, sort_keys=True) or inode != expected['inode']
                or __import__('hashlib').sha256(body).hexdigest() != expected['sha256']):
            _fail('execution-definition-changed')
        return c.NATIVE_DEFINITIONS / path.name, document, {'inode': inode, 'sha256': expected['sha256']}

    def _loaded(self, label, document, path):
        target = 'system/' + label
        try:
            raw = self.bridge.command(['/bin/launchctl', 'print', target], timeout=remaining(10))
        except AccessError as error:
            if error.code == 'host-command-failed' and error.returncode == 113:
                return None
            raise
        verify_loaded_launchd_definition(raw, target, path, document)
        def field(name, pattern, optional=False):
            rows = re.findall(r'^\t' + re.escape(name) + r' = ([^\n]*)$', raw, re.MULTILINE)
            if len(rows) != 1:
                if optional and not rows:
                    return None
                _fail('execution-observation-unavailable')
            if not re.fullmatch(pattern, rows[0]):
                _fail('execution-observation-unavailable')
            return rows[0]
        state = field('state', '[^\n]+')
        pid = field('pid', '[1-9][0-9]*', True)
        runs = int(field('runs', '[0-9]+'))
        exit_code = field('last exit code', '[0-9]+', True)
        if (state not in ('running', 'not running', 'waiting', 'spawn scheduled') or runs >= 2**31
                or (state == 'running') != (pid is not None) or pid is not None and int(pid) > 2147483647
                or exit_code is not None and int(exit_code) > 255):
            _fail('execution-observation-unavailable')
        return {'pid': int(pid) if pid else None, 'runs': runs, 'exitCode': int(exit_code) if exit_code else None}

    def _capture(self, job, loaded, doc, public):
        if loaded['pid'] is None:
            return
        identity = process_identity(loaded['pid'], uid=public['broker']['id'], gid=public['broker']['id'], executable=doc['hostPythonPath'])
        tree = _tree([list(row) for row in process_tree_snapshot(identity[0])])
        if (process_identity(identity[0], uid=public['broker']['id'], gid=public['broker']['id'], executable=doc['hostPythonPath']) != identity
                or list(identity[:3]) not in tree):
            _fail('execution-process-changed')
        if job['identity'] is not None and job['identity'] != list(identity):
            job['uncertain'] = True
        job['identity'] = list(identity)
        merged = {tuple(row) for row in job['tree']} | {tuple(row) for row in tree}
        job['tree'] = _tree([list(row) for row in sorted(merged)])

    def _admission(self, job):
        doc = job['invocation']
        ceiling = LaunchdAccessBridge._native_source_deadline_ns(self.bridge)
        if (c._native_boot_uuid() != doc['boot'] or not int(doc['issuedNs']) <= c._native_clock_ns()
                < min(ceiling, int(doc['admissionDeadlineNs']))):
            _fail('execution-admission-expired')

    def start(self, role, identifier=None):
        label = _label(role, identifier)
        value, raw, doc, public = self._read()
        pending = value['admission']
        if pending is not None and pending['label'] != label:
            _fail('execution-admission-pending')
        previous = value['jobs'].get(label)
        if previous is not None and previous['phase'] != 'stopped':
            return self.observe(role, identifier)  # Never repeat an uncertain mutation.
        if previous is not None and pending is None:
            previous = self.stop(role, identifier, requested=False)
            if role not in ('sync', 'direct'):
                return previous  # One-shot retry observes the original receipt.
            value, raw, doc, public = self._read()
        row = public['launch']['invocations'].get(label)
        if pending is None:
            if row is not None and not (row['phase'] in ('completed', 'retired') and previous is not None):
                _fail('execution-unbound-preparation')
            pending = {'label': label, 'role': role, 'identifier': identifier, 'beforeNonce': previous['nonce'] if previous else None,
                'ceilingNs': str(LaunchdAccessBridge._native_source_deadline_ns(self.bridge))}
            value['admission'] = pending
            raw = self._save(value, raw)
        if pending['beforeNonce'] is None:
            result = self.publisher.admit_active_slot(self.selection, role, identifier,
                _admission_ceiling_ns=int(pending['ceilingNs']))
        elif previous is not None and pending['beforeNonce'] == previous['nonce']:
            result = self.publisher.rearm_active_slot(self.selection, role, identifier,
                expected_nonce=previous['nonce'], per_job=True, _admission_ceiling_ns=int(pending['ceilingNs']))
        else:
            _fail('execution-recovery-required')
        value, raw, doc, public = self._read()
        path, definition, image = self._definition(public, role, identifier)
        job = {'role': role, 'identifier': identifier, 'nonce': result['invocation']['nonce'],
            'invocation': result['invocation'], 'definition': image, 'phase': 'bootstrap-intent',
            'baselineRuns': None, 'runs': None, 'identity': None, 'tree': [], 'exitCode': None,
            'bootoutIntent': False, 'uncertain': False}
        if self._loaded(label, definition, path) is not None:
            _fail('execution-unbound-loaded-job')
        value['jobs'][label] = job
        value['admission'] = None
        self._admission(job)
        self._save(value, raw)  # Intent precedes even inert bootstrap.
        return self.observe(role, identifier)

    def observe(self, role, identifier=None):
        label = _label(role, identifier)
        value, raw, doc, public = self._read()
        job = value['jobs'].get(label)
        if job is None:
            _fail('execution-recovery-required')
        if job['phase'] == 'stopped':
            return self.stop(role, identifier, requested=False)
        if job['bootoutIntent']:
            return self.stop(role, identifier, requested=False)
        path, definition, image = self._definition(public, role, identifier)
        if image != job['definition']:
            _fail('execution-definition-changed')
        loaded = self._loaded(label, definition, path)
        if job['phase'] == 'bootstrap-intent':
            self._admission(job)
            if loaded is None:
                try:
                    # Inert bootstrap may be recovered: RunAtLoad and KeepAlive
                    # are both false. No start intent has been committed yet.
                    self.bridge.command(['/bin/launchctl', 'bootstrap', 'system', str(path)], timeout=remaining(10))
                except AccessError:
                    pass
                loaded = self._loaded(label, definition, path)
            if loaded is None:
                return copy.deepcopy(job)
            if loaded['pid'] is not None or loaded['runs'] != 0:
                job.update(phase='unknown', uncertain=True)
                self._save(value, raw)
                return copy.deepcopy(job)
            job.update(phase='loaded', baselineRuns=0)
            raw = self._save(value, raw)
        if job['phase'] == 'loaded':
            self._admission(job)
            job['phase'] = 'start-intent'
            raw = self._save(value, raw)
            self._admission(job)
            try:
                self.bridge.command(['/bin/launchctl', 'kickstart', 'system/' + label], timeout=remaining(10))
            except AccessError:
                pass
            else:
                job['phase'] = 'queued'
                raw = self._save(value, raw)
            loaded = self._loaded(label, definition, path)
        if loaded is None:
            job.update(phase='unknown', uncertain=True)
        elif loaded['runs'] == job['baselineRuns'] and loaded['pid'] is None and job['phase'] in ('start-intent', 'queued'):
            if c._native_clock_ns() >= int(job['invocation']['admissionDeadlineNs']):
                job.update(phase='unknown', uncertain=True)
        elif job['baselineRuns'] is None or loaded['runs'] != job['baselineRuns'] + 1:
            job.update(phase='unknown', uncertain=True)
        else:
            job.update(runs=loaded['runs'], exitCode=loaded['exitCode'])
            self._capture(job, loaded, doc, public)
            if loaded['pid'] is not None:
                job['phase'] = 'observed'
            else:
                raw = self._save(value, raw)
                return self.stop(role, identifier, requested=False)
        self._save(value, raw)
        return copy.deepcopy(job)

    def stop(self, role, identifier=None, *, requested=True):
        label = _label(role, identifier)
        value, raw, doc, public = self._read()
        job = value['jobs'].get(label)
        if job is None:
            _fail('execution-recovery-required')
        row = public['launch']['invocations'].get(label)
        if row is None or row['invocation']['nonce'] != job['nonce']:
            _fail('execution-nonce-changed')
        if job['phase'] == 'stopped':
            verify_stopped(self.selection, role, identifier, job['nonce'])
            if row['phase'] != 'retired':
                self.publisher.transition_active_slot(self.selection, role, identifier,
                    expected_nonce=job['nonce'], phase='retired', per_job=True)
            return copy.deepcopy(job)
        if row['phase'] not in ('quiescing', 'unknown', 'retired', 'completed'):
            self.publisher.transition_active_slot(self.selection, role, identifier, expected_nonce=job['nonce'], phase='quiescing')
            value, raw, doc, public = self._read()
            job = value['jobs'][label]
        path, definition, image = self._definition(public, role, identifier)
        if image != job['definition']:
            _fail('execution-definition-changed')
        loaded = self._loaded(label, definition, path)
        if loaded is not None:
            try:
                self._capture(job, loaded, doc, public)
            except ProcessIdentityError as error:
                if error.errno != errno.ESRCH:
                    raise
                current = self._loaded(label, definition, path)
                if current is None or current['pid'] is not None or current['runs'] != loaded['runs']:
                    raise
                loaded = current  # Natural exit while Root was binding the PID.
            if job['baselineRuns'] is None or loaded['runs'] != job['baselineRuns'] + 1:
                job['uncertain'] = True
            job.update(runs=loaded['runs'], exitCode=loaded['exitCode'])
        elif not job['bootoutIntent']:
            job['uncertain'] = True
        job.update(phase='quiescing', bootoutIntent=True, uncertain=job['uncertain'] or requested)
        raw = self._save(value, raw)  # Persist identities before fixed-label stop.
        # Re-open binding and current loaded definition before any mutation.
        _view(self.selection)
        if self._definition(public, role, identifier)[2] != image:
            _fail('execution-definition-changed')
        current = self._loaded(label, definition, path)
        if current is not None:
            if loaded is None or current != loaded:
                _fail('execution-process-changed')
            try:
                self.bridge.command(['/bin/launchctl', 'bootout', 'system/' + label], timeout=remaining(10))
            except AccessError:
                pass  # Lost bootout reply is resolved by absence and birth checks.
        deadline = min(time.monotonic() + remaining(10), time.monotonic() + 10)
        while True:
            try:
                _stopped(self.selection, value, doc, public, label, job)
                break
            except (c.CustodyError, ProcessIdentityError):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(min(.05, remaining(.05)))
        job['phase'] = 'stopped'
        self._save(value, raw)
        self.publisher.transition_active_slot(self.selection, role, identifier,
            expected_nonce=job['nonce'], phase='retired', per_job=True)
        return copy.deepcopy(job)

    def __call__(self, arguments, timeout):
        plan = c.native_source_command(arguments, timeout)
        with self.bridge.bounded(timeout):
            return self._run_plan(plan, timeout)

    def _run_plan(self, plan, timeout):
        deadline = time.monotonic() + remaining(timeout)
        if plan.operation == 'verify':
            self._read()
            return 0
        if plan.operation in ('enable-trigger', 'disable-trigger'):
            self.trigger(plan.role, enabled=plan.operation == 'enable-trigger')
            return 0
        if plan.operation == 'quiesce-family':
            value = self._read()[0]
            for job in tuple(value['jobs'].values()):
                if job['role'] == plan.role:
                    self.stop(job['role'], job['identifier'])
            return 0
        if plan.operation == 'quiesce':
            value = self._read()[0]
            if plan.label in value['jobs']:
                self.stop(plan.role)
            return 0
        job = self.start(plan.role, plan.identifier)
        if plan.asynchronous:
            return 0 if job['phase'] in ('queued', 'observed', 'stopped') and not job['uncertain'] else 1
        while job['phase'] not in ('stopped', 'unknown') and time.monotonic() < deadline:
            time.sleep(min(.05, remaining(.05)))
            job = self.observe(plan.role, plan.identifier)
        return job['exitCode'] if job['phase'] == 'stopped' and not job['uncertain'] and job['exitCode'] is not None else 1

    def _direct_snapshot(self):
        rows = []
        parent = os.open(c.NATIVE_STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        directory = None
        try:
            directory = os.open('proposals', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            for name in sorted(os.listdir(directory)):
                if not re.fullmatch(r'(?:calendar|gmail-draft)-[0-9]{13}-[a-f0-9]{8}\.json', name):
                    continue
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    _fail('trigger-proposal-unsafe')
                rows.append([name, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns])
                if len(rows) > 4096:
                    _fail('trigger-proposal-limit')
        finally:
            if directory is not None:
                os.close(directory)
            os.close(parent)
        return self.publisher.sha(c._native_encoded(rows))

    def trigger(self, role, *, enabled):
        if role not in ('sync', 'direct') or type(enabled) is not bool:
            _fail('trigger-invalid')
        value, raw, _doc, _public = self._read()
        value['triggers'][role] = enabled
        if role == 'sync':
            value['triggers']['nextSyncNs'] = str(c._native_clock_ns() + 60 * 10**9) if enabled else None
        else:
            value['triggers']['directSnapshot'] = self._direct_snapshot() if enabled else None
        self._save(value, raw)

    def tick(self):
        value = self._read()[0]
        if value['admission'] is not None:
            pending = value['admission']
            self.start(pending['role'], pending['identifier'])
            value = self._read()[0]
        for job in tuple(value['jobs'].values()):
            if job['phase'] not in ('stopped', 'unknown'):
                self.observe(job['role'], job['identifier'])
            elif job['phase'] == 'unknown' and c._native_clock_ns() >= int(job['invocation']['workerDeadlineNs']):
                self.stop(job['role'], job['identifier'])
        value, raw, _doc, _public = self._read()
        now = c._native_clock_ns()
        for role in ('sync', 'direct'):
            trigger = value['triggers']
            if not trigger[role]:
                continue
            current = value['jobs'].get(_label(role, None))
            if current is not None and (current['phase'] != 'stopped' or current['uncertain']):
                continue
            fingerprint = self._direct_snapshot() if role == 'direct' else None
            due = (now >= int(trigger['nextSyncNs']) if role == 'sync' else fingerprint != trigger['directSnapshot'])
            if not due:
                continue
            # Trigger intent is durable before admission. Failure never causes
            # a later tick to replay an uncertain effect or a new start nonce.
            trigger['nextSyncNs' if role == 'sync' else 'directSnapshot'] = str(now + 60 * 10**9) if role == 'sync' else fingerprint
            raw = self._save(value, raw)
            self.start(role)
            value, raw, _doc, _public = self._read()


def _absent_tree(tree):
    for pid, sec, usec in _tree(tree):
        try:
            if process_birth(pid) == (sec, usec):
                _fail('execution-descendant-still-live')
        except ProcessIdentityError as error:
            if error.errno != errno.ESRCH:
                raise


def _stopped(selection, value, doc, public, label, job):
    executor = object.__new__(NativeSourceExecutor)
    executor.selection, executor.bridge = selection, selection.bridge
    role, identifier = c._native_label_parts(label)
    path, definition, image = executor._definition(public, role, identifier)
    if image != job['definition'] or executor._loaded(label, definition, path) is not None:
        _fail('execution-job-still-loaded')
    _absent_tree(job['tree'])
    allowed = set()
    witnesses = []
    for other_label, other in value['jobs'].items():
        if other_label == label or other['phase'] == 'stopped':
            continue
        path, definition, image = executor._definition(public, other['role'], other['identifier'])
        loaded = executor._loaded(other_label, definition, path)
        if image != other['definition']:
            _fail('execution-sibling-unconfirmed')
        if loaded is None or loaded['pid'] is None:
            # Concurrently completed or not-yet-started siblings grant no
            # process exemption. Their recorded descendants must also be gone.
            _absent_tree(other['tree'])
            witnesses.append((None, other['tree'], loaded, path, definition, other_label))
            continue
        if other['identity'] is None or loaded['pid'] != other['identity'][0]:
            _fail('execution-sibling-unconfirmed')
        identity = process_identity(loaded['pid'], uid=public['broker']['id'], gid=public['broker']['id'], executable=doc['hostPythonPath'])
        if list(identity) != other['identity']:
            _fail('execution-sibling-changed')
        tree = tuple(tuple(row) for row in _tree([list(row) for row in process_tree_snapshot(identity[0])]))
        if tuple(identity[:3]) not in tree:
            _fail('execution-sibling-changed')
        allowed.update(row[0] for row in tree)
        witnesses.append((identity, tuple(tree), loaded, path, definition, other_label))
    raw = selection.bridge.command(['/bin/ps', '-axo', 'pid=,uid='], timeout=remaining(10))
    if not raw.strip() or len(raw) > 1024 * 1024:
        _fail('execution-process-list-unavailable')
    seen = set()
    for line in raw.splitlines():
        row = re.fullmatch(r'\s*([1-9][0-9]*)\s+([0-9]+)\s*', line)
        if row is None:
            _fail('execution-process-list-unavailable')
        if int(row[1]) in seen:
            _fail('execution-process-list-unavailable')
        seen.add(int(row[1]))
        if int(row[2]) == public['broker']['id'] and int(row[1]) not in allowed:
            _fail('execution-unbound-broker-process')
    for identity, tree, loaded, path, definition, other_label in witnesses:
        if identity is None:
            _absent_tree(tree)
        elif (process_identity(identity[0], uid=public['broker']['id'], gid=public['broker']['id'], executable=doc['hostPythonPath']) != identity
                or tuple(process_tree_snapshot(identity[0])) != tree):
            _fail('execution-sibling-changed')
        if executor._loaded(other_label, definition, path) != loaded:
            _fail('execution-sibling-changed')
    _view(selection)


def verify_stopped(selection, role, identifier, nonce):
    """Fresh fixed Root proof; no request-supplied callback or cached PID proof."""
    doc, public = _view(selection)
    with protected_directory(c.NATIVE_META) as directory:
        value, _raw = c._native_json_at(directory, RECORD)
    _validate(value, doc, public)
    label = _label(role, identifier)
    job = value['jobs'].get(label)
    if job is None or job['nonce'] != nonce or job['phase'] != 'stopped' or not job['bootoutIntent']:
        _fail('execution-stop-proof-unavailable')
    _stopped(selection, value, doc, public, label, job)
    return copy.deepcopy(job)


def trigger_loop(make_bridge, stop):
    """Existing Root daemon worker. Disabled/absent state never provisions jobs."""
    if type(stop) is not threading.Event:
        _fail('trigger-control-invalid')
    while not stop.wait(1):
        if not os.path.lexists(c.NATIVE_META / RECORD):
            continue
        try:
            with protected_directory(c.NATIVE_META) as directory:
                value, _raw = c._native_json_at(directory, RECORD)
            if not isinstance(value, dict):
                continue
            # This is only a cheap no-work filter. A positive result grants
            # nothing: the selected deployment, journal and grants are fully
            # requalified below before a trigger can admit an invocation.
            triggers = value.get('triggers', {})
            jobs = value.get('jobs', {})
            if (value.get('admission') is None and isinstance(triggers, dict) and isinstance(jobs, dict)
                    and not any(triggers.get(role) is True for role in ('sync', 'direct'))
                    and all(isinstance(job, dict) and job.get('phase') == 'stopped' for job in jobs.values())):
                continue
            bridge = make_bridge()
            with bridge.bounded(30), bridge.locked():
                bridge.discover()
                selection = c.native_source_selection(bridge, bridge.connection_deployment())
                NativeSourceExecutor(selection).tick()
        except (AccessError, c.CustodyError, OSError, ValueError):
            # Root journals preserve uncertainty; no provider/body diagnostics.
            continue
