import importlib.util
from pathlib import Path
import plistlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

spec = importlib.util.spec_from_file_location('apps', Path(__file__).resolve().parents[1] / 'bin/pixel_macos_apps.py')
apps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(apps)


@pytest.fixture
def launch(tmp_path, monkeypatch):
    app = tmp_path.resolve() / 'Calculator.app'
    (app / 'Contents').mkdir(parents=True)
    (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': 'com.apple.calculator'}))
    monkeypatch.setattr(apps.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(apps.os, 'getuid', lambda: 501)
    original = apps.os.stat
    monkeypatch.setattr(apps.os, 'stat', lambda path, *a, **k:
        SimpleNamespace(st_uid=501) if str(path) == '/dev/console' else original(path, *a, **k))
    run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(apps.subprocess, 'run', run)
    status = dict(scope='owner-host', available=True, pending=False, configured_mode='full-access',
                  effective_mode='full-access', runtime_verified=True, reason=None)
    def call(request=None):
        return apps.launch_application(request or {'bundleId': 'com.apple.calculator'},
            approved_apps={'com.apple.calculator': str(app)}, access_status=lambda: status)
    return call, status, run, app


def test_launch_uses_approved_path_without_shell_or_arguments(launch):
    call, _, run, app = launch
    assert call()['status'] == 'launch-requested'
    assert run.call_args.args[0] == ['/usr/bin/open', '-a', str(app)]
    assert 'shell' not in run.call_args.kwargs


@pytest.mark.parametrize('field,value', [('pending', True), ('runtime_verified', False),
    ('configured_mode', 'sandboxed'), ('effective_mode', 'sandboxed'), ('available', False)])
def test_unverified_or_sandboxed_cannot_launch(launch, field, value):
    call, status, run, _ = launch
    status[field] = value
    with pytest.raises(apps.AppLaunchError):
        call()
    run.assert_not_called()


@pytest.mark.parametrize('payload', [{'bundleId': 'com.apple.Terminal'},
    {'bundleId': 'com.apple.calculator', 'args': ['--anything']},
    {'bundleId': 'com.apple.calculator;open /tmp/x'}, {'command': 'open -a Calculator'}])
def test_request_cannot_add_authority(launch, payload):
    call, _, run, _ = launch
    with pytest.raises(apps.AppLaunchError):
        call(payload)
    run.assert_not_called()


def test_timeout_is_uncertain_not_success(launch):
    call, _, run, _ = launch
    run.side_effect = apps.subprocess.TimeoutExpired('open', 15)
    with pytest.raises(apps.AppLaunchError, match='outcome-unknown'):
        call()
    run.assert_called_once()
