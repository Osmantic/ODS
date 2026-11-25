"""Prepare the authenticated WSL NAT control address for Docker Desktop."""
import ipaddress
import json
import os
from pathlib import Path
import platform
import stat
import subprocess
import sys
import tempfile

MANAGED = ('ODS_AGENT_BIND', 'ODS_AGENT_HOST', 'ODS_AGENT_ADDRESS_MODE')
MARKER = 'ODS_AGENT_ADDRESS_MODE=wsl-nat-bridge'
LEGACY_MODE = 'wsl-nat'
DOCKER_HOST = 'host.docker.internal'
PRIVATE = tuple(ipaddress.ip_network(v) for v in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))


class HelperError(Exception):
    pass


def _run(cmd, **kw):
    return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL, **kw)


def check_platform(runner=_run, release=None):
    release = platform.release() if release is None else release
    if 'microsoft' not in release.lower():
        return False
    # A stopped engine, an absent CLI, or a socket this user cannot read is an
    # ordinary WSL state, not an internal failure. Only a Docker Desktop engine
    # reaches the managed NAT route; anything else stays unmanaged and leaves
    # the environment untouched, so the caller reports the real reason instead
    # of an opaque {'error': 'internal'}.
    try:
        os_name = runner(['docker', 'info', '--format', '{{.OperatingSystem}}'], timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return 'docker desktop' in os_name.lower()


def check_root(root, uid=None):
    uid = os.getuid() if uid is None else uid
    if root != root.resolve(strict=True):
        raise HelperError('root')
    for directory in (root, root.parent):
        info = directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != uid
                or info.st_mode & 0o022):
            raise HelperError('root')
    return root.stat()


def detect_address(runner=_run):
    try:
        records = json.loads(runner(['ip', '-j', '-4', 'address', 'show', 'dev', 'eth0'], timeout=10))
    except (OSError, subprocess.SubprocessError, ValueError):
        # A missing iproute2, a failed query, or unparseable output all mean the
        # same thing here: there is no trustworthy address to bind. 'internal'
        # sent that to main()'s catch-all and told callers a bug had occurred.
        raise HelperError('address') from None
    choices = [item['local'] for interface in records if 'UP' in interface['flags']
               for item in interface['addr_info'] if item['scope'] == 'global' and item.get('family') == 'inet']
    if len(choices) != 1 or not any(ipaddress.ip_address(choices[0]) in net for net in PRIVATE):
        raise HelperError('address')
    return choices[0]


def _unquote(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        inner = value[1:-1]
        if value[0] in inner:
            raise HelperError('quote')
        return inner
    if any(c in value for c in ('"', "'", '\\', '\r', '\n')):
        raise HelperError('quote')
    return value


def parse_env(text):
    """Return (bind, host, mode, explicit) or raise HelperError."""
    seen = {}
    for line in text.splitlines():
        key, sep, value = line.partition('=')
        if not sep or key not in MANAGED:
            continue
        if key in seen:
            raise HelperError('duplicate')
        seen[key] = _unquote(value)
    bind = seen.get('ODS_AGENT_BIND', '')
    host = seen.get('ODS_AGENT_HOST', '')
    mode = seen.get('ODS_AGENT_ADDRESS_MODE', '')
    if mode and mode not in (LEGACY_MODE, 'wsl-nat-loopback', 'wsl-nat-bridge'):
        raise HelperError('marker')
    explicit = bool(bind or host)
    return bind, host, mode, explicit


def read_env(env, uid=None):
    uid = os.getuid() if uid is None else uid
    try:
        fd = os.open(env, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise HelperError('env') from None
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid
                or info.st_nlink != 1 or info.st_mode & 0o077
                or info.st_size > 1024 * 1024):
            raise HelperError('env')
        with os.fdopen(fd, 'r') as source:
            fd = -1
            text = source.read(1024 * 1024 + 1)
            if len(text.encode('utf-8')) > 1024 * 1024:
                raise HelperError('env')
            return text, info
    finally:
        if fd >= 0:
            os.close(fd)


def build_env(original, host):
    lines = [line for line in original.splitlines()
             if line.partition('=')[0] not in MANAGED]
    # Docker Desktop containers cannot route directly to WSL NAT eth0. The
    # owner-session Windows loopback relay can, and containers reach that relay
    # through host.docker.internal. Bind only the private WSL eth0 address.
    lines.append(f'ODS_AGENT_BIND={host}')
    lines.append(f'ODS_AGENT_HOST={DOCKER_HOST}')
    lines.append(MARKER)
    return '\n'.join(lines) + '\n'


def write_env(root, env, original, info, host, uid=None, detect=detect_address, root_info=None):
    uid = os.getuid() if uid is None else uid
    if not any(ipaddress.ip_address(host) in net for net in PRIVATE):
        raise HelperError('address')
    updated = build_env(original, host)
    if updated == original:
        return False
    temporary_fd, temporary_name = tempfile.mkstemp(prefix='.env-agent-', dir=root)
    try:
        os.fchmod(temporary_fd, 0o600)
        with os.fdopen(temporary_fd, 'w') as target:
            target.write(updated)
            target.flush()
            os.fsync(target.fileno())
        current = env.lstat()
        if (current.st_dev, current.st_ino, current.st_mtime_ns, current.st_size) != (
                info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size):
            raise HelperError('concurrent')
        current_root = check_root(root, uid)
        if root_info is not None and (current_root.st_dev, current_root.st_ino) != (root_info.st_dev, root_info.st_ino):
            raise HelperError('root')
        if detect() != host:
            raise HelperError('address')
        os.replace(temporary_name, env)
        directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return True
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def run(root, uid=None, runner=_run, detect=None, release=None):
    root = Path(root).absolute()
    env = root / '.env'
    uid = os.getuid() if uid is None else uid
    if not check_platform(runner=runner, release=release):
        return {'changed': False, 'mode': 'unmanaged', 'address': None}
    root_info = check_root(root, uid)
    original, info = read_env(env, uid)
    bind, host, mode, explicit = parse_env(original)
    if explicit and not mode:
        return {'changed': False, 'mode': 'explicit', 'address': None}
    try:
        network_mode = runner(['wslinfo', '--networking-mode'], timeout=10).strip()
    except (OSError, subprocess.SubprocessError):
        # An absent or unusable probe is a networking condition, not an internal
        # fault, so it must not reach main()'s catch-all. A failed probe cannot
        # rule out mirroring, so the mode stays unknown and the caller fails
        # loudly rather than guessing NAT and writing a bind it cannot justify.
        raise HelperError('networking') from None
    if network_mode == 'mirrored' and not mode:
        return {'changed': False, 'mode': 'unmanaged', 'address': None}
    if network_mode != 'nat':
        raise HelperError('networking')
    detect = detect or (lambda: detect_address(runner))
    address = detect()
    changed = write_env(root, env, original, info, address, uid=uid, detect=detect, root_info=root_info)
    return {'changed': changed, 'mode': 'wsl-nat-bridge', 'address': DOCKER_HOST}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print('usage: wsl-agent-address.py <install-root>', file=sys.stderr)
        return 2
    root = Path(argv[0])
    try:
        result = run(root)
    except HelperError as exc:
        print(json.dumps({'error': str(exc)}))
        return 2
    except Exception:
        print(json.dumps({'error': 'internal'}))
        return 2
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
