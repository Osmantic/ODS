"""Real source/receipt derivation in private fixtures; no services or sudo."""
import copy
import json
import os
import platform
import pwd
import socket
from pathlib import Path
import subprocess
import sys
import threading
import tempfile
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bin'))
sys.path.insert(0, str(ROOT / 'extensions/services/pixel-agent/host'))
import pixel_source_upgrade as upgrade  # noqa: E402
import pixel_access_bridge as access  # noqa: E402
import access_mode_config as modes  # noqa: E402
from test_pixel_model_transition import FakeBridge  # noqa: E402

TOKEN = 'd' * 64
REF = 'b' * 40


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(value if isinstance(value, bytes) else upgrade.encoded(value))
    path.chmod(0o600)


@pytest.fixture
def held_overlay(tmp_path, monkeypatch):
    return make_held_overlay(tmp_path, monkeypatch)


def make_held_overlay(tmp_path, monkeypatch, provider='cloud', new_inspector=False, new_project=False):
    uid, gid = os.getuid(), os.getgid()
    assert uid > 0, 'run these disposable fixtures as an ordinary user'
    home, installed, candidate = [tmp_path / name for name in ('home', 'installed', 'candidate')]
    for root in (home, installed, candidate):
        root.mkdir(mode=0o700)
    bridge = FakeBridge(tmp_path / 'state')
    bridge.state.chmod(0o700)
    bridge.home, bridge.install = home, installed
    bridge.owner = SimpleNamespace(pw_uid=uid, pw_gid=gid, pw_dir=str(home), pw_name=pwd.getpwuid(uid).pw_name)
    state = bridge.state / 'source-upgrade'
    state.mkdir(mode=0o700)
    manager = upgrade.SourceUpgrade(state, installed, uid, state_uid=uid)
    original_init = upgrade.SourceUpgrade.__init__
    monkeypatch.setattr(upgrade.SourceUpgrade, '__init__',
        lambda self, state, install, owner_uid, **kw: original_init(self, state, install, owner_uid, state_uid=uid))
    original_private = upgrade._overlay_private
    monkeypatch.setattr(upgrade, '_overlay_private', lambda root, name, wanted: original_private(root, name, uid if wanted == 0 else wanted))
    monkeypatch.setattr(access, 'private_json', lambda p, _uid, maximum=1048576: json.loads(p.read_bytes()))
    # Root-custodied mirror is represented by a private, ordinary-user fixture.
    system = tmp_path / 'system'
    mirror = system / 'program'
    mirror.mkdir(parents=True)
    monkeypatch.setattr(upgrade, 'SYSTEM_ROOT', system)
    monkeypatch.setattr(upgrade, 'SYSTEM_UID', uid)
    monkeypatch.setattr(upgrade, 'MIRROR', mirror)
    monkeypatch.setattr(upgrade, 'MIRROR_LEAVES', frozenset())
    for name in upgrade.ROOTS:
        (installed / name).mkdir()
        (candidate / name).mkdir()
    writer = ROOT / 'installers/lib/pixel-runtime-budget.py'
    save(candidate / 'installers/lib/pixel-runtime-budget.py', writer.read_bytes())
    if new_inspector or new_project:
        names = ['installers/lib/pixel-preview-inspection.py'] + [
            'extensions/services/pixel-agent/host/' + name for name in (
                'preview_inspection.py', 'preview_inspection_protocol.py',
                'workspace_preview.py', 'unix_peer.py', 'pixel-preview-inspection.service')]
        for name in names:
            save(candidate / name, (ROOT / name).read_bytes())
    if new_project:
        # Exact public #7025 installer; this source-upgrade branch intentionally
        # does not import or install the independently shipped project executor.
        project_installer = (ROOT / 'tests/fixtures/project-provision-91b1322dd.py').read_bytes()
        assert upgrade.sha(project_installer) == '8b78c83a8c2f6c31cdfc8433a8090d3a9c11f8637ff3f8ac87e253e76b5752fd'
        save(candidate / 'installers/lib/pixel-project-runtime.py', project_installer)
        for name in ('project_service', 'project_controller', 'project_runtime', 'project_runtime_protocol',
                     'project_capabilities', 'project_storage', 'project_diagnostics', 'project_snapshot',
                     'project_jobs', 'project_artifacts', 'project_dispatch', 'project_transport', 'project_authority'):
            save(candidate / ('extensions/services/pixel-agent/host/' + name + '.py'), b'# fixture service source\n')
    mirror_files = {'pixel_access_bridge.py': 'bin/pixel_access_bridge.py',
        'pixel_source_upgrade.py': 'bin/pixel_source_upgrade.py',
        'access_mode_server.py': 'extensions/services/pixel-agent/host/access_mode_server.py'}
    for name, relative in mirror_files.items():
        save(candidate / relative, b'fixture protected module')
        save(mirror / name, b'prior fixture module')
    sandbox = {
        'models': {'providers': {'ods-gateway': {'api': 'openai-completions',
            'apiKey': 'fixture-not-a-real-key', 'baseUrl': 'http://127.0.0.1:4000/v1',
            'models': [{'id': 'ods/current', 'name': 'ODS Current (fixture)',
                'contextWindow': 131072, 'maxTokens': 4096, 'reasoning': False}]}}},
        'agents': {'defaults': {}, 'list': [{'id': 'pixel', 'model': 'ods-gateway/ods/current',
            'sandbox': {'mode': 'all'}, 'tools': {'fs': {'workspaceOnly': True}}}]},
        'session': {}, 'tools': {}, 'plugins': {'entries': {'pixel-ods': {'config': {
            'perplexicaPort': 3004, 'modelRouteFingerprint': 'f' * 64,
            'workspacePreviewInspectionTransport': 'unix',
            'projectBuildSocket': '/var/lib/ods-pixel-project/control.sock'}}}}}
    if provider == 'local':
        sandbox['models']['providers'] = {'ods-local': {'api': 'openai-completions',
            'apiKey': 'local-no-auth', 'baseUrl': 'http://127.0.0.1:11434/v1',
            'models': [{'id': 'Qwen3.5-9B', 'name': 'ODS Local Qwen3.5-9B',
                'contextWindow': 16384, 'maxTokens': 4096, 'reasoning': False}]}}
        sandbox['agents']['list'][0]['model'] = 'ods-local/Qwen3.5-9B'
        del sandbox['plugins']['entries']['pixel-ods']['config']['modelRouteFingerprint']
    if new_inspector:
        del sandbox['plugins']['entries']['pixel-ods']['config']['workspacePreviewInspectionTransport']
    if new_project:
        del sandbox['plugins']['entries']['pixel-ods']['config']['projectBuildSocket']
    before_config, before_baseline = modes.enable(sandbox)
    before = upgrade.encoded(before_config)
    config_path = home / '.openclaw/openclaw.json'
    receipt_before = dict(version=1, status='full-access', config_path=str(config_path),
        config_sha256=upgrade.sha(before), baseline=before_baseline)
    next_config = copy.deepcopy(sandbox)
    next_config['session']['reset'] = {'mode': 'idle', 'idleMinutes': 240}
    candidate_raw = upgrade.encoded(next_config)
    after, restore_baseline = modes.rebase_enabled(before_config, next_config, before_baseline)
    anchor = upgrade.encoded(after)
    receipt_after = dict(receipt_before, baseline=restore_baseline, config_sha256=upgrade.sha(anchor))
    owner_state = home / '.openclaw/.ods-access-mode'
    for name, value in [('config-before', before), ('config-after', anchor),
                        ('receipt-before', receipt_before), ('receipt-after', receipt_after)]:
        save(owner_state / ('access-release-' + name + '.json'), value)
    save(owner_state / 'pixel-access-mode.json', receipt_after)
    save(owner_state / 'access-release-completed.json', dict(transactionId=TOKEN,
        configPath=str(config_path), configSha256=upgrade.sha(anchor),
        receiptSha256=upgrade.sha(upgrade.encoded(receipt_after)), outcome='apply'))
    save(installed / f'data/pixel/source-{REF}/dist/openclaw.json', candidate_raw)
    (installed / 'data/pixel').chmod(0o700)
    save(bridge.state / 'release-intent.json', dict(transactionId=TOKEN, candidateSha256=upgrade.sha(candidate_raw)))
    save(bridge.state / 'release-baseline.json', dict(transactionId=TOKEN, configSha256=upgrade.sha(before),
        receiptSha256=upgrade.sha(upgrade.encoded(receipt_before))))
    save(bridge.state / 'release-prepared.json', dict(transactionId=TOKEN,
        candidateSha256=upgrade.sha(candidate_raw), beforeSha=upgrade.sha(before), afterSha=upgrade.sha(anchor)))
    save(bridge.state / 'release-completed.json', dict(transactionId=TOKEN, configSha256=upgrade.sha(anchor), outcome='apply'))
    identity = dict(beforeRef='a' * 40, afterRef=REF, markerSha256='c' * 64,
        configSha256=upgrade.sha(before), receiptSha256=upgrade.sha(upgrade.encoded(receipt_before)))
    manager.stage(candidate, uid, identity)
    for name, relative in mirror_files.items():
        raw = (candidate / relative).read_bytes()
        manager.record_mirror_write(mirror / name, raw, 0o600, uid, gid)
        save(mirror / name, raw)
    manager.bind(TOKEN, lambda _: None)
    manager.publish(lambda _: None)
    manager.finish(lambda *_: None)
    # Produce the real installer output independently of the proof helper.
    save(config_path, anchor)
    renderer_args = [sys.executable, '-I', str(writer), str(config_path), '3004', '', str(home / '.openclaw'), 'unix']
    if new_project:
        renderer_args.append('/var/lib/ods-pixel-project/control.sock')
    result = subprocess.run(renderer_args, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    save(config_path, Path(result.stdout.strip()).read_bytes())
    bridge.config = dict(configured_status='full-access', config_sha256=upgrade.sha(config_path.read_bytes()))
    save(bridge.state / 'transition.json', dict(kind='model', transaction_id=TOKEN, token='e' * 64,
        phase='held', edge_revision='b' * 64, configured_mode='full-access', start_config_sha256=upgrade.sha(before)))
    bridge.native_state['phase'] = bridge.edge_state['phase'] = 'held'
    save(home / '.openclaw/.ods-access-runtime/state.json', dict(phase='held', revision='c' * 64,
        tokenHash=upgrade.sha(('e' * 64).encode())))
    monkeypatch.setattr(bridge, 'unit_boundary', lambda: 'fixture-boundary')
    def prove(token, mode):
        assert token == 'e' * 64 and mode == 'full-access'
        assert bridge.edge_state['phase'] == bridge.native_state['phase'] == 'held'
        bridge.calls.append('fresh-overlay-proof')
        if bridge.fail_verify:
            raise access.AccessError('runtime-proof-failed')
        bridge.native_state['proof'] = {'mode': 'full-access', 'executed': True, 'nonce': 'fresh'}
        save(bridge.state / 'verified.json', dict(pid=bridge.native_state['pid'],
            proof=bridge.native_state['proof'], boundary='fixture-boundary', config_sha256=bridge.config['config_sha256']))
    monkeypatch.setattr(bridge, 'verify_held_mode', prove)
    return SimpleNamespace(bridge=bridge, manager=manager, config=config_path,
        owner_state=owner_state, candidate=installed / f'data/pixel/source-{REF}/dist/openclaw.json')


def test_exact_renderer_completion_preserves_release_receipts_and_replay(held_overlay):
    f = held_overlay
    roots = {p: p.read_bytes() for p in f.bridge.state.glob('release-*.json')}
    owner = {p: p.read_bytes() for p in f.owner_state.glob('*.json')}
    assert f.bridge.model_finish(dict(transaction_id=TOKEN, outcome='applied')) == {'status': 'released', 'outcome': 'applied'}
    record = json.loads((f.bridge.state / 'source-overlay-completed.json').read_bytes())
    assert record['configSha256'] == upgrade.sha(f.config.read_bytes())
    assert record['beforeSha256'] != record['configSha256']
    assert 'fresh-overlay-proof' in f.bridge.calls
    assert all(p.read_bytes() == raw for p, raw in (roots | owner).items())
    assert f.bridge.model_finish(dict(transaction_id=TOKEN, outcome='applied'))['status'] == 'released'


@pytest.mark.parametrize('fault', ['config', 'renderer', 'candidate', 'receipt-baseline',
    'receipt-snapshot', 'pending-release', 'plan-token', 'plan-incomplete', 'root-completed', 'proof'])
def test_unbound_or_unproved_overlay_never_releases(held_overlay, fault):
    f = held_overlay
    if fault == 'config':
        value = json.loads(f.config.read_bytes())
        value['session']['unreviewed'] = True
        save(f.config, value)
        f.bridge.config['config_sha256'] = upgrade.sha(f.config.read_bytes())
    elif fault == 'renderer':
        save(f.bridge.install / 'installers/lib/pixel-runtime-budget.py', b'print("forged")')
    elif fault == 'candidate':
        save(f.candidate, b'{}')
    elif fault in ('receipt-baseline', 'receipt-snapshot'):
        path = f.owner_state / 'access-release-receipt-after.json'
        value = json.loads(path.read_bytes())
        value['baseline']['tools.fs.workspaceOnly']['value'] = False
        save(path, value)
        if fault == 'receipt-baseline':
            save(f.owner_state / 'pixel-access-mode.json', value)
            completed = json.loads((f.owner_state / 'access-release-completed.json').read_bytes())
            completed['receiptSha256'] = upgrade.sha(upgrade.encoded(value))
            save(f.owner_state / 'access-release-completed.json', completed)
    elif fault == 'pending-release':
        save(f.owner_state / 'access-release-journal.json', {})
    elif fault in ('plan-token', 'plan-incomplete'):
        value = f.manager.journal()
        value['hold' if fault == 'plan-token' else 'phase'] = 'f' * 64 if fault == 'plan-token' else 'applied'
        f.manager._save(value)
    elif fault == 'root-completed':
        save(f.bridge.state / 'release-completed.json', dict(transactionId='a' * 64, configSha256='b' * 64, outcome='apply'))
    else:
        f.bridge.fail_verify = True
    with pytest.raises(access.AccessError):
        f.bridge.model_finish(dict(transaction_id=TOKEN, outcome='applied'))
    assert f.bridge.edge_state['phase'] == f.bridge.native_state['phase'] == 'held'
    assert not any(call.endswith(':release') for call in f.bridge.calls)
    assert not (f.bridge.state / 'source-overlay-completed.json').exists()


def test_change_during_fresh_proof_retains_hold(held_overlay, monkeypatch):
    f = held_overlay
    def race(*_):
        save(f.owner_state / 'pixel-access-mode.json', {})
    monkeypatch.setattr(f.bridge, 'verify_held_mode', race)
    with pytest.raises(access.AccessError):
        f.bridge.model_finish(dict(transaction_id=TOKEN, outcome='applied'))
    assert f.bridge.edge_state['phase'] == f.bridge.native_state['phase'] == 'held'


def test_failure_after_derivation_record_reproves_on_same_candidate_resume(held_overlay):
    f = held_overlay
    f.bridge.fail_native = 'release'
    with pytest.raises(access.AccessError):
        f.bridge.model_finish(dict(transaction_id=TOKEN, outcome='applied'))
    assert (f.bridge.state / 'source-overlay-completed.json').exists()
    assert f.bridge.edge_state['phase'] == 'held'
    f.bridge.fail_native = None
    f.bridge.calls.clear()
    assert f.bridge.model_finish(dict(transaction_id=TOKEN, outcome='applied'))['status'] == 'released'
    assert 'fresh-overlay-proof' in f.bridge.calls


@pytest.mark.parametrize('fault', ['pid', 'proof', 'boundary', 'edge', 'completion'])
def test_change_during_second_derivation_never_releases(held_overlay, monkeypatch, fault):
    f = held_overlay
    original = upgrade.prove_runtime_overlay
    calls = []
    def race(*args):
        result = original(*args)
        calls.append(True)
        if len(calls) == 2:
            if fault == 'pid':
                f.bridge.native_state['pid'] += 1
            elif fault == 'proof':
                f.bridge.native_state['proof'] = None
            elif fault == 'boundary':
                monkeypatch.setattr(f.bridge, 'unit_boundary', lambda: 'changed')
            elif fault == 'edge':
                f.bridge.edge_state['revision'] = 'a' * 64
            else:
                save(f.bridge.state / 'source-overlay-completed.json', {'forged': True})
        return result
    monkeypatch.setattr(upgrade, 'prove_runtime_overlay', race)
    with pytest.raises(access.AccessError):
        f.bridge.model_finish(dict(transaction_id=TOKEN, outcome='applied'))
    assert f.bridge.edge_state['phase'] == 'held'
    assert not any(call.endswith(':release') for call in f.bridge.calls)


def test_subsequent_transaction_replaces_only_independently_proven_history(held_overlay):
    f = held_overlay
    assert f.bridge.model_finish(dict(transaction_id=TOKEN, outcome='applied'))['status'] == 'released'
    original = (f.bridge.state / 'source-overlay-completed.json').read_bytes()
    next_token = '9' * 64
    # Represent the next completed source and release lifecycle, with every
    # input authenticated anew. The old overlay receipt deliberately remains.
    plan = f.manager.journal()
    plan['hold'] = next_token
    f.manager._save(plan)
    for path in [*f.bridge.state.glob('release-*.json'), f.owner_state / 'access-release-completed.json']:
        value = json.loads(path.read_bytes())
        value['transactionId'] = next_token
        save(path, value)
    save(f.bridge.state / 'transition.json', dict(kind='model', transaction_id=next_token, token='e' * 64,
        phase='held', edge_revision='b' * 64, configured_mode='full-access', start_config_sha256='a' * 64))
    f.bridge.native_state['phase'] = f.bridge.edge_state['phase'] = 'held'
    f.bridge.calls.clear()
    assert f.bridge.model_finish(dict(transaction_id=next_token, outcome='applied'))['status'] == 'released'
    replacement = (f.bridge.state / 'source-overlay-completed.json').read_bytes()
    assert replacement != original
    assert json.loads(replacement)['transactionId'] == next_token
    assert 'fresh-overlay-proof' in f.bridge.calls


@pytest.mark.parametrize('fault', ['receipt', 'candidate', 'root-record', 'renderer', 'config', 'pid'])
def test_mutation_inside_renderer_is_detected_before_return(held_overlay, monkeypatch, fault):
    f = held_overlay
    original = upgrade._render_overlay
    def race(*args):
        result = original(*args)
        if fault == 'receipt':
            path = f.owner_state / 'pixel-access-mode.json'
            receipt = json.loads(path.read_bytes())
            receipt['baseline']['tools.fs.workspaceOnly']['value'] = False
            save(path, receipt)
        elif fault == 'candidate':
            save(f.candidate, b'{}')
        elif fault == 'root-record':
            save(f.bridge.state / 'release-baseline.json', {})
        elif fault == 'renderer':
            save(f.bridge.install / 'installers/lib/pixel-runtime-budget.py', b'changed')
        elif fault == 'config':
            save(f.config, b'{}')
        else:
            f.bridge.native_state['pid'] += 1
        return result
    monkeypatch.setattr(upgrade, '_render_overlay', race)
    with pytest.raises(access.AccessError):
        f.bridge.model_finish(dict(transaction_id=TOKEN, outcome='applied'))
    assert not any(call.endswith(':release') for call in f.bridge.calls)


def test_owner_receipt_serializer_may_differ_without_changing_any_field(held_overlay):
    f = held_overlay
    path = f.owner_state / 'pixel-access-mode.json'
    value = json.loads(path.read_bytes())
    save(path, (json.dumps(value, indent=2) + '\n').encode())
    assert path.read_bytes() != (f.owner_state / 'access-release-receipt-after.json').read_bytes()
    assert f.bridge.model_finish(dict(transaction_id=TOKEN, outcome='applied'))['status'] == 'released'


@pytest.mark.parametrize('raw', [b'{"status":"full-access","status":"full-access"}',
    b'{"config_sha256":NaN}', b'{"version":1.0}', b'{"version":true}'])
def test_receipt_semantics_never_accept_ambiguous_or_coerced_json(held_overlay, raw):
    f = held_overlay
    save(f.owner_state / 'pixel-access-mode.json', raw)
    with pytest.raises(access.AccessError):
        f.bridge.model_finish(dict(transaction_id=TOKEN, outcome='applied'))
    assert not any(call.endswith(':release') for call in f.bridge.calls)


def test_installer_shared_data_retains_private_pixel_candidate_custody(held_overlay):
    f = held_overlay
    (f.bridge.install / 'data').chmod(0o775)
    (f.bridge.install / 'data/pixel').chmod(0o700)
    assert f.bridge.model_finish(dict(transaction_id=TOKEN, outcome='applied'))['status'] == 'released'


@pytest.mark.parametrize('component', ['data', 'data/pixel', 'data/pixel/source-' + REF])
def test_candidate_parent_symlink_never_followed(held_overlay, component):
    f = held_overlay
    path = f.bridge.install / component
    original = path.with_name(path.name + '-real')
    path.rename(original)
    path.symlink_to(original, target_is_directory=True)
    with pytest.raises(access.AccessError):
        f.bridge.model_finish(dict(transaction_id=TOKEN, outcome='applied'))
    assert not any(call.endswith(':release') for call in f.bridge.calls)


def test_local_model_uses_original_local_route_without_cloud_fingerprint(tmp_path, monkeypatch):
    f = make_held_overlay(tmp_path, monkeypatch, provider='local')
    before = f.config.read_bytes()
    assert f.bridge.model_finish(dict(transaction_id=TOKEN, outcome='applied'))['status'] == 'released'
    assert f.config.read_bytes() == before
    value = json.loads(before)
    assert value['agents']['list'][0]['model'] == 'ods-local/Qwen3.5-9B'
    assert value['models']['providers']['ods-local']['apiKey'] == 'local-no-auth'
    assert 'modelRouteFingerprint' not in value['plugins']['entries']['pixel-ods']['config']


def test_local_route_change_is_not_a_runtime_overlay(tmp_path, monkeypatch):
    f = make_held_overlay(tmp_path, monkeypatch, provider='local')
    value = json.loads(f.config.read_bytes())
    value['models']['providers']['ods-local']['baseUrl'] = 'http://127.0.0.1:12345/v1'
    save(f.config, value)
    f.bridge.config['config_sha256'] = upgrade.sha(f.config.read_bytes())
    with pytest.raises(access.AccessError, match='source-overlay-not-derived'):
        f.bridge.model_finish(dict(transaction_id=TOKEN, outcome='applied'))
    assert not any(call.endswith(':release') for call in f.bridge.calls)


def test_sandbox_source_completion_never_uses_full_access_overlay(held_overlay, monkeypatch):
    f = held_overlay
    # The sandbox release has no Full Access migration intent. Its source plan
    # still has to be complete and its runtime still gets a fresh sandbox proof.
    for path in f.bridge.state.glob('release-*.json'):
        path.unlink()
    receipt = json.loads((f.owner_state / 'pixel-access-mode.json').read_bytes())
    sandbox, _ = modes.restore(json.loads(f.config.read_bytes()), receipt['baseline'])
    save(f.config, sandbox)
    original = f.config.read_bytes()
    f.bridge.config['configured_status'] = 'sandboxed'
    f.bridge.config['config_sha256'] = upgrade.sha(original)
    pending = f.bridge.pending()
    pending['configured_mode'] = 'sandboxed'
    save(f.bridge.state / 'transition.json', pending)
    monkeypatch.setattr(upgrade, 'prove_runtime_overlay', lambda *_: pytest.fail('sandbox must not derive a Full Access overlay'))
    proofs = []
    monkeypatch.setattr(f.bridge, 'verify_held_mode', lambda token, mode: proofs.append((token, mode)))
    assert f.bridge.model_finish(dict(transaction_id=TOKEN, outcome='applied'))['status'] == 'released'
    assert proofs == [('e' * 64, 'sandboxed')]
    assert f.config.read_bytes() == original
    agent = json.loads(original)['agents']['list'][0]
    assert agent['sandbox']['mode'] == 'all' and agent['tools']['fs']['workspaceOnly'] is True
    assert not (f.bridge.state / 'source-overlay-completed.json').exists()


def test_sandbox_cannot_adopt_full_access_completion(held_overlay):
    f = held_overlay
    f.bridge.config['configured_status'] = 'sandboxed'
    pending = f.bridge.pending()
    pending['configured_mode'] = 'sandboxed'
    save(f.bridge.state / 'transition.json', pending)
    with pytest.raises(access.AccessError, match='source-overlay-hold-required'):
        f.bridge.model_finish(dict(transaction_id=TOKEN, outcome='applied'))
    assert not any(call.endswith(':release') for call in f.bridge.calls)


@pytest.fixture
def provisioned_overlay(monkeypatch, request):
    # A real Unix listener exercises SO_PEERCRED. Only Docker/systemd are
    # disposable adapters; the source installer validates its actual contract.
    with tempfile.TemporaryDirectory(prefix='ods-prov-') as temporary:
        kind = getattr(request, 'param', 'inspection')
        f = make_held_overlay(Path(temporary), monkeypatch, new_inspector=kind == 'inspection', new_project=kind == 'project')
        system = upgrade.SYSTEM_ROOT
        host = f.bridge.install / 'extensions/services/pixel-agent/host'
        image = 'sha256:' + '9' * 64
        if kind == 'inspection':
            stem, unit_name = 'ods-pixel-inspection', 'pixel-preview-inspection.service'
            config = dict(imageId=image, docker='/usr/bin/docker', snapshotRoot='/var/lib/ods-pixel-preview',
                          ownerUid=os.getuid(), transport='local')
        else:
            stem, unit_name = 'ods-pixel-project', 'ods-pixel-project.service'
            config = dict(imageId=image, ownerUid=os.getuid(),
                          workspace=str(Path(pwd.getpwuid(os.getuid()).pw_dir) / '.openclaw/workspace-pixel'))
        save(system / ('etc/' + stem + '.json'), config)
        f.images = [dict(Id=image, Os='linux', Architecture={'x86_64': 'amd64', 'aarch64': 'arm64'}[platform.machine()],
            Config=dict(User='65534:65534', Entrypoint=['python3', '/source/preview_inspection_capsule.py'], Labels={
                'org.osmantic.ods.component': 'pixel-preview-inspection', 'org.osmantic.ods.inspection.protocol': '1',
                'org.osmantic.ods.inspection.select': 'native-single-select-v1',
                'org.osmantic.ods.inspection.playwright': '1.62.0'}))]
        contract = upgrade._provision_contract(f.manager, f.manager.journal(), kind, config, [f.images])
        for name in contract['files']:
            save(system / ('usr/local/libexec/' + stem) / name, (host / name).read_bytes())
        unit = (host / unit_name).read_bytes() if kind == 'inspection' else contract['unit'].encode()
        save(system / 'etc/systemd/system' / unit_name, unit)
        f.status = dict(LoadState='loaded', ActiveState='active', SubState='running', MainPID=str(os.getpid()),
            User='root' if kind == 'inspection' else f.bridge.owner.pw_name,
            FragmentPath='/etc/systemd/system/' + unit_name, DropInPaths='', NeedDaemonReload='no')
        original_command = f.bridge.command
        def command(args, timeout=20):
            if args[:3] == ['/usr/bin/docker', 'image', 'inspect']:
                return json.dumps(f.images)
            if args[:2] == ['/usr/bin/systemctl', 'show']:
                return '\n'.join(key + '=' + value for key, value in f.status.items())
            return original_command(args, timeout=timeout)
        monkeypatch.setattr(f.bridge, 'command', command)
        path = system / ('run/ods-pixel-inspection/control.sock' if kind == 'inspection' else 'var/lib/ods-pixel-project/control.sock')
        path.parent.mkdir(parents=True, mode=0o700)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(path))
        path.chmod(0o600)
        listener.listen(16)
        listener.settimeout(0.05)
        stop = threading.Event()
        def accept():
            while not stop.is_set():
                try:
                    connection, _ = listener.accept()
                except TimeoutError:
                    continue
                connection.close()
        thread = threading.Thread(target=accept)
        thread.start()
        try:
            yield f
        finally:
            stop.set()
            thread.join(timeout=2)
            listener.close()


