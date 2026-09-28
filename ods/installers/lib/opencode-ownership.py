#!/usr/bin/env python3
"""Explicit-root ownership record/verify for the HOME-scoped opencode-web.service.

Read-only verify and explicit record only. No unit/process/config mutation.
"""
import hashlib
import json
import os
import pwd
import re
import stat
import subprocess
import sys
from pathlib import Path

SCHEMA = 2
OWNER_STATUS_SCHEMA = 1
MANAGER = "ods"
UNIT_NAME = "opencode-web.service"
MARKER_REL = ".config/ods/opencode-web.owner.json"
UNIT_REL = ".config/systemd/user/opencode-web.service"
TEMPLATE_REL = "opencode/opencode-web.service"
PORTS_REL = "config/ports.json"
ENV_REL = ".env"
CFG_ROOT_REL = "opencode"
CFG_HOME_REL = ".config/opencode"
CFG_NAMES = ("opencode.json", "config.json")
MAX_UNIT = 65536
MAX_CFG = 1048576
MAX_MARKER = 65536
MAX_PORTS = 262144
MAX_ENV = 1048576


class Fail(Exception):
    pass


def _open_nofollow(path, flags=os.O_RDONLY):
    return os.open(str(path), flags | os.O_NOFOLLOW | os.O_NONBLOCK)


def _check_ancestors(path, uid, allow_public=False):
    p = Path(path).parent
    chain = []
    while True:
        chain.append(p)
        if p == p.parent:
            break
        p = p.parent
    for anc in reversed(chain):
        try:
            st = os.lstat(str(anc))
        except FileNotFoundError:
            raise Fail("missing ancestor")
        if stat.S_ISLNK(st.st_mode):
            raise Fail("symlink ancestor")
        if not stat.S_ISDIR(st.st_mode):
            raise Fail("non-directory ancestor")
        if st.st_uid != 0 and st.st_uid != uid:
            raise Fail("ancestor not owned")
        if st.st_uid == 0 and st.st_mode & 0o022:
            if not (anc == Path("/tmp") and st.st_mode & stat.S_ISVTX):
                raise Fail("ancestor writable by others")
        elif st.st_uid == uid and st.st_mode & 0o022:
            raise Fail("ancestor writable by others")



def regular(path, uid, maximum, private=False, allow_public_parent=False):
    _check_ancestors(path, uid, allow_public=allow_public_parent)
    try:
        fd = _open_nofollow(path)
    except OSError as e:
        raise Fail("open failed: %s" % e.__class__.__name__)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise Fail("not regular")
        if info.st_uid != uid:
            raise Fail("wrong owner")
        if info.st_nlink != 1:
            raise Fail("hardlink")
        if info.st_size > maximum:
            raise Fail("oversized")
        if private and info.st_mode & 0o077:
            raise Fail("not private")
        if not private and info.st_mode & 0o022:
            raise Fail("writable by others")
        data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise Fail("oversized")
        return data


def _reject_dup_pairs(pairs):
    seen = set()
    for k, _ in pairs:
        if k in seen:
            raise Fail("duplicate json field")
        seen.add(k)
    return dict(pairs)


def load_json(data):
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_reject_dup_pairs)
    except Fail:
        raise
    except Exception:
        raise Fail("invalid json")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_template(root, uid):
    return regular(root / TEMPLATE_REL, uid, MAX_UNIT)


def render_template(template, home, binary):
    text = template.decode("utf-8")
    text = text.replace("__HOME__", str(home))
    text = text.replace("__OPENCODE_BIN__", str(binary))
    text = text.replace("__OPENCODE_BIN_DIR__", str(Path(binary).parent))
    return text.encode("utf-8")


