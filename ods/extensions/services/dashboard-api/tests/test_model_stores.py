import importlib.util
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from model_stores import active_store, lemonade_profile, registered_stores, resolve_model_file, scan_model_files, validated_compose_overlay, resolve_runtime_selection


def registry(tmp_path, **profile):
    data, external = tmp_path / 'data', tmp_path / 'ssd-models'
    (data / 'models').mkdir(parents=True)
    external.mkdir()
    (data / 'models' / 'legacy.gguf').write_bytes(b'legacy')
    (external / 'new.gguf').write_bytes(b'checkpoint')
    executable = tmp_path / 'llama-server'
    executable.write_bytes(b'executable')
    settings = {'backend':'vulkan', 'executable':str(executable), 'contextLength':16384, 'mtp':True, **profile}
    (data / 'model-stores.json').write_text(json.dumps({'schemaVersion':1,'stores':[
        {'id':'ssd', 'hostPath':str(external), 'containerPath':'/model-stores/ssd', 'profiles':{'new.gguf':settings}}]}))
    return data, external


def test_external_store_preserves_default_and_real_paths(tmp_path):
    data, external = registry(tmp_path)
    assert scan_model_files(data) == {'legacy.gguf':data/'models/legacy.gguf','new.gguf':external/'new.gguf'}
    assert resolve_model_file(data, 'NEW.GGUF') == external/'new.gguf'
    assert active_store(data, 'ssd')['path'] == external
    assert active_store(data)['path'] == data/'models'
    with pytest.raises(ValueError):
        active_store(data, 'unregistered')
    assert resolve_model_file(data, '../ssd-models/new.gguf') is None


def test_collision_and_link_escape_never_select_arbitrary_checkpoint(tmp_path):
    data, external = registry(tmp_path)
    (data/'models/new.gguf').write_bytes(b'different model')
    assert resolve_model_file(data, 'new.gguf') is None
    try:
        (external/'outside.gguf').symlink_to(data/'models/legacy.gguf')
    except OSError:
        pytest.skip("Windows symlink creation requires Developer Mode or administrator privileges")
    (external/'empty.gguf').touch()
    (external/'incomplete.gguf.part').write_bytes(b'partial')
    (external/'mmproj-F16.gguf').write_bytes(b'vision')
    (external/'mtp-head.gguf').write_bytes(b'draft')
    assert scan_model_files(data) == {'legacy.gguf':data/'models/legacy.gguf'}


def test_bad_or_foreign_registry_cannot_map_arbitrary_container_paths(tmp_path):
    data, external = registry(tmp_path)
    body = json.loads((data/'model-stores.json').read_text())
    body['stores'][0]['containerPath'] = str(external)
    (data/'model-stores.json').write_text(json.dumps(body))
    assert len(registered_stores(data, container=True)) == 1
    (data/'model-stores.json').write_text('{broken')
    assert len(registered_stores(data)) == 1


def test_profile_is_typed_and_does_not_accept_shell_arguments(tmp_path):
    data, _ = registry(tmp_path, extraArgs='--evil ; do something')
    profile = lemonade_profile(data, 'new.gguf')
    assert profile['mtp'] is True
    assert profile['args'][-8:] == ['--spec-type','draft-mtp','--spec-draft-n-max','2','--spec-draft-type-k','q4_0','--spec-draft-type-v','q4_0']
    assert '--evil' not in profile['args']
    document = json.loads((data/'model-stores.json').read_text())
    document['stores'][0]['profiles']['new.gguf']['draftTokens'] = '; rm'
    (data/'model-stores.json').write_text(json.dumps(document))
    with pytest.raises(ValueError):
        lemonade_profile(data, 'new.gguf')


@pytest.mark.parametrize('load_args', [[], ['--mmap'], ['--load-mode','mmap']])
def test_profile_uses_only_qualified_load_mode_arguments(tmp_path, load_args):
    data, _ = registry(tmp_path, loadModeArguments=load_args)
    arguments = lemonade_profile(data, 'new.gguf')['args']
    assert ('--mmap' in arguments) == ('--mmap' in load_args)
    assert ('--load-mode' in arguments) == ('--load-mode' in load_args)
    if '--load-mode' in load_args:
        assert arguments[arguments.index('--load-mode')+1] == 'mmap'


def test_profile_rejects_unqualified_load_mode_instead_of_sending_arbitrary_flags(tmp_path):
    data, _ = registry(tmp_path, loadModeArguments=['--load-mode','mmap','--extra'])
    with pytest.raises(ValueError):
        lemonade_profile(data, 'new.gguf')


