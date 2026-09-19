"""Exercise the installer's credential selection without services or root writes."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import types
from unittest.mock import patch

import pytest

if sys.platform == 'win32':
    pytest.skip('POSIX custody rules are tested in the Linux fixture', allow_module_level=True)

ROOT = Path(__file__).resolve().parents[4]
SOURCE = (ROOT/'installers/lib/pixel-host-install.sh').read_text(encoding='utf-8')
FRAGMENT = SOURCE.split("environment_path = source / '.env'\n", 1)[1].split("write(config_dir / 'pixel-access-relay.key'", 1)[0]
PROGRAM = compile("environment_path = source / '.env'\n"+FRAGMENT, 'access-install-credentials', 'exec')
KEY, CHAT = 'o'*64, 'c'*64


def resolve(root, *, adopt=False, edge=None):
    namespace = {'source': root, 'owner': types.SimpleNamespace(pw_uid=os.getuid()),
        'os': os, 'stat': stat,
        'sys': types.SimpleNamespace(argv=['-', '', '', '', '18790', str(adopt).lower(), 'false'], stderr=sys.stderr),
        'json': json, 'subprocess': subprocess, 'settings_data_directory': lambda install, text: str(install/'data')}
    with patch.object(subprocess, 'run', return_value=types.SimpleNamespace(stdout=json.dumps([edge]).encode())) as run:
        exec(PROGRAM, namespace)
    return namespace, run


def env(root, text):
    path = root/'.env'
    path.write_text(text, encoding='utf-8')
    path.chmod(0o600)
    return path


def test_fresh_install_uses_local_owner_key_before_edge_exists(tmp_path):
    env(tmp_path, f'DASHBOARD_API_KEY="{KEY}"\nPIXEL_OPENWEBUI_KEY=\'{CHAT}\'\n')
    value, run = resolve(tmp_path)
    assert value['key'] == KEY
    run.assert_not_called()


@pytest.mark.parametrize('text', ['', f'PIXEL_OPENWEBUI_KEY={CHAT}\n',
    f'DASHBOARD_API_KEY={CHAT}\nPIXEL_OPENWEBUI_KEY={CHAT}\n'])
def test_missing_or_chat_scoped_key_fails_without_docker_or_secret_output(tmp_path, text, capsys):
    env(tmp_path, text)
    with patch.object(subprocess, 'run') as run, pytest.raises(SystemExit, match='Distinct Dashboard owner credential'):
        resolve(tmp_path)
    run.assert_not_called()
    assert KEY not in capsys.readouterr().out


def test_fresh_install_requires_safe_expected_environment_file(tmp_path):
    with pytest.raises(SystemExit, match='environment is unavailable'):
        resolve(tmp_path)
    path = env(tmp_path, f'DASHBOARD_API_KEY={KEY}\nPIXEL_OPENWEBUI_KEY={CHAT}\n')
    path.chmod(0o666)
    with pytest.raises(SystemExit, match='environment is unsafe'):
        resolve(tmp_path)
    path.unlink()
    elsewhere = tmp_path/'elsewhere'
    elsewhere.write_text(f'DASHBOARD_API_KEY={KEY}\n')
    path.symlink_to(elsewhere)
    with pytest.raises(OSError): resolve(tmp_path)


def test_hybrid_adoption_uses_live_edge_key_not_guest_environment(tmp_path):
    env(tmp_path, f'DASHBOARD_API_KEY={"x"*64}\nPIXEL_OPENWEBUI_KEY={CHAT}\n')
    edge = {'State': {'Running': True}, 'Config': {'Labels': {'com.docker.compose.service': 'pixel-edge'},
        'Env': [f'PIXEL_PREVIEW_PROXY_KEY={KEY}', f'PIXEL_OPENWEBUI_KEY={CHAT}']}}
    value, run = resolve(tmp_path, adopt=True, edge=edge)
    assert value['key'] == KEY
    assert run.call_args.args[0] == ['docker', 'inspect', 'ods-pixel-edge']
    (tmp_path/'.env').unlink()
    value, _ = resolve(tmp_path, adopt=True, edge=edge)
    assert value['key'] == KEY and value['settings_data_dir'] is None


@pytest.mark.parametrize('running,label', [(False, 'pixel-edge'), (True, 'other')])
def test_hybrid_adoption_does_not_fall_back_to_local_key(tmp_path, running, label):
    env(tmp_path, f'DASHBOARD_API_KEY={KEY}\nPIXEL_OPENWEBUI_KEY={CHAT}\n')
    with pytest.raises(SystemExit, match='managed Edge runtime is unavailable'):
        resolve(tmp_path, adopt=True, edge={'State': {'Running': running}, 'Config': {'Labels': {'com.docker.compose.service': label}}})
