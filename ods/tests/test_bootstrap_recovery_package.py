"""Portable artifact controls; physical cgroup qualification is separate."""
import hashlib
import ast
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from types import SimpleNamespace

import pytest

ODS = Path(__file__).resolve().parents[1]
SOURCE = ODS / 'recovery/bootstrap_current'
BUILDER = ODS / 'scripts/build-bootstrap-recovery-package.py'


def build(path):
    spec = importlib.util.spec_from_file_location('recovery_builder', BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build(path)


def test_artifact_reproducible_and_every_program_bound(tmp_path):
    first, second = tmp_path / 'first.tar', tmp_path / 'second.tar'
    receipt = build(first)
    build(second)
    assert first.read_bytes() == second.read_bytes()
    assert receipt['sha256'] == hashlib.sha256(first.read_bytes()).hexdigest()
    assert receipt['installed'] is False
    with tarfile.open(first) as archive:
        members = archive.getmembers()
        assert len(members) == 11
        assert all(item.isfile() and item.uid == item.gid == item.mtime == 0 and item.mode == 0o644 for item in members)
        files = {Path(item.name).name: archive.extractfile(item).read() for item in members}
    manifest = json.loads(files.pop('manifest.json'))
    assert manifest == {'version': 1, 'files': {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}}
    assert set(files) == {path.name for path in SOURCE.glob('*') if path.suffix in ('.py', '.json')}
    compatibility = json.loads(files['compatibility.json'])
    assert len(compatibility['bundle']) == 32
    assert hashlib.sha256(compatibility['unitText'].encode()).hexdigest() == compatibility['unitSha256']
    with pytest.raises(FileExistsError):
        build(first)


def test_loader_requires_isolation_before_import_or_host_access():
    result = subprocess.run([sys.executable, str(SOURCE / 'loader.py'), 'preflight'], capture_output=True, text=True)
    assert result.returncode != 0
    assert result.stderr.strip() == 'isolated-python-required'


@pytest.mark.skipif(sys.platform != 'linux' or os.geteuid() != 0, reason='Root owner-drop transport; qualified in isolated root fixture')
def test_owner_output_is_bounded_while_receiving_and_timeout_reaps():
    code = r'''
import os,sys,time
sys.path.insert(0,sys.argv[1])
from adapters import bounded_owner
from custody import Refused
fd=os.memfd_create('inert-qualification')
try:
 for source,wanted,timeout in [('print("x"*1100000)','owner-output-limit',5),('import time;time.sleep(5)','owner-operation-timeout',.1)]:
  before=time.monotonic()
  try:
   bounded_owner([sys.executable,'-I','-c',source],b'',uid=os.getuid(),gid=os.getgid(),environment={'PATH':'/usr/bin:/bin'},program=fd,timeout=timeout)
  except Refused as error:assert str(error)==wanted
  else:raise AssertionError('unbounded owner accepted')
  assert time.monotonic()-before<3
 try:os.waitpid(-1,os.WNOHANG)
 except ChildProcessError:pass
 else:raise AssertionError('owner child not reaped')
finally:os.close(fd)
'''
    subprocess.run([sys.executable, '-B', '-I', '-c', code, str(SOURCE)], check=True, timeout=10)


@pytest.mark.skipif(sys.platform != 'linux', reason='POSIX installer lock')
def test_lock_key_matches_unchanged_installer_cksum():
    code = r'''
import subprocess,sys
sys.path.insert(0,sys.argv[1])
from controller import lock_key
for path in ('/home/owner/ODS','/home/owner/ODS with spaces','/opt/local/ODS'):
 result=subprocess.check_output(['cksum'],input=path.encode()+b'\0').split()[0].decode()
 assert lock_key(path)==result
'''
    subprocess.run([sys.executable, '-B', '-I', '-c', code, str(SOURCE)], check=True, timeout=10)


@pytest.fixture
def contract_fixture(monkeypatch):
    monkeypatch.syspath_prepend(str(SOURCE))
    monkeypatch.syspath_prepend(str(ODS / 'bin'))
    specification = importlib.util.spec_from_file_location('recovery_preflight_test', SOURCE / 'preflight.py')
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    config = {
        'agents': {'defaults': {}, 'list': [{'id': 'pixel', 'model': 'ods-gateway/ods/current'}]},
        'models': {'providers': {'ods-gateway': {'models': [{
            'id': 'ods/current', 'name': 'ODS Current (fixture-model)', 'contextWindow': 65536,
            'maxTokens': 8192, 'reasoning': False, 'input': ['text', 'image'],
        }]}}},
        'plugins': {'entries': {'pixel-ods': {'config': {
            'modelImageInput': 'supported', 'modelRouteFingerprint': 'a' * 64,
        }}}},
    }
    answer = dict(modelProvider='ods-gateway', modelId='ods/current', modelName='ODS Current (fixture-model)',
                  modelContextWindow=65536, modelMaxTokens=8192, modelReasoning=False,
                  modelImageInput='supported', modelRouteFingerprint='a' * 64, modelThinkingControl='none')
    documents = [answer, copy.deepcopy(answer)]
    binding = dict(install='/fixture/install', owner=dict(uid=1000, home='/fixture/home'))

    def read(path, **kwargs):
        assert kwargs['uid'] == 1000 and kwargs['forbidden_mode'] == 0o077
        return json.dumps(documents[0 if str(path).startswith('/fixture/install/') else 1]).encode()

    monkeypatch.setattr(module, 'read', read)
    return module, config, documents, binding


@pytest.mark.parametrize('capability', ['supported', 'unsupported', 'unknown'])
def test_current_contract_preserves_capabilities_without_native_plugin_mutation(contract_fixture, capability):
    module, config, answers, binding = contract_fixture
    config['plugins']['entries']['pixel-ods']['config']['modelImageInput'] = capability
    config['models']['providers']['ods-gateway']['models'][0]['input'] = ['text'] if capability == 'unsupported' else ['text', 'image']
    for answer in answers:
        answer['modelImageInput'] = capability
    before = copy.deepcopy(config)
    result = module.current_contract(json.dumps(config).encode(), binding)
    assert result == dict(model='fixture-model', contextLength=65536, maxTokens=8192, reasoning=False,
                          imageInput=capability, routeFingerprint='a' * 64, thinkingControl='none')
    assert config == before
    assert 'modelThinkingControl' not in config['plugins']['entries']['pixel-ods']['config']


@pytest.mark.parametrize('condition', ['missing-control', 'answers-differ', 'wrong-route', 'missing-image', 'native-control-mismatch'])
def test_current_contract_refuses_unproven_metadata(contract_fixture, condition):
    module, config, answers, binding = contract_fixture
    if condition == 'missing-control':
        for answer in answers:
            answer.pop('modelThinkingControl')
    elif condition == 'answers-differ':
        answers[1]['modelImageInput'] = 'unknown'
    elif condition == 'wrong-route':
        answers[0]['modelName'] = 'ODS Current (different-model)'
    elif condition == 'missing-image':
        config['plugins']['entries']['pixel-ods']['config'].pop('modelImageInput')
    else:
        config['agents']['list'][0]['params'] = {'chat_template_kwargs': {'enable_thinking': False}}
    with pytest.raises((module.Refused, ValueError)):
        module.current_contract(json.dumps(config).encode(), binding)


@pytest.mark.skipif(sys.platform != 'linux', reason='POSIX controller locks')
@pytest.mark.parametrize('model', ['fixture-model', 'different-model'])
def test_cli_binds_current_metadata_and_refuses_a_different_target(contract_fixture, monkeypatch, model):
    import contextlib
    import controller
    module, config, _answers, binding = contract_fixture
    native = json.dumps(config).encode()
    binding['nativeSha256'] = hashlib.sha256(native).hexdigest()
    manifest = b'fixture-manifest'
    captured = dict(capturedSha256=hashlib.sha256(manifest).hexdigest())
    writes, starts = {}, []

    @contextlib.contextmanager
    def records(*args, **kwargs):
        yield SimpleNamespace(write=lambda name, raw, **kw: writes.update({name: raw}))

    monkeypatch.setattr(controller, 'claim_directory', records)
    monkeypatch.setattr(controller, 'read', lambda path, **kw: manifest if path.name == 'manifest.json' else native)
    monkeypatch.setattr(controller.preflight, 'snapshot', lambda *args: (binding, {}))
    monkeypatch.setattr(controller.preflight, 'current_contract', module.current_contract)
    monkeypatch.setattr(controller.adapters, 'source_inputs', lambda _: {})
    monkeypatch.setattr(controller, 'Locks', lambda: SimpleNamespace(acquire=lambda _: SimpleNamespace(close=lambda: None)))
    monkeypatch.setattr(controller, 'Scope', lambda *args: SimpleNamespace(require_drained=lambda: None, start=lambda: starts.append(True)))
    argv = ['recover', 'b' * 64, '--current-model', model, '--context', '65536', '--max-tokens', '8192']
    if model != 'fixture-model':
        with pytest.raises(module.Refused, match='confirmed-target-not-current'):
            controller.main(argv, Path('/fixture/package'), captured, {})
        assert writes == {} and starts == []
    else:
        assert controller.main(argv, Path('/fixture/package'), captured, {}) == 0
        target = json.loads(writes['claim.json'])['target']
        assert target['imageInput'] == 'supported' and target['thinkingControl'] == 'none'
        assert target['routeFingerprint'] == 'a' * 64
        assert starts == [True]


@pytest.mark.skipif(sys.platform != 'linux', reason='POSIX owner operation')
def test_owner_reconcile_dispatch_preserves_full_bound_contract(monkeypatch, capsys):
    # Execute the complete fixed owner main with its installed-agent transport
    # replaced. This verifies call dispatch, not a source-string assertion.
    tree = ast.parse((SOURCE / 'owner.py').read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
    request = dict(owner=dict(name='fixture', uid=1000, gid=1000, home='/fixture'), install='/fixture/ODS',
                   transaction='b' * 64, target=dict(model='fixture-model', contextLength=65536, maxTokens=8192,
                   reasoning=False, imageInput='supported', routeFingerprint='a' * 64, thinkingControl='always'))
    calls = []
    agent = SimpleNamespace(load_env=lambda _: dict(ODS_MODE='local', LLM_MODEL='fixture-model', GGUF_FILE='model.gguf', CTX_SIZE='65536'))

    def reconcile(*args, **kwargs):
        calls.append((args, kwargs))
        return 'reconciled'

    agent._reconcile_ods_managed_pixel_model = reconcile
    loader = SimpleNamespace(exec_module=lambda _: None)
    imports = SimpleNamespace(util=SimpleNamespace(spec_from_file_location=lambda *_: SimpleNamespace(loader=loader),
                                                   module_from_spec=lambda _: agent))
    namespace = dict(os=SimpleNamespace(geteuid=lambda: 1000), Path=Path, json=json, importlib=imports,
                     pwd=SimpleNamespace(getpwuid=lambda _: SimpleNamespace(pw_name='fixture', pw_uid=1000, pw_gid=1000, pw_dir='/fixture')),
                     sys=SimpleNamespace(argv=['owner.py', 'reconcile'], stdin=SimpleNamespace(buffer=__import__('io').BytesIO(json.dumps(request).encode()))))
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE / 'owner.py'), 'exec'), namespace)
    namespace['main']()
    assert calls == [(('fixture-model', 65536), dict(max_tokens=8192, reasoning=False, borrowed_transaction='b' * 64,
                     image_input='supported', route_fingerprint='a' * 64, thinking_control='always'))]
    assert json.loads(capsys.readouterr().out)['ok'] is True
