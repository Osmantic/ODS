"""PREPARED controls: actual subprocesses/pipes and disabled Source CLI only.

Darwin metadata/Seatbelt are explicitly simulated. Never a production test
flag, arbitrary authority callback, real account, SDK/model or Google request.
Do not execute this packet before Root's combined freeze/review GO.
"""

# ruff: noqa: E402
import ast
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import threading
import time

import pytest

if sys.platform != "linux" or not Path("/proc/self/stat").is_file():
    pytest.skip(
        "Linux/procfs explicit metadata simulation, not Darwin", allow_module_level=True
    )
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "bin")]
from test_native_source_broker_reader import broker, BOOT  # noqa: F401
from test_native_source_runtime_publication import built, source, package, helper  # noqa: F401
from test_native_source_child import identity, tree
from pixel_connections import custody as s
import pixel_macos_process as processes


# Only the disposable test driver has metadata substitutions. The application
# source file copied/pinned in its real bundle remains byte-for-byte production.
DRIVER = r"""
import ast,copy,ctypes,errno,importlib.util,json,os,platform,stat,sys,time,uuid
from pathlib import Path
from types import SimpleNamespace
config=Path(sys.argv[1])
cfg=json.loads(config.read_bytes())
args=sys.argv[2:]
entry=Path(cfg['program'])/'pixel_connections/custody.py'
spec=importlib.util.spec_from_file_location('source_consumer_fixture',entry)
s=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=s
spec.loader.exec_module(s)
for name,path in cfg['paths'].items():setattr(s,name,Path(path))
real_stat,real_lstat,real_fstat=os.stat,os.lstat,os.fstat
uid,gid=os.getuid(),os.getgid()
gate_inode=tuple(cfg['gateInode'])
world=Path(cfg['world'])
def virtual(info,path=None):
    data={key:getattr(info,key) for key in dir(info) if key.startswith('st_')}
    data['st_uid']=0
    data['st_gid']=cfg['brokerUid'] if (info.st_dev,info.st_ino)==gate_inode or path is not None and Path(path)==Path(cfg['paths']['NATIVE_STATE'])/'custody.json' else 0
    # Only relocated ancestors simulate protected Darwin directory metadata;
    # every object within the actual private world keeps its real mode/inode.
    if path is not None and stat.S_ISDIR(info.st_mode):
        value=Path(path)
        if value in world.parents:data['st_mode']=(info.st_mode&~0o7777)|0o755
    return SimpleNamespace(**data)
def fst(fd):
    try:path=os.readlink('/proc/self/fd/'+str(fd))
    except OSError:path=None
    return virtual(real_fstat(fd),path)
os.stat=lambda path,*a,**k:virtual(real_stat(path,*a,**k),path)
os.lstat=lambda path,*a,**k:virtual(real_lstat(path,*a,**k),path)
os.fstat=fst
for name in ('getuid','geteuid','getgid','getegid'):setattr(os,name,lambda:cfg['brokerUid'])
os.getgroups=lambda:[]
s._native_broker_platform=lambda:None
s._native_boot_uuid=lambda:cfg['boot']
s._native_clock_ns=lambda:time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW)
platform.system=lambda:'Darwin'
if cfg.get('delayBeforeReady'):time.sleep(cfg['delayBeforeReady'])
namespace={'ctypes':ctypes,'copy':copy,'errno':errno,'os':os,'uuid':uuid}
exec(compile(cfg['aclClass'],'<fixture-only-apple-metadata>','exec'),namespace)
acl=namespace['AppleACL']()
acl.acls={tuple(item['inode']):{'flags':0,'entries':[dict(row,uuid=bytes.fromhex(row['uuid'])) for row in item['entries']]} for item in cfg['acl']}
s._native_acl_library=lambda:acl
def no_acl(fd):
    assert not any('acl' in key.casefold() for key in os.listxattr(fd))
s._native_no_acl=no_acl
role=args[1]
identifier=None if len(args)<3 or args[2]=='-' else args[2]
# Actual Gate/public metadata/code proof before any application-peer import.
s._native_broker_application_imports(role,identifier)
import pixel_macos_custody as mac
from pixel_provider import runtime_custody as rt
import pixel_macos_process as proc
rt.DESCRIPTOR=s.NATIVE_RUNTIME_DESCRIPTOR
rt.NATIVE_MANIFEST=s.NATIVE_RUNTIME_MANIFEST
mac._require_no_acl=no_acl
rt._acl=no_acl
def linux_identity(pid):
    raw=Path('/proc/'+str(pid)+'/stat').read_text()
    fields=raw[raw.rfind(')')+2:].split()
    if fields[0] in ('Z','X'):raise ProcessLookupError()
    values=dict(line.split(':',1) for line in Path('/proc/'+str(pid)+'/status').read_text().splitlines() if ':' in line)
    u=[int(x) for x in values['Uid'].split()]
    g=[int(x) for x in values['Gid'].split()]
    if u[:3]!=[uid]*3 or g[:3]!=[gid]*3:raise ValueError('real Linux UID mismatch')
    return (pid,int(fields[19]),0,*([cfg['brokerUid']]*6),os.readlink('/proc/'+str(pid)+'/exe'))
def process_identity(pid,*,uid,gid,executable,allow_root=False):
    result=linux_identity(pid)
    if result[3:9]!=(uid,gid,uid,gid,uid,gid) or result[9]!=executable:raise ValueError('real executable mismatch')
    return result
proc.process_identity=process_identity
proc.process_tree_snapshot=lambda pid:(linux_identity(pid)[:3],)
marker=world/'source-import.json'
def audit(event,values):
    if event=='socket.connect':
        (world/'network-attempt').write_text('unexpected')
        raise AssertionError('No provider connection allowed in this fixture')
    if event=='exec' and values[0].co_filename==str(s.NATIVE_KIT/'broker.py'):
        marker.write_text(json.dumps({'sourceImported':True,'limbsDisabled':all(os.environ.get(key)=='0' for key in s.PREAUTH_FLAGS),'pid':os.getpid()}))
sys.addaudithook(audit)
try:
    raise SystemExit(s._native_child_main(args))
except s.CustodyError as error:
    print('error: '+error.code,file=sys.stderr)
    raise SystemExit(2)
"""


