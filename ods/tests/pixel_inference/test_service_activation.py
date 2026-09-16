"""Real owner config/environment transactions with simulated service/SDK proof.

These tests cannot establish root custody, actual SDK behavior or installation.
The separate disposable native/systemd qualification supplies those observations.
"""
import sys
if sys.platform == "win32":
    from unittest import SkipTest
    raise SkipTest("Requires POSIX host ownership, file locks, or Unix sockets; run under Linux/WSL")

import copy
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import MethodType

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'bin'))
sys.path.insert(0, str(ROOT / 'extensions/services/pixel-agent/host'))
import provider_transaction as owner
from pixel_access_bridge import AccessError, atomic_json
from pixel_provider import service_activation as service
from pixel_provider.activation_config import plan_activation
from pixel_settings.runtime import configured_fields
from test_service_environment import BINDING, arm, participant  # noqa: F401


@pytest.fixture
def lifecycle(participant):  # noqa: F811 - imported pytest fixture
    p, b = participant, participant.bridge
    config = {'agents': {'list': [{'id': 'pixel', 'model': 'local/model'}]},
              'plugins': {'entries': {'pixel-ods': {'enabled': True, 'hooks': {'allowConversationAccess': True}}}}}
    b.before = (json.dumps(config, indent=2) + '\n\n').encode()
    plan = plan_activation(config, revision=1, allow_cloud=False, activation_id=BINDING['activationId'])
    b.after = owner._encoded(plan['document'])
    b.config.write_bytes(b.before)
    b.pid, b.started, b.stopped, b.restarts, b.stops = 110, 1000, False, 0, 0
    b.boundary, b.definition = 'ProtectSystem=strict\nProtectHome=read-only', '/usr/bin/node pinned-entry'
    b.native_origin, b.native_key = 'http://127.0.0.1:18789', 'not-a-real-key'
    b.nrev, b.live_binding, b.failure = 'e' * 64, None, None
    b.fragment = p.dropin.parent / 'openclaw-gateway.service'
    b.fragment.write_text('[Service]\nExecStart=/usr/bin/node pinned-entry\n')
    b.fragment.chmod(0o644)
    b.record.update(runtimeCustody='f' * 64, boundary=b.boundary, mode='sandboxed')

    def command(self, args, timeout=20):
        if 'stop' in args:
            self.stops += 1
            if self.failure == 'stop': raise AccessError('host-command-failed')
            self.stopped = True
            return ''
        if 'restart' in args:
            self.restarts += 1
            if self.failure == 'restart':
                self.stopped = True
                raise AccessError('host-command-failed')
            self.stopped = False
            if self.failure != 'same-identity': self.pid += 1; self.started += 1000
            self.live_binding = json.loads(self.config.read_bytes())['plugins']['entries']['pixel-ods'].get('config', {}).get('managedProvider')
            return ''
        if 'daemon-reload' in args:
            if self.failure == 'reload': raise AccessError('host-command-failed')
            return ''
        if 'cat' in args:
            text = '# /etc/systemd/system/openclaw-gateway.service\n[Service]\nExecStart=' + self.definition + '\n'
            if p.dropin.exists(): text += '\n# ' + str(p.dropin) + '\n' + p.dropin.read_text()
            return text
        if '--property=FragmentPath,DropInPaths' in args:
            return f'FragmentPath={self.fragment}\nDropInPaths={p.dropin if p.dropin.exists() else ""}'
        if any('ExecMainStartTimestampMonotonic' in arg for arg in args):
            return f'MainPID={0 if self.stopped else self.pid}\nActiveState={"failed" if self.stopped else "active"}\nExecMainStartTimestampMonotonic={self.started}'
        return self.definition

    def native(self, operation=None, token=None, **kwargs):
        if operation and token != self.token: raise AccessError('native-lease-unconfirmed')
        return {'phase': self.phase, 'active': self.active, 'pid': self.pid, 'revision': self.nrev,
                'proof': {'pid': self.pid, 'mode': 'sandboxed', 'executed': True}}

    def http(self, origin, path, key, payload=None, **kwargs):
        if path == '/health': return {'ok': True}
        result = {'schemaVersion': 1, 'source': 'current-runtime-config', 'pid': self.pid,
                  'runtimeVersion': '2026.6.33', 'revision': self.nrev,
                  'observedAt': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')}
        if payload['operation'] == 'settings-readback':
            return dict(result, **configured_fields(json.loads(self.config.read_bytes())))
        binding = None if self.failure == 'registration' and self.live_binding else self.live_binding
        return dict(result, source='current-provider-registration', transportVerified=False,
                    registration={'status': 'active' if binding else 'inactive', 'binding': binding})

    b.command, b.native, b.http = MethodType(command, b), MethodType(native, b), MethodType(http, b)
    b.unit_boundary = lambda: b.boundary
    b.settings_source = lambda: 'qualified-owner-data'
    b.provision_probe = lambda: None
    b.stopped_native = lambda token: {'stopped': b.stopped and token == b.token}
    b.record['serviceDefinition'] = service.definition(b, p.dropin)
    atomic_json(b.state / 'transition.json', b.record)
    arm(p)
    return p


def callback(p):
    return service.activate(p.bridge, p.bridge.record, p, qualify_runtime=lambda: 'f' * 64)


def change(p):
    b = p.bridge
    return owner.change_provider(str(b.config), state_dir=str(b.home / '.openclaw/.ods-access-mode'),
        binding=copy.deepcopy(BINDING), transaction_id=b.record['transactionId'],
        expected_config_sha256=hashlib.sha256(b.before).hexdigest(),
        validate_config=lambda path: bool(json.loads(Path(path).read_bytes())),
        check_no_active_run=lambda: bool(b.active or b.streams), activate=lambda: callback(p))


def test_real_owner_apply_selects_new_environment_and_duplicate_callback_does_not_restart(lifecycle):
    p, b = lifecycle, lifecycle.bridge
    assert change(p)['status'] == 'registration-verified'
    assert b.restarts == 1 and b.config.read_bytes() == b.after
    assert b.live_binding == BINDING
    assert b.record['providerServiceVerified']['side'] == 'after'
    assert callback(p) == 'verified' and b.restarts == 1
    assert b.phase == 'held'  # Outer coordinator still must check completion/release.


def test_rejected_forward_uses_same_callback_to_restore_old_env_and_exact_config(lifecycle):
    p, b = lifecycle, lifecycle.bridge
    b.failure = 'registration'
    result = change(p)
    assert result['status'] == 'rolled-back' and result['binding'] is None
    assert b.config.read_bytes() == b.before and b.restarts == 2
    assert b.live_binding is None
    assert p.snapshot() == {'environment': None, 'dropin': None}
    assert b.record['providerServiceVerified']['side'] == 'before'


@pytest.mark.parametrize('failure', ['reload', 'restart'])
def test_failed_restart_requires_explicit_owner_recovery(lifecycle, failure):
    p, b = lifecycle, lifecycle.bridge
    b.failure = failure
    with pytest.raises(AccessError): change(p)
    sd = b.home / '.openclaw/.ods-access-mode'
    assert (sd / owner.JOURNAL).exists() and b.pending()['phase'] == 'restarting'
    b.failure = None
    result = owner.recover_provider(str(b.config), state_dir=str(sd), transaction_id=b.record['transactionId'],
        expected_config_sha256=hashlib.sha256(b.config.read_bytes()).hexdigest(),
        validate_config=lambda path: bool(json.loads(Path(path).read_bytes())),
        check_no_active_run=lambda: False, activate=lambda: callback(p))
    assert result['status'] == 'rolled-back' and b.config.read_bytes() == b.before
    assert not p.environment.exists() and not p.dropin.exists() and not b.stopped


def test_previously_stopped_unit_is_not_started_or_service_files_written(lifecycle):
    p, b = lifecycle, lifecycle.bridge
    b.config.write_bytes(b.after)
    b.stopped = True
    assert callback(p) == 'unavailable'
    assert not p.environment.exists() and not p.dropin.exists() and b.restarts == 0


@pytest.mark.parametrize('drift', ['custody', 'boundary', 'definition'])
def test_drift_refuses_before_service_write(lifecycle, drift):
    p, b = lifecycle, lifecycle.bridge
    b.config.write_bytes(b.after)
    if drift == 'custody': b.record['runtimeCustody'] = 'a' * 64; atomic_json(b.state / 'transition.json', b.record)
    if drift == 'boundary': b.boundary += '\nNoNewPrivileges=no'
    if drift == 'definition': b.definition = '/tmp/unreviewed'
    with pytest.raises(AccessError): callback(p)
    assert not p.environment.exists() and b.restarts == 0


def test_current_proof_does_not_hide_changed_environment(lifecycle):
    p, b = lifecycle, lifecycle.bridge
    change(p)
    p.environment.write_bytes(b'foreign')
    with pytest.raises(AccessError): callback(p)
    assert b.restarts == 1 and p.environment.read_bytes() == b'foreign'


@pytest.mark.parametrize('mutation', ['pid', 'time', 'transport', 'binding', 'schema', 'extra'])
def test_registration_is_exact_current_process_contract(mutation):
    identity = {'pid': 123}
    value = {'schemaVersion': 1, 'source': 'current-provider-registration', 'pid': 123,
             'runtimeVersion': '2026.6.33', 'revision': 'a' * 64,
             'observedAt': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'), 'transportVerified': False,
             'registration': {'status': 'active', 'binding': BINDING}}
    assert service._registration(value, identity, 'a' * 64, BINDING)
    if mutation == 'pid': value['pid'] += 1
    if mutation == 'time': value['observedAt'] = 'today'
    if mutation == 'transport': value['transportVerified'] = True
    if mutation == 'binding': value['registration']['binding'] = None
    if mutation == 'schema': value['schemaVersion'] = True
    if mutation == 'extra': value['unsafe'] = True
    assert not service._registration(value, identity, 'a' * 64, BINDING)
