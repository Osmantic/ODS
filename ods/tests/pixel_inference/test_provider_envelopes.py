"""Contract tests for pixel_provider/public.py.

These validators are the strict nonsecret envelopes the host boundary
(`pixel_provider/host_api.py`, `ods-host-agent.py`) exchanges with the runtime
coordinator. The dashboard polls `runtime_status` documents shaped exactly like
this; a regression here breaks status display or, worse, admits a malformed
binding into activation decisions.
"""
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'bin'))
from pixel_provider.public import (
    KEYS, REASONS, STATES, from_controller, normalize_binding, normalize_change,
    normalize_outcome, normalize_runtime, safe_reason, unavailable,
)

HEX = 'a' * 64
ACTIVATION = str(uuid.uuid4())


def binding(**changes):
    value = {'schemaVersion': 1, 'activationId': ACTIVATION, 'revision': 3, 'allowCloud': False}
    value.update(changes)
    return value


def runtime(status='applied', **changes):
    value = {
        'schemaVersion': 1, 'status': status, 'revision': HEX, 'providerRevision': 3,
        'binding': binding(), 'pending': False, 'registrationVerified': True,
        'transportVerified': False, 'lastVerifiedAt': '2026-09-17T04:05:06Z', 'reason': None,
    }
    value.update(changes)
    return value


class TestSafeReason:
    def test_known_reasons_pass_through(self):
        for reason in ('runtime-busy', 'provider-not-managed', 'model-lifecycle-busy'):
            assert safe_reason(reason) == reason

    def test_unknown_reasons_fall_back(self):
        assert safe_reason('made-up') == 'provider-controller-unavailable'
        assert safe_reason('made-up', fallback='runtime-busy') == 'runtime-busy'
        assert safe_reason(None) == 'provider-controller-unavailable'
        assert safe_reason(7) == 'provider-controller-unavailable'


class TestUnavailable:
    def test_shape(self):
        result = unavailable('runtime-busy')
        assert result['status'] == 'unavailable' and result['reason'] == 'runtime-busy'
        assert set(result) == KEYS
        assert all(result[k] is None for k in KEYS - {'schemaVersion', 'status', 'reason'})

    def test_rejects_foreign_reason(self):
        with pytest.raises(ValueError, match='invalid-provider-runtime-response'):
            unavailable('not-a-reason')


class TestBinding:
    def test_accepts_valid(self):
        assert normalize_binding(binding()) == binding()
        assert normalize_binding(None) is None

    @pytest.mark.parametrize('patch', [
        {'activationId': 'not-a-uuid'}, {'activationId': ACTIVATION.upper()},
        {'revision': -1}, {'revision': '3'}, {'allowCloud': 'false'},
        {'schemaVersion': 2},
    ])
    def test_rejects_invalid(self, patch):
        with pytest.raises(ValueError, match='invalid-provider-runtime-response'):
            normalize_binding(binding(**patch))

    def test_rejects_extra_and_missing_keys(self):
        with pytest.raises(ValueError):
            normalize_binding(binding(extra=1))
        bad = binding()
        del bad['revision']
        with pytest.raises(ValueError):
            normalize_binding(bad)


class TestNormalizeChange:
    @pytest.mark.parametrize('operation', ['apply', 'deactivate', 'recover'])
    def test_operations(self, operation):
        result = normalize_change({'operation': operation, 'revision': HEX, 'providerRevision': 2})
        assert result['operation'] == operation

    @pytest.mark.parametrize('patch', [
        {'operation': 'restart'}, {'revision': 'short'}, {'revision': HEX.upper()},
        {'providerRevision': '2'}, {'providerRevision': -1},
    ])
    def test_rejects_invalid(self, patch):
        request = {'operation': 'apply', 'revision': HEX, 'providerRevision': 2}
        request.update(patch)
        with pytest.raises(ValueError, match='invalid-provider-runtime-request'):
            normalize_change(request)