@pytest.fixture
def consumer(broker, monkeypatch):  # noqa: F811
    n = broker
    lines = s.parse_env(n.env.read_text())
    n.env.write_text(s.env_text(lines, {key: "0" for key in s.PREAUTH_FLAGS}))
    n.projection["launch"]["environment"]["sha256"] = helper.sha(n.env.read_bytes())
    n.gate.write_bytes(helper.encoded(n.projection))
    n.world = n.program.parent
    n.marker = n.world / "source-import.json"
    tree_ast = ast.parse((ROOT / "tests/test_native_source_custody.py").read_text())
    acl_class = next(
        node
        for node in tree_ast.body
        if isinstance(node, ast.ClassDef) and node.name == "AppleACL"
    )
    acl_source = ast.get_source_segment(
        (ROOT / "tests/test_native_source_custody.py").read_text(), acl_class
    )
    records = []
    for inode, value in n.acl.acls.items():
        records.append(
            {
                "inode": list(inode),
                "entries": [
                    dict(row, uuid=row["uuid"].hex()) for row in value["entries"]
                ],
            }
        )
    config = {
        "world": str(n.world),
        "program": str(n.program),
        "brokerUid": 60001,
        "boot": BOOT,
        "gateInode": n.projection["launch"]["environment"][
            "inode"
        ],  # overwritten below with actual Gate inode
        "paths": {
            key: str(getattr(s, key))
            for key in (
                "NATIVE_PROGRAM",
                "NATIVE_RUNTIMES",
                "NATIVE_STATE",
                "NATIVE_ENV",
                "NATIVE_KIT",
                "NATIVE_DEFINITIONS",
                "NATIVE_META",
            )
        },
        "acl": records,
        "aclClass": acl_source,
    }
    info = n.gate.stat()
    config["gateInode"] = [info.st_dev, info.st_ino]
    config["paths"].update(
        NATIVE_RUNTIME_DESCRIPTOR=str(helper.DESCRIPTOR),
        NATIVE_RUNTIME_MANIFEST=str(helper.RUNTIME_MANIFEST),
    )
    n.config = n.world / "driver-config.json"
    n.config.write_text(json.dumps(config, sort_keys=True))
    n.driver = n.world / "driver.py"
    n.driver.write_text(DRIVER)
    n.processes = []
    original_init = subprocess.Popen.__init__

    def popen(process, args, **kwargs):
        assert args[:2] == ["/usr/bin/sandbox-exec", "-D"]
        assert args[3] == "-f" and args[4] == str(n.kit / "source-sync.sb")
        assert args[6:9] == ["-I", "-S", "-B"]
        assert args[9] == str(n.program / "pixel_connections/custody.py")
        assert kwargs["shell"] is False and kwargs["close_fds"] is True
        assert len(kwargs["pass_fds"]) == 2
        assert not any(key.startswith("PIXEL_") for key in kwargs["env"])
        original_init(
            process,
            [args[5], "-I", "-S", "-B", str(n.driver), str(n.config), *args[10:]],
            **kwargs,
        )
        n.processes.append(process)

    # Popen remains its actual class. Only this fixture's constructor adapter
    # substitutes the absent Apple executable, after asserting the actual args.
    n.adapt = popen

    self_observer = processes.process_identity

    def observed(pid, *, uid, gid, executable, allow_root=False):
        if pid == os.getpid():
            return self_observer(pid, uid=uid, gid=gid, executable=executable)
        item = identity(pid)
        if item[9] != executable:
            raise ValueError("actual Linux executable differs")
        return (*item[:3], uid, gid, uid, gid, uid, gid, item[9])

    monkeypatch.setattr(processes, "process_identity", observed)
    monkeypatch.setattr(processes, "process_tree_snapshot", tree)
    yield n
    for process in n.processes:
        if process.poll() is None:
            process.kill()  # Fixture's exact owned handle only.
        process.wait(timeout=3)


