"""Service-manager operations used by the shared Pixel access transactions.

Platform adapters must prove stopped state independently of HTTP reachability.
Installation custody and access-mode isolation remain separate prerequisites.
"""
from pathlib import Path
import errno
import hashlib
import plistlib
import re
import sys
import time


def _boot_uuid(value, error):
    if not isinstance(value, str) or not re.fullmatch(
            r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', value):
        raise error('settings-process-unavailable')
    return value


def launchd_definition_digest(document, error):
    """Fingerprint a trusted plist snapshot; does not prove file custody."""
    if type(document) is not dict:
        raise error('launchd-provider-environment-unavailable')
    arguments = document.get('ProgramArguments')
    # The access coordinator executes isolated Python directly, without the
    # gateway's provider-editable env wrapper. Bind its entire plist unchanged.
    if (document.get('Label') == 'com.ods.pixel-access'
            and arguments == ['/usr/bin/python3', '-I',
                              '/usr/local/libexec/ods-pixel-access/access_mode_server.py']):
        return hashlib.sha256(plistlib.dumps(document, fmt=plistlib.FMT_BINARY, sort_keys=True)).hexdigest()
    if (not isinstance(arguments, list) or len(arguments) < 3
            or arguments[:2] != ['/usr/bin/env', '-i']
            or any(not isinstance(value, str) or not value for value in arguments)):
        raise error('launchd-provider-environment-unavailable')
    managed = {'OPENCLAW_REQUIRED_PLUGINS', 'PIXEL_ODS_PROVIDER_DEPLOYMENT'}
    assignments, command = [], []
    for value in arguments[2:]:
        if not command and '=' in value and value.partition('=')[0].isidentifier():
            if value.partition('=')[0] not in managed:
                assignments.append(value)
        else:
            command.append(value)
    environment = document.get('EnvironmentVariables', {})
    if not isinstance(environment, dict) or not command or any(
            value.split('=', 1)[0] in managed for value in environment):
        raise error('launchd-provider-environment-unavailable')
    normalized = dict(document)
    normalized['ProgramArguments'] = ['/usr/bin/env', '-i', *assignments, *command]
    if 'EnvironmentVariables' in normalized:
        normalized['EnvironmentVariables'] = dict(environment)
    return hashlib.sha256(plistlib.dumps(normalized, fmt=plistlib.FMT_BINARY, sort_keys=True)).hexdigest()


class SystemdGatewayService:
    def __init__(self, command, error, unit):
        self.command, self.error, self.unit = command, error, unit

    def pid(self, *, timeout=20, require_running=False):
        raw = self.command(['systemctl', 'show', self.unit, '--property=MainPID', '--value'], timeout=timeout)
        if not raw.isdecimal() or require_running and int(raw) <= 0:
            raise self.error('runtime-unavailable-or-busy')
        return int(raw)

    def assert_stopped(self):
        raw = self.command(['systemctl', 'show', self.unit, '--property=MainPID,ActiveState,ControlGroup'])
        fields = dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)
        if fields.get('MainPID') != '0' or fields.get('ActiveState') not in ('inactive', 'failed'):
            raise self.error('native-idle-unconfirmed')
        group = fields.get('ControlGroup', '')
        if group:
            root = Path('/sys/fs/cgroup')
            path = (root / group.lstrip('/')).resolve()
            if root not in path.parents:
                raise self.error('native-idle-unconfirmed')
            if path.exists() and (path / 'cgroup.procs').read_text().strip():
                raise self.error('native-idle-unconfirmed')

    def process_identity(self, *, timeout=20):
        return (self.pid(timeout=timeout, require_running=True),)

    def boot_identity(self):
        try:
            raw = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        except OSError:
            raise self.error('settings-process-unavailable') from None
        return _boot_uuid(raw, self.error)

    def transaction_identity(self, *, timeout=20):
        raw = self.command(['systemctl', 'show', self.unit,
            '--property=MainPID,ActiveState,ExecMainStartTimestampMonotonic'], timeout=timeout)
        fields = dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)
        try:
            pid, started = int(fields['MainPID']), int(fields['ExecMainStartTimestampMonotonic'])
        except (KeyError, ValueError):
            raise self.error('settings-process-unavailable') from None
        if fields.get('ActiveState') != 'active' or pid <= 0 or started <= 0:
            raise self.error('settings-process-not-active')
        return {'pid':pid, 'started':started, 'boot':self.boot_identity()}

    def boundary(self):
        return self.command(['systemctl', 'show', self.unit,
            '--property=ProtectSystem,ProtectHome,NoNewPrivileges,CapabilityBoundingSet,BindReadOnlyPaths,ReadOnlyPaths,PrivateTmp'])

    def stop(self, *, timeout=60):
        self.command(['systemctl', 'stop', self.unit], timeout=timeout)

    def reload(self):
        self.command(['systemctl', 'daemon-reload'])

    def restart(self, *, timeout=60):
        self.command(['systemctl', 'restart', self.unit], timeout=timeout)


