"""Snapshot identity matches the live protected-file definition contract."""
import copy
import hashlib
from pathlib import Path
import plistlib
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
from pixel_gateway_service import LaunchdGatewayService, launchd_definition_digest
import pixel_macos_custody as custody


def document():
    return dict(Label='com.ods.test', UserName='owner',
        ProgramArguments=['/usr/bin/env', '-i', 'HOME=/Users/owner', '/trusted/node', '/trusted/main.mjs'],
        RunAtLoad=True)


def test_snapshot_matches_protected_live_definition(monkeypatch):
    value = document()
    original = copy.deepcopy(value)
    monkeypatch.setattr(custody, 'protected_bytes', lambda path: plistlib.dumps(value))
    service = LaunchdGatewayService(lambda *a: None, ValueError, 'system/com.ods.test',
                                    lambda: None, plist=Path('/trusted/service.plist'))
    expected = hashlib.sha256(plistlib.dumps(value, fmt=plistlib.FMT_BINARY, sort_keys=True)).hexdigest()
    assert service.definition() == launchd_definition_digest(value, ValueError) == expected
    assert value == original


@pytest.mark.parametrize('field', ['node', 'entrypoint', 'owner', 'label', 'home', 'keepalive'])
def test_unmanaged_changes_remain_bound(field):
    value = document()
    baseline = launchd_definition_digest(value, ValueError)
    if field == 'node': value['ProgramArguments'][-2] = '/other/node'
    elif field == 'entrypoint': value['ProgramArguments'][-1] = '/other/main.mjs'
    elif field == 'owner': value['UserName'] = 'other'
    elif field == 'label': value['Label'] = 'com.ods.other'
    elif field == 'home': value['ProgramArguments'][2] = 'HOME=/Users/other'
    else: value['KeepAlive'] = True
    assert launchd_definition_digest(value, ValueError) != baseline


@pytest.mark.parametrize('name', ['OPENCLAW_REQUIRED_PLUGINS', 'PIXEL_ODS_PROVIDER_DEPLOYMENT'])
def test_only_managed_env_assignments_are_excluded(name):
    value = document()
    baseline = launchd_definition_digest(value, ValueError)
    value['ProgramArguments'].insert(2, name + '=changed')
    assert launchd_definition_digest(value, ValueError) == baseline
    value = document()
    value['ProgramArguments'].append(name + '=command-argument')
    assert launchd_definition_digest(value, ValueError) != baseline
    value = document()
    value['EnvironmentVariables'] = {name: 'changed'}
    with pytest.raises(ValueError): launchd_definition_digest(value, ValueError)


@pytest.mark.parametrize('value', [None, [], {}, {'ProgramArguments': ['/bin/sh']},
    {'ProgramArguments': ['/usr/bin/env', '-i', 'HOME=/Users/owner']}])
def test_invalid_snapshots_refused(value):
    with pytest.raises(ValueError): launchd_definition_digest(value, ValueError)


def test_access_coordinator_direct_python_definition_is_fully_bound(monkeypatch):
    value = dict(Label='com.ods.pixel-access', ProgramArguments=[
        '/usr/bin/python3', '-I', '/usr/local/libexec/ods-pixel-access/access_mode_server.py'])
    monkeypatch.setattr(custody, 'protected_bytes', lambda path: plistlib.dumps(value))
    service = LaunchdGatewayService(lambda *a: None, ValueError, 'system/com.ods.pixel-access',
        lambda: None, plist=Path('/Library/LaunchDaemons/com.ods.pixel-access.plist'))
    baseline = service.definition()
    assert baseline == launchd_definition_digest(value, ValueError)
    value['EnvironmentVariables'] = {'PIXEL_ODS_PROVIDER_DEPLOYMENT': 'changed'}
    assert service.definition() != baseline
    value['ProgramArguments'].remove('-I')
    with pytest.raises(ValueError): service.definition()