def test_actual_post_go_child_executes_only_disabled_pinned_source(
    consumer, monkeypatch
):
    n = consumer
    real_popen = subprocess.Popen
    # Adapt the single process-creation boundary without replacing Popen's class.
    # A dedicated production spawn function will be the exact patch target.
    monkeypatch.setattr(subprocess.Popen, "__init__", n.adapt)
    result = s._native_consume_source("sync")
    assert subprocess.Popen is real_popen
    assert result == 0 and len(n.processes) == 1 and n.processes[0].poll() == 0
    receipt = json.loads(n.marker.read_bytes())
    assert receipt["sourceImported"] and receipt["limbsDisabled"]
    assert not (n.world / "network-attempt").exists()
    status = json.loads((n.state / "projection/status.json").read_bytes())
    assert status["schemaVersion"] == 1
    assert status["kind"] == "pixel-source-broker-status"
    assert set(status["sources"]) == {"email", "calendar", "social"}
    assert all(
        value["enabled"] is False and value["state"] == "disabled"
        for value in status["sources"].values()
    )


def test_actual_child_has_no_source_import_or_environment_until_go(consumer):
    n = consumer
    ready_r, ready_w = os.pipe()
    go_r, go_w = os.pipe()
    process = subprocess.Popen(
        [
            n.projection["hostPython"],
            "-I",
            "-S",
            "-B",
            str(n.driver),
            str(n.config),
            "--native-broker-post-seatbelt",
            "sync",
            "-",
            str(ready_w),
            str(go_r),
        ],
        stdin=subprocess.DEVNULL,
        close_fds=True,
        pass_fds=(ready_w, go_r),
        cwd=n.state,
        env=s._native_consumer_environment(),
    )
    n.processes.append(process)
    os.close(ready_w)
    os.close(go_r)
    try:
        assert select.select([ready_r], [], [], 10)[0]
        frame = os.read(ready_r, 2048)
        invocation = s._native_invocation(
            n.projection["launch"]["invocations"][s.NATIVE_LABEL + "sync"]["invocation"]
        )
        assert frame == s._native_barrier_frame(invocation, "ready")
        assert (
            not n.marker.exists() and not (n.state / "projection/status.json").exists()
        )
        os.write(go_w, s._native_barrier_frame(invocation, "go"))
        assert process.wait(timeout=10) == 0
        assert n.marker.exists() and not (n.world / "network-attempt").exists()
    finally:
        os.close(ready_r)
        os.close(go_w)