def test_first_inspector_requires_exact_provisioned_service_and_real_peer(provisioned_overlay):
    f = provisioned_overlay
    before = {path: path.read_bytes() for path in f.bridge.state.glob('release-*.json')}
    assert f.bridge.model_finish(dict(transaction_id=TOKEN, outcome='applied'))['status'] == 'released'
    proof = json.loads((f.bridge.state / 'source-overlay-completed.json').read_bytes())
    assert upgrade.HEX.fullmatch(proof['provisionSha256'])
    assert all(path.read_bytes() == raw for path, raw in before.items())


@pytest.mark.parametrize('fault', ['owner', 'image', 'select-label', 'unit', 'file', 'peer', 'dropin', 'reload', 'config-during-render'])
def test_new_inspector_provision_fault_never_releases(provisioned_overlay, monkeypatch, fault):
    f = provisioned_overlay
    config_path = upgrade.SYSTEM_ROOT / 'etc/ods-pixel-inspection.json'
    if fault == 'owner':
        value = json.loads(config_path.read_bytes())
        value['ownerUid'] += 1
        save(config_path, value)
    elif fault == 'image':
        f.images[0]['Config']['User'] = 'root'
    elif fault == 'select-label':
        del f.images[0]['Config']['Labels']['org.osmantic.ods.inspection.select']
    elif fault == 'unit':
        save(upgrade.SYSTEM_ROOT / 'etc/systemd/system/pixel-preview-inspection.service', b'forged')
    elif fault == 'file':
        save(upgrade.SYSTEM_ROOT / 'usr/local/libexec/ods-pixel-inspection/preview_inspection.py', b'forged')
    elif fault in ('peer', 'dropin', 'reload'):
        f.status[{'peer': 'MainPID', 'dropin': 'DropInPaths', 'reload': 'NeedDaemonReload'}[fault]] = {
            'peer': str(os.getpid() + 1), 'dropin': '/unreviewed.conf', 'reload': 'yes'}[fault]
    else:
        original = upgrade._render_overlay
        def race(*args, **kwargs):
            value = original(*args, **kwargs)
            body = json.loads(config_path.read_bytes())
            body['ownerUid'] += 1
            save(config_path, body)
            return value
        monkeypatch.setattr(upgrade, '_render_overlay', race)
    with pytest.raises(access.AccessError):
        f.bridge.model_finish(dict(transaction_id=TOKEN, outcome='applied'))
    assert not any(call.endswith(':release') for call in f.bridge.calls)


