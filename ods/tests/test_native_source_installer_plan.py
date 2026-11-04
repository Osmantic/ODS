"""Normal plan production binds the canonical Source family before execution.

The existing Core/service migration qualification is a fixed test boundary.
Bundle verification, make_plan/make_migration_plan, owner input custody and
the complete Source family/intent rendering are actual production code.
No Root context, private state, account or service is created.
"""
# ruff: noqa: E402,F401,F811
import hashlib
import json
import os
from pathlib import Path
import plistlib
import sys
from types import SimpleNamespace

import pytest

if sys.platform != 'linux':
    pytest.skip('Linux canonical bundle driver, not physical Darwin', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_source_runtime_publication import built, source, package, services, runtime_custody
from test_macos_pixel_access_install import deployment, installer
from pixel_connections import custody


@pytest.mark.parametrize('existing_source', [False, True])
def test_normal_migration_plan_binds_actual_source_family_without_creating_authority(built, deployment, monkeypatch, existing_source):
    args = deployment
    root = Path(args['install_dir'])
    owner = installer.pwd.getpwnam(args['owner_name'])
    # Only Root ownership is relocated; modes, hashes, exact inventories and
    # immutable Source/component verification remain the real implementations.
    monkeypatch.setattr(runtime_custody, 'ROOT_UID', os.getuid())
    monkeypatch.setattr(installer, 'RUNTIME_CONFIG_ROOT', root / 'owner-configs')
    monkeypatch.setattr(installer._launchd, 'GATEWAY_PLIST', Path(args['source_plist']))
    source_path = Path(args['source_plist'])
    bind = installer._launchd.binding
    monkeypatch.setattr(installer._launchd, 'binding', lambda document, **kwargs:
        bind(document, plist=source_path, **kwargs))
    document = plistlib.loads(source_path.read_bytes())
    environment, _command = installer._env_assignments(document['ProgramArguments'])
    config_path = Path(environment['OPENCLAW_CONFIG_PATH'])
    manifest = json.loads((built.root / 'bundle.json').read_bytes())
    original = json.dumps({'gateway': {'auth': {'token': 'invented-plan-token'}},
        'plugins': {'allow': ['pixel-ods'],
                    'load': {'paths': [str(built.root / relative) for relative in manifest['plugins']]},
                    'entries': {'pixel-ods': {'enabled': True}}}}).encode()
    config_path.write_bytes(original)
    config_path.chmod(0o600)
    sidefiles = {'gateway.env': b'PIXEL_EMAIL_INBOX=0\n', 'onboarding.json': b'{}\n'}
    for name, body in sidefiles.items():
        path = config_path.with_name(name)
        path.write_bytes(body)
        path.chmod(0o600)
    staged = {'status': 'staged', 'pixelSourceRef': built.package[1],
        'connectionSidefiles': {name: hashlib.sha256(body).hexdigest() for name, body in sidefiles.items()}}
    config_path.with_name('candidate.json').write_text(json.dumps(staged))
    config_path.with_name('candidate.json').chmod(0o600)
    runtime = installer._bundle_plan(built.root, built.digest, environment, owner)
    selected = {'currentDigest': 'c' * 64, 'runtime': runtime,
        'preservation': {'stateDir': environment['OPENCLAW_STATE_DIR']},
        'previousConfig': str(config_path), 'previousConfigBytes': original}
    monkeypatch.setattr(installer, 'qualify_migration_selection', lambda **_kwargs: selected)
    monkeypatch.setattr(installer, '_bind_services', lambda *_args, **_kwargs: None)
    select_helper = installer._native_services.helper
    def helpers(name):
        # Load the actual declared peer before substituting this OS interpreter
        # observation; a nonexistent helper name must fail this normal plan.
        loaded = select_helper(name)
        if name == 'ops-service':
            monkeypatch.setattr(loaded, 'select_python', lambda: built.python)
        return loaded
    monkeypatch.setattr(installer._native_services, 'helper', helpers)
    state = root / 'hypothetical-private-source'
    if existing_source:
        state.mkdir(mode=0o700)
    monkeypatch.setattr(custody, 'NATIVE_STATE', state)
    before = {p.name: (p.read_bytes(), p.stat().st_ino) for p in
        (source_path, config_path, config_path.with_name('candidate.json'),
         *(config_path.with_name(name) for name in sidefiles))}
    plan = installer.make_migration_plan(install_dir=args['install_dir'], owner_name=args['owner_name'],
        openclaw_bin=args['openclaw_bin'], gateway_port=args['gateway_port'], candidate=root,
        runtime_bundle=built.root, bundle_digest=built.digest, current_digest='c' * 64,
        services_bundle=root, services_digest='d' * 64, source_ref=built.package[1])
    intent = plan['source_lifecycle']
    assert intent['mode'] == ('withdrawal' if existing_source else 'absent')
    assert intent['currentDigest'] == 'c' * 64 and intent['candidateDigest'] == built.digest
    assert (intent['ownerUid'], intent['ownerGid']) == (owner.pw_uid, owner.pw_gid)
    family = installer._source_family_files(plan)
    records = [dict(path=str(path), before=None, after=body, mode=attrs['mode'], gid=attrs['gid'])
        for path, body, attrs in family]
    assert {row['path'] for row in records} == set(installer._upgrade.SOURCE_ADDITIONS)
    assert intent['familySha256'] == installer._upgrade.source_family_hash(records)
    assert {p.name: (p.read_bytes(), p.stat().st_ino) for p in
        (source_path, config_path, config_path.with_name('candidate.json'),
         *(config_path.with_name(name) for name in sidefiles))} == before
    assert state.exists() is existing_source
    assert not Path(plan['runtime_bundle']['config_path']).exists()
    assert 'source_sidefiles_before' not in plan['runtime_bundle']  # Actual old bridge must still capture it.
