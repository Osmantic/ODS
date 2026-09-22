"""Opt-in root-only ACL proof; no accounts or services are installed.

Defaults to _sandbox as a test identity; ODS_TEST_OPS_BROKER_USER selects an
already provisioned dedicated broker. Neither identity is modified by this test.
All content is synthetic and lives in a fresh, removed /private/var/lib directory.
"""
import importlib.util
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
import tempfile
import time

import pytest


pytestmark = pytest.mark.skipif(
    sys.platform != 'darwin' or os.geteuid() != 0 or
    os.environ.get('ODS_TEST_OPS_ACL_LIVE') != '1', reason='explicit root macOS ACL qualification')


def test_real_distinct_identity_requests_projections_and_private_denial():
    spec = importlib.util.spec_from_file_location('ops_state_live',
        Path(__file__).resolve().parents[1] / 'installers/macos/lib/pixel-native-ops-state.py')
    ops = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ops)
    gateway = pwd.getpwuid(int(os.environ['SUDO_UID']))
    broker = pwd.getpwnam(os.environ.get('ODS_TEST_OPS_BROKER_USER', '_sandbox'))
    source = Path(os.environ['ODS_TEST_PIXEL_SOURCE'])
    node = os.environ['ODS_TEST_PIXEL_NODE']

    def run(user, argv, *, success=True):
        result = subprocess.run(argv, user=user.pw_uid, group=user.pw_gid,
            extra_groups=os.getgrouplist(user.pw_name, user.pw_gid), cwd='/',
            env={'PATH': '/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE': '1'},
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)
        assert (result.returncode == 0) == success, result.stderr
        return result.stdout

    def python(user, code, *args, success=True):
        return run(user, ['/usr/bin/python3', '-c', code, *map(str, args)], success=success)

    with tempfile.TemporaryDirectory(prefix='ods-ops-acl-', dir='/private/var/lib') as temporary:
        root = Path(temporary)
        root.chmod(0o755)
        for src, name in [(source / 'deploy/ops-broker/broker.py', 'broker.py'),
                          (source / 'plugin-ops/publish-json.js', 'publish-json.mjs')]:
            shutil.copyfile(src, root / name)
            (root / name).chmod(0o644)
        state = ops.provision(state=root / 'state', gateway_uid=gateway.pw_uid,
                              broker_uid=broker.pw_uid, broker_gid=broker.pw_gid)
        with pytest.raises(ValueError, match='new-operations-state-required'):
            ops.provision(state=state, gateway_uid=gateway.pw_uid,
                          broker_uid=broker.pw_uid, broker_gid=broker.pw_gid)
        publisher = ("import {publishBrokerJson} from " + repr((root / 'publish-json.mjs').as_uri()) +
                     "; await publishBrokerJson(process.argv[1], 'ops-fixture', {probe:true});")
        for directory in ops.SUBMISSIONS:
            run(gateway, [node, '--input-type=module', '-e', publisher, str(state / directory)])
            assert 'true' in python(broker, 'import sys; print(open(sys.argv[1]).read())',
                                    state / directory / 'ops-fixture.json')
        publish = ('import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); '
                   'from broker import atomic_json; atomic_json(Path(sys.argv[2]), {"revision": int(sys.argv[3])})')
        for relative in ['inventory.json', *(name + '/probe.json' for name in ops.PROJECTIONS)]:
            path = state / relative
            for revision in (1, 2):
                python(broker, publish, root, path, revision)
                assert str(revision) in python(gateway, 'import sys; print(open(sys.argv[1]).read())', path)
            python(gateway, 'import sys; open(sys.argv[1], "w").write("altered")', path, success=False)
            python(gateway, 'import os,sys; os.unlink(sys.argv[1])', path, success=False)
        for relative in ops.PRIVATE + ops.STORAGE:
            path = state / relative / 'secret.json'
            python(broker, publish, root, path, 1)
            python(gateway, 'import sys; open(sys.argv[1]).read()', path, success=False)
            python(gateway, 'import sys; open(sys.argv[1], "w").write("altered")', path, success=False)
        python(gateway, 'import os,sys; os.listdir(sys.argv[1])', state, success=False)
        assert not list(root.glob('.pixel-ops-*'))


def test_manager_socket_acl_without_broker_group_membership():
    spec = importlib.util.spec_from_file_location('manager_runtime_live',
        Path(__file__).resolve().parents[1] / 'installers/macos/lib/pixel-native-ops-state.py')
    ops = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ops)
    owner = pwd.getpwuid(int(os.environ['SUDO_UID']))
    broker = pwd.getpwnam('_ods_pixel_ops')
    outsider = pwd.getpwnam('_sandbox')
    assert broker.pw_gid not in os.getgrouplist(owner.pw_name, owner.pw_gid)
    with tempfile.TemporaryDirectory(prefix='ods-manager-acl-', dir='/private/var/lib') as temporary:
        root = Path(temporary)
        root.chmod(0o755)
        runtime = ops.provision_manager_runtime(runtime=root / 'run', gateway_uid=owner.pw_uid,
            broker_uid=broker.pw_uid, broker_gid=broker.pw_gid)
        endpoint = runtime / 'manager.sock'
        server = ('import socket,os,sys,ctypes; s=socket.socket(socket.AF_UNIX); '
            's.bind(sys.argv[1]); os.chmod(sys.argv[1],0o600); s.listen(); c,_=s.accept(); '
            'uid=ctypes.c_uint32(); gid=ctypes.c_uint32(); '
            'assert ctypes.CDLL(None).getpeereid(c.fileno(),ctypes.byref(uid),ctypes.byref(gid))==0; '
            'c.sendall(b"ok"); print(uid.value); c.close(); s.close()')
        client = ('import socket,sys; s=socket.socket(socket.AF_UNIX); s.settimeout(5); '
                  's.connect(sys.argv[1]); assert s.recv(2)==b"ok"')
        environment = {'PATH': '/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE': '1'}
        process = subprocess.Popen(['/usr/bin/python3', '-I', '-c', server, str(endpoint)],
            user=owner.pw_uid, group=owner.pw_gid, extra_groups=[], cwd='/', env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 10
            while not endpoint.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            assert endpoint.exists()
            rejected = subprocess.run(['/usr/bin/python3', '-I', '-c', client, str(endpoint)],
                user=outsider.pw_uid, group=outsider.pw_gid, extra_groups=[], cwd='/', env=environment,
                capture_output=True, text=True, timeout=10)
            assert rejected.returncode != 0 and 'PermissionError' in rejected.stderr
            accepted = subprocess.run(['/usr/bin/python3', '-I', '-c', client, str(endpoint)],
                user=broker.pw_uid, group=broker.pw_gid, extra_groups=[], cwd='/', env=environment,
                capture_output=True, text=True, timeout=10)
            assert accepted.returncode == 0, accepted.stderr
            stdout, stderr = process.communicate(timeout=10)
            assert process.returncode == 0 and stdout.strip() == str(broker.pw_uid), stderr
            for code in ('import os,sys; os.listdir(sys.argv[1])',
                         'import sys; open(sys.argv[1]+"/replace", "w").write("no")'):
                denied = subprocess.run(['/usr/bin/python3', '-I', '-c', code, str(runtime)],
                    user=broker.pw_uid, group=broker.pw_gid, extra_groups=[], cwd='/', env=environment,
                    capture_output=True, text=True, timeout=10)
                assert denied.returncode != 0 and 'PermissionError' in denied.stderr
            assert runtime.stat().st_gid == owner.pw_gid
        finally:
            if process.poll() is None:
                process.terminate()
                try: process.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=5)
