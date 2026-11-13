"""Six inert plist plans and the actual refusing CLI; never launchctl/jobs."""
import importlib.util
import os
from pathlib import Path
import plistlib
import subprocess
import sys

import pytest

if sys.platform == 'win32':
    pytest.skip('POSIX private CLI fixture', allow_module_level=True)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bin'))
from pixel_connections import custody  # noqa: E402

spec = importlib.util.spec_from_file_location('disabled_source_definitions',
    ROOT / 'installers/macos/lib/pixel-native-source-broker.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
IDENTITY = {'schema': 1, 'name': '_ods_pixel_sources', 'id': 60001,
    'userGuid': '01234567-89AB-4DEF-8123-0123456789AB',
    'groupGuid': '11234567-89AB-4DEF-8123-0123456789AB'}
CASES = [('sync', None, ()), ('direct', None, ('--drain-direct',)),
    ('action', 'calendar-1893456789012-12abcdef', ('--approve', 'calendar-1893456789012-12abcdef')),
    ('reconcile', 'gmail-draft-1893456789012-34abcdef', ('--reconcile', 'gmail-draft-1893456789012-34abcdef')),
    ('exchange', 'abcdef0123456789abcdef0123456789', ('--exchange', 'abcdef0123456789abcdef0123456789')),
    ('revoke', None, ('--revoke',))]


@pytest.mark.parametrize('role,identifier,arguments', CASES)
def test_all_six_role_definitions_are_disabled_without_any_automatic_trigger(role, identifier, arguments):
    path, body, mode = helper.disabled_definition(role, identity=IDENTITY,
        python='/usr/bin/python3', identifier=identifier)
    value = plistlib.loads(body)
    label = 'com.ods.pixel-native-source-' + role + ('.' + identifier if identifier else '')
    assert str(path) == '/Library/LaunchDaemons/' + label + '.plist' and mode == 0o644
    assert value['Label'] == label
    assert value['Disabled'] is True and value['RunAtLoad'] is False and value['KeepAlive'] is False
    assert not {'StartInterval', 'StartCalendarInterval', 'WatchPaths', 'QueueDirectories'} & set(value)
    assert value['UserName'] == value['GroupName'] == '_ods_pixel_sources'
    assert value['InitGroups'] is False and value['AbandonProcessGroup'] is False
    assert value['Umask'] == 63 and value['ExitTimeOut'] == 5
    assert value['ProgramArguments'] == ['/usr/bin/env', '-i', 'HOME=/private/var/empty',
        'USER=_ods_pixel_sources', 'LOGNAME=_ods_pixel_sources', 'PATH=/usr/bin:/bin:/usr/sbin:/sbin',
        'PYTHONDONTWRITEBYTECODE=1', 'PYTHONNOUSERSITE=1', '/usr/bin/python3', '-I', '-S', '-B',
        '/usr/local/libexec/ods-pixel-access/pixel_connections/custody.py',
        '--native-broker-child', role, *([identifier] if identifier else [])]
    assert value['StandardOutPath'] == '/private/var/log/pixel-source-broker/' + role + '.stdout.log'
    assert value['StandardErrorPath'] == '/private/var/log/pixel-source-broker/' + role + '.stderr.log'
    assert custody.native_child_arguments(role, identifier) == arguments
    assert 'EnvironmentVariables' not in value
    assert helper.disabled_definition(role, identity=IDENTITY, python='/usr/bin/python3', identifier=identifier)[1] == body


def test_existing_source_command_grammar_is_not_broadened_for_renderer_inputs():
    with pytest.raises(custody.CustodyError):
        helper.disabled_definition('action', identity=IDENTITY, python='/usr/bin/python3', identifier='flow-id-is-not-an-action')
    with pytest.raises(custody.CustodyError):
        helper.disabled_definition('sync', identity=IDENTITY, python='/usr/bin/python3', identifier='unexpected')
    with pytest.raises(custody.CustodyError):
        helper.disabled_definition('unknown', identity=IDENTITY, python='/usr/bin/python3')
    assert custody.NATIVE_CHILD_BUDGETS == {'sync': 300, 'direct': 90, 'action': 120,
        'reconcile': 120, 'exchange': 120, 'revoke': 60}


@pytest.mark.parametrize('role,identifier,_arguments', CASES)
def test_actual_private_cli_refuses_before_reading_any_owner_or_root_state(tmp_path, role, identifier, _arguments):
    command = [sys.executable, '-I', '-S', '-B', str(ROOT / 'bin/pixel_connections/custody.py'),
        '--native-broker-child', role, *([identifier] if identifier else [])]
    result = subprocess.run(command, cwd=tmp_path, env={'HOME': str(tmp_path),
        'XDG_CONFIG_HOME': str(tmp_path / 'absent'), 'PATH': os.defpath},
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5)
    assert result.returncode == 2
    expected = 'native-source-bootstrap-isolation-required' if sys.platform == 'darwin' else 'native-source-broker-platform-required'
    assert result.stdout == '' and result.stderr == 'error: ' + expected + '\n'
    assert list(tmp_path.iterdir()) == []


def test_generation_refusal_does_not_mint_admission_or_invoke_root_qualifiers(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('No generation means no Root/owner I/O or dispatch')
    monkeypatch.setattr(custody, '_native_selected', forbidden)
    monkeypatch.setattr(custody, '_native_environment_context', forbidden)
    monkeypatch.setattr(custody.subprocess, 'Popen', forbidden)
    expected = 'native-source-bootstrap-isolation-required' if sys.platform == 'darwin' else 'native-source-broker-platform-required'
    with pytest.raises(custody.CustodyError, match=expected):
        custody._native_child_main(['--native-broker-child', 'sync'])
