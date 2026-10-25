"""Real old native/Edge operations and bounded owner inference/reconciliation."""
import os
import fcntl
from pathlib import Path
import selectors
import subprocess
import time

from custody import Refused, decode, digest, encode, read
import preflight


def bounded_owner(command, request, *, uid, gid, environment, program, timeout):
    """Bound output while receiving it; descendants cannot keep a pipe forever."""
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        pass_fds=(program,), cwd='/', user=uid, group=gid, extra_groups=[], env=environment)
    output = bytearray()
    try:
        process.stdin.write(request)
        process.stdin.close()
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                if time.monotonic() >= deadline:
                    raise Refused('owner-operation-timeout')
                ready = selector.select(.1)
                if ready:
                    block = os.read(process.stdout.fileno(), 65536)
                    if not block:
                        break
                    output.extend(block)
                    if len(output) > 1024*1024:
                        raise Refused('owner-output-limit')
                elif process.poll() is not None:
                    break
        remaining = max(.01, deadline - time.monotonic())
        return subprocess.CompletedProcess(command, process.wait(timeout=remaining), bytes(output))
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()
        process.stdin.close()


def source_inputs(binding):
    from pixel_source_upgrade import SourceUpgrade, read_file
    owner = binding['owner']
    manager = SourceUpgrade(preflight.STATE / 'source-upgrade', Path(binding['install']), owner['uid'])
    plan = manager.journal()
    if plan is None or plan['phase'] != 'complete' or plan['outcome'] != 'applied':
        raise Refused('source-custody-conflict')
    result = {}
    for name, item in plan['after'].items():
        # Fixed owner entry points import these two shared modules at startup;
        # all sibling Python and installer renderers/helpers are also bound.
        # Unrelated dashboard routers, tests, doctor and macOS phases do not
        # execute in this path and are not treated as frozen owner programs.
        executable = (name.startswith('bin/') and name.endswith('.py')
                      or name.startswith('installers/lib/') and name.endswith(('.py', '.sh'))
                      or name in ('extensions/services/dashboard-api/model_memory.py',
                                  'extensions/services/dashboard-api/model_stores.py'))
        if executable:
            if item is None:
                continue
            raw = read(Path(binding['install']) / name, uid=owner['uid'], maximum=4*1024*1024)
            current, original_raw = read_file(Path(binding['install']), name, owner['uid'])
            if current != item or raw != original_raw:
                raise Refused('owner-source-changed')
            result[name] = item['sha256']
    if not {'bin/ods-host-agent.py', 'installers/lib/pixel-host-install.sh', 'installers/lib/sudo.sh'} <= result.keys():
        raise Refused('owner-source-binding-unavailable')
    return result


def make_bridge(binding, settings):
    from pixel_access_bridge import SystemdAccessBridge
    owner = binding['owner']
    # Preserve the installed coordinator's exact authenticated Edge route.
    # Keys remain in memory and request stdin; never argv, stdout, or receipts.
    if settings.get('edge_owner_key_sha256'):
        raw = read('/etc/ods/pixel-access-relay.key', uid=owner['uid'], maximum=4096, forbidden_mode=0o077)
        if digest(raw) != settings['edge_owner_key_sha256']:
            raise Refused('edge-owner-auth-changed')
        key = raw.decode('ascii')
    else:
        key = ''
        raw = read(Path(binding['install']) / '.env', uid=owner['uid'], maximum=1024*1024)
        for line in raw.decode().splitlines():
            if line.startswith('DASHBOARD_API_KEY='):
                key = line.partition('=')[2].strip().strip("\"'")
        if not key:
            raise Refused('edge-owner-auth-unavailable')
    return SystemdAccessBridge(binding['install'], key, installed_binary=settings['openclaw_bin'],
        gateway_owner=owner['name'], settings_data_dir=settings.get('settings_data_dir'),
        gateway_binding=settings.get('gateway_binding'), gateway_port=settings.get('gateway_port'))


class OwnerWorker:
    def __init__(self, package, manifest, binding, target, transaction, scope):
        self.package, self.binding, self.target, self.transaction, self.scope = Path(package), binding, target, transaction, scope
        self.sources = source_inputs(binding)
        self.program = manifest['ownerProgram']
        if digest(self.program) != manifest['files']['owner.py']:
            raise Refused('captured-owner-program-changed')

    def run(self, operation):
        if operation not in ('inference', 'reconcile', 'restart'):
            raise Refused('fixed-owner-operation-required')
        self.scope.require_current()
        if source_inputs(self.binding) != self.sources:
            raise Refused('owner-source-changed')
        owner = self.binding['owner']
        env_path = Path(self.binding['install']) / '.env'
        before = digest(read(env_path, uid=owner['uid'], maximum=1024*1024))
        request = dict(owner=owner, install=self.binding['install'], target=self.target, transaction=self.transaction)
        self.scope.prepare_owner()
        program = os.memfd_create('ods-fixed-owner', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        try:
            os.write(program, self.program)
            os.lseek(program, 0, os.SEEK_SET)
            fcntl.fcntl(program, fcntl.F_ADD_SEALS, fcntl.F_SEAL_SEAL | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE)
            result = bounded_owner([str(Path('/usr/bin/python3').resolve()), '-I', '/proc/self/fd/' + str(program), operation],
                encode(request), program=program, timeout=1000 if operation == 'reconcile' else 350,
                uid=owner['uid'], gid=owner['gid'],
                environment=dict(HOME=owner['home'], USER=owner['name'], LOGNAME=owner['name'], PATH='/usr/local/bin:/usr/bin:/bin',
                                 LANG='C.UTF-8', PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1'))
        finally:
            os.close(program)
            self.scope.drain_owner(owner['uid'])
        # Any timeout/failure escapes the controller; systemd then kills and
        # reaps the entire cgroup before Scope.start permits another resume.
        if result.returncode or len(result.stdout) > 1024*1024:
            raise Refused('owner-operation-failed')
        rows = result.stdout.splitlines()
        expected = dict(ok=True, operation=operation, target=self.target)
        if not rows or decode(rows[-1]) != expected:
            raise Refused('owner-operation-unconfirmed')
        if digest(read(env_path, uid=owner['uid'], maximum=1024*1024)) != before or source_inputs(self.binding) != self.sources:
            raise Refused('owner-inputs-changed')
        return expected
