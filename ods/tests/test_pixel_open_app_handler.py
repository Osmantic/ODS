import ast
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

SOURCE = Path(__file__).resolve().parents[1] / 'bin/ods-host-agent.py'
sys.path.insert(0, str(SOURCE.parent))
import pixel_macos_apps
import pixel_access_relay


@pytest.mark.parametrize('case', ['success', 'unauthorized', 'busy', 'denied'])
def test_app_handler_auth_lock_and_release(tmp_path, monkeypatch, case):
    method = next(node for node in ast.walk(ast.parse(SOURCE.read_text()))
                  if isinstance(node, ast.FunctionDef) and node.name == '_handle_pixel_open_app')
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config/pixel-approved-apps.json').write_text('{"com.apple.calculator":"/System/Applications/Calculator.app"}')
    reply, end = Mock(), Mock()
    begin = Mock(return_value=(case != 'busy', {}))
    status = {'fixture': True}
    relay = Mock(return_value=(200, status))
    monkeypatch.setattr(pixel_access_relay, 'request_runtime_access', relay)
    def launch(body, *, approved_apps, access_status):
        assert body == {'bundleId': 'com.apple.calculator'}
        assert access_status() == status
        if case == 'denied':
            raise pixel_macos_apps.AppLaunchError('verified-full-access-required')
        return {'status': 'launch-requested'}
    launcher = Mock(side_effect=launch)
    monkeypatch.setattr(pixel_macos_apps, 'launch_application', launcher)
    scope = dict(check_auth=lambda self: case != 'unauthorized',
        read_json_body=lambda self: {'bundleId': 'com.apple.calculator'},
        INSTALL_DIR=tmp_path, json=json, load_env=lambda path: {},
        _begin_model_lifecycle=begin, _end_model_lifecycle=end, json_response=reply)
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(SOURCE), 'exec'), scope)
    scope['_handle_pixel_open_app'](SimpleNamespace())
    if case == 'unauthorized':
        begin.assert_not_called()
        launcher.assert_not_called()
    elif case == 'busy':
        launcher.assert_not_called()
        end.assert_not_called()
        assert reply.call_args.args[1] == 409
    else:
        end.assert_called_once_with('pixel_open_app')
        assert reply.call_args.args[1] == (403 if case == 'denied' else 200)