def test_cold_start_cannot_increase_measured_profile_context(tmp_path, monkeypatch):
    import model_stores
    data, external = registry(tmp_path,
        modelSha256=hashlib.sha256(b'checkpoint').hexdigest(),
        runtimeSha256=hashlib.sha256(b'executable').hexdigest(),
        memoryQualification={'contextLength':16384,'visionProjectorFile':'mmproj.gguf',
            'visionProjectorSha256':hashlib.sha256(b'projector').hexdigest()})
    (external/'mmproj.gguf').write_bytes(b'projector')
    (tmp_path/'.env').write_text('GGUF_FILE=new.gguf\nODS_ACTIVE_MODEL_STORE=ssd\nCTX_SIZE=65536\n')
    validations = []
    monkeypatch.setattr(model_stores,'validate_profile_command',lambda *args,**kwargs:validations.append(args))
    with pytest.raises(ValueError,match='memory-qualified profile'):
        resolve_runtime_selection(tmp_path,verify_hashes=True)
    assert validations == []
    # Ownership lookup must still work for Stop even with a rejected setting.
    selected = resolve_runtime_selection(tmp_path,verify_hashes=False,allow_missing_model=True)
    assert selected['profile']['contextLength'] == 16384
    (tmp_path/'.env').write_text('GGUF_FILE=new.gguf\nODS_ACTIVE_MODEL_STORE=ssd\nCTX_SIZE=16384\n')
    assert resolve_runtime_selection(tmp_path,verify_hashes=True)['profile']['contextLength'] == 16384
    assert len(validations) == 1


def test_host_uses_registered_store_and_real_lemonade_load_options(tmp_path, monkeypatch):
    data, external = registry(tmp_path)
    agent_path = Path(__file__).resolve().parents[4] / 'bin/ods-host-agent.py'
    spec = importlib.util.spec_from_file_location('test_mtp_store_agent', agent_path)
    agent = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(agent)
    monkeypatch.setattr(agent, 'INSTALL_DIR', tmp_path)
    assert agent._installed_model_file('new.gguf') == external/'new.gguf'
    assert agent._active_model_directory({'ODS_ACTIVE_MODEL_STORE':'ssd'}) == external
    requests = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self, _limit): return b'{"status":"success"}'
    monkeypatch.setattr(agent.urllib_request, 'urlopen', lambda request, **kwargs: requests.append(request) or Response())
    monkeypatch.setattr(agent, '_lemonade_runtime_base_url', lambda env:'http://127.0.0.1:13305')
    agent._load_registered_lemonade_profile({'CTX_SIZE':'8192'}, 'extra.new.gguf', lemonade_profile(data,'new.gguf'))
    payload = json.loads(requests[0].data)
    assert requests[0].full_url == 'http://127.0.0.1:13305/api/v1/load'
    assert payload['ctx_size'] == 8192 and payload['save_options'] is True
    assert '--spec-type draft-mtp' in payload['llamacpp_args']
    assert payload['model_name'] == 'extra.new.gguf'


def test_registration_creates_readonly_mount_without_selecting_or_moving_files(tmp_path):
    script = Path(__file__).resolve().parents[4] / 'scripts/register-model-store.py'
    spec = importlib.util.spec_from_file_location('register_model_store_test', script)
    registrar = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(registrar)
    install, external = tmp_path/'install', tmp_path/'separate disk'
    install.mkdir()
    external.mkdir()
    (install/'.env').write_text('GGUF_FILE=old.gguf\n')
    (external/'new.gguf').write_bytes(b'checkpoint')
    result = registrar.register(install, 'ssd', external)
    assert result['modelSelected'] is False
    assert (install/'.env').read_text() == 'GGUF_FILE=old.gguf\n'
    assert (external/'new.gguf').read_bytes() == b'checkpoint'
    assert not (install/'data/models/new.gguf').exists()
    volumes = json.loads((install/'.model-stores.compose.json').read_text())['services']['dashboard-api']['volumes']
    assert volumes == [{'type':'bind','source':external.as_posix(),'target':'/model-stores/ssd','read_only':True,'bind':{'create_host_path':False}}]
    assert resolve_model_file(install/'data','new.gguf') == external/'new.gguf'
    assert validated_compose_overlay(install) == install/'.model-stores.compose.json'
    body = json.loads((install/'.model-stores.compose.json').read_text())
    body['services']['dashboard-api']['privileged'] = True
    (install/'.model-stores.compose.json').write_text(json.dumps(body))
    with pytest.raises(ValueError):
        validated_compose_overlay(install)
    registrar.register(install,'ssd',external)
    assert len(json.loads((install/'data/model-stores.json').read_text())['stores']) == 1
    document = json.loads((install/'data/model-stores.json').read_text())
    document['stores'][0]['profiles'] = {'first.gguf':{'mtp':True},'second.gguf':{'mtp':False}}
    (install/'data/model-stores.json').write_text(json.dumps(document))
    registrar.register(install,'ssd',external)
    assert json.loads((install/'data/model-stores.json').read_text())['stores'][0]['profiles'] == document['stores'][0]['profiles']


