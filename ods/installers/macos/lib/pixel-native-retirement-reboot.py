"""Explicit reboot barrier for retirement without a live process-tree witness.

Do not infer descendant absence from an exited launchd parent. Preserve the
root-owned plists outside LaunchDaemons, unload the verified jobs, and require
a later boot before the existing retirement code can archive the deployment.
"""
import json
import os
from pathlib import Path
import time
import uuid


class Recovery:
    def __init__(self, state, plists, *, read, directory, save, command, absent):
        self.state = Path(state)
        self.plists = tuple(plists)
        self.targets = tuple('system/' + path.stem for path in self.plists)
        self.archive = self.state / 'retirement-plists'
        self.receipt = self.state / 'retirement.json'
        self.read, self.directory, self.save = read, directory, save
        self.command, self.absent = command, absent
        self.document = None
        if os.path.lexists(self.receipt):
            value = json.loads(read(self.receipt))
            if type(value) is dict and value.get('schema') == 2:
                self.document = value
                if (set(value) != {'schema', 'kind', 'owner', 'boot', 'hashes', 'phase'}
                        or value['kind'] != 'native-retirement-reboot'
                        or type(value['owner']) is not int or value['owner'] <= 0
                        or type(value['hashes']) is not dict
                        or value['phase'] not in ('staging', 'awaiting-reboot')
                        or not isinstance(value['boot'], str)
                        or str(uuid.UUID(value['boot'])) != value['boot']):
                    raise ValueError('native-retirement-reboot-receipt-invalid')
        if os.path.lexists(self.archive):
            if self.document is None:
                raise ValueError('native-retirement-unbound-plist-archive')
            with directory(self.archive):
                if not set(os.listdir(self.archive)) <= {path.name for path in self.plists}:
                    raise ValueError('native-retirement-foreign-plist-archive')

    def path(self, original):
        """Resolve only the six fixed plists; their logical hash keys stay stable."""
        if original not in self.plists or self.document is None:
            return original
        archived = self.archive / original.name
        source_exists, archive_exists = os.path.lexists(original), os.path.lexists(archived)
        if source_exists == archive_exists:
            raise ValueError('native-retirement-plist-location-ambiguous')
        if self.document['phase'] == 'awaiting-reboot' and source_exists:
            raise ValueError('native-retirement-plist-restored-before-recovery')
        return original if source_exists else archived

    def unchanged(self, snapshots, *, code='native-retirement-authority-changed'):
        if self.document is not None and json.loads(self.read(self.receipt)) != self.document:
            raise ValueError('native-retirement-reboot-receipt-changed')
        for name, body in snapshots.items():
            if self.read(self.path(Path(name))) != body:
                raise ValueError(code)

    def bind(self, owner, hashes):
        if (self.document is None or self.document['owner'] != owner
                or self.document['hashes'] != hashes):
            raise ValueError('native-retirement-reboot-authority-mismatch')

    def prepare(self, *, owner, boot, hashes, snapshots):
        if self.document is None:
            if os.path.lexists(self.receipt):
                raise ValueError('native-retirement-existing-stop-witness')
            self.unchanged(snapshots)
            self.document = dict(schema=2, kind='native-retirement-reboot', owner=owner,
                                 boot=boot, hashes=hashes, phase='staging')
            # Journal before the first rename or bootout. Interruptions retain
            # the exact old authority and are resumed only by this explicit mode.
            self.save(self.receipt, self.document)
        else:
            self.bind(owner, hashes)
            self.unchanged(snapshots)
            if self.document['phase'] == 'awaiting-reboot':
                if self.document['boot'] != boot:
                    raise ValueError('native-retirement-use-resume-stopped-recovery')
                if not all(self.absent(target) for target in self.targets):
                    raise ValueError('native-retirement-job-reappeared')
                return self.result()
            # A reboot during staging is not the completion barrier. Finish
            # removing autostart definitions and require another reboot.
            if self.document['boot'] != boot:
                self.document = dict(self.document, boot=boot)
                self.save(self.receipt, self.document)
        with self.directory(self.archive, create=True) as fd:
            os.fchmod(fd, 0o700)
            for original in self.plists:
                self.unchanged(snapshots)
                if self.path(original) == original:
                    os.rename(original, self.archive / original.name)
                    os.fsync(fd)
                    parent_fd = os.open(original.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    try:
                        os.fsync(parent_fd)
                    finally:
                        os.close(parent_fd)
        self.unchanged(snapshots)
        for target in self.targets:
            if not self.absent(target):
                self.command(['/bin/launchctl', 'bootout', target])
            # launchd can retain the job briefly after bootout returns.
            deadline = time.monotonic() + 30
            while not self.absent(target):
                if time.monotonic() >= deadline:
                    raise ValueError('native-retirement-recovery-stop-failed')
                time.sleep(0.1)
        if not all(self.absent(target) for target in self.targets):
            raise ValueError('native-retirement-job-reappeared')
        self.unchanged(snapshots)
        self.document = dict(self.document, phase='awaiting-reboot')
        self.save(self.receipt, self.document)
        return self.result()

    def result(self):
        return {'status': 'reboot-required', 'phase': self.document['phase'],
                'retired': False, 'installationPreserved': True}

    def verify_reboot(self, *, owner, boot, hashes, snapshots):
        self.bind(owner, hashes)
        self.unchanged(snapshots)
        if self.document['phase'] != 'awaiting-reboot':
            raise ValueError('native-retirement-reboot-staging-incomplete')
        if boot == self.document['boot']:
            raise ValueError('native-retirement-reboot-required')
        if not all(self.absent(target) for target in self.targets):
            raise ValueError('native-retirement-job-reappeared')
        # With unchanged root-owned plists quarantined before the boot boundary,
        # old native process trees cannot survive or automatically respawn.
        return {target: () for target in self.targets}
