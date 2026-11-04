"""Private disposable path/transaction seams; not physical Darwin qualification.

Root metadata and Darwin ACL calls are explicitly simulated for these owned
files. Bundle/runtime hashing, the actual planner, worker main and journals run.
No installed path, account, launchctl, provider or model is used.
"""
# ruff: noqa: E402 -- isolated POSIX imports follow the platform skip and path binding.
import ast
import contextvars
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
import plistlib
import stat
import sys
from types import SimpleNamespace

import pytest

if sys.platform == 'win32':
    pytest.skip('POSIX owner files required', allow_module_level=True)
import pwd
import grp
ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / 'extensions/services/pixel-agent/host'
sys.path[:0] = [str(ROOT / 'bin'), str(HOST), str(ROOT / 'tests')]
import pixel_access_bridge as bridge_module
import pixel_access_protocol as protocol
import pixel_access_mode as controller
import pixel_macos_custody as mac_custody
from pixel_provider import runtime_custody as custody
from pixel_connections import planner, native_mcp, state
import connection_transaction as transaction
from test_pixel_connection_planner import disconnected_config, gateway, INSTALL


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def private(path, data, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(data)
    path.chmod(mode)
    return path


def metadata_tree_driver(namespace, owner):
    """Run the production FD traversal with only relocated Darwin observations.

    Private os bindings avoid modifying process-global functions or their
    concurrent tests. Every call still opens/stats/enumerates the actual tree.
    RuntimeCustody and bundle.verify retain their separate content/hash proofs.
    """
    namespace = Path(namespace)
    module = load(ROOT / 'bin/pixel_macos_custody.py', 'fixture_metadata_custody')
    real = SimpleNamespace(**vars(os))
    local = SimpleNamespace(**vars(os))
    external = {(info.st_dev, info.st_ino) for path in namespace.parents
        for info in (real.stat(path, follow_symlinks=False),)}
    active = contextvars.ContextVar('fixture_metadata_root', default=None)
    locations, owners = {}, {}

    def location(path, directory=None):
        path = Path(path)
        return path if path.is_absolute() else locations[directory] / path

    def opened(path, flags, *args, **kwargs):
        fd = real.open(path, flags, *args, **kwargs)
        locations[fd] = location(path, kwargs.get('dir_fd'))
        active.get()['fds'].add(fd)
        return fd

    def closed(fd):
        locations.pop(fd, None)
        active.get()['fds'].discard(fd)
        return real.close(fd)

    def observed(info, path):
        root = active.get()
        identity = (info.st_dev, info.st_ino)
        fields = None
        if root is not None and identity in external and stat.S_ISDIR(info.st_mode):
            # Exact pre-observed ancestors outside the fixture stand for the
            # canonical Root parents; /tmp's sticky writable mode is not one.
            fields = dict(st_uid=0, st_mode=info.st_mode & ~0o022)
        elif root is not None and (path == namespace or namespace in path.parents):
            uid = owners.get(identity, info.st_uid)
            fields = dict(st_uid=0 if uid == owner else uid)
        if fields is None:
            return info
        return SimpleNamespace(**{key: fields.get(key, getattr(info, key))
            for key in dir(info) if key.startswith('st_')})

    def fstat(fd):
        return observed(real.fstat(fd), locations[fd])

    def current(path, *args, **kwargs):
        return observed(real.stat(path, *args, **kwargs), location(path, kwargs.get('dir_fd')))

    local.open, local.close, local.fstat, local.stat = opened, closed, fstat, current
    module.os = local
    module._require_no_acl = lambda fd: custody._acl(fd)

    def check(path):
        path = Path(path)
        if path != namespace and namespace not in path.parents:
            raise AssertionError('metadata fixture cannot qualify outside its namespace')
        custody._parents(path / 'entry')
        state = {'root': path, 'fds': set()}
        token = active.set(state)
        try:
            return module.protected_tree_metadata(path)
        except module.CustodyError as error:
            raise mac_custody.CustodyError(str(error)) from None
        finally:
            active.reset(token)
            assert not state['fds']
    return SimpleNamespace(check=check, module=module, owners=owners)


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    uid, gid = os.getuid(), os.getgid()
    monkeypatch.setattr(custody, 'ROOT_UID', uid)
    monkeypatch.setattr(custody, '_parents', lambda path: native_mcp._parents(path, uid))
    monkeypatch.setattr(custody, '_acl', lambda fd: None)  # No physical Mac ACL claim.
    original_read = custody._read
    monkeypatch.setattr(custody, '_read', lambda path, owner, maximum: original_read(path, uid, maximum))

    def protected_bytes(path, *, limit=1024 * 1024):
        if type(limit) is not int or limit < 1:
            raise mac_custody.CustodyError('macos-custody-path-invalid')
        path = Path(path)
        custody._parents(path)
        custody._regular(path, maximum=limit)
        with path.open('rb') as handle:
            body = handle.read(limit + 1)
        if len(body) > limit:
            raise mac_custody.CustodyError('macos-custody-file-too-large')
        return body

    metadata_tree = metadata_tree_driver(tmp_path, uid)
    monkeypatch.setattr(mac_custody, 'protected_bytes', protected_bytes)
    monkeypatch.setattr(mac_custody, 'protected_tree_metadata', metadata_tree.check)
    bundle_helper = ROOT / 'installers/macos/lib/pixel-runtime-bundle.py'
    bundle_module = load(bundle_helper, '_test_connection_bundle')
    parent = tmp_path / 'runtimes'
    parent.mkdir(mode=0o700)
    monkeypatch.setattr(planner, 'NATIVE_RUNTIME_ROOT', parent)
    root = parent / 'preparing'
    root.mkdir(mode=0o755)
    private(root / 'node', b'not executed\n', 0o755)
    private(root / 'runtime/openclaw.mjs', b'// not executed\n', 0o644)
    private(root / 'runtime/package.json', b'{"name":"openclaw","version":"2026.6.33"}', 0o644)
    (root / 'runtime').chmod(0o755)
    (root / 'plugins').mkdir(mode=0o755)
    for i, name in enumerate(planner.DEPLOYMENT_PLUGINS):
        directory = root / 'plugins' / str(i)
        directory.mkdir(mode=0o755)
        private(directory / 'openclaw.plugin.json', json.dumps({'id': name}).encode(), 0o644)
        private(directory / 'index.js', b'// not executed\n', 0o644)
    manifest = {'schemaVersion': 1, 'openclawVersion': '2026.6.33',
                'plugins': ['plugins/0', 'plugins/1', 'plugins/2'], 'entries': bundle_module.inventory(root)}
    raw = bundle_module._encode(manifest)
    private(root / 'bundle.json', raw, 0o644)
    destination = parent / hashlib.sha256(raw).hexdigest()
    root.rename(destination)
    root = destination
    source = tmp_path / 'source'
    private(source / 'installers/macos/lib/pixel-runtime-bundle.py', bundle_helper.read_bytes(), 0o644)
    python = private(tmp_path / 'python', b'not executed\n', 0o755)
    launcher = private(tmp_path / 'launcher', ('#!/bin/sh\nexec /usr/bin/env -u NODE_OPTIONS -u NODE_PATH '
        + str(root / 'node') + ' ' + str(root / 'runtime') + '/openclaw.mjs "$@"\n').encode(), 0o755)
    runtime = {'schemaVersion': 1, 'runtimeRoot': str(root / 'runtime'), 'sourceRoot': str(source),
        'node': str(root / 'node'), 'launcher': str(launcher), 'hostPython': str(python),
        'manifest': str(tmp_path / 'runtime-manifest.json'), 'manifestSha256': None,
        'policy': custody.required_policy()}
    record = {'runtime': custody.tree_manifest(root / 'runtime'), 'source': custody.tree_manifest(source),
        'node': custody._regular(root / 'node'), 'launcher': custody._regular(launcher), 'hostPython': custody._regular(python)}
    private(Path(runtime['manifest']), (json.dumps(record) + '\n').encode())
    runtime['manifestSha256'] = hashlib.sha256(Path(runtime['manifest']).read_bytes()).hexdigest()
    descriptor = private(tmp_path / 'descriptor.json', json.dumps(runtime).encode())
    monkeypatch.setattr(custody, 'DESCRIPTOR', descriptor)
    home = tmp_path / 'home'
    home.mkdir(mode=0o700)
    state = home / '.openclaw'
    state.mkdir(mode=0o700)
    config = private(tmp_path / 'config/openclaw.json', json.dumps(disconnected_config()).encode())
    env = private(config.parent / 'gateway.env', b"PIXEL_LIMB_EMAIL_ENABLED='0'\nPIXEL_LIMB_CALENDAR_ENABLED='0'\n"
        b"PIXEL_LIMB_CALENDAR_WRITE_ENABLED='0'\nPIXEL_CALENDAR_DIRECT_ENABLED='0'\n")
    answers = private(config.parent / 'onboarding.json', b'{}')
    user, group = pwd.getpwuid(uid).pw_name, grp.getgrgid(gid).gr_name
    environment = {'HOME': str(home), 'OPENCLAW_CONFIG_PATH': str(config), 'OPENCLAW_STATE_DIR': str(state),
                   'PATH': str(root) + ':/usr/bin:/bin'}
    doc = {'Label': 'com.ods.fixture', 'UserName': user, 'GroupName': group,
           'ProgramArguments': ['/usr/bin/env', '-i', *[k + '=' + v for k, v in environment.items()],
                                str(root / 'node'), str(root / 'runtime/openclaw.mjs')]}
    plist = private(tmp_path / 'gateway.plist', plistlib.dumps(doc), 0o644)
    bridge = object.__new__(bridge_module.LaunchdAccessBridge)
    bridge.owner = SimpleNamespace(pw_uid=uid, pw_gid=gid, pw_name=user)
    bridge.home, bridge.binary, bridge._runtime_config_path = home, str(launcher), config
    bridge.gateway_owner, bridge.gateway_target, bridge.gateway_plist = user, 'system/com.ods.fixture', plist
    bridge.gateway_process = {'uid': uid, 'gid': gid, 'executable': str(root / 'node')}
    bridge.verify_gateway_installation_binding = lambda: None  # Loaded process is outside this cold fixture.
    bridge.install = tmp_path / 'ods'
    selected = bridge.connection_deployment()
    return SimpleNamespace(bridge=bridge, value=selected, root=root, source=source, config=config, env=env,
        answers=answers, home=home, state=state, environment=environment, plist=plist, metadata_tree=metadata_tree)


def verify(f):
    return planner.require_native_deployment(f.value.document, uid=os.getuid(), gid=os.getgid(),
                                             home=str(f.home), config_path=str(f.config))


def test_root_and_owner_rederive_same_immutable_paths_from_actual_inventories(deployment):
    f = deployment
    assert verify(f) == f.value == f.bridge.connection_deployment()
    data = f.value.document
    data['pluginPaths']['pixel-source-broker'] = '/an/owner/choice'
    assert verify(f) == f.value
    planned = planner.plan_config(disconnected_config(), gateway({'gmail.read': True}), install_dir=None,
                                   plugin_paths=f.value.document['pluginPaths'])
    assert str(f.root / 'plugins/0') in planned['plugins']['load']['paths']
    assert not any('/current/plugin' in path and path.endswith('/plugin') for path in planned['plugins']['load']['paths'])


@pytest.mark.parametrize('change', ['uid', 'gid', 'config', 'source', 'runtime', 'plugin', 'plist', 'missing-env', 'missing-answers'])
def test_current_custody_or_identity_drift_refuses_before_owner_state_write(deployment, tmp_path, change):
    f = deployment
    before = {p: p.read_bytes() for p in (f.config, f.env, f.answers)}
    if change in ('uid', 'gid', 'config'):
        doc = f.value.document
        doc[{'uid':'ownerUid', 'gid':'ownerGid', 'config':'configPath'}[change]] = (
            os.getuid() + 1 if change == 'uid' else os.getgid() + 1 if change == 'gid' else str(tmp_path / 'wrong/openclaw.json'))
        if change == 'config':
            doc['gatewayEnvPath'], doc['answersPath'] = str(tmp_path / 'wrong/gateway.env'), str(tmp_path / 'wrong/onboarding.json')
        f.value = planner.ConnectionDeployment.parse(doc)
    elif change == 'source':
        with (f.source / 'installers/macos/lib/pixel-runtime-bundle.py').open('ab') as out:
            out.write(b'\n# changed\n')
    elif change == 'runtime':
        (f.root / 'runtime/openclaw.mjs').write_bytes(b'changed')
    elif change == 'plugin':
        (f.root / 'plugins/0/index.js').write_bytes(b'changed')
    elif change == 'plist':
        f.plist.write_bytes(f.plist.read_bytes() + b'\n')
    else:
        (f.env if change == 'missing-env' else f.answers).unlink()
        # A Linux-looking alternate must not be adopted.
        private(f.home / '.config/pixel-agent/gateway.env', b'not the selected file')
    state_dir = tmp_path / 'must-not-exist'
    with pytest.raises((bridge_module.AccessError, ValueError, OSError)):
        transaction.connection_status(str(f.config), state_dir=str(state_dir), env_path=str(f.env),
            answers_path=str(f.answers), deployment=f.value, verify_deployment=lambda: verify(f))
    assert not state_dir.exists()
    for path, data in before.items():
        if path.exists():
            assert path.read_bytes() == data


def worker_main():
    # Execute the actual function; the protected installed-import bootstrap is
    # outside this non-root fixture and is not claimed as qualified here.
    tree = ast.parse((HOST / 'access_mode_worker.py').read_text())
    definition = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
    scope = {'os': os, 'platform': platform, 'protocol': protocol, 'controller': controller,
             'sys': sys, 'json': json, 'subprocess': __import__('subprocess')}
    output = []
    scope['emit'] = output.append
    exec(compile(ast.Module(body=[definition], type_ignores=[]), 'actual-worker-main', 'exec'), scope)
    return scope['main'], output


def test_actual_owner_worker_receives_root_paths_and_refuses_a_missing_publication(deployment, monkeypatch):
    f = deployment
    monkeypatch.setattr(platform, 'system', lambda: 'Darwin')
    # This preexisting owner/worker control now uses Darwin's nonsecret Root644
    # metadata contract. Only Root UID/GID are simulated; inspect/reader/hash,
    # native deployment comparison and owner-private state checks remain real.
    descriptor = custody.DESCRIPTOR
    runtime = json.loads(descriptor.read_bytes())
    manifest = Path(runtime['manifest'])
    for path in (descriptor, manifest):
        path.chmod(0o644)
    monkeypatch.setattr(custody, 'NATIVE_MANIFEST', manifest)
    monkeypatch.setattr(custody, 'ROOT_UID', 0)
    code = {f.root, Path(runtime['sourceRoot'])}
    code.update(path for root in tuple(code) for path in root.rglob('*'))
    code.update((descriptor, manifest, f.plist, Path(runtime['launcher']), Path(runtime['hostPython'])))
    identities = {(path.stat().st_dev, path.stat().st_ino) for path in code}
    original_stat, original_lstat, original_fstat = os.stat, os.lstat, os.fstat
    def root_metadata(info):
        if (info.st_dev, info.st_ino) not in identities:
            return info
        fields = {name: getattr(info, name) for name in dir(info) if name.startswith('st_')}
        return SimpleNamespace(**dict(fields, st_uid=0, st_gid=0))
    monkeypatch.setattr(os, 'stat', lambda *args, **kwargs: root_metadata(original_stat(*args, **kwargs)))
    monkeypatch.setattr(os, 'lstat', lambda *args, **kwargs: root_metadata(original_lstat(*args, **kwargs)))
    monkeypatch.setattr(os, 'fstat', lambda *args, **kwargs: root_metadata(original_fstat(*args, **kwargs)))
    monkeypatch.setenv('HOME', str(f.home))
    monkeypatch.setenv('OPENCLAW_CONFIG_PATH', str(f.config))
    monkeypatch.setattr(controller, '_default_state_dir', lambda: str(f.home / 'absent-legacy'))
    request = {'operation':'connection-owner-status', 'openclaw':f.value.document['entrypointPath'],
        'config_sha256':None, 'confirmed':False, 'answers_path':str(f.answers), 'connection_deployment':f.value.document}
    assert protocol.request(request) is request
    main, out = worker_main()
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(request) + '\n'))
    main()
    assert 'result' in out[0]
    state_dir = f.config.parent / '.ods-access-mode'
    assert state_dir.is_dir()
    f.env.unlink()
    before = {p.name: p.read_bytes() for p in state_dir.iterdir() if p.is_file()}
    main, out = worker_main()
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(request) + '\n'))
    main()
    assert out == [{'error':'connection-deployment-unavailable'}]
    assert {p.name:p.read_bytes() for p in state_dir.iterdir() if p.is_file()} == before


