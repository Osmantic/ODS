"""Internal Root-managed enrollment for reviewed native apps.

There is deliberately no public route here. Every mutation joins the existing
connection transaction; OpenClaw alone owns OAuth and credentials. The durable
plan contains choices and hashes, never callback codes, PKCE, tokens or an
authorization URL. Public/host wiring must use these operations, not write the
nativeApps record or consume Consent directly.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile

from pixel_access_bridge import AccessError, runtime_config_path

from . import coordinator as c, custody, native_apps as apps, native_mcp as native, oauth, planner, state

JOURNAL = 'native-app-enrollment.json'
MAX_BYTES = 256 * 1024
PROOF_MAX_SECONDS = 300
PHASES = ('preparing', 'prepared', 'beginning', 'consent', 'authorized', 'claiming', 'activating',
          'verifying', 'ready', 'withdrawing', 'forgetting', 'disconnected', 'cancelled', 'failed')
TERMINAL = ('ready', 'disconnected', 'cancelled', 'failed')
KEYS = {'kind', 'schemaVersion', 'app', 'flowId', 'phase', 'intent', 'createdAt', 'updatedAt',
        'activationId', 'step', 'stable', 'proof', 'error'}
STEP_KEYS = {'kind', 'transactionId', 'beforeRecord', 'afterRecord', 'beforeConfigSha256',
             'afterConfigSha256', 'target', 'answers', 'custodyHeld', 'outcome', 'beforeOwner',
             'beforeEnvSha256', 'afterEnvSha256', 'beforeAnswersSha256', 'afterAnswersSha256'}
PROOF_KEYS = {'reachable', 'permitted', 'reason', 'scope', 'configSha256', 'runtimeRevision', 'checkedAt'}


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _checksum(value):
    return type(value) is str and bool(native.HEX.fullmatch(value))


def _next_record(before, app, enabled, now):
    record = copy.deepcopy(before) if before is not None else {
        'kind': state.KIND, 'schemaVersion': state.SCHEMA_VERSION,
        'ownerTimeZone': None, 'connectors': {}}
    record['updatedAt'] = now
    record.setdefault('nativeApps', {})[app] = enabled
    return state.validate(record)


def _valid_record(value):
    if value is not None:
        state.validate(value)
        if len(native.encoded(value)) > state.MAX_BYTES:
            raise ValueError()


def validate_journal(value):
    """A bounded metadata-only state machine; unknown fields are never ignored."""
    try:
        if (type(value) is not dict or set(value) != KEYS or value['kind'] != 'native-app-enrollment'
                or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
                or value['app'] not in apps.IDS or type(value['app']) is not str
                or type(value['flowId']) is not str or not oauth.FLOW_ID.fullmatch(value['flowId'])
                or value['phase'] not in PHASES or value['intent'] not in ('connect', 'withdraw')
                or value['activationId'] is not None and not _checksum(value['activationId'])
                or value['error'] is not None and (type(value['error']) is not str
                    or not re.fullmatch(r'[a-z][a-z0-9-]{0,95}', value['error']))):
            raise ValueError()
        created, updated = (native._instant(value[key]) for key in ('createdAt', 'updatedAt'))
        if updated < created:
            raise ValueError()
        stable = value['stable']
        if stable is not None and (type(stable) is not dict or set(stable) != {'recordSha256', 'configSha256'}
                or not all(_checksum(item) for item in stable.values())):
            raise ValueError()
        step = value['step']
        if step is not None:
            if (type(step) is not dict or set(step) != STEP_KEYS or step['kind'] not in ('prepare', 'activate', 'withdraw')
                    or not all(_checksum(step[key]) for key in ('transactionId', 'beforeConfigSha256', 'afterConfigSha256',
                        'beforeEnvSha256', 'afterEnvSha256', 'beforeAnswersSha256', 'afterAnswersSha256'))
                    or type(step['custodyHeld']) is not bool
                    or step['outcome'] not in (None, 'applied', 'unchanged', 'rolled-back', 'not-applied')):
                raise ValueError()
            _valid_record(step['beforeRecord'])
            _valid_record(step['afterRecord'])
            if step['afterRecord'] != _next_record(step['beforeRecord'], value['app'], step['kind'] == 'activate',
                                                  step['afterRecord']['updatedAt']):
                raise ValueError()
            if (step['target'] != c.gateway_target(step['afterRecord'], custody_held=step['custodyHeld'])
                    or step['answers'] != state.connection_answers(step['afterRecord'], google_custody=step['custodyHeld'])):
                raise ValueError()
            planner.target(step['target'])
            owner = step['beforeOwner']
            if (type(owner) is not dict or set(owner) != {'live', 'answers'}
                    or not state.valid_connection_answers(owner['answers'])):
                raise ValueError()
            planner.target(owner['live'])
        proof = value['proof']
        if proof is not None:
            if (type(proof) is not dict or set(proof) != PROOF_KEYS
                    or not all(_checksum(proof[key]) for key in ('configSha256', 'runtimeRevision'))):
                raise ValueError()
            native.worker_result({key: proof[key] for key in ('reachable', 'permitted', 'reason', 'scope')}, 'probe')
            native._instant(proof['checkedAt'])
            if stable is None or proof['configSha256'] != stable['configSha256']:
                raise ValueError()
        if value['phase'] == 'ready' and (proof is None or proof['permitted'] is not True or stable is None):
            raise ValueError()
        if value['phase'] in ('claiming', 'activating', 'verifying', 'ready') and value['activationId'] is None:
            raise ValueError()
        if value['phase'] in ('preparing', 'claiming', 'activating', 'withdrawing') and step is None:
            raise ValueError()
        if value['phase'] in ('withdrawing', 'forgetting', 'disconnected') and value['intent'] != 'withdraw':
            raise ValueError()
        return value
    except (ValueError, KeyError, TypeError, AttributeError):
        raise native.NativeError('invalid-native-enrollment-journal') from None


def _read(path):
    parent = path.parent.lstat()
    if (path.parent.resolve() != path.parent or not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.geteuid() or parent.st_mode & 0o077):
        raise native.NativeError('unsafe-native-enrollment-journal')
    try:
        return validate_journal(native.decode(native._read(path, owners=(os.geteuid(),), private=True, maximum=MAX_BYTES)))
    except FileNotFoundError:
        if path.is_symlink():
            raise native.NativeError('unsafe-native-enrollment-journal') from None
        return None


def guard_idle(bridge):
    """Other gateway mutations call this INSIDE their existing Root lock."""
    journal = _read(Path(bridge.state) / JOURNAL)
    if journal and journal['phase'] not in TERMINAL:
        raise native.NativeError('native-app-transition-in-progress')


class Controller:
    """One fixed reviewed app, one Root lock, existing owner/gateway lifecycle."""

    def __init__(self, bridge, *, app='notion', now=None):
        self.entry = apps.app(app)
        self.bridge, self.app = bridge, app
        self.path = Path(bridge.state) / JOURNAL
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.consent = native.Consent(bridge, app=app, server_name=self.entry['serverName'], now=self.now)

    def _read(self):
        journal = _read(self.path)
        if journal and journal['app'] != self.app:
            raise native.NativeError('another-native-sign-in-pending')
        return journal

    def _put(self, journal, **changes):
        journal = dict(journal, **changes, updatedAt=native._iso(self.now()))
        raw = native.encoded(validate_journal(journal)) + b'\n'
        if len(raw) > MAX_BYTES:
            raise native.NativeError('invalid-native-enrollment-journal')
        # Validate directory custody even on the first write. The metadata file
        # is private and uses the existing recognized crash-temp prefix.
        _read(self.path)
        fd, temporary = tempfile.mkstemp(prefix='.transition-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'wb') as handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            self._fsync(self.path.parent)
        finally:
            if os.path.lexists(temporary):
                os.unlink(temporary)
        return journal

    @staticmethod
    def _fsync(directory):
        fd = os.open(directory, os.O_DIRECTORY | os.O_RDONLY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _record_write(self, expected, after):
        if c._record() != expected:
            raise native.NativeError('native-app-record-changed')
        c._write_record(after)
        self._fsync(c.RECORD.parent)
        if c._record() != after:
            raise native.NativeError('native-app-record-changed')

    def _config(self):
        return c._read(runtime_config_path(self.bridge), self.bridge.owner.pw_uid)

    def _sidefiles(self):
        # These are the same fixed protected-deployment paths supplied to the
        # existing owner worker. Neither browser nor model selects a path.
        return {key: native._read(path, owners=(self.bridge.owner.pw_uid,), private=True)
            for key, path in (('env', self.bridge.home / '.config/pixel-agent/gateway.env'),
                              ('answers', self.bridge.install / 'data/pixel/onboarding.json'))}

    def _stable(self):
        return {'recordSha256': native.digest(c._record()), 'configSha256': self._config()[1]}

    def _require_stable(self, journal):
        if journal['stable'] is None or self._stable() != journal['stable']:
            raise native.NativeError('native-app-state-changed')

    def _other_transitions(self):
        # An independent Google consent/activation cannot race native enrollment.
        # Generic model/settings/source changes are also refused by bridge/owner
        # binding and the actual connection transaction's pending checks.
        lifecycle = c.read_lifecycle(c._lifecycle_path(self.bridge))
        if lifecycle and lifecycle['phase'] not in ('failed',):
            raise native.NativeError('connection-transition-in-progress')

    def _fresh_source(self):
        # Root's transition may be absent after Source has reserved its exact
        # before-image. Fresh app mutations must respect that reservation too.
        try:
            from pixel_source_upgrade import guard_fresh_admission, UpgradeError
        except ImportError:
            raise native.NativeError('source-transition-unavailable') from None
        try:
            guard_fresh_admission(self.bridge)
        except UpgradeError as error:
            raise native.NativeError(str(error)) from None

    def _flow(self, flow_id):
        journal = self._read()
        if (type(flow_id) is not str or not oauth.FLOW_ID.fullmatch(flow_id)
                or journal is None or flow_id != journal['flowId']):
            raise native.NativeError('native-flow-mismatch')
        return journal

    def _plan(self, kind):
        if self.bridge.pending():
            raise native.NativeError('transition-recovery-required')
        before = c._record()
        config, before_sha = self._config()
        owner = self.bridge.worker('connection-owner-status')
        sides = self._sidefiles()
        contract = native.decode(sides['answers'])
        if (owner['pending'] or owner['configSha256'] != before_sha or type(contract) is not dict
                or owner['envSha256'] != _hash(sides['env'])
                or owner['answers'] != {key: contract.get(key) for key in state.ANSWER_KEYS}):
            raise native.NativeError('native-app-state-changed')
        after = _next_record(before, self.app, kind == 'activate', native._iso(self.now()))
        held = custody.holds_sign_in(c.PATHS)
        google = (before or {}).get('connectors', {}).get('google')
        if (google and held and google['capabilities']['calendar.direct']
                and google.get('directPaused', False) != c._full_access(self.bridge)):
            raise native.NativeError('connection-access-mode-sync-required')
        target = c.gateway_target(after, custody_held=held)
        planned = planner.plan_config(config, target, install_dir=str(self.bridge.home / '.local/share/pixel'))
        after_sha = _hash((json.dumps(planned, indent=2, sort_keys=True) + '\n').encode('utf-8'))
        answers = state.connection_answers(after, google_custody=held)
        for key, value in answers.items():
            if value is None:
                contract.pop(key, None)
            else:
                contract[key] = value
        after_answers = (json.dumps(contract, indent=2, sort_keys=True) + '\n').encode('utf-8')
        after_env = planner.plan_gateway_env(sides['env'].decode('utf-8'), target).encode('utf-8')
        return {'kind': kind, 'transactionId': secrets.token_hex(32), 'beforeRecord': before, 'afterRecord': after,
                'beforeConfigSha256': before_sha, 'afterConfigSha256': after_sha, 'target': target,
                'answers': answers, 'custodyHeld': held, 'outcome': None,
                'beforeOwner': {key: owner[key] for key in ('live', 'answers')},
                'beforeEnvSha256': _hash(sides['env']), 'afterEnvSha256': _hash(after_env),
                'beforeAnswersSha256': _hash(sides['answers']), 'afterAnswersSha256': _hash(after_answers)}

    def _step_state(self, step):
        record = c._record()
        if record not in (step['beforeRecord'], step['afterRecord']):
            raise native.NativeError('native-app-record-changed')
        if custody.holds_sign_in(c.PATHS) != step['custodyHeld']:
            raise native.NativeError('native-app-custody-changed')
        if self._config()[1] not in (step['beforeConfigSha256'], step['afterConfigSha256']):
            raise native.NativeError('native-app-state-changed')
        sides = self._sidefiles()
        if (_hash(sides['env']) not in (step['beforeEnvSha256'], step['afterEnvSha256'])
                or _hash(sides['answers']) not in (step['beforeAnswersSha256'], step['afterAnswersSha256'])):
            raise native.NativeError('native-app-state-changed')
        return record

    def _change(self, step):
        return c._change_gateway(self.bridge, step['target'], step['answers'], transaction_id=step['transactionId'],
            before_config_sha256=step['beforeConfigSha256'], after_config_sha256=step['afterConfigSha256'])

    def _run_step(self, journal):
        step = journal['step']
        record = self._step_state(step)
        if step['outcome'] is not None:
            # A known durable outcome is re-proved, never submitted again. In
            # particular, do not briefly restore an enable choice after its
            # rollback was already acknowledged.
            return self._settled(journal, step['outcome'])
        if record != step['afterRecord']:
            self._record_write(record, step['afterRecord'])
        try:
            outcome = self._change(step)
        except AccessError as error:
            self._put(journal, error=error.code if re.fullmatch(r'[a-z][a-z0-9-]{0,95}', error.code)
                      else 'native-app-transition-unconfirmed')
            raise
        return self._settled(journal, outcome)

    def _settled(self, journal, outcome):
        step = journal['step']
        expected = step['beforeConfigSha256'] if outcome in ('rolled-back', 'not-applied') else step['afterConfigSha256']
        if outcome not in ('applied', 'unchanged', 'rolled-back', 'not-applied') or self._config()[1] != expected:
            raise native.NativeError('native-app-transition-unconfirmed')
        owner = self.bridge.worker('connection-owner-status')
        sides = self._sidefiles()
        before = outcome in ('rolled-back', 'not-applied')
        side = 'before' if before else 'after'
        if (owner['pending'] or owner['configSha256'] != expected
                or _hash(sides['env']) != step[side + 'EnvSha256']
                or _hash(sides['answers']) != step[side + 'AnswersSha256']
                or (before and {key: owner[key] for key in ('live', 'answers')} != step['beforeOwner'])
                or (not before and (owner['answers'] != step['answers'] or not c._live_matches(owner, step['target'])))):
            raise native.NativeError('native-app-transition-unconfirmed')
        if outcome in ('rolled-back', 'not-applied') and c._record() != step['beforeRecord']:
            self._record_write(step['afterRecord'], step['beforeRecord'])
        if outcome in ('applied', 'unchanged') and c._record() != step['afterRecord']:
            raise native.NativeError('native-app-record-changed')
        # A byte match alone is insufficient even for a disabled preparation.
        native._verified_runtime(self.bridge, {'configSha256': expected})
        return self._put(journal, step=dict(step, outcome=outcome), stable=self._stable(), proof=None, error=None)

    def _settle_existing(self, journal):
        """Withdrawal/cancellation may settle an old write, NEVER freshly grant it."""
        step = journal['step']
        if step is None or step['outcome'] is not None:
            return journal
        self._step_state(step)
        pending = self.bridge.pending()
        if pending:
            if pending.get('kind') != 'connection' or pending.get('transactionId') != step['transactionId']:
                raise native.NativeError('transition-recovery-required')
            # A held exact step is already in progress, so settling it may
            # acknowledge a prior applied result or restore its before-images.
            outcome = self._change(step)
        else:
            owner = self.bridge.worker('connection-owner-status')
            completion = owner.get('completion')
            if completion and completion.get('transactionId') == step['transactionId']:
                outcome = self._change(step)  # Closed matching receipt: proof only, never an apply.
            elif not owner['pending'] and owner['configSha256'] == step['beforeConfigSha256']:
                outcome = 'not-applied'  # A durable intent that never reached an owner write.
            else:
                raise native.NativeError('native-app-transition-unconfirmed')
        return self._settled(journal, outcome)

    def _finish_claim(self, journal):
        saved = self.consent._read()
        if saved is None or saved['flowId'] != journal['flowId']:
            if journal['activationId'] is not None:
                raise native.NativeError('native-activation-claim-mismatch')
            return
        if saved['phase'] in ('activating', 'settled'):
            self.consent._finish_authorization_locked(flow_id=journal['flowId'], activation_id=journal['activationId'])

    def _public(self, journal, *, verified=False, error=None):
        if journal is None:
            return {'app': self.app, 'phase': 'absent', 'flowId': None, 'error': None,
                    'portalAccessVerified': False, 'scope': 'pixel-owner-default'}
        result = {key: journal[key] for key in ('app', 'phase', 'flowId', 'error')}
        result.update(portalAccessVerified=verified, scope='pixel-owner-default')
        if error:
            result.update(error=error, phase='verifying')
        if journal['phase'] == 'disconnected':
            result['providerRevoked'] = False
        if verified:
            result['checkedAt'] = journal['proof']['checkedAt']
        return result

    def _cached_ready(self, journal):
        if journal['phase'] != 'ready':
            return False
        try:
            self._require_stable(journal)
            proof = journal['proof']
            age = (self.now() - native._instant(proof['checkedAt'])).total_seconds()
            if not 0 <= age <= PROOF_MAX_SECONDS:
                return False
            return native._verified_runtime(self.bridge, {'configSha256': proof['configSha256']}) == proof['runtimeRevision']
        except (AccessError, native.NativeError):
            return False

    def begin(self, *, redirect_port):
        if type(redirect_port) is not int or not 1024 <= redirect_port <= 65535:
            raise native.NativeError('invalid-native-oauth-redirect')
        with self.bridge.bounded(300), self.bridge.locked():
            self.bridge.discover()
            self._other_transitions()
            journal = self._read()
            if journal and journal['phase'] == 'prepared':
                self._require_stable(journal)
            else:
                if journal and journal['phase'] not in TERMINAL:
                    raise native.NativeError('native-sign-in-in-progress')
                # A source hold can survive bind() before Root transition.json
                # exists. Admit no new app flow across that durable reservation.
                self._fresh_source()
                record = c._record()
                config = self._config()[0]
                if (state.native_app_choices(record).get(self.app) is True
                        or apps.configured_choices(config).get(self.app) is True):
                    raise native.NativeError('native-app-already-connected')
                saved = self.consent._read()
                if saved and saved['phase'] not in ('failed', 'cancelled', 'settled'):
                    raise native.NativeError('native-sign-in-in-progress')
                now = native._iso(self.now())
                journal = {'kind': 'native-app-enrollment', 'schemaVersion': 1, 'app': self.app,
                    'flowId': secrets.token_hex(16), 'phase': 'preparing', 'intent': 'connect',
                    'createdAt': now, 'updatedAt': now, 'activationId': None,
                    'step': self._plan('prepare'), 'stable': None, 'proof': None, 'error': None}
                journal = self._put(journal)
                journal = self._prepare(journal)
                if journal['phase'] == 'failed':
                    return self._public(journal)
            journal = self._put(journal, phase='beginning')
            # Same flow ID was persisted in the enrollment intent before the
            # native consent side effect. No authorization URL is journaled.
            result = self.consent._begin_locked(redirect_port=redirect_port, flow_id=journal['flowId'])
            journal = self._put(journal, phase=result['phase'])
            return dict(self._public(journal), authUrl=result['authUrl'], expiresAt=result['expiresAt'])

    def _prepare(self, journal):
        journal = self._run_step(journal)
        if journal['step']['outcome'] == 'rolled-back':
            return self._put(journal, phase='failed', error='native-app-preparation-rolled-back')
        return self._put(journal, phase='prepared')

    def complete(self, *, flow_id, callback_url):
        with self.bridge.bounded(70), self.bridge.locked():
            self.bridge.discover()
            journal = self._flow(flow_id)
            if journal['phase'] != 'consent':
                raise native.NativeError('native-callback-already-claimed')
            self._require_stable(journal)
            result = self.consent._complete_locked(flow_id=flow_id, callback_url=callback_url)
            journal = self._put(journal, phase=result['phase'], error=result['error'])
            return self._public(journal)

    def activate(self, *, flow_id):
        with self.bridge.bounded(300), self.bridge.locked():
            self.bridge.discover()
            journal = self._flow(flow_id)
            if journal['intent'] != 'connect':
                raise native.NativeError('native-app-withdrawal-in-progress')
            if journal['phase'] == 'authorized':
                self._require_stable(journal)
                step = self._plan('activate')
                journal = self._put(journal, phase='claiming', step=step, activationId=step['transactionId'])
            if journal['phase'] not in ('claiming', 'activating', 'verifying', 'ready'):
                raise native.NativeError('native-authorization-required')
            journal = self._activate(journal)
            return self._public(journal, verified=self._cached_ready(journal))

    def _activate(self, journal):
        if journal['phase'] == 'ready' and self._cached_ready(journal):
            return journal
        if journal['phase'] == 'ready':
            # A settled grant may survive an ordinary configuration update.
            # Revalidate the CURRENT exact app and protected owner choice,
            # without replaying its old plan or rewriting unrelated settings.
            config, config_sha = self._config()
            if (state.native_app_choices(c._record()).get(self.app) is not True
                    or apps.configured_choices(config).get(self.app) is not True):
                raise native.NativeError('native-app-state-changed')
            native._verified_runtime(self.bridge, {'configSha256': config_sha})
            journal = self._put(journal, phase='verifying', stable=self._stable(), proof=None)
        if journal['phase'] == 'claiming':
            saved = self.consent._flow(journal['flowId'])
            if saved['phase'] == 'authorized':
                self.consent._recover_locked(flow_id=journal['flowId'])
                saved = self.consent._flow(journal['flowId'])
            if saved['phase'] in ('failed', 'cancelled'):
                return self._unclaimed(journal, phase='failed', error=saved['error'] or 'native-authorization-required')
            if saved['phase'] == 'authorized':
                try:
                    self.consent._claim_authorization_locked(flow_id=journal['flowId'], activation_id=journal['activationId'])
                except native.NativeError as error:
                    if error.code != 'native-flow-expired':
                        raise
                    result = self.consent._recover_locked(flow_id=journal['flowId'])
                    if result['phase'] != 'failed':
                        raise
                    return self._unclaimed(journal, phase='failed', error=result['error'])
            elif saved['phase'] != 'activating' or saved['activationId'] != journal['activationId']:
                raise native.NativeError('native-activation-claim-mismatch')
            journal = self._put(journal, phase='activating')
        if journal['phase'] == 'activating':
            journal = self._run_step(journal)
            if journal['step']['outcome'] == 'rolled-back':
                self._finish_claim(journal)
                return self._put(journal, phase='failed', error='native-app-activation-rolled-back')
            journal = self._put(journal, phase='verifying')
        self._require_stable(journal)
        proof = native._check_access_locked(self.bridge, self.entry['serverName'])
        proof['checkedAt'] = native._iso(self.now())
        journal = self._put(journal, phase='verifying', proof=proof,
                            error=None if proof['permitted'] else 'native-app-' + proof['reason'])
        if not proof['permitted']:
            return journal
        self._finish_claim(journal)
        return self._put(journal, phase='ready')

    def _unclaimed(self, journal, *, phase, error):
        """Close a proven never-started plan, not an unknown reservation."""
        saved = self.consent._flow(journal['flowId'])
        step = journal['step']
        if (journal['phase'] != 'claiming' or step['kind'] != 'activate' or 'activationId' in saved
                or saved['phase'] not in ('authorized', 'failed', 'cancelled')
                or self.bridge.pending() or c._record() != step['beforeRecord']
                or self._config()[1] != step['beforeConfigSha256']):
            raise native.NativeError('native-activation-settlement-required')
        # _settled joins exact environment/onboarding before-images, the owner
        # status and live Root proof. No native or gateway operation is replayed.
        journal = self._settled(journal, 'not-applied')
        if phase == 'cancelled':
            self.consent._cancel_locked(flow_id=journal['flowId'])
        return self._put(journal, phase=phase, error=error)

    def recover(self, *, flow_id):
        with self.bridge.bounded(300), self.bridge.locked():
            self.bridge.discover()
            journal = self._flow(flow_id)
            if journal['intent'] == 'withdraw':
                return self._public(self._withdraw(journal))
            if journal['phase'] == 'preparing':
                return self._public(self._prepare(journal))
            if journal['phase'] in ('beginning', 'consent', 'authorized'):
                self._require_stable(journal)
                saved = self.consent._read()
                if saved is None or saved['flowId'] != journal['flowId']:
                    journal = self._put(journal, phase='failed', error='native-oauth-attempt-unconfirmed')
                else:
                    result = self.consent._recover_locked(flow_id=flow_id)
                    journal = self._put(journal, phase=result['phase'], error=result['error'])
            elif journal['phase'] in ('claiming', 'activating', 'verifying', 'ready'):
                journal = self._activate(journal)
            return self._public(journal, verified=self._cached_ready(journal))

    def cancel(self, *, flow_id):
        with self.bridge.bounded(300), self.bridge.locked():
            self.bridge.discover()
            journal = self._flow(flow_id)
            if journal['phase'] == 'claiming' and journal['intent'] == 'connect':
                return self._public(self._unclaimed(journal, phase='cancelled', error=None))
            if journal['activationId'] is not None or journal['intent'] == 'withdraw':
                raise native.NativeError('native-activation-settlement-required')
            journal = self._settle_existing(journal)
            saved = self.consent._read()
            if saved and saved['flowId'] == flow_id:
                self.consent._cancel_locked(flow_id=flow_id)
            return self._public(self._put(journal, phase='cancelled', proof=None, error=None))

    def disconnect(self):
        with self.bridge.bounded(300), self.bridge.locked():
            self.bridge.discover()
            self._other_transitions()
            journal = self._read()
            if journal is None or journal['intent'] != 'withdraw' or journal['phase'] not in ('withdrawing', 'forgetting'):
                self._fresh_source()
            if journal is not None and journal['phase'] == 'disconnected':
                # A completed withdrawal is not a resumable plan. Other
                # admitted writers may have changed its old before-images.
                # Re-prove an unchanged disabled choice without reopening it;
                # a later explicit grant instead needs a fresh withdrawal.
                config, config_sha = self._config()
                protected = state.native_app_choices(c._record()).get(self.app)
                configured = apps.configured_choices(config).get(self.app)
                if protected is False and configured is False:
                    native._verified_runtime(self.bridge, {'configSha256': config_sha})
                    return self._public(journal)
                if protected is not True or configured is not True:
                    raise native.NativeError('native-app-state-changed')
                journal = dict(journal, intent='withdraw', phase='withdrawing',
                    step=self._plan('withdraw'), stable=None, proof=None, error=None)
            if journal is None:
                saved = self.consent._read()
                if saved and saved['phase'] not in ('failed', 'cancelled', 'settled'):
                    raise native.NativeError('native-enrollment-recovery-required')
                now = native._iso(self.now())
                journal = {'kind': 'native-app-enrollment', 'schemaVersion': 1, 'app': self.app,
                    'flowId': secrets.token_hex(16), 'phase': 'withdrawing', 'intent': 'withdraw',
                    'createdAt': now, 'updatedAt': now, 'activationId': None,
                    'step': self._plan('withdraw'), 'stable': None, 'proof': None, 'error': None}
            else:
                # Intent wins over a pending enable plan BEFORE settlement. A
                # recovery must never resume this as fresh authorization.
                journal = dict(journal, intent='withdraw', phase='withdrawing', proof=None, error=None)
            journal = self._put(journal)
            return self._public(self._withdraw(journal))

    def _withdraw(self, journal):
        if journal['phase'] == 'disconnected':
            return journal
        if journal['phase'] != 'forgetting':
            if journal['step']['kind'] != 'withdraw':
                journal = self._settle_existing(journal)
            if journal['step']['kind'] != 'withdraw' or journal['step']['outcome'] in ('rolled-back', 'not-applied'):
                journal = self._put(journal, phase='withdrawing', step=self._plan('withdraw'))
            if journal['step']['outcome'] is None:
                journal = self._run_step(journal)
            if journal['step']['outcome'] == 'rolled-back':
                return self._put(journal, error='native-app-withdrawal-rolled-back')
            journal = self._put(journal, phase='forgetting')
        self._require_stable(journal)
        native._forget_disabled_locked(self.bridge, self.entry['serverName'])
        self._finish_claim(journal)
        saved = self.consent._read()
        if saved and saved['flowId'] == journal['flowId'] and saved['phase'] not in ('settled', 'activating'):
            self.consent._cancel_locked(flow_id=journal['flowId'])
        return self._put(journal, phase='disconnected', error=None, proof=None)

    def status(self):
        """Cached scoped status, with fresh Root proof but no provider/Node work."""
        with self.bridge.bounded(20), self.bridge.locked():
            self.bridge.discover()
            journal = self._read()
            verified = bool(journal and self._cached_ready(journal))
            return self._public(journal, verified=verified,
                error='native-app-verification-required' if journal and journal['phase'] == 'ready' and not verified else None)
