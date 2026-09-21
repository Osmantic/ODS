"""Fixed native endpoints must agree without accepting request-selected paths."""
import importlib.util
import json
import os
import plistlib
from pathlib import Path
import pwd
import subprocess
import sys

import pytest


HOST = Path(__file__).resolve().parents[1] / 'host'


@pytest.mark.parametrize('platform', ['darwin', 'linux'])
def test_native_host_endpoints_and_broker_identity(monkeypatch, platform):
    monkeypatch.setattr(sys, 'platform', platform)
    modules = {}
    for name in ('extension_manager', 'artifact_promoter', 'extension_search'):
        spec = importlib.util.spec_from_file_location('platform_' + name, HOST / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules[name] = module
    prefix = '/private/var' if platform == 'darwin' else ''
    state = '/private/var/lib' if platform == 'darwin' else '/var/lib'
    manager, promoter = modules['extension_manager'], modules['artifact_promoter']
    assert manager.BROKER_USER == promoter.BROKER_USER == ('_ods_pixel_ops' if platform == 'darwin' else 'pixel-ops-broker')
    manager_root = '/private/var/lib' if platform == 'darwin' else '/run'
    assert manager.SOCKET_PATH == Path(manager_root + '/ods-pixel-manager/extension-manager.sock')
    assert promoter.SOCKET_PATH == Path(manager_root + '/ods-pixel-artifact-promoter/promoter.sock')
    assert manager.OPS_RESULTS_DIR == promoter.RESULTS_ROOT == Path(state + '/pixel-ops-broker/results')
    expected_catalog = ('/usr/local/libexec/ods-pixel-services/helpers/extension-catalog.json'
        if platform == 'darwin' else '/opt/pixel-ops-broker/ods-extension-catalog.json')
    assert modules['extension_search'].CATALOG_PATH == Path(expected_catalog)
    with pytest.raises(modules['extension_search'].CatalogError):
        modules['extension_search'].main(['/tmp/arbitrary.json', 'all'])
    with pytest.raises(manager.ManagerError):
        manager.client(Path('/tmp/arbitrary.sock'), 'list', 'all')
    with pytest.raises(promoter.PromotionError):
        promoter.serve(Path('/tmp/arbitrary.sock'), promoter.RESULTS_ROOT,
                       promoter.ARTIFACTS_ROOT, Path('/tmp/workspace'), 'fixture')


@pytest.mark.skipif(sys.platform != 'darwin', reason='actual native policy generation')
def test_shared_policy_writer_matches_native_manager_and_quarantine(tmp_path):
    ods = HOST.parents[3]
    home = tmp_path / 'Owner Home'
    home.mkdir(mode=0o700)
    policy = home / 'policy.json'
    command = ('source "$1"\n'
               'ods_pixel_run_as_owner() { shift 2; "$@"; }\n'
               'INSTALL_DIR="$2"\n'
               '_ods_pixel_write_operations_policy "$3" "$4" "$5"\n')
    result = subprocess.run(['/bin/bash', '-c', command, 'policy-test',
        str(ods / 'installers/lib/pixel-host-install.sh'), str(ods),
        pwd.getpwuid(os.getuid()).pw_name, str(home), str(policy)],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    value = json.loads(policy.read_text())
    assert value['download']['stagingRoot'] == '/private/var/lib/pixel-ops-broker/artifacts'
    assert value['targets']['ods-host']['defaultCwd'] == '/private/var/lib/pixel-ops-broker'
    encoded = json.dumps(value)
    assert '/private/var/lib/ods-pixel-manager/extension-manager.sock' in encoded
    spec = importlib.util.spec_from_file_location('native_manager_publication',
        ods / 'installers/macos/lib/pixel-native-manager-service.py')
    native_manager = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native_manager)
    rendered = native_manager.render(python='/usr/bin/python3',
        program_root='/usr/local/libexec/ods-pixel-services/manager',
        environment=str(home / '.env'), owner=pwd.getpwuid(os.getuid()).pw_name, port=3002)
    arguments = plistlib.loads(rendered['plist'])['ProgramArguments']
    serve = arguments.index('serve')
    for action in ('list', 'inspect', 'install', 'enable', 'disable', 'remove'):
        command = value['actions']['ods.extensions.' + action]['argv']
        assert command[1] == arguments[serve - 1]
        assert command[2:4] == ['client', arguments[serve + 1]]
    assert '/usr/local/libexec/ods-pixel-extension-manager.py' not in encoded
    search = value['actions']['ods.extensions.search']['argv']
    helper_root = '/usr/local/libexec/ods-pixel-services/helpers/'
    assert search[1:3] == [helper_root + 'extension_search.py', helper_root + 'extension-catalog.json']
    assert value['actions']['host.gpu']['argv'][1] == helper_root + 'system_observe.py'
    assert '/opt/pixel-ops-broker/' not in encoded