def test_native_journal_binds_deployment_and_changed_source_cannot_roll_it_back(deployment, tmp_path, monkeypatch):
    f = deployment
    monkeypatch.setattr(transaction, '_plugin_ready', lambda *args, **kwargs: True)
    state_dir = str(tmp_path / 'transaction')
    def checksum():
        return hashlib.sha256(f.config.read_bytes()).hexdigest()
    kwargs = dict(state_dir=state_dir, install_dir=None, openclaw=f.value.document['entrypointPath'],
        transaction_id='a'*64, expected_config_sha256=checksum(), validate_config=lambda staged: True,
        check_no_active_run=lambda: False, activate=lambda: 'unavailable', deployment=f.value,
        verify_deployment=lambda: verify(f))
    with pytest.raises(transaction.ConnectionTransactionError, match='activation-unavailable'):
        transaction.apply_connection(str(f.config), gateway({'gmail.read':True}),
            state.connection_answers(None, google_custody=False), env_path=str(f.env), answers_path=str(f.answers), **kwargs)
    journal = Path(state_dir) / transaction.JOURNAL
    saved = journal.read_bytes()
    assert json.loads(saved)['deploymentSha256'] == f.value.checksum
    before = f.config.read_bytes()
    wrong = json.loads(saved)
    wrong['deploymentSha256'] = 'f'*64
    journal.write_text(json.dumps(wrong))
    kwargs['expected_config_sha256'] = checksum()
    with pytest.raises(transaction.ConnectionTransactionError, match='deployment-changed'):
        transaction.recover_connection(str(f.config), **kwargs)
    assert f.config.read_bytes() == before
    journal.write_bytes(saved)
    with (f.source / 'installers/macos/lib/pixel-runtime-bundle.py').open('ab') as out:
        out.write(b'\n# later source\n')
    kwargs['expected_config_sha256'] = checksum()
    with pytest.raises(bridge_module.AccessError, match='custody-unqualified'):
        transaction.recover_connection(str(f.config), **kwargs)
    assert journal.read_bytes() == saved and f.config.read_bytes() == before


