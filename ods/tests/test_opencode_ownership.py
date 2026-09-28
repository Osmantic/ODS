import importlib.util
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='Linux user-service ownership contract')

HELPER_ENV = "OPENCODE_OWNER_HELPER"


def _load_helper():
    path = os.environ.get(HELPER_ENV) or str(Path(__file__).parents[1] / "installers/lib/opencode-ownership.py")
    assert path, "OPENCODE_OWNER_HELPER must be set"
    spec = importlib.util.spec_from_file_location("opencode_ownership", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def helper():
    return _load_helper()


@pytest.fixture
def uid():
    u = os.getuid()
    assert u != 0
    return u


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "root"
    (r / "opencode").mkdir(parents=True)
    (r / "config").mkdir()
    return r


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    (h / ".config/systemd/user").mkdir(parents=True)
    (h / ".config/opencode").mkdir(parents=True)
    return h


@pytest.fixture
def binary(root):
    b = root / "opencode/opencode-web"
    b.write_bytes(b"#!/bin/sh\nexit 0\n")
    os.chmod(b, 0o755)
    return b


@pytest.fixture
def template_bytes(helper):
    here = Path(helper.__file__).resolve()
    src = here.parents[2] / "opencode" / "opencode-web.service"
    return src.read_bytes()


def _render(helper, template_bytes, home, binary):
    return helper.render_template(template_bytes, home, binary)


def _write_unit(home, data):
    p = home / ".config/systemd/user/opencode-web.service"
    p.write_bytes(data)
    os.chmod(p, 0o644)
    return p


def _write_ports(root, port, env_var="OPENCODE_PORT"):
    doc = {"ports": [{"service_id": "opencode", "compose_managed": False,
                      "env_var": env_var, "external_default": port}]}
    p = root / "config/ports.json"
    p.write_text(json.dumps(doc))
    os.chmod(p, 0o644)
    return p


def _write_env(root, env_var, port):
    p = root / ".env"
    p.write_text("ODS_MODEL_SWITCHBOARD=disabled\nODS_MODE=local\nOLLAMA_PORT=8080\n%s=%d\n" % (env_var, port))
    os.chmod(p, 0o600)
    return p


def _write_cfgs(root, home, name="opencode.json", data=None):
    if data is None:
        data = json.dumps({"model": "llama-server/fixture", "provider": {
            "llama-server": {"npm": "@ai-sdk/openai-compatible", "options": {
                "baseURL": "http://127.0.0.1:8080/v1", "apiKey": "no-key"}}}}).encode()
    a = root / "opencode" / name
    b = home / ".config/opencode" / name
    a.write_bytes(data)
    b.write_bytes(data)
    os.chmod(a, 0o600)
    os.chmod(b, 0o600)


def _setup_valid(helper, root, home, binary, template_bytes, port=3003):
    (root / 'opencode/opencode-web.service').write_bytes(template_bytes)
    unit = _render(helper, template_bytes, home, binary)
    _write_unit(home, unit)
    _write_ports(root, port)
    _write_env(root, "OPENCODE_PORT", port)
    _write_cfgs(root, home)
    _write_cfgs(root, home, name='config.json')
    return unit


def _fake_systemctl(helper, home, binary, port, pid=4242, start=1000):
    unit_path = home / ".config/systemd/user/opencode-web.service"
    exec_raw = ("{ path=%s ; argv[]=%s serve --port %d --hostname 127.0.0.1 "
                "; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; "
                "pid=0 ; code=(null) ; status=0/0 }" % (binary, binary, port))
    return {
        "LoadState": "loaded",
        "ActiveState": "active",
        "SubState": "running",
        "FragmentPath": str(unit_path),
        "DropInPaths": "",
        "MainPID": str(pid),
        "WorkingDirectory": str(home),
        "ExecStart": exec_raw,
    }


def _patch_proc(monkeypatch, helper, binary, uid, pid=4242, start=1000,
                inode="12345"):
    monkeypatch.setattr(helper, "_proc_uid", lambda p: uid)
    monkeypatch.setattr(helper, "_proc_exe", lambda p: str(binary))
    monkeypatch.setattr(helper, "_proc_starttime", lambda p: start)
    monkeypatch.setattr(helper, "_listener_inode", lambda port: {inode})
    monkeypatch.setattr(helper, "_pid_fd_inodes", lambda p: {inode})


def test_record_then_verify_ok(helper, root, home, binary, template_bytes, uid,
                               monkeypatch):
    port = 3003
    _setup_valid(helper, root, home, binary, template_bytes, port)
    res = helper.record(root, home, uid)
    assert res["state"] == "recorded"
    marker = home / ".config/ods/opencode-web.owner.json"
    assert marker.exists()
    st = os.lstat(marker)
    assert stat.S_IMODE(st.st_mode) == 0o600
    assert st.st_uid == uid
    _patch_proc(monkeypatch, helper, binary, uid)
    out = helper.verify(root, home, uid,
                        systemctl_runner=lambda u, n: _fake_systemctl(
                            helper, home, binary, port))
    assert out["port"] == port
    assert out["mainPid"] == 4242
    assert out["startTicks"] == 1000
    assert out["provenInstallationRoot"] is True
    assert out["ownerService"] is True
    assert out["schemaVersion"] == 1, 'the Windows ownership status wire contract is unchanged'


def test_record_idempotent(helper, root, home, binary, template_bytes, uid):
    _setup_valid(helper, root, home, binary, template_bytes)
    assert helper.record(root, home, uid)["state"] == "recorded"
    assert helper.record(root, home, uid)["state"] == "idempotent"


def test_verify_missing_marker_fails(helper, root, home, binary,
                                     template_bytes, uid):
    _setup_valid(helper, root, home, binary, template_bytes)
    with pytest.raises(helper.Fail):
        helper.verify(root, home, uid, systemctl_runner=lambda u, n: {})


def test_duplicate_ports_identical_fails(helper, root, home, binary,
                                         template_bytes, uid):
    _setup_valid(helper, root, home, binary, template_bytes)
    p = root / "config/ports.json"
    doc = json.loads(p.read_text())
    doc["ports"].append(dict(doc["ports"][0]))
    p.write_text(json.dumps(doc))
    with pytest.raises(helper.Fail):
        helper.record(root, home, uid)


def test_duplicate_json_field_fails(helper, root, home, binary,
                                    template_bytes, uid):
    _setup_valid(helper, root, home, binary, template_bytes)
    p = root / "config/ports.json"
    p.write_text('{"ports": [], "ports": []}')
    with pytest.raises(helper.Fail):
        helper.record(root, home, uid)


def test_env_override_mismatch_fails(helper, root, home, binary,
                                     template_bytes, uid):
    _setup_valid(helper, root, home, binary, template_bytes, port=3003)
    _write_env(root, "OPENCODE_PORT", 3004)
    with pytest.raises(helper.Fail):
        helper.record(root, home, uid)


def test_env_override_match_ok(helper, root, home, binary, template_bytes, uid):
    _setup_valid(helper, root, home, binary, template_bytes, port=3003)
    _write_env(root, "OPENCODE_PORT", 3003)
    assert helper.record(root, home, uid)["state"] == "recorded"


def test_env_duplicate_key_fails(helper, root, home, binary,
                                 template_bytes, uid):
    _setup_valid(helper, root, home, binary, template_bytes, port=3003)
    p = root / ".env"
    p.write_text("OPENCODE_PORT=3003\nOPENCODE_PORT=3003\n")
    os.chmod(p, 0o600)
    with pytest.raises(helper.Fail):
        helper.record(root, home, uid)


def test_env_not_private_fails(helper, root, home, binary,
                               template_bytes, uid):
    _setup_valid(helper, root, home, binary, template_bytes, port=3003)
    p = _write_env(root, "OPENCODE_PORT", 3003)
    os.chmod(p, 0o644)
    with pytest.raises(helper.Fail):
        helper.record(root, home, uid)


def test_public_0755_ancestor_accepted(helper, root, home, binary,
                                       template_bytes, uid):
    _setup_valid(helper, root, home, binary, template_bytes)
    os.chmod(root, 0o755)
    assert helper.record(root, home, uid)["state"] == "recorded"


def test_group_writable_ancestor_fails(helper, root, home, binary,
                                       template_bytes, uid):
    _setup_valid(helper, root, home, binary, template_bytes)
    os.chmod(root, 0o775)
    with pytest.raises(helper.Fail):
        helper.record(root, home, uid)


def test_symlink_parent_fails(helper, root, home, binary, template_bytes, uid,
                              tmp_path):
    _setup_valid(helper, root, home, binary, template_bytes)
    real = tmp_path / "realroot"
    real.mkdir()
    link = tmp_path / "linkroot"
    link.symlink_to(real)
    with pytest.raises(helper.Fail):
        helper.record(link, home, uid)


def test_symlink_unit_file_fails(helper, root, home, binary, template_bytes,
                                 uid, tmp_path):
    _setup_valid(helper, root, home, binary, template_bytes)
    unit = home / ".config/systemd/user/opencode-web.service"
    target = tmp_path / "elsewhere.service"
    target.write_bytes(unit.read_bytes())
    unit.unlink()
    unit.symlink_to(target)
    with pytest.raises(helper.Fail):
        helper.record(root, home, uid)


def test_foreign_root_marker_fails(helper, root, home, binary,
                                   template_bytes, uid, tmp_path):
    _setup_valid(helper, root, home, binary, template_bytes)
    helper.record(root, home, uid)
    other = tmp_path / "otherroot"
    (other / "opencode").mkdir(parents=True)
    (other / "config").mkdir()
    with pytest.raises(helper.Fail):
        helper.record(other, home, uid)


def test_changed_template_fails(helper, root, home, binary, template_bytes,
                                uid):
    _setup_valid(helper, root, home, binary, template_bytes)
    helper.record(root, home, uid)
    t = root / "opencode/opencode-web.service"
    t.write_bytes(template_bytes + b"\n# extra\n")
    with pytest.raises(helper.Fail):
        helper.record(root, home, uid)


def test_changed_unit_fails(helper, root, home, binary, template_bytes, uid):
    _setup_valid(helper, root, home, binary, template_bytes)
    helper.record(root, home, uid)
    unit = home / ".config/systemd/user/opencode-web.service"
    unit.write_bytes(unit.read_bytes() + b"\n# tamper\n")
    with pytest.raises(helper.Fail):
        helper.record(root, home, uid)


def test_config_hash_mismatch_fails(helper, root, home, binary,
                                    template_bytes, uid):
    _setup_valid(helper, root, home, binary, template_bytes)
    helper.record(root, home, uid)
    (home / ".config/opencode/opencode.json").write_bytes(b'{"a":2}')
    with pytest.raises(helper.Fail):
        helper.record(root, home, uid)


@pytest.mark.parametrize('defect', ['pid-owner', 'deleted-exe', 'pid-reuse', 'socket-owner', 'drop-in', 'private-config'])
def test_verify_rejects_runtime_identity_change(helper, root, home, binary, template_bytes, uid, monkeypatch, defect):
    _setup_valid(helper, root, home, binary, template_bytes)
    helper.record(root, home, uid)
    marker = home / helper.MARKER_REL
    original = marker.read_bytes()
    _patch_proc(monkeypatch, helper, binary, uid)
    properties = _fake_systemctl(helper, home, binary, 3003)
    if defect == 'pid-owner':
        monkeypatch.setattr(helper, '_proc_uid', lambda pid: uid + 1)
    elif defect == 'deleted-exe':
        monkeypatch.setattr(helper, '_proc_exe', lambda pid: str(binary) + ' (deleted)')
    elif defect == 'pid-reuse':
        ticks = iter([1000, 1001])
        monkeypatch.setattr(helper, '_proc_starttime', lambda pid: next(ticks))
    elif defect == 'socket-owner':
        monkeypatch.setattr(helper, '_pid_fd_inodes', lambda pid: {'unrelated'})
    elif defect == 'drop-in':
        properties['DropInPaths'] = str(home / 'foreign.conf')
    else:
        (root / 'opencode/config.json').write_bytes(b'{"fixture_changed":true}')
    with pytest.raises(helper.Fail):
        helper.verify(root, home, uid, systemctl_runner=lambda u, n: properties)
    assert marker.read_bytes() == original


@pytest.mark.parametrize('argv', [
    '/bin/tool serve --port 3003 --hostname 0.0.0.0',
    '/bin/tool serve --port 0 --hostname 127.0.0.1',
    '/bin/tool serve --port 3003 --hostname 127.0.0.1 extra',
])
def test_rejects_external_or_unexpected_service_argv(helper, argv):
    with pytest.raises(helper.Fail):
        helper.parse_unit_execstart(('ExecStart=' + argv).encode())


def _emit_actual_phase_config(helper, root, home, env_values):
    """Run the real route/config fresh and rerun branches, never service/CLI code."""
    phase = Path(helper.__file__).parents[1] / 'phases/07-devtools.sh'
    source = phase.read_text()
    start = source.index('        _opencode_model_id="${LLM_MODEL}"')
    end = source.index('        # Install OpenCode Web UI as user-level', start)
    emitter = source[start:end]
    assert 'systemctl' not in emitter and 'ownership.py' not in emitter
    config = home / '.config/opencode'
    assert root.is_relative_to(home.parent) and home.is_relative_to(root.parent)
    environment = {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'LLM_MODEL': 'fixture-model',
                   'MAX_CONTEXT': '4096', **env_values}
    script = 'set -eu\nai_bad() { exit 91; }\nai_ok() { :; }\nai_warn() { :; }\n'
    script += 'OPENCODE_CONFIG_DIR="$HOME/.config/opencode"\n' + emitter
    result = subprocess.run(['bash', '-s', '--', str(config/'opencode.json'),
                             str(config/'config.json')], input=script, text=True,
                            capture_output=True, env=environment, timeout=5)
    assert result.returncode == 0, 'isolated production emitter failed'
    assert result.stdout == '' and result.stderr == ''
    env_path = root / '.env'
    env_path.write_text(''.join('%s=%s\n' % item for item in env_values.items()))
    env_path.chmod(0o600)


@pytest.mark.parametrize('env_values', [
    {'LITELLM_KEY': 'fixture-gateway-key'},
    {'ODS_MODEL_SWITCHBOARD': 'enabled', 'LITELLM_KEY': 'fixture-gateway-key', 'LITELLM_PORT': '4017'},
    {'ODS_MODEL_SWITCHBOARD': 'disabled', 'EXTERNAL_LLM_URL': 'https://example.test/v1',
     'EXTERNAL_LLM_MODEL': 'fixture-external', 'LITELLM_KEY': 'fixture-gateway-key'},
    {'ODS_MODEL_SWITCHBOARD': 'disabled', 'ODS_MODE': 'lemonade', 'LITELLM_KEY': 'fixture-gateway-key'},
    {'ODS_MODEL_SWITCHBOARD': 'disabled', 'ODS_MODE': 'lemonade'},
    {'ODS_MODEL_SWITCHBOARD': 'disabled', 'ODS_MODE': 'local', 'OLLAMA_PORT': '8088'},
])
def test_actual_phase_emitter_records_without_invented_root_configs(
        helper, root, home, binary, template_bytes, uid, monkeypatch, env_values):
    _setup_valid(helper, root, home, binary, template_bytes)
    for name in helper.CFG_NAMES:
        (root/helper.CFG_ROOT_REL/name).unlink()
        (home/helper.CFG_HOME_REL/name).unlink()
    _emit_actual_phase_config(helper, root, home, env_values)
    protected = {name: ((home/helper.CFG_HOME_REL/name).read_bytes(),
                        (home/helper.CFG_HOME_REL/name).stat().st_ino)
                 for name in helper.CFG_NAMES}
    for name in helper.CFG_NAMES:
        assert stat.S_IMODE((home/helper.CFG_HOME_REL/name).stat().st_mode) == 0o600
        assert not (root/helper.CFG_ROOT_REL/name).exists()
    assert helper.record(root, home, uid)['state'] == 'recorded'
    marker = (home/helper.MARKER_REL).read_bytes()
    assert helper.record(root, home, uid)['state'] == 'idempotent'
    _patch_proc(monkeypatch, helper, binary, uid)
    assert helper.verify(root, home, uid, systemctl_runner=lambda u, n:
                         _fake_systemctl(helper, home, binary, 3003))['ownerService']
    assert (home/helper.MARKER_REL).read_bytes() == marker
    for name, (data, inode) in protected.items():
        path = home/helper.CFG_HOME_REL/name
        assert path.read_bytes() == data and path.stat().st_ino == inode
        assert not (root/helper.CFG_ROOT_REL/name).exists()
    # Run the actual installer rewrite/sync branch with the same choices.
    _emit_actual_phase_config(helper, root, home, env_values)
    assert helper.record(root, home, uid)['state'] == 'idempotent'
    assert (home/helper.MARKER_REL).read_bytes() == marker
    for name in helper.CFG_NAMES:
        assert stat.S_IMODE((home/helper.CFG_HOME_REL/name).stat().st_mode) == 0o600
        assert not (root/helper.CFG_ROOT_REL/name).exists()


def _without_root_configs(helper, root, home, binary, template_bytes):
    _setup_valid(helper, root, home, binary, template_bytes)
    for name in helper.CFG_NAMES:
        (root/helper.CFG_ROOT_REL/name).unlink()


@pytest.mark.parametrize('defect', ['key', 'url', 'npm', 'other-provider', 'duplicate-json',
                                  'duplicate-env', 'invalid-port', 'nonfinite', 'pair',
                                  'symlink', 'hardlink', 'public-mode', 'foreign-root-copy'])
def test_legacy_admission_rejects_unrelated_or_unsafe_config_without_mutation(
        helper, root, home, binary, template_bytes, uid, defect, tmp_path):
    _without_root_configs(helper, root, home, binary, template_bytes)
    a, b = (home/helper.CFG_HOME_REL/name for name in helper.CFG_NAMES)
    doc = json.loads(a.read_bytes())
    provider = doc['provider']['llama-server']
    if defect == 'key':
        provider['options']['apiKey'] = 'different-fixture-key'
    elif defect == 'url':
        provider['options']['baseURL'] = 'http://127.0.0.1:4000/v1'
    elif defect == 'npm':
        provider['npm'] = '@example/foreign'
    elif defect == 'other-provider':
        doc['provider'] = {'other': provider}
    elif defect == 'duplicate-env':
        with (root/'.env').open('a') as stream:
            stream.write('OLLAMA_PORT=8080\n')
    elif defect == 'invalid-port':
        with (root/'.env').open('a') as stream:
            stream.write('LITELLM_PORT=99999\n')
    elif defect == 'nonfinite':
        doc['metadata'] = float('nan')
    if defect in {'key', 'url', 'npm', 'other-provider', 'nonfinite'}:
        a.write_text(json.dumps(doc))
        b.write_bytes(a.read_bytes())
    if defect == 'duplicate-json':
        a.write_text('{"provider":{},"provider":{}}')
        b.write_bytes(a.read_bytes())
    elif defect == 'pair':
        b.write_bytes(b'{}')
    elif defect in {'symlink', 'hardlink'}:
        foreign = tmp_path/'foreign-config.json'
        foreign.write_bytes(a.read_bytes())
        foreign.chmod(0o600)
        a.unlink()
        a.symlink_to(foreign) if defect == 'symlink' else os.link(foreign, a)
    elif defect == 'public-mode':
        a.chmod(0o644)
    elif defect == 'foreign-root-copy':
        foreign = root/helper.CFG_ROOT_REL/helper.CFG_NAMES[0]
        foreign.write_bytes(b'{"foreign":true}')
        foreign.chmod(0o600)
    before = {path: path.read_bytes() for path in (a, b, root/'.env')}
    with pytest.raises(helper.Fail):
        helper.record(root, home, uid)
    assert not (home/helper.MARKER_REL).exists()
    assert {path: path.read_bytes() for path in before} == before


def test_legacy_custom_settings_are_preserved_and_format_only_rerun_is_idempotent(
        helper, root, home, binary, template_bytes, uid):
    _without_root_configs(helper, root, home, binary, template_bytes)
    paths = [home/helper.CFG_HOME_REL/name for name in helper.CFG_NAMES]
    doc = json.loads(paths[0].read_bytes())
    doc['model'] = 'other/custom-choice'
    doc['provider']['other'] = {'custom': True}
    doc['customSetting'] = {'keep': ['these', 'values']}
    for path in paths:
        path.write_text(json.dumps(doc, indent=2))
    before = [path.read_bytes() for path in paths]
    helper.record(root, home, uid)
    marker = (home/helper.MARKER_REL).read_bytes()
    assert [path.read_bytes() for path in paths] == before
    for path in paths:
        path.write_text(json.dumps(doc, separators=(',', ':')))
    assert helper.record(root, home, uid)['state'] == 'idempotent'
    assert (home/helper.MARKER_REL).read_bytes() == marker
    doc['customSetting']['keep'].append('changed')
    for path in paths:
        path.write_text(json.dumps(doc))
    with pytest.raises(helper.Fail):
        helper.verify(root, home, uid, systemctl_runner=lambda u, n: {})
    with pytest.raises(helper.Fail):
        helper.record(root, home, uid)
    assert (home/helper.MARKER_REL).read_bytes() == marker


def test_previous_schema_marker_is_preserved_and_requires_explicit_migration(
        helper, root, home, binary, template_bytes, uid):
    _without_root_configs(helper, root, home, binary, template_bytes)
    helper.record(root, home, uid)
    marker = home/helper.MARKER_REL
    doc = json.loads(marker.read_bytes())
    doc['schemaVersion'] = 1
    del doc['configSha256']
    marker.write_text(json.dumps(doc))
    before = marker.read_bytes()
    with pytest.raises(helper.Fail):
        helper.record(root, home, uid)
    assert marker.read_bytes() == before
