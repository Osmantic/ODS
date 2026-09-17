"""Contract tests for dashboard-api/pixel_provider_runtime_public.py.

The dashboard parity mirror of bin/pixel_provider/public.py — the strict
nonsecret envelope validators between the owner-confirmed provider-runtime
control plane and the dashboard. `test_pixel_provider_runtime_integration.py`
drives these through HTTP on happy paths; this file pins the validation
matrix: state-dependent invariants, binding/outcome cross-checks, and the
exact allowlists.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pixel_provider_runtime_public as pub

HEX = 'a' * 64
UUID = '123e4567-e89b-12d3-a456-426614174000'
TS = '2026-09-17T00:00:00Z'


def binding(**overrides):
    value = {'schemaVersion': 1, 'activationId': UUID, 'revision': 3,
             'allowCloud': False}
    value.update(overrides)
    return value


def envelope(status='applied', **overrides):
    value = {'schemaVersion': 1, 'status': status, 'revision': HEX,
             'providerRevision': 3, 'binding': binding(), 'pending': False,
             'registrationVerified': True, 'transportVerified': False,
             'lastVerifiedAt': TS, 'reason': None}
    value.update(overrides)
    return value


class TestNormalizeBinding:
    def test_valid(self):
        assert pub.normalize_binding(binding()) == binding()

    def test_none_passthrough(self):
        assert pub.normalize_binding(None) is None

    @pytest.mark.parametrize('mutation', [
        {'schemaVersion': 2}, {'revision': '3'}, {'revision': -1},
        {'revision': 2**53}, {'allowCloud': 'no'}, {'activationId': 42},
        {'activationId': 'not-a-uuid'}, {'activationId': UUID.upper()},
        {'extra': 1},
    ])
    def test_invalid_bindings(self, mutation):
        bad = binding(**mutation)
        if 'extra' in mutation:
            bad['extra'] = 1
            bad.pop('schemaVersion', None) if mutation['extra'] == 1 and 'schemaVersion' in mutation else None
        with pytest.raises(ValueError, match='invalid-provider-runtime-response'):
            pub.normalize_binding(bad)

    def test_missing_key_rejected(self):
        bad = binding()
        bad.pop('allowCloud')
        with pytest.raises(ValueError):
            pub.normalize_binding(bad)


class TestNormalizeChange:
    @pytest.mark.parametrize('operation', ['apply', 'deactivate', 'recover'])
    def test_valid_operations(self, operation):
        request = {'operation': operation, 'revision': HEX,
                   'providerRevision': 7}
        assert pub.normalize_change(request) == request

    @pytest.mark.parametrize('req', [
        {'operation': 'restart', 'revision': HEX, 'providerRevision': 1},
        {'operation': 'apply', 'revision': 'not-hex', 'providerRevision': 1},
        {'operation': 'apply', 'revision': HEX, 'providerRevision': -1},
        {'operation': 'apply', 'revision': HEX, 'providerRevision': True},
        {'operation': 'apply', 'revision': HEX},
        {'operation': 'apply', 'revision': HEX, 'providerRevision': 1,
         'extra': 2},
        'not-a-dict',
    ])
    def test_invalid_changes(self, req):
        with pytest.raises(ValueError,
                           match='invalid-provider-runtime-request'):
            pub.normalize_change(req)


class TestUnavailable:
    @pytest.mark.parametrize('reason', sorted(pub.REASONS))
    def test_all_reasons(self, reason):
        result = pub.unavailable(reason)
        assert result['status'] == 'unavailable'
        assert result['reason'] == reason
        assert set(result) == pub.KEYS
        # Every non-fixed key is None — no phantom state in an outage.
        assert all(result[key] is None for key in
                   pub.KEYS - {'schemaVersion', 'status', 'reason'})

    def test_unknown_reason_rejected(self):
        with pytest.raises(ValueError):
            pub.unavailable('made-up-reason')

    def test_safe_reason_allowlist(self):
        assert pub.safe_reason('runtime-busy') == 'runtime-busy'
        assert pub.safe_reason('unknown', fallback='x') == 'x'
        assert pub.safe_reason(None, fallback='y') == 'y'


class TestNormalizeRuntime:
    def test_applied_envelope(self):
        result = pub.normalize_runtime(envelope())
        assert result['status'] == 'applied'

    @pytest.mark.parametrize('status', ['not-applied', 'pending'])
    def test_unbound_states(self, status):
        result = pub.normalize_runtime(envelope(
            status=status, binding=None, registrationVerified=False,
            pending=(status == 'pending'), lastVerifiedAt=None))
        assert result['status'] == status

    def test_inactive(self):
        result = pub.normalize_runtime(envelope(status='inactive',
                                                binding=None))
        assert result['status'] == 'inactive'

    def test_saved_changes_keeps_stale_binding(self):
        # saved-changes: applied binding revision differs from providerRevision.
        result = pub.normalize_runtime(envelope(
            status='saved-changes', providerRevision=4))
        assert result['status'] == 'saved-changes'

    def test_applied_requires_binding_at_current_revision(self):
        # binding.revision == providerRevision is required for 'applied'.
        with pytest.raises(ValueError):
            pub.normalize_runtime(envelope(providerRevision=4))

    def test_saved_changes_with_current_revision_rejected(self):
        with pytest.raises(ValueError):
            pub.normalize_runtime(envelope(status='saved-changes'))

    def test_unavailable_requires_all_null_fields(self):
        result = pub.normalize_runtime(
            envelope(status='unavailable', revision=None,
                     providerRevision=None, binding=None, pending=None,
                     registrationVerified=None, transportVerified=None,
                     lastVerifiedAt=None, reason='runtime-busy'))
        assert result['reason'] == 'runtime-busy'

    def test_unavailable_with_state_is_rejected(self):
        with pytest.raises(ValueError):
            pub.normalize_runtime(
                envelope(status='unavailable', reason='runtime-busy'))

    def test_unavailable_unknown_reason_rejected(self):
        with pytest.raises(ValueError):
            pub.normalize_runtime(
                envelope(status='unavailable', revision=None,
                         providerRevision=None, binding=None, pending=None,
                         registrationVerified=None, transportVerified=None,
                         lastVerifiedAt=None,
                         reason='secret-internal-detail'))

    @pytest.mark.parametrize('mutation', [
        {'schemaVersion': 2},
        {'revision': 'short'},
        {'pending': 'yes'},
        {'registrationVerified': 1},
        {'transportVerified': True},  # transport is never marked verified here
        {'reason': 'leftover'},
        {'lastVerifiedAt': 'not-a-timestamp'},
        {'lastVerifiedAt': '2026-09-17T00:00:00+02:00'},
    ])
    def test_invalid_envelope_fields(self, mutation):
        with pytest.raises(ValueError,
                           match='invalid-provider-runtime-response'):
            pub.normalize_runtime(envelope(**mutation))

    def test_pending_flag_must_match_status(self):
        with pytest.raises(ValueError):
            pub.normalize_runtime(envelope(status='pending', pending=False,
                                           binding=None,
                                           registrationVerified=False,
                                           lastVerifiedAt=None))

    def test_extra_key_rejected(self):
        value = envelope()
        value['debug'] = 'leak'
        with pytest.raises(ValueError):
            pub.normalize_runtime(value)


class TestFromController:
    def test_controller_envelope_gains_schema(self):
        raw = envelope()
        raw.pop('schemaVersion')
        raw.pop('reason')
        result = pub.from_controller(raw)
        assert result['status'] == 'applied'

    def test_controller_extra_key_rejected(self):
        raw = envelope()
        raw.pop('schemaVersion')
        raw.pop('reason')
        raw['internal'] = 'x'
        with pytest.raises(ValueError):
            pub.from_controller(raw)


class TestNormalizeOutcome:
    def outcome(self, **overrides):
        value = {'outcome': 'applied', 'binding': binding(),
                 'registrationVerified': True, 'transportVerified': False}
        value.update(overrides)
        return value

    def request(self, operation='apply', revision=3):
        return {'operation': operation, 'revision': HEX,
                'providerRevision': revision}

    def test_apply_outcome(self):
        result = pub.normalize_outcome(self.outcome(), self.request())
        assert result['outcome'] == 'applied'

    def test_apply_requires_binding_at_requested_revision(self):
        with pytest.raises(ValueError,
                           match='provider-runtime-outcome-mismatch'):
            pub.normalize_outcome(self.outcome(), self.request(revision=9))

    def test_apply_with_rolled_back_outcome_rejected(self):
        with pytest.raises(ValueError,
                           match='provider-runtime-outcome-mismatch'):
            pub.normalize_outcome(self.outcome(outcome='rolled-back'),
                                  self.request())

    def test_deactivate_requires_no_binding(self):
        result = pub.normalize_outcome(self.outcome(binding=None),
                                       self.request('deactivate'))
        # deactivate outcome 'applied' with cleared binding is valid
        assert result['outcome'] == 'applied'
        with pytest.raises(ValueError,
                           match='provider-runtime-outcome-mismatch'):
            pub.normalize_outcome(self.outcome(),
                                  self.request('deactivate'))

    def test_recover_accepts_rolled_back(self):
        result = pub.normalize_outcome(
            self.outcome(outcome='rolled-back', binding=None),
            self.request('recover'))
        assert result['outcome'] == 'rolled-back'

    def test_registration_verified_required(self):
        with pytest.raises(ValueError):
            pub.normalize_outcome(
                self.outcome(registrationVerified=False), self.request())

    def test_transport_never_verified_publicly(self):
        with pytest.raises(ValueError):
            pub.normalize_outcome(self.outcome(transportVerified=True),
                                  self.request())

    def test_invalid_request_cross_check(self):
        with pytest.raises(ValueError,
                           match='invalid-provider-runtime-request'):
            pub.normalize_outcome(self.outcome(),
                                  {'operation': 'restart'})
