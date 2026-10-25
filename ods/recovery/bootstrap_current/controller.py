"""Explicit one-shot legacy recovery; existing guards and finish remain authoritative."""
import argparse
import contextlib
import fcntl
import os
from pathlib import Path
import pwd
import re
import stat
import threading

from custody import Directory, Refused, decode, digest, encode, identity, process_start, read
import adapters
import preflight
from quarantine import Quarantine
from scope import Scope
from socket_custody import OwnerSocket

CLAIMS = Path('/var/lib/ods-bootstrap-recovery')


def claim_directory(transaction, create=False):
    if re.fullmatch('[a-f0-9]{64}', transaction) is None:
        raise Refused('invalid-transaction')
    if create:
        with Directory(CLAIMS.parent) as parent:
            try:
                parent.mkdir(CLAIMS.name).close()
            except FileExistsError:
                pass
        with Directory(CLAIMS) as parent:
            try:
                parent.mkdir(transaction).close()
            except FileExistsError:
                # A death before claim publication can leave only this empty
                # root-created directory. No existing receipt is overwritten.
                with Directory(CLAIMS / transaction) as existing:
                    if os.listdir(existing.fd) or stat.S_IMODE(existing.validate().st_mode) != 0o700:
                        raise Refused('claim-already-exists') from None
    return Directory(CLAIMS / transaction)


def lock_key(path):
    data = os.fsencode(path) + b'\0'
    length, crc = len(data), 0
    while length:
        data += bytes([length & 255])
        length >>= 8
    for byte in data:
        crc ^= byte << 24
        for _ in range(8):
            crc = ((crc << 1) ^ (0x04c11db7 if crc & 0x80000000 else 0)) & 0xffffffff
    return str(crc ^ 0xffffffff)