@pytest.mark.parametrize('provisioned_overlay', ['project'], indirect=True)
def test_first_project_socket_uses_actual_public_installer_contract(provisioned_overlay):
    f = provisioned_overlay
    assert f.bridge.model_finish(dict(transaction_id=TOKEN, outcome='applied'))['status'] == 'released'
    assert 'provisionSha256' in json.loads((f.bridge.state / 'source-overlay-completed.json').read_bytes())


@pytest.mark.parametrize('provisioned_overlay', ['project'], indirect=True)
@pytest.mark.parametrize('fault', ['workspace', 'owner', 'image', 'unit', 'file', 'peer', 'during-proof'])
def test_new_project_provision_fault_never_releases(provisioned_overlay, monkeypatch, fault):
    f = provisioned_overlay
    path = upgrade.SYSTEM_ROOT / 'etc/ods-pixel-project.json'
    if fault in ('workspace', 'owner'):
        value = json.loads(path.read_bytes())
        value['workspace' if fault == 'workspace' else 'ownerUid'] = '/unreviewed' if fault == 'workspace' else os.getuid() + 1
        save(path, value)
    elif fault == 'image':
        f.images[0]['Id'] = 'sha256:' + '8' * 64
    elif fault == 'unit':
        save(upgrade.SYSTEM_ROOT / 'etc/systemd/system/ods-pixel-project.service', b'forged')
    elif fault == 'file':
        save(upgrade.SYSTEM_ROOT / 'usr/local/libexec/ods-pixel-project/project_service.py', b'forged')
    elif fault == 'peer':
        f.status['MainPID'] = str(os.getpid() + 1)
    else:
        original = f.bridge.verify_held_mode
        def race(*args):
            original(*args)
            value = json.loads(path.read_bytes())
            value['imageId'] = 'sha256:' + '8' * 64
            save(path, value)
        monkeypatch.setattr(f.bridge, 'verify_held_mode', race)
    with pytest.raises(access.AccessError):
        f.bridge.model_finish(dict(transaction_id=TOKEN, outcome='applied'))
    assert not any(call.endswith(':release') for call in f.bridge.calls)