def test_restart_resolver_preserves_external_models_and_returns_to_default(tmp_path, monkeypatch):
    data, external = registry(tmp_path)
    agent_path = Path(__file__).resolve().parents[4] / 'bin/ods-host-agent.py'
    spec = importlib.util.spec_from_file_location('restart_model_store_test', agent_path)
    agent = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(agent)
    monkeypatch.setattr(agent, 'INSTALL_DIR', tmp_path)
    monkeypatch.delenv('ODS_HOST_INSTALL_DIR', raising=False)
    (tmp_path/'.env').write_text('GGUF_FILE=new.gguf\nODS_ACTIVE_MODEL_STORE=ssd\n')
    (tmp_path/'.compose-flags').write_text('-f docker-compose.base.yml')
    flags = agent.resolve_compose_flags()
    mount = data/'.active-model-store.compose.json'
    assert str(mount) in flags
    assert json.loads(mount.read_text())['services']['llama-server']['volumes'][0]['source'] == str(external)
    (tmp_path/'.compose-flags').write_text(' '.join(flags))
    (tmp_path/'.env').write_text('GGUF_FILE=legacy.gguf\n')
    assert str(mount) not in agent.resolve_compose_flags()
    assert agent._active_model_bind_directory({}) == str(data/'models')


def test_status_metadata_can_identify_a_stopped_or_missing_external_runtime(tmp_path):
    data, external = registry(tmp_path)
    (tmp_path/'.env').write_text('GGUF_FILE=new.gguf\nODS_ACTIVE_MODEL_STORE=ssd\nCTX_SIZE=8192\n')
    selected = resolve_runtime_selection(tmp_path, verify_hashes=False)
    assert selected['modelPath'] == str(external/'new.gguf')
    assert selected['profile']['contextLength'] == 8192
    (external/'new.gguf').unlink()
    (tmp_path/'llama-server').unlink()
    metadata = resolve_runtime_selection(tmp_path, verify_hashes=False, allow_missing_model=True)
    assert metadata['available'] is False
    assert metadata['profile']['executable'] == str(tmp_path/'llama-server')
    with pytest.raises(ValueError):
        resolve_runtime_selection(tmp_path, verify_hashes=False)
    with pytest.raises(ValueError):
        resolve_runtime_selection(tmp_path, verify_hashes=True, allow_missing_model=True)


@pytest.mark.skipif(sys.platform == 'win32', reason='Bash resolver integration runs on Unix')
def test_normal_stack_resolution_recreates_active_mount_after_restart(tmp_path):
    data, external = registry(tmp_path)
    source_root = Path(__file__).resolve().parents[4]
    module_dir = tmp_path/'extensions/services/dashboard-api'
    module_dir.mkdir(parents=True)
    for name in ('model_stores.py','env_values.py'):
        shutil.copyfile(source_root/'extensions/services/dashboard-api'/name,module_dir/name)
    (tmp_path/'docker-compose.base.yml').write_text('services:\n  dashboard-api:\n    image: api:test\n  llama-server:\n    image: llama:test\n')
    (tmp_path/'docker-compose.nvidia.yml').write_text('services: {}\n')
    (tmp_path/'.env').write_text('GGUF_FILE=new.gguf\nODS_ACTIVE_MODEL_STORE=ssd\n')
    overlay = {'services':{'dashboard-api':{'volumes':[{'type':'bind','source':str(external),'target':'/model-stores/ssd','read_only':True,'bind':{'create_host_path':False}}]}}}
    (tmp_path/'.model-stores.compose.json').write_text(json.dumps(overlay))
    command = ['bash',str(source_root/'scripts/resolve-compose-stack.sh'),'--script-dir',str(tmp_path),'--tier','1','--gpu-backend','nvidia']
    environment = {**os.environ,'ODS_PYTHON_CMD':sys.executable}
    flags = subprocess.run(command,env=environment,capture_output=True,text=True,check=True).stdout
    assert '.active-model-store.compose.json' in flags
    mounted = json.loads((data/'.active-model-store.compose.json').read_text())
    assert mounted['services']['llama-server']['volumes'][0]['source'] == str(external)
    (tmp_path/'.env').write_text('GGUF_FILE=legacy.gguf\nODS_ACTIVE_MODEL_STORE=default\n')
    restored = subprocess.run(command,env=environment,capture_output=True,text=True,check=True).stdout
    assert '.active-model-store.compose.json' not in restored
    assert '.model-stores.compose.json' in restored