def test_source_drift_during_staging_refuses_before_journal_or_owner_file_replacement(deployment, tmp_path):
    f = deployment
    before = {p:p.read_bytes() for p in (f.config,f.env,f.answers)}
    def stage_then_drift(path):
        assert Path(path).is_file()
        with (f.source / 'installers/macos/lib/pixel-runtime-bundle.py').open('ab') as out:
            out.write(b'\n# drift\n')
        return True
    state_dir = tmp_path / 'transaction'
    with pytest.raises(controller.AccessModeRejected, match='live run check failed'):
        transaction.apply_connection(str(f.config), gateway({'gmail.read':True}),
            state.connection_answers(None,google_custody=False), state_dir=str(state_dir),
            env_path=str(f.env), answers_path=str(f.answers), install_dir=None,
            openclaw=f.value.document['entrypointPath'], transaction_id='b'*64,
            expected_config_sha256=hashlib.sha256(f.config.read_bytes()).hexdigest(), validate_config=stage_then_drift,
            check_no_active_run=lambda:False, activate=lambda:'verified', deployment=f.value, verify_deployment=lambda:verify(f))
    assert not (state_dir / transaction.JOURNAL).exists()
    assert not (state_dir / transaction.BACKUP).exists()
    assert {p:p.read_bytes() for p in before} == before


