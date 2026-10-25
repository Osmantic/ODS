"""One fixed root systemd unit owns every local recovery/owner descendant."""
import os
import ctypes
from pathlib import Path
import re
import signal
import subprocess
import time

from custody import Directory, Refused, digest, read


class Scope:
    def __init__(self, transaction, package, package_sha256):
        if not re.fullmatch('[a-f0-9]{64}', transaction) or not re.fullmatch('[a-f0-9]{64}', package_sha256):
            raise Refused('invalid-scope-binding')
        self.transaction, self.package, self.package_sha256 = transaction, Path(package), package_sha256
        self.unit = 'ods-bootstrap-recovery-' + transaction + '.service'
        self.description = 'ODS bootstrap recovery ' + package_sha256 + ':' + transaction
        self.group = '/system.slice/' + self.unit

    def properties(self):
        result = subprocess.run(['/usr/bin/systemctl', 'show', self.unit,
            '--property=LoadState,Description,MainPID,ActiveState,ControlGroup,User,Group,KillMode,SendSIGKILL,Delegate,NoNewPrivileges,ProtectControlGroups,Restart,PrivateTmp,AmbientCapabilities'],
            check=True, capture_output=True, text=True, timeout=10)
        return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)

    def validate(self, value):
        expected = dict(Description=self.description, User='root', Group='root', KillMode='control-group',
                        SendSIGKILL='yes', Delegate='no', NoNewPrivileges='yes', ProtectControlGroups='yes', Restart='no',
                        PrivateTmp='no', AmbientCapabilities='cap_setgid cap_setuid')
        if any(value.get(key) != wanted for key, wanted in expected.items()):
            raise Refused('scope-identity-changed')
        if value.get('ControlGroup') not in ('', self.group):
            raise Refused('scope-cgroup-changed')

    def require_drained(self):
        value = self.properties()
        if value.get('LoadState') != 'not-found':
            self.validate(value)
            if value.get('MainPID') != '0' or value.get('ActiveState') not in ('inactive', 'failed'):
                raise Refused('previous-worker-still-active')
        path = Path('/sys/fs/cgroup' + self.group)
        try:
            with Directory(path) as directory:
                events = directory.read('cgroup.events', maximum=4096).decode()
                rows = dict(line.split() for line in events.splitlines())
                if rows.get('populated') != '0' or directory.read('cgroup.procs', maximum=65536).strip():
                    raise Refused('previous-worker-descendants-remain')
        except FileNotFoundError:
            # An absent cgroup cannot contain a live process. Never substitute
            # an absent MainPID or the death of just one owner process for this.
            pass

    def require_current(self):
        value = self.properties()
        self.validate(value)
        if value.get('MainPID') != str(os.getpid()) or value.get('ControlGroup') != self.group:
            raise Refused('fixed-controller-service-required')
        if Path('/proc/self/cgroup').read_text().strip() != '0::' + self.group:
            raise Refused('controller-cgroup-mismatch')
        with Directory(Path('/sys/fs/cgroup' + self.group)) as directory:
            if directory.stat('cgroup.procs').st_uid != 0 or directory.stat('cgroup.procs').st_mode & 0o022:
                raise Refused('owner-cgroup-escape-authority')

    def start(self):
        self.require_drained()
        with Directory(self.package) as directory:
            if digest(directory.read('manifest.json')) != self.package_sha256:
                raise Refused('package-changed')
        python = Path('/usr/bin/python3').resolve(strict=True)
        read(python, maximum=32*1024*1024)
        command = ['/usr/bin/systemd-run', '--quiet', '--wait', '--collect', '--unit=' + self.unit,
                   '--description=' + self.description]
        for setting in ('Type=exec', 'User=root', 'Group=root', 'KillMode=control-group', 'SendSIGKILL=yes',
                        'TimeoutStopSec=5', 'TimeoutStartSec=1200', 'RuntimeMaxSec=1200', 'Restart=no',
                        'Delegate=no', 'NoNewPrivileges=yes', 'ProtectControlGroups=yes',
                        'AmbientCapabilities=CAP_SETUID CAP_SETGID', 'PrivateTmp=no',
                        'RestrictSUIDSGID=yes', 'UMask=0077', 'WorkingDirectory=/', 'StandardOutput=null', 'StandardError=null'):
            command.append('--property=' + setting)
        command.extend([str(python), '-I', str(self.package / 'loader.py'), '_controller', self.transaction])
        result = subprocess.run(command, capture_output=True, timeout=1230)
        deadline = time.monotonic() + 10
        while True:
            try:
                self.require_drained()
                break
            except Refused:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.1)
        if result.returncode:
            raise Refused('recovery-service-failed')

    def prepare_owner(self):
        self.require_current()
        # Adopt double-forked grandchildren so successful owner exit cannot
        # leave a writer behind while the controller verifies/restores service.
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(36, 1, 0, 0, 0):  # PR_SET_CHILD_SUBREAPER
            raise Refused('worker-subreaper-unavailable')

    def drain_owner(self, uid):
        self.require_current()
        deadline = time.monotonic() + 5
        while True:
            with Directory(Path('/sys/fs/cgroup' + self.group)) as directory:
                pids = {int(row) for row in directory.read('cgroup.procs', maximum=65536).split()} - {os.getpid()}
            for pid in pids:
                try:
                    handle = os.pidfd_open(pid)
                except ProcessLookupError:
                    continue
                try:
                    status = Path('/proc', str(pid), 'status').read_text()
                    owner = next(line.split()[1:] for line in status.splitlines() if line.startswith('Uid:'))
                    group = Path('/proc', str(pid), 'cgroup').read_text().strip()
                    if owner != [str(uid)]*4 or group != '0::' + self.group:
                        raise Refused('unexpected-worker-custody')
                    # pidfd signals cannot target a subsequently recycled PID.
                    signal.pidfd_send_signal(handle, signal.SIGSTOP)
                    signal.pidfd_send_signal(handle, signal.SIGKILL)
                except (ProcessLookupError, FileNotFoundError):
                    pass
                finally:
                    os.close(handle)
            child_remains = False
            while True:
                try:
                    pid, _ = os.waitpid(-1, os.WNOHANG)
                    if pid == 0:
                        child_remains = True
                        break
                except ChildProcessError:
                    break
            if not pids and not child_remains:
                return
            if time.monotonic() >= deadline:
                # Escapes the controller; the kernel/systemd service boundary
                # then kills all remaining descendants before any resume.
                raise Refused('owner-descendants-not-drained')
            time.sleep(.01)
