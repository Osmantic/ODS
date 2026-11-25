import importlib.util
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from model_stores import active_store, registered_runtime_profile, registered_stores, resolve_model_file, scan_model_files, validated_compose_overlay, resolve_runtime_selection


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
    (external/'outside.gguf').symlink_to(data/'models/legacy.gguf')
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
    profile = registered_runtime_profile(data, 'new.gguf')
    assert profile['mtp'] is True
    assert profile['args'][-8:] == ['--spec-type','draft-mtp','--spec-draft-n-max','2','--spec-draft-type-k','q4_0','--spec-draft-type-v','q4_0']
    assert '--evil' not in profile['args']
    document = json.loads((data/'model-stores.json').read_text())
    document['stores'][0]['profiles']['new.gguf']['draftTokens'] = '; rm'
    (data/'model-stores.json').write_text(json.dumps(document))
    with pytest.raises(ValueError):
        registered_runtime_profile(data, 'new.gguf')


@pytest.mark.parametrize('load_args', [[], ['--mmap'], ['--load-mode','mmap']])
def test_profile_uses_only_qualified_load_mode_arguments(tmp_path, load_args):
    data, _ = registry(tmp_path, loadModeArguments=load_args)
    arguments = registered_runtime_profile(data, 'new.gguf')['args']
    assert ('--mmap' in arguments) == ('--mmap' in load_args)
    assert ('--load-mode' in arguments) == ('--load-mode' in load_args)
    if '--load-mode' in load_args:
        assert arguments[arguments.index('--load-mode')+1] == 'mmap'


def test_profile_rejects_unqualified_load_mode_instead_of_sending_arbitrary_flags(tmp_path):
    data, _ = registry(tmp_path, loadModeArguments=['--load-mode','mmap','--extra'])
    with pytest.raises(ValueError):
        registered_runtime_profile(data, 'new.gguf')