def parse_unit_execstart(unit_bytes):
    text = unit_bytes.decode("utf-8")
    exec_lines = []
    for line in text.splitlines():
        if line.startswith("ExecStart="):
            exec_lines.append(line[len("ExecStart="):])
    if len(exec_lines) != 1:
        raise Fail("expected exactly one ExecStart")
    raw = exec_lines[0].strip()
    if raw.startswith("{"):
        raise Fail("structured ExecStart not supported")
    parts = raw.split()
    if len(parts) != 6:
        raise Fail("unexpected ExecStart argv")
    binary, sub, port_flag, port_val, host_flag, host_val = parts
    if sub != "serve" or port_flag != "--port" or host_flag != "--hostname":
        raise Fail("unexpected ExecStart argv")
    if host_val != "127.0.0.1":
        raise Fail("unexpected hostname")
    if not re.fullmatch(r"[0-9]+", port_val):
        raise Fail("bad port")
    port = int(port_val)
    if not (1 <= port <= 65535):
        raise Fail("port out of range")
    if not binary.startswith("/"):
        raise Fail("binary not absolute")
    if os.path.normpath(binary) != binary:
        raise Fail("binary not normalized")
    if any(c.isspace() for c in binary):
        raise Fail("binary contains whitespace")
    return binary, port



def parse_unit_workingdir(unit_bytes):
    text = unit_bytes.decode("utf-8")
    vals = []
    for line in text.splitlines():
        if line.startswith("WorkingDirectory="):
            vals.append(line[len("WorkingDirectory="):])
    if len(vals) != 1:
        raise Fail("expected exactly one WorkingDirectory")
    return vals[0]


def read_ports_registry(root, uid):
    data = regular(root / PORTS_REL, uid, MAX_PORTS)
    doc = load_json(data)
    ports = doc.get("ports")
    if not isinstance(ports, list):
        raise Fail("ports registry malformed")
    matches = []
    for entry in ports:
        if not isinstance(entry, dict):
            continue
        if entry.get("service_id") == "opencode":
            matches.append(entry)
    if len(matches) != 1:
        raise Fail("expected exactly one opencode entry")
    entry = matches[0]
    if entry.get("compose_managed") is not False:
        raise Fail("opencode not compose_managed=false")
    env_var = entry.get("env_var")
    default = entry.get("external_default")
    if not isinstance(env_var, str) or not env_var:
        raise Fail("opencode env_var missing")
    if isinstance(default, bool) or not isinstance(default, int):
        raise Fail("opencode default invalid")
    if not (1 <= default <= 65535):
        raise Fail("opencode default invalid")
    return env_var, default



def read_env_override(root, uid, env_var):
    path = root / ENV_REL
    if not os.path.lexists(str(path)):
        return None
    data = regular(path, uid, MAX_ENV, private=True)
    text = data.decode("utf-8", errors="strict")
    found = None
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        if key != env_var:
            continue
        if found is not None:
            raise Fail("duplicate env key")
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        if not re.fullmatch(r"[0-9]+", val):
            raise Fail("malformed env value")
        n = int(val)
        if not (1 <= n <= 65535):
            raise Fail("env port out of range")
        found = n
    return found


def resolve_registered_port(root, uid):
    env_var, default = read_ports_registry(root, uid)
    override = read_env_override(root, uid, env_var)
    return override if override is not None else default


def compare_configs(root, home, uid):
    """Bind HOME config to this root's managed provider without invented copies."""
    keys = {"ODS_MODEL_SWITCHBOARD", "EXTERNAL_LLM_URL", "EXTERNAL_LLM_MODEL",
            "ODS_MODE", "LITELLM_KEY", "LITELLM_PORT", "OLLAMA_PORT"}
    env = {}
    text = regular(root / ENV_REL, uid, MAX_ENV, private=True).decode("utf-8")
    for line in text.splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key not in keys:
            continue
        if key in env:
            raise Fail("duplicate managed env key")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        env[key] = value
    for key in ("LITELLM_PORT", "OLLAMA_PORT"):
        value = env.get(key, "")
        if value and (not re.fullmatch(r"[0-9]+", value) or not 1 <= int(value) <= 65535):
            raise Fail("invalid managed provider port")
    gateway = ((env.get("ODS_MODEL_SWITCHBOARD") or "enabled") == "enabled" or
               bool(env.get("EXTERNAL_LLM_URL") and env.get("EXTERNAL_LLM_MODEL")) or
               env.get("ODS_MODE") == "lemonade")
    if gateway:
        port = env.get("LITELLM_PORT") or "4000"
        api_key = env.get("LITELLM_KEY") or ""
        # Only the legacy Lemonade branch permits the installer's no-key default.
        strict_key = ((env.get("ODS_MODEL_SWITCHBOARD") or "enabled") == "enabled" or
                      bool(env.get("EXTERNAL_LLM_URL") and env.get("EXTERNAL_LLM_MODEL")))
        if not api_key and strict_key:
            raise Fail("managed gateway key missing")
        api_key = api_key or "no-key"
    else:
        port = env.get("OLLAMA_PORT") or "8080"
        api_key = "no-key"
    expected_url = "http://127.0.0.1:%s/v1" % port
    blobs = [regular(home / CFG_HOME_REL / name, uid, MAX_CFG, private=True)
             for name in CFG_NAMES]
    if blobs[0] != blobs[1]:
        raise Fail("home config pair mismatch")
    doc = load_json(blobs[0])
    providers = doc.get("provider") if isinstance(doc, dict) else None
    provider = providers.get("llama-server") if isinstance(providers, dict) else None
    options = provider.get("options") if isinstance(provider, dict) else None
    if (not isinstance(provider, dict) or provider.get("npm") != "@ai-sdk/openai-compatible" or
            not isinstance(options, dict) or options.get("baseURL") != expected_url or
            options.get("apiKey") != api_key):
        raise Fail("config not associated with root provider")
    # Older explicit copies, when present, remain strict evidence; never create them.
    for name in CFG_NAMES:
        path = root / CFG_ROOT_REL / name
        if os.path.lexists(str(path)):
            if regular(path, uid, MAX_CFG, private=True) != blobs[CFG_NAMES.index(name)]:
                raise Fail("config mismatch")
    try:
        canonical = json.dumps(doc, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError):
        raise Fail("invalid config value")
    return sha256(canonical)


