import importlib.util
import json
import os
import stat
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
    p.write_text("%s=%d\n" % (env_var, port))
    os.chmod(p, 0o600)
    return p


def _write_cfgs(root, home, name="opencode.json", data=b'{"a":1}'):
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