def test_linux_private_request_and_path_defaults_keep_exact_grammar(monkeypatch):
    monkeypatch.setattr(platform, 'system', lambda: 'Linux')
    bridge = SimpleNamespace(home=Path('/home/owner'), install=Path('/opt/ods'))
    paths = bridge_module.connection_paths(bridge)
    assert paths == {'env':Path('/home/owner/.config/pixel-agent/gateway.env'),
        'answers':Path('/opt/ods/data/pixel/onboarding.json'), 'plan':{'install_dir':INSTALL}, 'deployment':None}
    request = {'operation':'connection-owner-status', 'openclaw':'/usr/bin/openclaw',
               'config_sha256':None, 'confirmed':False, 'answers_path':str(paths['answers'])}
    assert set(protocol.request(request)) == protocol.KEYS['connection-owner-status']
    assert 'connection_deployment' not in json.dumps(request)
    assert planner.plan_config(disconnected_config(), gateway({'gmail.read':True}), **paths['plan']) == planner.plan_config(
        disconnected_config(), gateway({'gmail.read':True}), install_dir=INSTALL)
    root = object.__new__(bridge_module.SystemdAccessBridge)
    root.owner = SimpleNamespace(pw_name='owner')
    root.home, root.install, root.binary = bridge.home, bridge.install, '/usr/bin/openclaw'
    captured = []
    class Captured(Exception):
        pass
    def capture(value):
        captured.append(value)
        raise Captured()
    monkeypatch.setattr(protocol,'request',capture)
    with pytest.raises(Captured):
        root.worker('connection-owner-status')
    assert captured == [request]


