"""Contract tests for pixel_settings_public.

Dashboard-side strict validators for the Pixel settings control plane:
the bounded preferences surface, the optimistic-concurrency edit envelope,
the settings/status response, and the runtime-state machine mirror
(not-applied/applied/saved-changes/restored/pending/unavailable).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pixel_settings_public as pub  # noqa: E402

INT53 = 2**53 - 1
SHA = 'a' * 64


def prefs(**kw):
    doc = {'contextTokens': 32768, 'temperature': 0.7,
           'compactionNotify': True, 'thinking': 'medium'}
    doc.update(kw)
    return doc


def runtime_response(**kw):
    doc = {
        'schemaVersion': 1, 'status': 'applied', 'revision': SHA,
        'settingsRevision': 5, 'appliedRevision': 5,
        'capabilities': {
            'providerContextTokens': 32768, 'providerMaxOutputTokens': 4096,
            'activeContextTokens': 32768, 'activeMaxOutputTokens': 4096,
            'backendContextTokens': 32768,
            'capacitySource': 'provider-declared',
            'supportedThinkingLevels': ['medium'],
            'samplingSupported': True, 'pixelOnlyRuntime': True,
        },
        'pending': False, 'lastVerifiedAt': '2026-09-17T00:00:00Z',
        'reason': None,
    }
    doc.update(kw)
    return doc


class TestPreferences:
    def test_valid_subset(self):
        assert pub.normalize_preferences(prefs()) == prefs()

    def test_none_values_pass_through(self):
        assert pub.normalize_preferences({'temperature': None}) == {
            'temperature': None}

    @pytest.mark.parametrize('doc', [None, [], 'x', {'unknown': 1},
                                     {5: 'x'}])
    def test_invalid_shape(self, doc):
        with pytest.raises(ValueError, match='invalid-settings-fields'):
            pub.normalize_preferences(doc)

    @pytest.mark.parametrize('name,bad', [
        ('contextTokens', 4095), ('contextTokens', 10_000_001),
        ('contextTokens', 'x'), ('contextTokens', True),
        ('maxOutputTokens', 0),
        ('compactionMode', 'aggressive'),
        ('compactionReserveTokens', -1),
        ('compactionKeepRecentTokens', 0),
        ('compactionHistoryShare', 0.09), ('compactionHistoryShare', 0.91),
        ('compactionHistoryShare', float('nan')),
        ('compactionHistoryShare', float('inf')),
        ('compactionRecentTurns', 13),
        ('compactionTimeoutSeconds', 0),
        ('compactionNotify', 1),
        ('thinking', 'ultra'),
        ('verbosity', 'loud'),
        ('reasoningVisibility', 'always'),
        ('toolProgress', 'silent'),
        ('temperature', -0.1), ('temperature', 2.1),
        ('topP', 0), ('topP', 1.1),
        ('toolResultMaxChars', 0),
        ('bootstrapMaxChars', 2_000_001),
        ('bootstrapTotalMaxChars', 'x'),
    ])
    def test_field_validation(self, name, bad):
        with pytest.raises(ValueError, match='invalid-setting-' + name):
            pub.normalize_preferences({name: bad})

    @pytest.mark.parametrize('name,good', [
        ('contextTokens', 4096), ('contextTokens', 10_000_000),
        ('maxOutputTokens', 1), ('maxOutputTokens', 10_000_000),
        ('compactionMode', 'default'), ('compactionMode', 'safeguard'),
        ('compactionReserveTokens', 0), ('compactionKeepRecentTokens', 1),
        ('compactionHistoryShare', 0.1), ('compactionHistoryShare', 0.9),
        ('compactionRecentTurns', 0), ('compactionRecentTurns', 12),
        ('compactionTimeoutSeconds', 1), ('compactionTimeoutSeconds', 3600),
        ('compactionNotify', False), ('compactionMemoryFlush', True),
        ('thinking', 'off'), ('thinking', 'xhigh'), ('thinking', 'adaptive'),
        ('thinking', 'max'), ('verbosity', 'full'),
        ('reasoningVisibility', 'stream'), ('toolProgress', 'raw'),
        ('temperature', 0), ('temperature', 2), ('temperature', 1.5),
        ('topP', 0.000001), ('topP', 1),
        ('toolResultMaxChars', 2_000_000), ('bootstrapMaxChars', 1),
    ])
    def test_field_bounds_accepted(self, name, good):
        assert pub.normalize_preferences({name: good}) == {name: good}

    def test_bool_is_not_int(self):
        with pytest.raises(ValueError):
            pub.normalize_preferences({'contextTokens': True})


class TestEdit:
    def test_valid(self):
        doc = {'expectedRevision': 3, 'changes': {'temperature': 1.0}}
        assert pub.normalize_edit(doc) == doc

    @pytest.mark.parametrize('doc', [
        None, [], {'expectedRevision': 3},
        {'expectedRevision': 3, 'changes': {}, 'extra': 1},
        {'expectedRevision': -1, 'changes': {}},
        {'expectedRevision': 2**53 - 1, 'changes': {}},
        {'expectedRevision': '3', 'changes': {}},
        {'expectedRevision': True, 'changes': {}},
        {'expectedRevision': 3, 'changes': {'unknown': 1}},
    ])
    def test_invalid(self, doc):
        with pytest.raises(ValueError, match='invalid-'):
            pub.normalize_edit(doc)


class TestResponse:
    def _response(self, status='not-inspected',
                  reason='runtime-status-separate'):
        return {'configuration': {
                    'schemaVersion': 1, 'revision': 9,
                    'preferences': prefs()},
                'runtime': {'status': status, 'reason': reason}}

    def test_valid(self):
        doc = self._response()
        result = pub.normalize_response(doc)
        assert result['configuration']['revision'] == 9
        assert result['runtime'] == {'status': 'not-inspected',
                                    'reason': 'runtime-status-separate'}

    def test_legacy_pair_accepted(self):
        doc = self._response('not-applied',
                             'settings-runtime-not-integrated')
        assert pub.normalize_response(doc)

    @pytest.mark.parametrize('status,reason', [
        ('applied', 'x'), ('not-applied', 'runtime-status-separate'),
        ('not-inspected', 'settings-runtime-not-integrated'),
    ])
    def test_runtime_status_reason_pairs(self, status, reason):
        with pytest.raises(ValueError):
            pub.normalize_response(self._response(status, reason))

    @pytest.mark.parametrize('mutation', [
        ('root', {'extra': 1}),
        ('config', {'schemaVersion': 2}),
        ('config', {'revision': -1}), ('config', {'revision': 2**53}),
        ('config', {'revision': True}),
        ('runtime', {'status': 'not-inspected'}),
    ])
    def test_invalid_shapes(self, mutation):
        where, change = mutation
        doc = self._response()
        if where == 'root':
            doc.update(change)
        elif where == 'config':
            doc['configuration'].update(change)
        else:
            doc['runtime'] = dict(doc['runtime'], **change)
            doc['runtime'].pop('reason', None)
        with pytest.raises(ValueError):
            pub.normalize_response(doc)

    def test_preferences_propagate(self):
        doc = self._response()
        doc['configuration']['preferences'] = {'bad': 1}
        with pytest.raises(ValueError, match='invalid-settings-fields'):
            pub.normalize_response(doc)


class TestRuntimeChange:
    def test_valid(self):
        doc = {'operation': 'apply', 'revision': SHA, 'settingsRevision': 5}
        assert pub.normalize_runtime_change(doc) == doc

    @pytest.mark.parametrize('doc', [
        {'operation': 'delete', 'revision': SHA, 'settingsRevision': 5},
        {'operation': 'apply', 'revision': 'x', 'settingsRevision': 5},
        {'operation': 'apply', 'revision': 'A' * 64, 'settingsRevision': 5},
        {'operation': 'apply', 'revision': SHA, 'settingsRevision': -1},
        {'operation': 'apply', 'revision': SHA, 'settingsRevision': '5'},
        {'operation': 'apply', 'revision': SHA},
        {'operation': 'apply', 'revision': SHA, 'settingsRevision': 5,
         'extra': 1},
    ])
    def test_invalid(self, doc):
        with pytest.raises(ValueError, match='invalid-settings-request'):
            pub.normalize_runtime_change(doc)


class TestRuntimeOutcome:
    @pytest.mark.parametrize('doc', [
        {'outcome': 'applied', 'appliedRevision': 5},
        {'outcome': 'rolled-back', 'appliedRevision': None},
        {'outcome': 'rolled-back', 'appliedRevision': 5},
    ])
    def test_valid(self, doc):
        assert pub.normalize_runtime_outcome(doc) == doc

    @pytest.mark.parametrize('doc', [
        {'outcome': 'applied', 'appliedRevision': None},
        {'outcome': 'committed', 'appliedRevision': 5},
        {'outcome': 'applied', 'appliedRevision': -1},
        {'outcome': 'applied'},
        {'outcome': 'applied', 'appliedRevision': 5, 'extra': 1},
    ])
    def test_invalid(self, doc):
        with pytest.raises(ValueError, match='invalid-settings-response'):
            pub.normalize_runtime_outcome(doc)


class TestRuntime:
    def test_applied(self):
        doc = runtime_response()
        assert pub.normalize_runtime(doc)['status'] == 'applied'

    def test_saved_changes_has_different_revision(self):
        doc = runtime_response(status='saved-changes', appliedRevision=4)
        assert pub.normalize_runtime(doc)['status'] == 'saved-changes'

    def test_applied_revision_mismatch_rejected(self):
        with pytest.raises(ValueError):
            pub.normalize_runtime(
                runtime_response(status='applied', appliedRevision=4))

    def test_saved_changes_same_revision_rejected(self):
        with pytest.raises(ValueError):
            pub.normalize_runtime(
                runtime_response(status='saved-changes', appliedRevision=5))

    def test_restored_allows_null_applied(self):
        doc = runtime_response(status='restored', appliedRevision=None)
        assert pub.normalize_runtime(doc)['status'] == 'restored'

    def test_not_applied_requires_null_fields(self):
        doc = runtime_response(status='not-applied', appliedRevision=None,
                               lastVerifiedAt=None)
        assert pub.normalize_runtime(doc)['status'] == 'not-applied'

    def test_not_applied_with_revision_rejected(self):
        with pytest.raises(ValueError):
            pub.normalize_runtime(
                runtime_response(status='not-applied', appliedRevision=5))

    def test_pending_requires_pending_true_and_null_fields(self):
        doc = runtime_response(status='pending', pending=True,
                               appliedRevision=None, capabilities=None,
                               lastVerifiedAt=None)
        assert pub.normalize_runtime(doc)['status'] == 'pending'

    def test_pending_with_capabilities_rejected(self):
        with pytest.raises(ValueError):
            pub.normalize_runtime(
                runtime_response(status='pending', pending=True))

    def test_non_pending_with_pending_flag_rejected(self):
        with pytest.raises(ValueError):
            pub.normalize_runtime(runtime_response(pending=True))

    def test_unavailable_all_null(self):
        doc = {
            'schemaVersion': 1, 'status': 'unavailable',
            'revision': None, 'settingsRevision': None,
            'appliedRevision': None, 'capabilities': None,
            'pending': None, 'lastVerifiedAt': None,
            'reason': 'runtime-busy',
        }
        assert pub.normalize_runtime(doc)['reason'] == 'runtime-busy'

    @pytest.mark.parametrize('mutation', [
        {'reason': 'Internal Secret Path'},
        {'reason': None},
        {'revision': SHA},
        {'pending': False},
    ])
    def test_unavailable_invariants(self, mutation):
        doc = {
            'schemaVersion': 1, 'status': 'unavailable',
            'revision': None, 'settingsRevision': None,
            'appliedRevision': None, 'capabilities': None,
            'pending': None, 'lastVerifiedAt': None,
            'reason': 'runtime-busy',
        }
        doc.update(mutation)
        with pytest.raises(ValueError):
            pub.normalize_runtime(doc)

    @pytest.mark.parametrize('ts', [
        '2026-09-17 00:00:00', '2026-09-17T00:00:00+00:00',
        'not-a-date', '2026-13-45T99:99:99Z',
    ])
    def test_timestamp_format(self, ts):
        with pytest.raises(ValueError):
            pub.normalize_runtime(runtime_response(lastVerifiedAt=ts))

    def test_fractional_seconds_ok(self):
        doc = runtime_response(lastVerifiedAt='2026-09-17T00:00:00.123Z')
        assert pub.normalize_runtime(doc)

    def test_reason_must_be_null_on_live_states(self):
        with pytest.raises(ValueError):
            pub.normalize_runtime(runtime_response(reason='oops'))

    def test_revision_hex_required(self):
        with pytest.raises(ValueError):
            pub.normalize_runtime(runtime_response(revision='x'))


class TestRuntimeCapabilities:
    @pytest.mark.parametrize('mutation', [
        {'providerContextTokens': 0},
        {'providerMaxOutputTokens': 40000},   # > providerContextTokens
        {'activeMaxOutputTokens': 40000},     # > activeContextTokens
        {'backendContextTokens': 0},
        {'capacitySource': 'invented'},
        {'capacitySource': 'backend-observed', 'backendContextTokens': None},
        {'samplingSupported': 1},
        {'pixelOnlyRuntime': 'yes'},
        {'supportedThinkingLevels': ['ultra']},
        {'supportedThinkingLevels': ['medium', 'medium']},
        {'supportedThinkingLevels': 'medium'},
        {'extra': 1},
    ])
    def test_capability_invariants(self, mutation):
        caps = dict(runtime_response()['capabilities'], **mutation)
        with pytest.raises(ValueError, match='invalid-'):
            pub.normalize_runtime(runtime_response(capabilities=caps))

    def test_backend_context_nullable_for_non_observed(self):
        caps = dict(runtime_response()['capabilities'],
                    backendContextTokens=None)
        assert pub.normalize_runtime(runtime_response(capabilities=caps))
