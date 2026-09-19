"""Contract tests for bin/model_switchboard/state.py.

The model-state record is the switchboard's durable authority: the host agent
is the only writer, readers must never observe partial JSON, ``seq``/
``routeSeq`` monotonicity orders mutations, and history may only contain
*verified* routes. These tests pin the schema validator, atomic persistence,
last-known-good read semantics, route recording, and .env reconstruction.
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
from model_switchboard import state as st


def valid_active(**overrides):
    active = {
        'routeSeq': 1, 'catalogId': 'qwen3-4b', 'runtimeModelId': 'qwen3-4b.gguf',
        'publicModel': 'ods/current',
        'backend': {'kind': 'llama-server', 'endpointId': 'llama-main',
                    'nativeRoute': None},
        'contextLength': 32768,
        'capabilities': {'chat': True, 'tools': True, 'vision': False,
                         'agentViable': False},
        'verifiedAt': '2026-09-17T00:00:00Z',
        'proof': {'identity': 'qwen3-4b.gguf', 'completion': True},
    }
    active.update(overrides)
    return active


def valid_doc(**overrides):
    doc = st.initial_state()
    doc['active'] = valid_active()
    doc['seq'] = doc['routeSeq'] = 1
    doc.update(overrides)
    return doc


class TestValidateState:
    def test_initial_state_is_valid(self):
        assert st.validate_state(st.initial_state()) == []

    def test_fully_populated_state_is_valid(self):
        doc = valid_doc(history=[{'routeSeq': 0, 'catalogId': 'old',
                                  'runtimeModelId': 'old.gguf',
                                  'verifiedAt': '2026-09-16T00:00:00Z'}],
                        operation={'id': 'op-1', 'phase': 'staging',
                                   'requestedModelId': 'next-7b',
                                   'startedAt': '2026-09-17T00:00:00Z',
                                   'error': None})
        assert st.validate_state(doc) == []

    @pytest.mark.parametrize('doc', [None, [], 'x', 42])
    def test_non_object_root(self, doc):
        assert st.validate_state(doc) == ['state root must be an object']

    @pytest.mark.parametrize('key', ['schema', 'seq', 'routeSeq', 'active',
                                     'history', 'availability', 'desired'])
    def test_required_top_level_keys(self, key):
        doc = valid_doc()
        doc.pop(key)
        assert any('missing required keys' in e and key in e
                   for e in st.validate_state(doc))

    def test_unexpected_top_level_key_rejected(self):
        assert any('unexpected keys' in e
                   for e in st.validate_state(valid_doc(debug=True)))

    @pytest.mark.parametrize('value', [-1, 1.5, 'x', True, None])
    def test_seq_must_be_nonnegative_int(self, value):
        errors = st.validate_state(valid_doc(seq=value))
        assert any('seq must be a non-negative integer' in e for e in errors)

    def test_operation_phase_allowlist(self):
        doc = valid_doc(operation={'id': 'o', 'phase': 'teleporting',
                                   'requestedModelId': 'm',
                                   'startedAt': 't'})
        assert any('operation.phase' in e for e in st.validate_state(doc))

    @pytest.mark.parametrize('kind', ['llama-server', 'lemonade', 'hipfire',
                                      'unknown'])
    def test_backend_kind_allowlist_accepts_known(self, kind):
        doc = valid_doc()
        doc['active']['backend']['kind'] = kind
        assert st.validate_state(doc) == []

    def test_backend_kind_unknown_value_rejected(self):
        doc = valid_doc()
        doc['active']['backend']['kind'] = 'vllm'
        assert any('backend.kind' in e for e in st.validate_state(doc))

    @pytest.mark.parametrize('key', ['chat', 'tools', 'vision', 'agentViable'])
    def test_capability_must_be_bool(self, key):
        doc = valid_doc()
        doc['active']['capabilities'][key] = 1  # truthy int is not a bool
        assert any(key in e for e in st.validate_state(doc))

    def test_history_limit_enforced(self):
        doc = valid_doc(history=[{'routeSeq': i, 'catalogId': 'c',
                                  'runtimeModelId': 'r', 'verifiedAt': None}
                                 for i in range(st.HISTORY_LIMIT + 1)])
        assert any('history exceeds' in e for e in st.validate_state(doc))

    def test_history_entry_missing_keys(self):
        doc = valid_doc(history=[{'routeSeq': 0}])
        errors = st.validate_state(doc)
        assert any('history[0]' in e and 'missing required keys' in e
                   for e in errors)

    @pytest.mark.parametrize('mode', ['serve_active', 'queue'])
    def test_availability_modes(self, mode):
        assert st.validate_state(valid_doc(
            availability={'mode': mode, 'queueDeadline': None})) == []

    def test_availability_unknown_mode(self):
        doc = valid_doc(availability={'mode': 'draining',
                                      'queueDeadline': None})
        assert any('availability' in e for e in st.validate_state(doc))

    def test_proof_requires_identity_and_completion(self):
        doc = valid_doc()
        doc['active']['proof'] = {'identity': 'x'}
        assert any('proof' in e for e in st.validate_state(doc))


class TestPersistence:
    def test_roundtrip_and_file_mode(self, tmp_path):
        path = tmp_path / 'model-state.json'
        doc = valid_doc()
        st.atomic_write_state(path, doc)
        assert json.loads(path.read_text()) == doc
        if os.name == 'posix':
            assert path.stat().st_mode & 0o777 == st.STATE_FILE_MODE

    def test_write_refuses_invalid_doc(self, tmp_path):
        path = tmp_path / 'model-state.json'
        with pytest.raises(st.StateError, match='refusing to write'):
            st.atomic_write_state(path, {'schema': 'wrong'})
        assert not path.exists()

    def test_write_cleans_temp_on_failure(self, tmp_path, monkeypatch):
        path = tmp_path / 'model-state.json'
        monkeypatch.setattr(os, 'replace',
                            lambda *_: (_ for _ in ()).throw(OSError('locked')))
        with pytest.raises(OSError):
            st.atomic_write_state(path, valid_doc())
        assert not path.exists()
        assert not list(tmp_path.glob('*.tmp'))

    def test_read_missing_returns_none_no_errors(self, tmp_path):
        assert st.read_state(tmp_path / 'absent.json') == (None, [])

    def test_read_malformed_returns_errors_not_exception(self, tmp_path):
        path = tmp_path / 'model-state.json'
        path.write_text('{not json')
        doc, errors = st.read_state(path)
        assert doc is None and errors and 'not valid JSON' in errors[0]

    def test_read_invalid_schema_returns_last_good(self, tmp_path):
        path = tmp_path / 'model-state.json'
        good = valid_doc()
        st.atomic_write_state(path, good)
        assert st.read_state(path) == (good, [])
        path.write_text(json.dumps({'schema': 'corrupted'}))
        doc, errors = st.read_state(path)
        assert errors and doc == good  # last-known-good, never the corrupt doc


class TestRecordVerifiedRoute:
    def record(self, path, **kwargs):
        defaults = dict(catalog_id='m-a', runtime_model_id='a.gguf',
                        backend_kind='llama-server', endpoint_id='ep-1',
                        context_length=32768,
                        capabilities={'chat': True, 'tools': True},
                        proof_identity='a.gguf')
        defaults.update(kwargs)
        return st.record_verified_route(path, **defaults)

    def test_first_record_from_missing_file(self, tmp_path):
        doc = self.record(tmp_path / 's.json')
        assert doc['seq'] == 1 and doc['routeSeq'] == 1
        assert doc['active']['catalogId'] == 'm-a'
        assert doc['desired'] == {'catalogId': 'm-a'}
        assert doc['operation'] is None
        assert doc['active']['verifiedAt'] is not None
        assert doc['active']['proof'] == {'identity': 'a.gguf',
                                        'completion': True}
        assert doc['active']['capabilities'] == {
            'chat': True, 'tools': True, 'vision': False, 'agentViable': False}

    def test_same_route_bumps_seq_not_routeseq(self, tmp_path):
        path = tmp_path / 's.json'
        self.record(path)
        doc = self.record(path)
        assert doc['seq'] == 2 and doc['routeSeq'] == 1
        assert doc['history'] == []

    def test_route_change_promotes_verified_previous_to_history(self, tmp_path):
        path = tmp_path / 's.json'
        self.record(path)
        doc = self.record(path, catalog_id='m-b', runtime_model_id='b.gguf',
                          proof_identity='b.gguf')
        assert doc['routeSeq'] == 2
        assert doc['history'] == [{'routeSeq': 1, 'catalogId': 'm-a',
                                   'runtimeModelId': 'a.gguf',
                                   'verifiedAt': doc['history'][0]['verifiedAt']}]
        assert doc['history'][0]['verifiedAt'] is not None

    def test_unverified_previous_never_enters_history(self, tmp_path):
        path = tmp_path / 's.json'
        self.record(path, proof_completion=False)
        doc = self.record(path, catalog_id='m-b', runtime_model_id='b.gguf')
        assert doc['routeSeq'] == 2 and doc['history'] == []

    def test_reconstructed_active_never_enters_history(self, tmp_path):
        path = tmp_path / 's.json'
        self.record(path, reconstructed=True)
        doc = self.record(path, catalog_id='m-b', runtime_model_id='b.gguf')
        assert doc['history'] == []

    def test_history_capped_at_limit(self, tmp_path):
        path = tmp_path / 's.json'
        for i in range(st.HISTORY_LIMIT + 3):
            self.record(path, catalog_id=f'm-{i}', runtime_model_id=f'{i}.gguf')
        doc = self.record(path, catalog_id='final', runtime_model_id='f.gguf')
        assert len(doc['history']) == st.HISTORY_LIMIT
        assert doc['history'][0]['catalogId'] == f'm-{st.HISTORY_LIMIT + 2}'

    def test_backend_kind_normalized_to_unknown(self, tmp_path):
        doc = self.record(tmp_path / 's.json', backend_kind='vllm-future')
        assert doc['active']['backend']['kind'] == 'unknown'

    def test_malformed_existing_state_refuses_blind_overwrite(self, tmp_path):
        path = tmp_path / 's.json'
        path.write_text('{corrupt')
        with pytest.raises(st.StateError, match='refusing blind overwrite'):
            self.record(path)
        assert path.read_text() == '{corrupt'  # evidence preserved

    def test_reconstructed_marks_unproven(self, tmp_path):
        doc = self.record(tmp_path / 's.json', reconstructed=True)
        assert doc['active']['reconstructed'] is True
        assert doc['active']['verifiedAt'] is None
        assert doc['active']['proof']['completion'] is True


class TestMigrateEnvIdentity:
    def test_cloud_mode_yields_none(self):
        assert st.migrate_env_identity({'ODS_MODE': 'cloud',
                                        'GGUF_FILE': 'm.gguf'}) is None

    def test_empty_env_yields_none(self):
        assert st.migrate_env_identity({}) is None

    def test_gguf_file_strips_suffix_for_catalog(self):
        result = st.migrate_env_identity({'GGUF_FILE': 'qwen3-4b.gguf'})
        assert result['catalogId'] == 'qwen3-4b'
        assert result['runtimeModelId'] == 'qwen3-4b.gguf'
        assert result['backendKind'] == 'llama-server'

    def test_extra_prefixed_lemonade_id_stripped(self):
        result = st.migrate_env_identity(
            {'LEMONADE_MODEL': 'extra.Qwen3-4B', 'LLM_BACKEND': 'lemonade'})
        assert result['catalogId'] == 'Qwen3-4B'
        assert result['runtimeModelId'] == 'extra.Qwen3-4B'

    def test_backend_hint_when_llm_backend_absent(self):
        result = st.migrate_env_identity(
            {'LEMONADE_MODEL': 'm', 'AMD_INFERENCE_RUNTIME': 'lemonade'})
        assert result['backendKind'] == 'lemonade'

    def test_unknown_backend_normalized(self):
        result = st.migrate_env_identity(
            {'GGUF_FILE': 'm.gguf', 'LLM_BACKEND': 'tensorrt'})
        assert result['backendKind'] == 'unknown'

    def test_external_lemonade_uses_lemonade_as_catalog(self):
        result = st.migrate_env_identity(
            {'LEMONADE_MODEL': 'qwen3-4b', 'LLM_MODEL': 'catalog-name',
             'LEMONADE_EXTERNAL': 'true'})
        assert result['catalogId'] == 'qwen3-4b'
        result = st.migrate_env_identity(
            {'LEMONADE_MODEL': 'qwen3-4b', 'LLM_MODEL': 'catalog-name',
             'AMD_INFERENCE_RUNTIME_MODE': 'external-lemonade'})
        assert result['catalogId'] == 'qwen3-4b'

    def test_managed_lemonade_not_external(self):
        result = st.migrate_env_identity(
            {'LEMONADE_MODEL': 'qwen3-4b', 'LLM_MODEL': 'catalog-name',
             'AMD_INFERENCE_MANAGED': '1'})
        assert result['catalogId'] == 'catalog-name'

    @pytest.mark.parametrize('key', ['CTX_SIZE', 'MAX_CONTEXT'])
    def test_context_from_env(self, key):
        result = st.migrate_env_identity(
            {'GGUF_FILE': 'm.gguf', key: '65536'})
        assert result['contextLength'] == 65536

    def test_context_nonnumeric_skipped(self):
        result = st.migrate_env_identity(
            {'GGUF_FILE': 'm.gguf', 'CTX_SIZE': 'huge', 'MAX_CONTEXT': '8192'})
        assert result['contextLength'] == 8192


class TestInitializeIfMissing:
    def test_writes_reconstructed_unproven_state(self, tmp_path):
        path = tmp_path / 's.json'
        doc = st.initialize_if_missing(path, {'GGUF_FILE': 'm.gguf'})
        assert doc['active']['reconstructed'] is True
        assert doc['active']['proof']['completion'] is False
        assert doc['active']['backend']['endpointId'] == 'llama-server-default'
        assert path.exists()

    def test_never_overwrites_existing_file(self, tmp_path):
        path = tmp_path / 's.json'
        path.write_text('{"not": "a state"}')
        assert st.initialize_if_missing(path, {'GGUF_FILE': 'm.gguf'}) is None
        assert path.read_text() == '{"not": "a state"}'

    def test_no_env_identity_writes_nothing(self, tmp_path):
        path = tmp_path / 's.json'
        assert st.initialize_if_missing(path, {}) is None
        assert not path.exists()

    def test_lemonade_default_endpoint(self, tmp_path):
        doc = st.initialize_if_missing(
            tmp_path / 's.json',
            {'LEMONADE_MODEL': 'm', 'LLM_BACKEND': 'lemonade'})
        assert doc['active']['backend']['endpointId'] == 'lemonade-default'

    def test_agent_viable_threshold(self, tmp_path):
        doc = st.initialize_if_missing(
            tmp_path / 's.json',
            {'GGUF_FILE': 'm.gguf', 'CTX_SIZE': '65536'})
        assert doc['active']['capabilities']['agentViable'] is True
        path2 = tmp_path / 's2.json'
        doc = st.initialize_if_missing(
            path2, {'GGUF_FILE': 'm.gguf', 'CTX_SIZE': '32768'})
        assert doc['active']['capabilities']['agentViable'] is False
