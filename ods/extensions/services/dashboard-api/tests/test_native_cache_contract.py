"""Registered cache precision: no inference processes or real telemetry."""
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys

import pytest

import model_stores as stores
from test_model_stores import registry


def row(tmp_path, **changes):
    data, external = registry(tmp_path, mtp=False, **changes)
    document = json.loads((data / 'model-stores.json').read_text())
    return data, external, document['stores'][0]


def test_legacy_and_explicit_q4_are_identical(tmp_path):
    _, _, store = row(tmp_path)
    before = stores.native_profile(store, 'new.gguf')
    store['profiles']['new.gguf']['cacheType'] = 'q4_0'
    assert stores.native_profile(store, 'new.gguf') == before


@pytest.mark.parametrize('cache', [None, '', 'F16', 'f16;q4_0', ['f16'], 'f16\n', 16])
def test_invalid_precision_is_not_silently_ignored(tmp_path, cache):
    _, _, store = row(tmp_path, cacheType=cache)
    with pytest.raises(ValueError, match='cache'):
        stores.native_profile(store, 'new.gguf')


def test_f16_projects_real_launch_args(tmp_path):
    _, _, store = row(tmp_path, cacheType='f16')
    profile = stores.native_profile(store, 'new.gguf')
    for flag in ('--cache-type-k', '--cache-type-v'):
        assert profile['args'][profile['args'].index(flag) + 1] == 'f16'


@pytest.mark.parametrize('record', [{}, {'runtimeMode': 'native'}, {'runtimeMode': 'lemonade'}])
def test_f16_never_drops_or_adopts_legacy_memory_proof(tmp_path, record):
    data, _, store = row(tmp_path, cacheType='f16', memoryQualification=record)
    before = (data / 'model-stores.json').read_bytes()
    with pytest.raises(ValueError, match='memory qualification'):
        stores.native_profile(store, 'new.gguf')
    assert (data / 'model-stores.json').read_bytes() == before


def test_f16_mtp_refuses(tmp_path):
    _, _, store = row(tmp_path, cacheType='f16')
    store['profiles']['new.gguf']['mtp'] = True
    with pytest.raises(ValueError, match='MTP'):
        stores.native_profile(store, 'new.gguf')


def gguf_bytes():
    def string(value):
        encoded = value.encode()
        return struct.pack('<Q', len(encoded)) + encoded

    values = {'general.architecture': 'qwen3', 'qwen3.context_length': 32768,
              'qwen3.block_count': 28, 'qwen3.attention.head_count': 16,
              'qwen3.attention.head_count_kv': 8, 'qwen3.attention.key_length': 128,
              'qwen3.attention.value_length': 128}
    body = b'GGUF' + struct.pack('<IQQ', 3, 0, len(values))
    for key, value in values.items():
        body += string(key)
        body += struct.pack('<I', 8 if isinstance(value, str) else 4)
        body += string(value) if isinstance(value, str) else struct.pack('<I', value)
    return body


def complete_fixture(tmp_path, monkeypatch, module=stores):
    data, external, store = row(tmp_path, cacheType='f16', contextLength=4096)
    checkpoint = external / 'new.gguf'
    checkpoint.write_bytes(gguf_bytes())
    profile = store['profiles']['new.gguf']
    for path, field in ((checkpoint, 'modelSha256'), (tmp_path / 'llama-server', 'runtimeSha256')):
        profile[field] = hashlib.sha256(path.read_bytes()).hexdigest()
    (data / 'model-stores.json').write_text(json.dumps({'schemaVersion': 1, 'stores': [store]}))
    (tmp_path / '.env').write_text('ODS_MODE=local\nLLM_BACKEND=llama-server\nGPU_BACKEND=amd\n'
                                 'AMD_INFERENCE_LOCATION=host\nAMD_INFERENCE_RUNTIME_MODE=windows-native-llama-server\n'
                                 'ODS_ACTIVE_MODEL_STORE=ssd\nGGUF_FILE=new.gguf\nLLM_MODEL=new\nCTX_SIZE=4096\nN_GPU_LAYERS=0\n')
    monkeypatch.setattr(module, 'physical_ram_bytes', lambda: (16 * 1024**3, 8 * 1024**3))
    # --help is the only external process involved; all selection/admission is real.
    monkeypatch.setattr(module, 'validate_profile_command', lambda *_: None)
    return checkpoint, store