def read_marker(home, uid):
    path = home / MARKER_REL
    if not os.path.lexists(str(path)):
        return None
    data = regular(path, uid, MAX_MARKER, private=True)
    doc = load_json(data)
    fields = {"schemaVersion", "manager", "installRoot", "ownerUid", "unitPath", "unitSha256", "binaryPath", "port", "configSha256"}
    if not isinstance(doc, dict) or set(doc) != fields:
        raise Fail("marker fields mismatch")
    if doc.get("schemaVersion") != SCHEMA:
        raise Fail("marker schema mismatch")
    if doc.get("manager") != MANAGER:
        raise Fail("marker manager mismatch")
    return doc


def write_marker(home, uid, payload):
    _check_ancestors(home / ".config" / "admission", uid)
    ods_dir = home / ".config/ods"
    if not os.path.lexists(str(ods_dir)):
        os.mkdir(str(ods_dir), 0o700)
    st = os.lstat(str(ods_dir))
    if not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode):
        raise Fail("ods dir unsafe")
    if st.st_uid != uid or st.st_mode & 0o077:
        raise Fail("ods dir not private")
    path = home / MARKER_REL
    data = json.dumps(payload, sort_keys=True).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    try:
        fd = os.open(str(path), flags, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "wb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_nlink != 1:
            raise Fail("marker fd unsafe")
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return True


def _systemctl_show(uid, unit):
    env = dict(os.environ)
    env["XDG_RUNTIME_DIR"] = "/run/user/%d" % uid
    env["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=/run/user/%d/bus" % uid
    props = ("LoadState", "ActiveState", "SubState", "FragmentPath",
             "DropInPaths", "MainPID", "WorkingDirectory", "ExecStart")
    cmd = ["/usr/bin/systemctl", "--user", "show", unit,
           "--property=" + ",".join(props)]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             env=env, timeout=5, check=False)
    except subprocess.TimeoutExpired:
        raise Fail("systemctl timeout")
    if res.returncode != 0:
        raise Fail("systemctl failed")
    values = {}
    for line in res.stdout.splitlines():
        name, sep, value = line.partition("=")
        if not sep or name not in props:
            continue
        if name in values:
            raise Fail("duplicate systemctl property")
        values[name] = value
    if set(values) != set(props):
        raise Fail("missing systemctl property")
    return values


def _parse_execstart_bracket(raw):
    m = re.fullmatch(
        r"\{ path=(\S+) ; argv\[\]=(\S+) ([^;]*?) ; ignore_errors=no ;[^{}]* \}",
        raw)
    if not m:
        raise Fail("unexpected ExecStart format")
    path = m.group(1)
    argv0 = m.group(2)
    rest = m.group(3).strip()
    args = rest.split() if rest else []
    return path, argv0, args


def _proc_uid(pid):
    st = os.stat("/proc/%d" % pid)
    return st.st_uid


def _proc_starttime(pid):
    with open("/proc/%d/stat" % pid, "rb") as f:
        data = f.read()
    rp = data.rfind(b")")
    if rp < 0:
        raise Fail("bad stat")
    fields = data[rp + 2:].split()
    if len(fields) < 20:
        raise Fail("short stat")
    return int(fields[19])


def _proc_exe(pid):
    return os.readlink("/proc/%d/exe" % pid)


def _check_binary(path, uid):
    _check_ancestors(path, uid)
    try:
        fd = _open_nofollow(path)
        try:
            st = os.fstat(fd)
        finally:
            os.close(fd)
    except OSError:
        raise Fail("binary stat failed")
    if not stat.S_ISREG(st.st_mode):
        raise Fail("binary not regular")
    if st.st_uid not in (0, uid):
        raise Fail("binary wrong owner")
    if st.st_mode & 0o022:
        raise Fail("binary writable by others")
    if not st.st_mode & 0o111:
        raise Fail("binary not executable")


def _listener_inode(port):
    target = "0100007F:%04X" % port
    inodes = set()
    for proc in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(proc, "r") as f:
                next(f, None)
                for line in f:
                    parts = line.split()
                    if len(parts) < 10:
                        continue
                    local, state, inode = parts[1], parts[3], parts[9]
                    if state != "0A":
                        continue
                    if local == target:
                        inodes.add(inode)
        except OSError:
            continue
    return inodes


def _pid_fd_inodes(pid):
    inodes = set()
    fd_dir = "/proc/%d/fd" % pid
    try:
        names = os.listdir(fd_dir)
    except OSError:
        return inodes
    for name in names:
        try:
            link = os.readlink(os.path.join(fd_dir, name))
        except OSError:
            continue
        m = re.fullmatch(r"socket:\[(\d+)\]", link)
        if m:
            inodes.add(m.group(1))
    return inodes


def verify(root, home, uid, systemctl_runner=None):
    root = Path(root)
    home = Path(home)
    if not root.is_absolute() or root == Path("/") or root.is_symlink():
        raise Fail("bad root")
    if root.resolve() != root:
        raise Fail("root not normalized")
    if not home.is_absolute() or home.resolve() != home:
        raise Fail("home not normalized")
    if uid != os.getuid():
        raise Fail("uid mismatch")
    if uid == 0:
        raise Fail("uid must be nonzero")
    marker = read_marker(home, uid)
    if marker is None:
        raise Fail("marker missing")
    if marker.get("installRoot") != str(root):
        raise Fail("marker root mismatch")
    if marker.get("ownerUid") != uid or marker.get("unitPath") != str(home / UNIT_REL):
        raise Fail("marker uid mismatch")
    unit_path = home / UNIT_REL
    unit_bytes = regular(unit_path, uid, MAX_UNIT)
    if sha256(unit_bytes) != marker.get("unitSha256"):
        raise Fail("unit hash mismatch")
    binary, unit_port = parse_unit_execstart(unit_bytes)
    if binary != marker.get("binaryPath"):
        raise Fail("binary path mismatch")
    if unit_port != marker.get("port"):
        raise Fail("unit port mismatch")
    registered = resolve_registered_port(root, uid)
    if registered != unit_port:
        raise Fail("registered port mismatch")
    if compare_configs(root, home, uid) != marker.get("configSha256"):
        raise Fail("config hash mismatch")
    template = read_template(root, uid)
    rendered = render_template(template, home, binary)
    if rendered != unit_bytes:
        raise Fail("unit differs from rendered template")
    if systemctl_runner is None:
        values = _systemctl_show(uid, UNIT_NAME)
    else:
        values = systemctl_runner(uid, UNIT_NAME)
    if values.get("LoadState") != "loaded":
        raise Fail("unit not loaded")
    if values.get("ActiveState") != "active" or values.get("SubState") != "running":
        raise Fail("unit not active")
    if values.get("FragmentPath") != str(unit_path):
        raise Fail("fragment path mismatch")
    if values.get("DropInPaths"):
        raise Fail("drop-ins present")
    if values.get("WorkingDirectory") != str(home):
        raise Fail("working directory mismatch")
    main_pid = values.get("MainPID")
    if not main_pid or not re.fullmatch(r"[0-9]+", main_pid):
        raise Fail("bad MainPID")
    pid = int(main_pid)
    if pid <= 0:
        raise Fail("bad MainPID")
    if _proc_uid(pid) != uid:
        raise Fail("pid uid mismatch")
    start_ticks = _proc_starttime(pid)
    exe = _proc_exe(pid)
    if exe.endswith(" (deleted)"):
        raise Fail("deleted exe")
    if os.path.realpath(exe) != os.path.realpath(binary):
        raise Fail("exe mismatch")
    _check_binary(binary, uid)
    raw_exec = values.get("ExecStart", "")
    path, argv0, args = _parse_execstart_bracket(raw_exec)
    if path != binary or argv0 != binary:
        raise Fail("ExecStart path mismatch")
    if args != ["serve", "--port", str(unit_port), "--hostname", "127.0.0.1"]:
        raise Fail("ExecStart argv mismatch")
    listener = _listener_inode(unit_port)
    if not listener:
        raise Fail("no listener")
    pid_inodes = _pid_fd_inodes(pid)
    if not (listener & pid_inodes):
        raise Fail("listener not owned by pid")
    if _proc_starttime(pid) != start_ticks:
        raise Fail("pid reused")
    return {
        "schemaVersion": OWNER_STATUS_SCHEMA,
        "manager": MANAGER,
        "installRoot": str(root),
        "ownerUid": uid,
        "unit": UNIT_NAME,
        "ownerUser": pwd.getpwuid(uid).pw_name,
        "port": unit_port,
        "mainPid": pid,
        "startTicks": start_ticks,
        "provenInstallationRoot": True,
        "ownerService": True,
    }


def record(root, home, uid):
    root = Path(root)
    home = Path(home)
    if not root.is_absolute() or root == Path("/") or root.is_symlink():
        raise Fail("bad root")
    if root.resolve() != root:
        raise Fail("root not normalized")
    if not home.is_absolute() or home.resolve() != home:
        raise Fail("home not normalized")
    if uid != os.getuid():
        raise Fail("uid mismatch")
    if uid == 0:
        raise Fail("uid must be nonzero")
    unit_path = home / UNIT_REL
    unit_bytes = regular(unit_path, uid, MAX_UNIT)
    binary, unit_port = parse_unit_execstart(unit_bytes)
    working = parse_unit_workingdir(unit_bytes)
    if working != str(home):
        raise Fail("working directory mismatch")
    config_hash = compare_configs(root, home, uid)
    template = read_template(root, uid)
    rendered = render_template(template, home, binary)
    if rendered != unit_bytes:
        raise Fail("unit differs from rendered template")
    registered = resolve_registered_port(root, uid)
    if registered != unit_port:
        raise Fail("registered port mismatch")
    if compare_configs(root, home, uid) != config_hash:
        raise Fail("config changed during admission")
    _check_binary(binary, uid)
    payload = {
        "schemaVersion": SCHEMA,
        "manager": MANAGER,
        "installRoot": str(root),
        "ownerUid": uid,
        "unitPath": str(unit_path),
        "unitSha256": sha256(unit_bytes),
        "binaryPath": binary,
        "port": unit_port,
        "configSha256": config_hash,
    }
    existing = read_marker(home, uid)
    if existing is not None:
        if existing == payload:
            return {"state": "idempotent", "marker": str(home / MARKER_REL)}
        raise Fail("marker exists with different state")
    created = write_marker(home, uid, payload)
    if not created:
        raise Fail("marker create race")
    return {"state": "recorded", "marker": str(home / MARKER_REL)}


def main(argv):
    if len(argv) != 3:
        print("usage: opencode-ownership.py {verify|record} ROOT", file=sys.stderr)
        return 2
    action, root = argv[1], argv[2]
    home = pwd.getpwuid(os.getuid()).pw_dir
    uid = os.getuid()
    try:
        if action == "verify":
            result = verify(root, home, uid)
        elif action == "record":
            result = record(root, home, uid)
        else:
            print("unknown action", file=sys.stderr)
            return 2
    except Fail as e:
        print("opencode-ownership: %s" % e, file=sys.stderr)
        return 1
    except Exception:
        print("opencode-ownership: internal error", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