class LaunchdGatewayService:
    """Native lifecycle only; not authorization to enable host access modes.

    verify is a trusted deployment-custody check, called before every operation.
    Production uses a system job; GUI domains support isolated qualification.
    No request may supply target or the verifier.
    """
    def __init__(self, command, error, target, verify, *, process=None, plist=None,
                 verify_definition=None, save_stop=None, load_stop=None, allow_root_process=False):
        if (not isinstance(target, str) or not re.fullmatch(
                r'(?:system|gui/[0-9]+)/[A-Za-z0-9][A-Za-z0-9.-]{0,127}', target)
                or not callable(verify)):
            raise error('invalid-launchd-service')
        self.command, self.error, self.target, self.verify = command, error, target, verify
        self.verify_definition = verify_definition or verify
        if (save_stop is None) != (load_stop is None) or (save_stop is not None
                and (not callable(save_stop) or not callable(load_stop))):
            raise error('invalid-launchd-stop-store')
        self.save_stop, self.load_stop = save_stop, load_stop
        self.is_launchd = True
        # Supplied by the protected deployment, not by an API request or plist
        # inferred from the currently running user-owned qualification job.
        self.process = dict(process) if isinstance(process, dict) else None
        if (type(allow_root_process) is not bool or allow_root_process and
                (target != 'system/com.ods.pixel-native-promoter' or not self.process or self.process.get('uid') != 0)):
            raise error('invalid-root-service-process-opt-in')
        self.allow_root_process = allow_root_process
        self.plist = Path(plist) if isinstance(plist, str) else plist
        self._stopping_tree = None

    def _boot_identity(self):
        raw = self.command(['/usr/sbin/sysctl', '-n', 'kern.bootsessionuuid'])
        return _boot_uuid(raw.strip().lower(), self.error)

    def _restore_stop(self):
        if self.load_stop is None:
            return
        value = self.load_stop()
        if (type(value) is not dict or set(value) != {'target', 'boot', 'definition', 'processes'}
                or value['target'] != self.target or value['boot'] != self._boot_identity()
                or value['definition'] != self.definition()
                or type(value['processes']) is not list or not 1 <= len(value['processes']) <= 4096):
            raise self.error('native-stop-witness-unavailable')
        processes = value['processes']
        if (any(type(row) is not list or len(row) != 3
                or any(type(item) is not int for item in row)
                or not 0 < row[0] <= 2147483647 or row[1] <= 0 or not 0 <= row[2] < 1000000
                for row in processes)
                or len({row[0] for row in processes}) != len(processes)):
            raise self.error('native-stop-witness-unavailable')
        self._stopping_tree = tuple(tuple(row) for row in processes)

    def process_identity(self, *, timeout=20):
        from pixel_macos_process import ProcessIdentityError, process_identity
        if self.process is None or set(self.process) != {'uid', 'gid', 'executable'}:
            raise self.error('gateway-process-specification-required')
        deadline = time.monotonic() + timeout
        def budget():
            value = deadline - time.monotonic()
            if value <= 0:
                raise self.error('runtime-operation-timeout')
            return value
        pid = self.pid(timeout=budget(), require_running=True)
        try:
            identity = process_identity(pid, **self.process,
                **({'allow_root': True} if self.allow_root_process else {}))
        except ProcessIdentityError as exc:
            raise self.error(str(exc)) from None
        if self.pid(timeout=budget(), require_running=True) != pid:
            raise self.error('gateway-process-changed')
        budget()
        return identity

    def transaction_identity(self, *, timeout=20):
        deadline = time.monotonic() + timeout
        def budget():
            value = deadline - time.monotonic()
            if value <= 0:
                raise self.error('runtime-operation-timeout')
            return value
        before = self.process_identity(timeout=budget())
        raw = self.command(['/usr/sbin/sysctl', '-n', 'kern.bootsessionuuid'], timeout=budget())
        boot = _boot_uuid(raw.strip().lower(), self.error)
        after = self.process_identity(timeout=budget())
        if before != after:
            raise self.error('gateway-process-changed')
        budget()
        # Darwin exposes birth time as epoch microseconds, not systemd's
        # monotonic start. Existing restart checks remain fail-closed if the
        # clock moves backwards; never synthesize a later timestamp.
        return {'pid':before[0], 'started':before[1] * 1000000 + before[2], 'boot':boot}

    def installation_binding(self, filename, expected):
        """Require both protected plist bytes and the matching loaded job.

        expected is supplied by trusted installation metadata, never inferred
        from the user's current plist. This is not a process isolation proof.
        """
        from pixel_macos_custody import (CustodyError, launchd_document_binding,
                                         verify_loaded_launchd_definition)
        self.verify()
        try:
            binding = launchd_document_binding(filename, expected)
            raw = self.command(['/bin/launchctl', 'print', self.target])
            verify_loaded_launchd_definition(raw, self.target, filename, expected)
            if launchd_document_binding(filename, expected) != binding:
                raise CustodyError('launchd-document-changed')
        except CustodyError as exc:
            raise self.error(str(exc)) from None
        return binding

    def definition(self, _owned_plist=None):
        """Fingerprint the loaded gateway definition without managed env keys.

        Provider activation edits only two explicit ``env -i`` assignments in
        the trusted plist. Everything else stays part of the base service
        definition and remains bound across recovery.
        """
        if not isinstance(self.plist, (str, Path)):
            raise self.error('launchd-plist-required')
        try:
            from pixel_macos_custody import CustodyError, protected_bytes
            raw = protected_bytes(self.plist)
            document = plistlib.loads(raw)
        except (CustodyError, OSError, ValueError, TypeError, plistlib.InvalidFileException):
            raise self.error('launchd-plist-unavailable') from None
        return launchd_definition_digest(document, self.error)

    def pid(self, *, timeout=20, require_running=False):
        self.verify()
        raw = self.command(['/bin/launchctl', 'print', self.target], timeout=timeout)
        # launchctl has no JSON status API. Match only its top-level fields;
        # nested resource groups also have a state and are not the gateway.
        lines = raw.splitlines()
        if not lines or not lines[0].startswith(self.target + ' = {'):
            raise self.error('gateway-process-mismatch')
        states = re.findall(r'^\tstate = ([^\n]+)$', raw, re.MULTILINE)
        pids = re.findall(r'^\tpid = ([^\n]+)$', raw, re.MULTILINE)
        if len(states) != 1 or len(pids) > 1 or pids and not pids[0].isdecimal():
            raise self.error('gateway-process-mismatch')
        pid = int(pids[0]) if pids else 0
        if states[0] == 'running' and pid > 0:
            return pid
        if not require_running and states[0] in ('not running', 'waiting', 'spawn scheduled') and pid == 0:
            return 0
        raise self.error('runtime-unavailable-or-busy')

    def restart(self, *, timeout=60):
        self.verify()
        self.command(['/bin/launchctl', 'kickstart', '-k', self.target], timeout=timeout)

    def stop(self, *, timeout=60):
        # Capture the complete live tree before bootout. The caller still
        # obtains an independent stopped-state proof after launchd unloads it.
        self.verify()
        if sys.platform == 'darwin':
            from pixel_macos_process import ProcessIdentityError, process_tree_snapshot
            try:
                self._stopping_tree = process_tree_snapshot(
                    self.pid(timeout=min(timeout, 20), require_running=True))
            except ProcessIdentityError as exc:
                raise self.error(str(exc)) from None
        if self.save_stop is not None:
            if not self._stopping_tree or len(self._stopping_tree) > 4096:
                raise self.error('native-stop-witness-unavailable')
            self.save_stop({'target': self.target, 'boot': self._boot_identity(),
                            'definition': self.definition(),
                            'processes': [list(row) for row in self._stopping_tree]})
        self.command(['/bin/launchctl', 'bootout', self.target], timeout=timeout)

    def assert_stopped(self):
        self._restore_stop()
        if self._stopping_tree is None:
            raise self.error('native-idle-unconfirmed')
        from pixel_macos_process import ProcessIdentityError, process_birth
        for _ in range(120):
            try:
                raw = self.command(['/bin/launchctl', 'print', self.target])
                if not raw:
                    raise self.error('native-idle-unconfirmed')
            except self.error as error:
                if (getattr(error, 'code', str(error)) != 'host-command-failed'
                        or getattr(error, 'returncode', None) != 113):
                    raise
                raw = ''
            if raw:
                time.sleep(0.25)
                continue
            survivors = []
            for pid, started, usec in self._stopping_tree:
                try:
                    if process_birth(pid) == (started, usec):
                        survivors.append(pid)
                except ProcessIdentityError as error:
                    if (str(error) != 'gateway-process-unavailable'
                            or getattr(error, 'errno', None) != errno.ESRCH):
                        raise self.error(str(error)) from None
            if not survivors:
                return
            time.sleep(0.25)
        raise self.error('native-idle-unconfirmed')

    def boundary(self):
        raise self.error('macos-isolation-adapter-missing')

    def reload(self):
        # The provider transaction booted the job out before replacing its
        # plist. Bootstrap loads that exact protected file; kickstart alone
        # would retain launchd's previous in-memory definition.
        if not isinstance(self.plist, (str, Path)):
            raise self.error('launchd-plist-required')
        self.assert_stopped()
        self.verify_definition()
        domain = self.target.rsplit('/', 1)[0]
        if self.save_stop is not None:
            # Once a new job can start, the old tree can no longer prove idle.
            # Persist this before bootstrap so a controller crash cannot reuse
            # an earlier process witness for a later gateway instance.
            self.save_stop(None)
        self._stopping_tree = None
        self.command(['/bin/launchctl', 'bootstrap', domain, str(self.plist)])