def test_resolver_admits_exact_f16_estimate_and_refuses_changed_artifacts(tmp_path, monkeypatch):
    checkpoint, _ = complete_fixture(tmp_path, monkeypatch)
    selected = stores.resolve_runtime_selection(tmp_path)
    assert selected['profile']['cacheType'] == 'f16'
    assert selected['memoryAdmission']['source'] == 'architecture-estimate'
    assert selected['memoryAdmission']['requiredGiB'] > 0.4375  # native f16 KV alone
    assert selected['memoryAdmission']['capacityGiB'] == 5.6
    checkpoint.write_bytes(checkpoint.read_bytes() + b'drift')
    with pytest.raises(ValueError, match='changed since qualification'):
        stores.resolve_runtime_selection(tmp_path)


@pytest.mark.parametrize('failure', ['low-ram', 'unknown-ram', 'context', 'gpu', 'projector'])
def test_start_refusals_preserve_all_files(tmp_path, monkeypatch, failure):
    checkpoint, store = complete_fixture(tmp_path, monkeypatch)
    if failure == 'low-ram':
        monkeypatch.setattr(stores, 'physical_ram_bytes', lambda: (16 * 1024**3, 10))
    if failure == 'unknown-ram':
        monkeypatch.setattr(stores, 'physical_ram_bytes', lambda: (0, 0))
    if failure in ('context', 'gpu'):
        env = tmp_path / '.env'
        env.write_text(env.read_text().replace('CTX_SIZE=4096', 'CTX_SIZE=65536') if failure == 'context'
                       else env.read_text().replace('N_GPU_LAYERS=0', 'N_GPU_LAYERS=auto'))
    if failure == 'projector':
        projector = checkpoint.parent / 'mmproj.gguf'
        projector.write_bytes(b'vision')
        (tmp_path / 'data/model-imports.json').write_text(json.dumps({'models': [
            {'gguf_file': 'new.gguf', 'mmproj_file': projector.name}]}))
    before = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    with pytest.raises(ValueError):
        stores.resolve_runtime_selection(tmp_path)
    assert {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before


def script_module(filename):
    path = Path(__file__).resolve().parents[4] / 'scripts' / filename
    spec = importlib.util.spec_from_file_location(filename.replace('-', '_'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def authored(tmp_path, monkeypatch):
    checkpoint, _ = complete_fixture(tmp_path, monkeypatch)
    qualifier = script_module('qualify-mtp.py')
    monkeypatch.setattr(qualifier, 'probe_runtime', lambda _: {'mtp': False, 'loadModeArguments': []})
    commands: list[list[str]] = []
    monkeypatch.setattr(qualifier, 'validate_runtime_command', commands.append)
    output = tmp_path / 'baseline.json'
    monkeypatch.setattr(sys, 'argv', ['qualify-mtp.py', '--runtime', str(tmp_path / 'llama-server'),
                        '--model', str(checkpoint), '--hardware-id', 'fixture-cpu', '--context', '4096',
                        '--expected-sha256', hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
                        '--baseline-only', '--cache-type', 'f16', '--output', str(output)])
    assert qualifier.main() == 0
    result = json.loads(output.read_text())
    assert commands == [result['baselineCommand']]
    assert result['mtpCommand'] is None and result['recommendation'] == 'baseline'
    assert 'memoryQualification' not in result
    return output, checkpoint


def test_non_mtp_authoring_and_registration_bind_artifacts_argv_cache(tmp_path, monkeypatch):
    output, checkpoint = authored(tmp_path, monkeypatch)
    registrar = script_module('register-model-store.py')
    registrar.register(tmp_path, 'ssd', checkpoint.parent, output, backend='vulkan')
    profile = stores.resolve_runtime_selection(tmp_path)['profile']
    assert profile['cacheType'] == 'f16' and profile['mtp'] is False
    assert profile['memoryQualification'] is None
    raw = json.loads((tmp_path / 'data/model-stores.json').read_text())['stores'][0]['profiles']['new.gguf']
    assert 'memoryQualification' not in raw


@pytest.mark.parametrize('change', ['signature', 'argv', 'artifact', 'fit', 'mtp'])
def test_registration_refuses_inconsistent_authoring_without_writes(tmp_path, monkeypatch, change):
    output, checkpoint = authored(tmp_path, monkeypatch)
    document = json.loads(output.read_text())
    if change == 'signature':
        document['profile']['context'] = 8192
    if change == 'argv':
        document['baselineCommand'][-1] = 'q4_0'
    if change == 'artifact':
        checkpoint.write_bytes(checkpoint.read_bytes() + b'drift')
    if change == 'fit':
        registry_path = tmp_path / 'data/model-stores.json'
        original = json.loads(registry_path.read_text())
        original['stores'][0]['profiles']['new.gguf']['memoryQualification'] = {'original': 'retain'}
        registry_path.write_text(json.dumps(original))
    output.write_text(json.dumps(document))
    before = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    with pytest.raises(ValueError):
        script_module('register-model-store.py').register(tmp_path, 'ssd', checkpoint.parent, output,
                                                        enable_mtp=change == 'mtp', backend='vulkan')
    assert {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('enough', [False, True])
def test_host_activation_admits_before_first_mutation_and_projects_f16(tmp_path, monkeypatch, enough):
    import test_host_agent as fixtures
    host = fixtures._mod
    complete_fixture(tmp_path, monkeypatch, host._model_stores)
    monkeypatch.setattr(host, 'INSTALL_DIR', tmp_path)
    monkeypatch.setattr(host.platform, 'system', lambda: 'Windows')
    monkeypatch.delenv('ODS_HOST_INSTALL_DIR', raising=False)
    monkeypatch.setattr(host, '_load_model_library_records', lambda: [])
    monkeypatch.setattr(host, '_managed_wsl_runtime', lambda _: {'managed': False})
    monkeypatch.setattr(host, '_ods_managed_pixel_identity', lambda: None)
    monkeypatch.setattr(host, '_select_runtime_profile', lambda *_: None)
    monkeypatch.setattr(host.subprocess, 'run', lambda *_a, **_k: pytest.fail('unexpected external process'))
    observed: list = []
    def ram():
        observed.append('fresh-ram')
        return 16 * 1024**3, 8 * 1024**3 if enough else 10
    monkeypatch.setattr(host._model_stores, 'physical_ram_bytes', ram)
    monkeypatch.setattr(host, '_capture_hermes_live_config', lambda _: {'source': 'deferred_absent', 'exists': False})
    monkeypatch.setattr(host, '_capture_opencode_config', lambda: None)
    monkeypatch.setattr(host, '_container_exists', lambda _: False)
    monkeypatch.setattr(host, '_capture_container_state', lambda _: {'exists': False, 'running': False})
    monkeypatch.setattr(host, '_capture_perplexica_config', lambda *_: None)
    monkeypatch.setattr(host, '_begin_pixel_model_transaction', lambda _: observed.append('begin'))
    monkeypatch.setattr(host, '_restart_windows_native_llama_server', lambda *_: pytest.fail('started a service'))
    class AtWrite(BaseException):
        pass
    def capture_write(path, text):
        observed.append(('would-write', path, text))
        raise AtWrite()
    monkeypatch.setattr(host, '_write_bound_env_text', capture_write)
    before = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    handler = fixtures._FakeHandler(b'')
    if enough:
        with pytest.raises(AtWrite):
            host.AgentHandler._do_model_activate(handler, 'new')
        assert observed[:2] == ['fresh-ram', 'begin']
        text = observed[2][2]
        assert 'LLAMA_ARG_CACHE_TYPE_K=f16\n' in text and 'LLAMA_ARG_CACHE_TYPE_V=f16\n' in text
        assert 'N_GPU_LAYERS=0\n' in text
    else:
        host.AgentHandler._do_model_activate(handler, 'new')
        assert handler.response_code == 400
        assert 'Insufficient memory' in handler.parse_response()['error']
        assert observed == ['fresh-ram']
    assert {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before