def test_fixture_protected_reader_keeps_production_keyword_limit(deployment):
    path = deployment.root / 'runtime/openclaw.mjs'
    body = path.read_bytes()
    assert mac_custody.protected_bytes(path, limit=len(body)) == body
    with pytest.raises(bridge_module.AccessError, match='file-too-large'):
        mac_custody.protected_bytes(path, limit=len(body) - 1)
    for limit in (True, 0, -1, '1'):
        with pytest.raises(mac_custody.CustodyError, match='path-invalid'):
            mac_custody.protected_bytes(path, limit=limit)


def test_metadata_gate_uses_real_traversal_without_reading_content(deployment, monkeypatch):
    driver = deployment.metadata_tree
    (deployment.root / 'internal-link').symlink_to('runtime/openclaw.mjs')
    monkeypatch.setattr(custody, 'tree_manifest', lambda *_args, **_kw: pytest.fail('metadata gate hashed content'))
    monkeypatch.setattr(driver.module.os, 'read', lambda *_args: pytest.fail('metadata gate read content'))
    monkeypatch.setattr(driver.module.os, 'fdopen', lambda *_args, **_kw: pytest.fail('metadata gate read content'))
    driver.check(deployment.root)


@pytest.mark.parametrize('change', ['mode', 'directory-mode', 'owner', 'acl', 'hardlink', 'outside-link', 'absolute-link'])
def test_metadata_driver_keeps_root_fd_refusals(deployment, monkeypatch, change):
    f = deployment
    path = f.root / 'runtime/openclaw.mjs'
    if change == 'mode': path.chmod(0o666)
    elif change == 'directory-mode': path.parent.chmod(0o777)
    elif change == 'owner':
        info = path.stat()
        f.metadata_tree.owners[(info.st_dev, info.st_ino)] = os.getuid() + 1
    elif change == 'acl':
        selected = (path.stat().st_dev, path.stat().st_ino)
        def acl(fd):
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) == selected:
                raise mac_custody.CustodyError('fixture-acl-present')
        monkeypatch.setattr(custody, '_acl', acl)
    elif change == 'hardlink': os.link(path, path.with_suffix('.alias'))
    elif change == 'outside-link': (f.root / 'escaped').symlink_to('../../source')
    else: (f.root / 'absolute').symlink_to(path)
    with pytest.raises((mac_custody.CustodyError, bridge_module.AccessError)):
        f.metadata_tree.check(f.root)


@pytest.mark.parametrize('change', ['same-size-mtime', 'inode-body', 'namespace', 'size'])
def test_metadata_only_gate_does_not_replace_runtime_or_bundle_hashes(deployment, change):
    f = deployment
    with f.bridge._native_runtime_operation():
        f.bridge.connection_deployment()
        path = f.root / 'runtime/openclaw.mjs'
        if change == 'same-size-mtime':
            before = path.stat()
            body = path.read_bytes()
            path.write_bytes(bytes([body[0] ^ 1]) + body[1:])
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        elif change == 'inode-body':
            pending = path.with_suffix('.next')
            pending.write_bytes(b'changed executable body')
            pending.chmod(0o644)
            os.replace(pending, path)
        elif change == 'namespace': (f.root / 'unexpected.js').write_bytes(b'pass')
        else: path.write_bytes(path.read_bytes() + b'changed-size')
        # Valid metadata cannot confer content authority. The actual complete
        # deployment still rejects the changed hash/inventory on this same tree.
        f.metadata_tree.check(f.root)
        with pytest.raises((bridge_module.AccessError, ValueError)):
            f.bridge.connection_deployment()