class TestNormalizeRuntime:
    def test_unavailable_requires_reason_and_nulls(self):
        result = normalize_runtime(unavailable('provider-not-managed'))
        assert result['status'] == 'unavailable'
        value = unavailable('runtime-busy')
        value['reason'] = 'foreign-reason'
        with pytest.raises(ValueError, match='invalid-provider-runtime-response'):
            normalize_runtime(value)

    def test_unavailable_rejects_non_null_fields(self):
        value = unavailable('runtime-busy')
        value['revision'] = HEX
        with pytest.raises(ValueError, match='invalid-provider-runtime-response'):
            normalize_runtime(value)

    @pytest.mark.parametrize('status,pending', [('pending', True), ('not-applied', False)])
    def test_pending_states(self, status, pending):
        value = runtime(status=status, binding=None, pending=pending,
                        registrationVerified=False, lastVerifiedAt=None)
        assert normalize_runtime(value)['status'] == status

    def test_pending_requires_flag_and_no_verification(self):
        with pytest.raises(ValueError):
            normalize_runtime(runtime(status='pending', binding=None, pending=False,
                                      registrationVerified=False, lastVerifiedAt=None))
        with pytest.raises(ValueError):
            normalize_runtime(runtime(status='pending', pending=True,
                                      registrationVerified=True, lastVerifiedAt=None))
        with pytest.raises(ValueError):
            normalize_runtime(runtime(status='pending', pending=True,
                                      registrationVerified=False,
                                      lastVerifiedAt='2026-09-17T00:00:00Z'))

    def test_applied_requires_binding_at_current_revision(self):
        assert normalize_runtime(runtime())['status'] == 'applied'
        with pytest.raises(ValueError):
            normalize_runtime(runtime(binding=binding(revision=2)))
        with pytest.raises(ValueError):
            normalize_runtime(runtime(binding=None))

    def test_saved_changes_means_stale_binding(self):
        assert normalize_runtime(runtime(status='saved-changes', binding=binding(revision=2)))[
            'status'] == 'saved-changes'
        with pytest.raises(ValueError):
            normalize_runtime(runtime(status='saved-changes'))

    def test_inactive_has_no_binding(self):
        assert normalize_runtime(runtime(status='inactive', binding=None))['status'] == 'inactive'
        with pytest.raises(ValueError):
            normalize_runtime(runtime(status='inactive'))

    @pytest.mark.parametrize('patch', [
        {'registrationVerified': False}, {'pending': True},
        {'transportVerified': True}, {'reason': 'runtime-busy'},
        {'revision': 'nope'}, {'providerRevision': 'x'},
        {'lastVerifiedAt': '17-09-2026'}, {'lastVerifiedAt': '2026-09-17 04:05:06'},
    ])
    def test_active_states_reject_inconsistent_fields(self, patch):
        with pytest.raises(ValueError, match='invalid-provider-runtime-response'):
            normalize_runtime(runtime(**patch))

    def test_unknown_status_rejected(self):
        with pytest.raises(ValueError):
            normalize_runtime(runtime(status='healthy'))

    def test_missing_or_extra_keys_rejected(self):
        value = runtime()
        del value['pending']
        with pytest.raises(ValueError):
            normalize_runtime(value)
        with pytest.raises(ValueError):
            normalize_runtime(runtime(extra=1))


class TestFromController:
    def test_wraps_controller_payload(self):
        payload = {key: runtime()[key] for key in KEYS - {'schemaVersion', 'reason'}}
        result = from_controller(payload)
        assert result['schemaVersion'] == 1 and result['status'] == 'applied'

    def test_rejects_prefilled_envelope(self):
        payload = dict(runtime())
        with pytest.raises(ValueError):
            from_controller(payload)


class TestNormalizeOutcome:
    def request(self, operation='apply', provider_revision=3):
        return {'operation': operation, 'revision': HEX, 'providerRevision': provider_revision}

    def outcome(self, **changes):
        value = {'outcome': 'applied', 'binding': binding(),
                 'registrationVerified': True, 'transportVerified': False}
        value.update(changes)
        return value

    def test_applied_outcome(self):
        assert normalize_outcome(self.outcome())['outcome'] == 'applied'

    @pytest.mark.parametrize('patch', [
        {'outcome': 'failed'}, {'registrationVerified': False},
        {'transportVerified': True}, {'binding': 'x'},
    ])
    def test_rejects_invalid(self, patch):
        with pytest.raises(ValueError, match='invalid-provider-runtime-response'):
            normalize_outcome(self.outcome(**patch))

    def test_apply_requires_binding_at_requested_revision(self):
        assert normalize_outcome(self.outcome(), self.request())['outcome'] == 'applied'
        with pytest.raises(ValueError, match='provider-runtime-outcome-mismatch'):
            normalize_outcome(self.outcome(binding=binding(revision=2)), self.request())
        with pytest.raises(ValueError, match='provider-runtime-outcome-mismatch'):
            normalize_outcome(self.outcome(binding=None), self.request())
        with pytest.raises(ValueError, match='provider-runtime-outcome-mismatch'):
            normalize_outcome(self.outcome(outcome='rolled-back'), self.request())

    def test_deactivate_requires_no_binding(self):
        assert normalize_outcome(self.outcome(binding=None), self.request('deactivate'))
        with pytest.raises(ValueError, match='provider-runtime-outcome-mismatch'):
            normalize_outcome(self.outcome(), self.request('deactivate'))

    def test_recover_accepts_rolled_back(self):
        result = normalize_outcome(self.outcome(outcome='rolled-back', binding=None),
                                   self.request('recover'))
        assert result['outcome'] == 'rolled-back'