def test_cold_start_cannot_increase_measured_profile_context(tmp_path, monkeypatch):
    import model_stores
    data, external = registry(tmp_path,
        modelSha256=hashlib.sha256(b'checkpoint').hexdigest(),
        runtimeSha256=hashlib.sha256(b'executable').hexdigest(),
        memoryQualification={'runtimeMode':'native','contextLength':16384,'visionProjectorFile':'mmproj.gguf',
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


def test_lemonade_measured_fit_never_qualifies_a_native_launch(tmp_path, monkeypatch):
    """R6: a fit measured through Lemonade's launch is not native evidence."""
    import model_stores
    data, external = registry(tmp_path,
        modelSha256=hashlib.sha256(b'checkpoint').hexdigest(),
        runtimeSha256=hashlib.sha256(b'executable').hexdigest(),
        memoryQualification={'runtimeMode':'lemonade','gpuLayers':'99','contextLength':16384,
            'visionProjectorFile':'mmproj.gguf',
            'visionProjectorSha256':hashlib.sha256(b'projector').hexdigest()})
    (tmp_path/'.env').write_text('GGUF_FILE=new.gguf\nODS_ACTIVE_MODEL_STORE=ssd\nCTX_SIZE=32768\n')
    validations: list = []
    monkeypatch.setattr(model_stores,'validate_profile_command',lambda *args,**kwargs:validations.append(args))
    # Neither the qualified context nor its (absent) projector binds the
    # launch: the Lemonade-era record stays on disk but is ignored.
    selected = resolve_runtime_selection(tmp_path,verify_hashes=True)
    assert selected['profile']['memoryQualification'] is None
    assert selected['profile']['contextLength'] == 32768
    assert len(validations) == 1
    stored = json.loads((data/'model-stores.json').read_text())
    assert stored['stores'][0]['profiles']['new.gguf']['memoryQualification']['runtimeMode'] == 'lemonade'


def test_host_launches_the_registered_executable_from_its_store(tmp_path, monkeypatch):
    data, external = registry(tmp_path)
    agent_path = Path(__file__).resolve().parents[4] / 'bin/ods-host-agent.py'
    spec = importlib.util.spec_from_file_location('test_mtp_store_agent', agent_path)
    agent = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(agent)
    monkeypatch.setattr(agent, 'INSTALL_DIR', tmp_path)
    assert agent._installed_model_file('new.gguf') == external/'new.gguf'
    assert agent._active_model_directory({'ODS_ACTIVE_MODEL_STORE':'ssd'}) == external
    env = {'GGUF_FILE':'new.gguf', 'ODS_ACTIVE_MODEL_STORE':'ssd', 'CTX_SIZE':'8192',
           'LLAMA_ARG_SPEC_TYPE':'draft-mtp', 'LLAMA_ARG_SPEC_DRAFT_N_MAX':'2'}
    monkeypatch.setattr(agent, 'load_env', lambda _path: env)
    monkeypatch.setattr(agent.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(agent.subprocess, 'run', lambda *_a, **_k: pytest.fail('a registered profile needs no probe'))
    launched: list = []
    class Process:
        pid = 4321
    def popen(args, **_kwargs):
        launched.append(args)
        return Process()
    monkeypatch.setattr(agent.subprocess, 'Popen', popen)
    agent._launch_native_llama_server(tmp_path/'.env', tmp_path/'bundled-llama-server.exe', tmp_path/'log', tmp_path/'pid')
    command = launched[0]
    # The qualified executable serves the model from its registered store.
    assert command[0] == str(tmp_path/'llama-server')
    assert command[command.index('--model') + 1] == str(external/'new.gguf')
    assert command[command.index('--alias') + 1] == 'new.gguf'
    assert command[command.index('--ctx-size') + 1] == '8192'
    assert command[command.index('--spec-type') + 1] == 'draft-mtp'
    assert command[command.index('--spec-draft-n-max') + 1] == '2'


def test_native_launch_loads_exactly_the_memory_qualified_projector(tmp_path, monkeypatch):
    agent_path = Path(__file__).resolve().parents[4] / 'bin/ods-host-agent.py'
    spec = importlib.util.spec_from_file_location('test_mtp_store_agent_mmproj', agent_path)
    agent = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(agent)
    launched: list = []
    class Process:
        pid = 4321
    monkeypatch.setattr(agent.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(agent.subprocess, 'run', lambda *_a, **_k: pytest.fail('a registered profile needs no probe'))
    def popen(args, **_kwargs):
        launched.append(args)
        return Process()
    monkeypatch.setattr(agent.subprocess, 'Popen', popen)
    fit = {'runtimeMode':'native','gpuLayers':'auto','contextLength':16384,'visionProjectorFile':'mmproj-F16.gguf',
           'visionProjectorSha256':hashlib.sha256(b'projector').hexdigest()}
    for mode, expected in (('native', True), ('lemonade', False)):
        root = tmp_path / mode
        data, external = registry(root, memoryQualification={**fit, 'runtimeMode': mode})
        (external/'mmproj-F16.gguf').write_bytes(b'projector')
        monkeypatch.setattr(agent, 'INSTALL_DIR', root)
        monkeypatch.setattr(agent, 'load_env', lambda _path: {'GGUF_FILE':'new.gguf', 'ODS_ACTIVE_MODEL_STORE':'ssd',
                                                              'CTX_SIZE':'16384'})
        agent._launch_native_llama_server(root/'.env', root/'bundled.exe', root/'log', root/'pid')
        command = launched.pop()
        # A native fit was measured with its projector loaded; a Lemonade-era
        # record qualifies nothing, so the plain text model launches.
        assert ('--mmproj' in command) is expected, mode
        if expected:
            assert command[command.index('--mmproj') + 1] == str(external/'mmproj-F16.gguf')


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


def test_native_launch_loads_a_vision_imports_own_projector(tmp_path, monkeypatch):
    agent_path = Path(__file__).resolve().parents[4] / 'bin/ods-host-agent.py'
    spec = importlib.util.spec_from_file_location('test_native_launch_import_mmproj', agent_path)
    agent = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(agent)
    launched: list = []

    class Process:
        pid = 4321

    def popen(args, **_kwargs):
        launched.append(args)
        return Process()

    models = tmp_path / 'data' / 'models'
    models.mkdir(parents=True)
    (models / 'hf-vision-Q4.gguf').write_bytes(b'weights')
    (models / 'hf-vision-mmproj-F16.gguf').write_bytes(b'projector')
    monkeypatch.setattr(agent, 'INSTALL_DIR', tmp_path)
    monkeypatch.setattr(agent.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(agent.subprocess, 'Popen', popen)
    monkeypatch.setattr(agent, 'load_env', lambda _path: {'GGUF_FILE': 'hf-vision-Q4.gguf', 'CTX_SIZE': '8192'})
    for record, expected in (({'gguf_file': 'hf-vision-Q4.gguf', 'mmproj_file': 'hf-vision-mmproj-F16.gguf'}, True),
                             ({'gguf_file': 'hf-vision-Q4.gguf'}, False)):
        monkeypatch.setattr(agent, '_library_record_for_gguf', lambda _gguf, _record=record: _record)
        agent._launch_native_llama_server(tmp_path / '.env', tmp_path / 'llama-server', tmp_path / 'log', tmp_path / 'pid')
        command = launched.pop()
        assert ('--mmproj' in command) is expected
        if expected:
            assert command[command.index('--mmproj') + 1] == str(models / 'hf-vision-mmproj-F16.gguf')


def test_selection_reports_a_vision_imports_projector_for_native_restarts(tmp_path):
    # WP2: `ods start` and installer reruns on macOS read the projector here,
    # the way the host agent's switch does, so a vision import keeps its vision.
    models = tmp_path / 'data' / 'models'
    models.mkdir(parents=True)
    (models / 'vision.gguf').write_bytes(b'weights')
    (models / 'vision-mmproj-F16.gguf').write_bytes(b'projector')
    (tmp_path / '.env').write_text('GGUF_FILE=vision.gguf\n')
    imports = {'version': 1, 'models': [{'id': 'hf-vision', 'source': 'huggingface', 'gguf_file': 'vision.gguf',
                                         'mmproj_file': 'vision-mmproj-F16.gguf'}]}
    (tmp_path / 'data' / 'model-imports.json').write_text(json.dumps(imports))

    selected = resolve_runtime_selection(tmp_path, verify_hashes=False)
    assert selected['projectorPath'] == str((models / 'vision-mmproj-F16.gguf').resolve())

    (models / 'vision-mmproj-F16.gguf').unlink()
    assert resolve_runtime_selection(tmp_path, verify_hashes=False)['projectorPath'] is None
    (tmp_path / 'data' / 'model-imports.json').write_text('{not json')
    assert resolve_runtime_selection(tmp_path, verify_hashes=False)['projectorPath'] is None


def test_selection_reports_a_qualified_profiles_measured_projector(tmp_path):
    # A memory-qualified native profile was measured with its projector loaded;
    # restarts launch exactly that, never an import record's projector instead.
    fit = {'runtimeMode': 'native', 'contextLength': 16384, 'visionProjectorFile': 'measured-mmproj.gguf',
           'visionProjectorSha256': hashlib.sha256(b'measured').hexdigest()}
    data, external = registry(tmp_path, memoryQualification=fit)
    (tmp_path / '.env').write_text('GGUF_FILE=new.gguf\nODS_ACTIVE_MODEL_STORE=ssd\n')
    (external / 'measured-mmproj.gguf').write_bytes(b'measured')
    (external / 'import-mmproj.gguf').write_bytes(b'other')
    imports = {'models': [{'id': 'x', 'source': 'huggingface', 'gguf_file': 'new.gguf', 'mmproj_file': 'import-mmproj.gguf'}]}
    (data / 'model-imports.json').write_text(json.dumps(imports))
    selected = resolve_runtime_selection(tmp_path, verify_hashes=False)
    assert selected['projectorPath'] == str((external / 'measured-mmproj.gguf').resolve())


def test_vision_projectors_are_not_listed_as_models(tmp_path):
    # WP2: an import keeps its projector next to its weights as
    # hf-<repo>-mmproj-<quant>-<hash>.gguf; it must not appear as a runnable model.
    models = tmp_path / 'data' / 'models'
    models.mkdir(parents=True)
    for name in ('hf-unsloth-gemma-4-E4B-it-GGUF-gemma-4-E4B-it-Q4_K_M-1a2b3c4d.gguf',
                 'hf-unsloth-gemma-4-E4B-it-GGUF-mmproj-F16-ca7d7e15.gguf',
                 'mmproj-F16.gguf', 'llava.mmproj.gguf', 'mtp-head.gguf'):
        (models / name).write_bytes(b'gguf')
    listed = scan_model_files(tmp_path / 'data')
    assert sorted(listed) == ['hf-unsloth-gemma-4-E4B-it-GGUF-gemma-4-E4B-it-Q4_K_M-1a2b3c4d.gguf']