class Locks:
    def __init__(self):
        self.handles = []
        self.directories = []

    def open(self, directory, name, uid):
        created = False
        try:
            fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory)
            created = True
        except FileExistsError:
            fd = os.open(name, os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o077:
                raise Refused('unsafe-lock')
            if created:
                os.fchown(fd, uid, -1)
            if os.fstat(fd).st_uid != uid:
                raise Refused('unsafe-lock-owner')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.handles.append((fd, os.dup(directory), name, identity(os.fstat(fd))))
        except BaseException:
            os.close(fd)
            raise

    def acquire(self, binding):
        try:
            parent = os.open('/tmp', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                info = os.fstat(parent)
                if info.st_uid != 0 or not info.st_mode & stat.S_ISVTX:
                    raise Refused('unsafe-lock-parent')
                for uid in sorted({0, binding['owner']['uid']}):
                    name = 'ods-model-lifecycle-' + str(uid)
                    made = False
                    try:
                        os.mkdir(name, 0o700, dir_fd=parent)
                        made = True
                    except FileExistsError:
                        pass
                    directory = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
                    try:
                        info = os.fstat(directory)
                        if info.st_uid != (0 if made else uid) or info.st_mode & 0o077:
                            raise Refused('unsafe-lifecycle-directory')
                        if made:
                            os.fchown(directory, uid, -1)
                        self.directories.append((Path('/tmp') / name, os.dup(directory), identity(os.fstat(directory)), uid))
                        self.open(directory, 'ods-model-lifecycle-' + lock_key(binding['install']) + '.lock', uid)
                    finally:
                        os.close(directory)
            finally:
                os.close(parent)
            for path in (preflight.STATE, preflight.STATE / 'source-upgrade'):
                with Directory(path) as directory:
                    self.directories.append((path, os.dup(directory.fd), identity(directory.validate()), 0))
                    self.open(directory.fd, 'lock', 0)
            return self
        except BaseException:
            self.close()
            raise

    def validate(self):
        if not self.handles:
            raise Refused('locks-required')
        for path, fd, original, uid in self.directories:
            named = path.lstat()
            if (identity(os.fstat(fd)) != original or identity(named) != original
                    or not stat.S_ISDIR(named.st_mode) or named.st_uid != uid or named.st_mode & 0o077):
                raise Refused('lock-directory-replaced')
        for fd, directory, name, original in self.handles:
            if identity(os.fstat(fd)) != original or identity(os.stat(name, dir_fd=directory, follow_symlinks=False)) != original:
                raise Refused('lock-replaced')

    def close(self):
        while self.handles:
            fd, directory, _, _ = self.handles.pop()
            os.close(fd)
            os.close(directory)
        while self.directories:
            os.close(self.directories.pop()[1])


class Recovery:
    def __init__(self, package, manifest, compatibility, transaction):
        self.package, self.compatibility = package, compatibility
        self.records = claim_directory(transaction)
        self.claim = decode(self.records.read('claim.json'))
        if (self.claim.get('version') != 1 or self.claim.get('transaction') != transaction
                or self.claim.get('packageSha256') != manifest['capturedSha256']
                or digest(read(package / 'manifest.json')) != manifest['capturedSha256']
                or self.claim.get('phase') not in ('prepared', 'claimed', 'reconciling', 'finishing', 'completed', 'restored', 'cancelled-held', 'restored-held')
                or self.claim.get('action') not in ('recover', 'cancel')
                or type(self.claim.get('mutated')) is not bool):
            raise Refused('claim-invalid')
        self.binding = self.claim['binding']
        self.target = self.claim['target']
        self.transaction = transaction
        self.scope = Scope(transaction, package, self.claim['packageSha256'])
        self.scope.require_current()
        self.locks = Locks().acquire(self.binding)
        self.serial = threading.RLock()
        self.socket = None
        settings_raw = read(preflight.SETTINGS, maximum=8192)
        if digest(settings_raw) != self.binding['settingsSha256']:
            raise Refused('installed-settings-changed')
        original = adapters.make_bridge(self.binding, decode(settings_raw))
        recovery = self
        class Borrowed(type(original)):
            @contextlib.contextmanager
            def locked(self):
                with recovery.serial:
                    recovery.locks.validate()
                    yield
        self.bridge = Borrowed.__new__(Borrowed)
        self.bridge.__dict__.update(original.__dict__)
        self.service = Quarantine(self.records, transaction, compatibility)
        self.worker = adapters.OwnerWorker(package, manifest, self.binding, self.target, transaction, self.scope)

    def save(self, phase):
        self.claim['phase'] = phase
        self.records.write('claim.json', encode(self.claim))

    def custody(self):
        self.scope.require_current()
        self.locks.validate()
        preflight.capture_bundle(self.compatibility)
        if digest(read(preflight.SETTINGS, maximum=8192)) != self.binding['settingsSha256']:
            raise Refused('installed-settings-changed')
        owner = pwd.getpwnam(self.binding['owner']['name'])
        if dict(name=owner.pw_name, uid=owner.pw_uid, gid=owner.pw_gid, home=owner.pw_dir) != self.binding['owner']:
            raise Refused('owner-changed')
        if digest(read(preflight.UNIT_PATH)) != self.binding['unitSha256']:
            raise Refused('original-unit-changed')
        with Directory(preflight.STATE) as directory:
            if any(name in os.listdir(directory.fd) for name in preflight.RELEASE):
                raise Refused('retained-release-custody')
        from pixel_source_upgrade import SourceUpgrade
        manager = SourceUpgrade(preflight.STATE / 'source-upgrade', Path(self.binding['install']), owner.pw_uid)
        plan = manager.journal()
        if plan is None or plan['phase'] != 'complete' or plan['hold'] == self.transaction:
            raise Refused('source-custody-conflict')
        manager.verify_mirror()
        with Directory(preflight.STATE / 'source-upgrade') as directory:
            if {name: digest(directory.read(name)) for name in self.binding['source']} != self.binding['source']:
                raise Refused('original-source-custody-changed')
        if adapters.source_inputs(self.binding) != self.claim['ownerSources']:
            raise Refused('owner-source-changed')

    def held(self):
        self.custody()
        journal = self.bridge.model_journal(self.transaction)
        for key in ('kind', 'transaction_id', 'token', 'start_config_sha256', 'configured_mode'):
            if journal[key] != self.binding['original'][key]:
                raise Refused('original-journal-changed')
        if not self.service.stopped():
            raise Refused('coordinator-not-quarantined')

    def dispatch(self, uid, operation, request):
        with self.serial:
            if uid not in (0, self.binding['owner']['uid']):
                raise Refused('owner-required')
            self.held()
            if operation == 'model-status' and request is None:
                return self.bridge.model_status()
            if operation == 'installer-model-verify' and request == {'transaction_id': self.transaction}:
                return self.bridge.verify_installer_model_access(self.transaction)
            raise Refused('fixed-operation-required')

    def current(self):
        self.custody()
        self.bridge.discover(allow_installing=True)
        config, native, edge = self.bridge.worker(), self.bridge.native(), self.bridge.edge()
        verified = decode(read(preflight.STATE / 'verified.json', maximum=8192))
        pid, = self.bridge.gateway_service.process_identity()
        start = process_start(pid)
        if start is None:
            raise Refused('native-process-unavailable')
        raw = read(Path(self.binding['owner']['home']) / '.openclaw/openclaw.json', uid=self.binding['owner']['uid'], maximum=2*1024*1024)
        from pixel_model_contract import projection
        target = projection(decode(raw))['contract']
        proof = native.get('proof')
        if (any(target.get(key) != value for key, value in self.target.items())
                or config.get('config_sha256') != digest(raw) or config.get('config_sha256') != verified.get('config_sha256')
                or config.get('configured_status') != self.binding['original']['configured_mode']
                or pid != native.get('pid') or pid != verified.get('pid') or native.get('available') is not True
                or native.get('stopped') or native.get('active') != 0 or edge.get('streams') != 0
                or edge.get('capability') != 'available' or type(proof) is not dict or proof.get('executed') is not True
                or proof.get('pid') != pid or proof.get('mode') != config.get('configured_status')
                or proof != verified.get('proof') or verified.get('boundary') != self.bridge.unit_boundary()):
            raise Refused('fresh-current-state-unproven')
        return dict(config=config, pid=pid, start=start, boundary=verified['boundary'],
                    nativeRevision=native['revision'], edgeRevision=edge['revision'], target=self.target)

    def terminal(self):
        if self.claim['phase'] in ('cancelled-held', 'restored-held'):
            self.held()
            raw = read(Path(self.binding['owner']['home']) / '.openclaw/openclaw.json', uid=self.binding['owner']['uid'])
            if self.claim['mutated'] or digest(raw) != self.binding['nativeSha256']:
                raise Refused('cancel-requires-unmodified-config')
            self.bridge.verify_installer_model_access(self.transaction)
            return
        done = self.bridge.model_completion(dict(transaction_id=self.transaction, outcome='applied'))
        if done is None or self.bridge.pending() is not None:
            raise Refused('protected-completion-required')
        current = self.current()
        if (current != decode(self.records.read('terminal.json')) or done['config_sha256'] != current['config']['config_sha256']
                or self.bridge.native()['phase'] != 'idle' or self.bridge.edge()['phase'] != 'idle'):
            raise Refused('completion-current-binding-changed')
        self.worker.run('inference')

    def run(self):
        self.custody()
        # Reestablish quarantine even after partial restoration: no stale
        # completion receipt can start the coordinator before a fresh proof.
        self.service.hold()
        self.socket = OwnerSocket(Path('/run/ods-pixel-access'), self.records, self.transaction,
                                  self.binding['owner'], self.dispatch, self.service.stopped, self.scope)
        if self.claim['phase'] in ('finishing', 'completed', 'restored'):
            if self.bridge.model_completion(dict(transaction_id=self.transaction, outcome='applied')) is None:
                self.held()
                self.worker.run('inference')
                if self.current() != decode(self.records.read('terminal.json')):
                    raise Refused('finish-current-binding-changed')
                self.bridge.model_finish(dict(transaction_id=self.transaction, outcome='applied'))
            self.save('completed')
            self.socket.recover()
            self.socket = None
            self.service.restore(self.terminal, None)
            self.save('restored')
            return
        self.held()
        self.bridge.verify_installer_model_access(self.transaction)
        if self.claim['action'] == 'cancel' or self.claim['phase'] in ('cancelled-held', 'restored-held'):
            self.save('cancelled-held')
            self.socket.recover()
            self.socket = None
            self.service.restore(self.terminal, None)
            self.save('restored-held')
            return
        self.worker.run('inference')
        self.socket.start()
        self.claim['mutated'] = True
        self.save('reconciling')
        self.worker.run('reconcile')
        self.worker.run('restart')
        self.held()
        self.bridge.verify_installer_model_access(self.transaction)
        self.worker.run('inference')
        self.records.write('terminal.json', encode(self.current()))
        self.save('finishing')
        self.bridge.model_finish(dict(transaction_id=self.transaction, outcome='applied'))
        self.save('completed')
        self.service.restore(self.terminal, self.socket)
        self.socket = None
        self.save('restored')

    def close(self):
        try:
            if self.socket is not None:
                self.socket.close()
        finally:
            self.locks.close()
            self.records.close()


def main(argv, package, manifest, compatibility):
    parser = argparse.ArgumentParser(description='Restricted legacy current-route recovery; no source or root-bundle upgrade.')
    parser.add_argument('action', choices=('preflight', 'recover', 'resume', 'cancel', '_controller'))
    parser.add_argument('transaction', nargs='?')
    parser.add_argument('--current-model')
    parser.add_argument('--context', type=int)
    parser.add_argument('--max-tokens', type=int)
    parser.add_argument('--reasoning', action='store_true')
    args = parser.parse_args(argv)
    if args.action == 'preflight':
        print(encode(preflight.inspect(compatibility, args.transaction)).decode())
        return 0
    if args.transaction is None or re.fullmatch('[a-f0-9]{64}', args.transaction) is None:
        raise Refused('transaction-required')
    if args.action == '_controller':
        controller = Recovery(package, manifest, compatibility, args.transaction)
        try:
            controller.run()
        finally:
            controller.close()
        return 0
    package_sha = manifest['capturedSha256']
    if digest(read(package / 'manifest.json')) != package_sha:
        raise Refused('package-changed-after-capture')
    scope = Scope(args.transaction, package, package_sha)
    scope.require_drained()
    if args.action == 'recover':
        from pixel_model_contract import target
        proposed = target(dict(model=args.current_model, contextLength=args.context, maxTokens=args.max_tokens or 4096, reasoning=args.reasoning))
        binding, _ = preflight.snapshot(compatibility, args.transaction)
        locks = Locks().acquire(binding)
        try:
            second, _ = preflight.snapshot(compatibility, args.transaction)
            if binding != second:
                raise Refused('preflight-changed')
            sources = adapters.source_inputs(binding)
            with claim_directory(args.transaction, create=True) as records:
                raw = read(Path(binding['owner']['home']) / '.openclaw/openclaw.json', uid=binding['owner']['uid'])
                if digest(raw) != binding['nativeSha256']:
                    raise Refused('initial-config-changed')
                records.write('claim.json', encode(dict(version=1, transaction=args.transaction, target=proposed,
                    binding=binding, ownerSources=sources, packageSha256=package_sha, phase='prepared', action='recover', mutated=False)), absent=True)
                # The claim already durably binds the original hash. A crash
                # before this optional audit snapshot must not orphan custody.
                records.write('native-before.json', raw, absent=True)
        finally:
            locks.close()
    else:
        if args.current_model is not None or args.context is not None or args.max_tokens is not None or args.reasoning:
            raise Refused('resume-uses-original-target')
        with claim_directory(args.transaction) as records:
            claim = decode(records.read('claim.json'))
            if claim.get('transaction') != args.transaction or claim.get('packageSha256') != package_sha:
                raise Refused('claim-binding-changed')
            if args.action == 'cancel':
                if claim.get('mutated') is not False:
                    raise Refused('cancel-requires-unmodified-config')
                claim['action'] = 'cancel'
                records.write('claim.json', encode(claim))
    scope.start()
    print(encode(dict(ok=True, transaction=args.transaction, serviceDrained=True, admissionContinuouslyHeld=False)).decode())
    return 0