@pytest.mark.parametrize("failure", ["bad-go", "eof", "quiescing"])
def test_real_waiting_child_refuses_without_importing_source(consumer, failure):
    n = consumer
    ready_r, ready_w = os.pipe()
    go_r, go_w = os.pipe()
    process = subprocess.Popen(
        [
            n.projection["hostPython"],
            "-I",
            "-S",
            "-B",
            str(n.driver),
            str(n.config),
            "--native-broker-post-seatbelt",
            "sync",
            "-",
            str(ready_w),
            str(go_r),
        ],
        stdin=subprocess.DEVNULL,
        close_fds=True,
        pass_fds=(ready_w, go_r),
        cwd=n.state,
        env=s._native_consumer_environment(),
    )
    n.processes.append(process)
    os.close(ready_w)
    os.close(go_r)
    try:
        assert select.select([ready_r], [], [], 10)[0]
        invocation = s._native_invocation(
            n.projection["launch"]["invocations"][s.NATIVE_LABEL + "sync"]["invocation"]
        )
        assert os.read(ready_r, 2048) == s._native_barrier_frame(invocation, "ready")
        if failure == "bad-go":
            os.write(go_w, b'{"not":"the captured GO"}\n')
        elif failure == "eof":
            os.close(go_w)
            go_w = None
        else:
            value = json.loads(n.gate.read_bytes())
            value["phase"] = value["launch"]["phase"] = "quiescing"
            candidate = n.gate.with_name("fixture-gate-next")
            candidate.write_bytes(helper.encoded(value))
            candidate.chmod(0o640)
            candidate.replace(n.gate)
            os.write(go_w, s._native_barrier_frame(invocation, "go"))
        assert process.wait(timeout=10) == 2
        assert not n.marker.exists() and not (n.world / "network-attempt").exists()
    finally:
        os.close(ready_r)
        if go_w is not None:
            os.close(go_w)


def test_cancellation_after_real_ready_reaps_owned_child_without_source(
    consumer, monkeypatch
):
    n = consumer
    monkeypatch.setattr(subprocess.Popen, "__init__", n.adapt)
    original = processes.process_identity
    cancel = threading.Event()

    def observed(pid, **kwargs):
        value = original(pid, **kwargs)
        if pid != os.getpid():
            cancel.set()  # After actual stable Python witness, before GO.
        return value

    monkeypatch.setattr(processes, "process_identity", observed)
    assert s._native_consume_source("sync", cancel=cancel) == 2
    assert (
        cancel.is_set() and len(n.processes) == 1 and n.processes[0].poll() is not None
    )
    assert not n.marker.exists() and not (n.world / "network-attempt").exists()


def test_wrong_executable_witness_never_releases_source(consumer, monkeypatch):
    n = consumer
    monkeypatch.setattr(subprocess.Popen, "__init__", n.adapt)
    original = processes.process_identity

    def observed(pid, **kwargs):
        value = original(pid, **kwargs)
        return (*value[:9], "/invented/wrong-image") if pid != os.getpid() else value

    monkeypatch.setattr(processes, "process_identity", observed)
    assert s._native_consume_source("sync") == 2
    assert len(n.processes) == 1 and n.processes[0].poll() is not None
    assert not n.marker.exists()


def test_original_absolute_deadline_cleans_up_real_waiting_python(
    consumer, monkeypatch
):
    n = consumer
    value = json.loads(n.gate.read_bytes())
    invocation = value["launch"]["invocations"][s.NATIVE_LABEL + "sync"]["invocation"]
    now = time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW)
    invocation.update(
        issuedNs=str(now),
        admissionDeadlineNs=str(now + 2 * 10**9),
        workerDeadlineNs=str(now + 4 * 10**9),
    )
    n.gate.write_bytes(helper.encoded(value))
    config = json.loads(n.config.read_bytes())
    config["delayBeforeReady"] = 10
    n.config.write_text(json.dumps(config, sort_keys=True))
    monkeypatch.setattr(subprocess.Popen, "__init__", n.adapt)
    assert s._native_consume_source("sync") == 2
    assert len(n.processes) == 1 and n.processes[0].poll() is not None
    assert (
        time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW)
        < int(invocation["workerDeadlineNs"]) + 100_000_000
    )
    assert not n.marker.exists() and not (n.world / "network-attempt").exists()
