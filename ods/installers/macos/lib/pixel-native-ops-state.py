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
import stat
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
RETAINED_HOME = Path('/private/var/lib/pixel-ops-broker')


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


def reusable_empty_home(state, *, broker_uid=None, broker_gid=None):
    """Read-only proof for the one retained identity-only home on macOS."""
    state = Path(state)
    if state != RETAINED_HOME:
        return False
    try:
        fd = os.open(state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return False
    try:
        info = os.fstat(fd)
        if broker_uid is None or broker_gid is None:
            broker = pwd.getpwnam('_ods_pixel_ops')
            broker_uid, broker_gid = broker.pw_uid, broker.pw_gid
        return (stat.S_ISDIR(info.st_mode) and info.st_uid == broker_uid
            and info.st_gid == broker_gid and stat.S_IMODE(info.st_mode) == 0o750
            and not os.listdir(fd))
    except (OSError, KeyError):
        return False
    finally:
        os.close(fd)


def provision(*, state, gateway_uid, broker_uid, broker_gid, reuse_empty_home=False):
    if sys.platform != 'darwin' or os.geteuid() != 0:
        raise ValueError('macos-root-required')
    gateway, _ = identities(gateway_uid, broker_uid, broker_gid)
    state = Path(state)
    # The trusted parent also prevents a gateway from replacing the destination.
    with custody.protected_directory(state.parent):
        if state.name in ('', '.', '..'):
            raise ValueError('new-operations-state-required')
        retained = os.path.lexists(state)
        if retained and not (reuse_empty_home and reusable_empty_home(state,
                broker_uid=broker_uid, broker_gid=broker_gid)):
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
            if retained:
                # System Policy may prohibit unlinking a service-account home.
                # Secure the verified empty inode first; a failure here leaves
                # the installation journal active for explicit recovery.
                fd = os.open(state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    if not reusable_empty_home(state, broker_uid=broker_uid,
                            broker_gid=broker_gid):
                        raise ValueError('new-operations-state-required')
                    os.fchown(fd, 0, 0)
                    os.fchmod(fd, 0o700)
                    subprocess.run(['/bin/chmod', '-N', str(state)], check=True,
                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, timeout=10)
                    # POSIX mode/ownership do not revoke named Darwin ACL
                    # grants. Verify their removal on the pinned root-owned
                    # inode before checking for additions made while writable.
                    custody._verify_fd(fd, directory=True)
                    if os.listdir(fd):
                        raise ValueError('operations-home-changed-during-provision')
                    for child in stage.iterdir():
                        os.rename(child, state / child.name)
                    reader = 'user:' + gateway.pw_name + ' allow '
                    acl(state, reader + 'search,readattr,readsecurity')
                    acl(state, reader + 'read,readattr,readextattr,readsecurity,file_inherit,only_inherit')
                    os.fchmod(fd, 0o750)
                    os.fchown(fd, broker_uid, broker_gid)
                    os.fsync(fd)
                finally:
                    os.close(fd)
            else:
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
