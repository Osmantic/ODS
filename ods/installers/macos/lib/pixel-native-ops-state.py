"""Create a new Operations spool with Darwin ACLs, never repair one in place.

The caller must provision a dedicated broker identity and stop its service first.
This helper does not install the broker, approve requests, or start a service.
"""
import grp
import importlib.util
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
import tempfile


SPEC = importlib.util.spec_from_file_location('ops_custody',
    Path(__file__).resolve().parents[3] / 'bin/pixel_macos_custody.py')
custody = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(custody)

PRIVATE = ('request-archive', 'plans', 'approvals', 'runtime', 'private',
           'authority', 'authority/leases', '.ssh')
PROJECTIONS = ('results', 'events')
STORAGE = ('artifacts',)
SUBMISSIONS = ('requests', 'cancel')


def identities(gateway_uid, broker_uid, broker_gid):
    if (any(type(value) is not int or value <= 0 for value in
            (gateway_uid, broker_uid, broker_gid)) or gateway_uid == broker_uid):
        raise ValueError('separate-nonroot-operations-identities-required')
    gateway, broker = pwd.getpwuid(gateway_uid), pwd.getpwuid(broker_uid)
    group = grp.getgrgid(broker_gid)
    if (broker.pw_gid != broker_gid or broker.pw_shell not in
            ('/usr/bin/false', '/bin/false', '/usr/sbin/nologin')
            or broker_gid in os.getgrouplist(gateway.pw_name, gateway.pw_gid)
            or any(name != broker.pw_name for name in group.gr_mem)):
        raise ValueError('private-nonlogin-operations-group-required')
    return gateway, broker


def acl(path, entry):
    subprocess.run(['/bin/chmod', '+a', entry, str(path)], check=True,
                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                   stderr=subprocess.PIPE, timeout=10)


def provision(*, state, gateway_uid, broker_uid, broker_gid):
    if sys.platform != 'darwin' or os.geteuid() != 0:
        raise ValueError('macos-root-required')
    gateway, _ = identities(gateway_uid, broker_uid, broker_gid)
    state = Path(state)
    # The trusted parent also prevents a gateway from replacing the destination.
    with custody.protected_directory(state.parent):
        if state.name in ('', '.', '..') or os.path.lexists(state):
            raise ValueError('new-operations-state-required')
        stage = Path(tempfile.mkdtemp(prefix='.pixel-ops-', dir=state.parent))
        try:
            # All children remain inaccessible until the root's final chown.
            for name in PRIVATE + PROJECTIONS + STORAGE + SUBMISSIONS:
                path = stage / name
                path.mkdir(mode=0o700)
                os.chown(path, gateway_uid if name in SUBMISSIONS else broker_uid, broker_gid)
                path.chmod(0o2770 if name in SUBMISSIONS else
                           0o2750 if name in PROJECTIONS + STORAGE else 0o700)
            reader = 'user:' + gateway.pw_name + ' allow '
            for name in PROJECTIONS:
                acl(stage / name, reader + 'list,search,readattr,readextattr,readsecurity')
                acl(stage / name, reader + 'read,readattr,readextattr,readsecurity,file_inherit,only_inherit')
            acl(stage, reader + 'search,readattr,readsecurity')
            # Inventory is replaced atomically by the upstream broker; inheritance
            # supplies read access to each new inode without granting directory list.
            # Install this last so no private directory inherits a reader ACL.
            acl(stage, reader + 'read,readattr,readextattr,readsecurity,file_inherit,only_inherit')
            stage.chmod(0o750)
            os.chown(stage, broker_uid, broker_gid)
            os.rename(stage, state)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    return state


def provision_manager_runtime(*, runtime, gateway_uid, broker_uid, broker_gid):
    """Create a socket-only manager directory without sharing the broker group.

    Only the manager's socket may live here: logs and credentials belong in
    separate directories. The owner creates the socket; the broker receives
    inherited read/write access to it but cannot list or modify the directory.
    """
    if sys.platform != 'darwin' or os.geteuid() != 0:
        raise ValueError('macos-root-required')
    gateway, broker = identities(gateway_uid, broker_uid, broker_gid)
    runtime = Path(runtime)
    with custody.protected_directory(runtime.parent):
        if runtime.name in ('', '.', '..') or os.path.lexists(runtime):
            raise ValueError('new-manager-runtime-required')
        stage = Path(tempfile.mkdtemp(prefix='.pixel-manager-', dir=runtime.parent))
        try:
            acl(stage, 'user:' + broker.pw_name + ' allow search,readattr,readsecurity')
            acl(stage, 'user:' + broker.pw_name + ' allow read,write,readattr,readsecurity,file_inherit,only_inherit')
            os.chown(stage, gateway_uid, gateway.pw_gid)
            os.rename(stage, runtime)
        finally:
            if stage.exists(): shutil.rmtree(stage)
    return runtime
