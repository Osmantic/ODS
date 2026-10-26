"""Exact original unit quarantine, durably bound to one recovery claim."""
import os
from pathlib import Path
import subprocess
import time

from custody import Directory, Refused, decode, digest, encode, read
import preflight


class Quarantine:
    def __init__(self, records, transaction, compatibility):
        self.records, self.transaction, self.compatibility = records, transaction, compatibility
        self.marker = records.path / 'quarantine'
        self.directory = Path(str(preflight.UNIT_PATH) + '.d')
        self.policy = self.directory / '90-bootstrap-recovery.conf'
        self.bytes = ('[Unit]\nConditionPathExists=!' + str(self.marker) + '\n[Service]\nRestart=no\n').encode()

    def record(self, phase=None):
        binding = dict(version=1, transaction=self.transaction, unitSha256=self.compatibility['unitSha256'], policySha256=digest(self.bytes))
        if phase is not None:
            self.records.write('quarantine.json', encode(dict(binding, phase=phase)))
            return
        value = decode(self.records.read('quarantine.json'))
        if {k: v for k, v in value.items() if k != 'phase'} != binding or value['phase'] not in ('intent', 'held', 'restoring', 'restored'):
            raise Refused('quarantine-record-changed')
        return value

    def artifacts(self):
        if digest(read(preflight.UNIT_PATH)) != self.compatibility['unitSha256']:
            raise Refused('original-unit-changed')
        try:
            with Directory(self.directory) as directory:
                names = os.listdir(directory.fd)
                if names == [self.policy.name]:
                    if directory.read(self.policy.name) != self.bytes:
                        raise Refused('quarantine-policy-changed')
                elif names or self.record()['phase'] not in ('intent', 'restoring'):
                    raise Refused('unrelated-unit-dropin')
        except FileNotFoundError:
            pass

    def command(self, action):
        if action not in ('start', 'stop'):
            raise Refused('fixed-unit-action-required')
        self.artifacts()
        subprocess.run(['/usr/bin/systemctl', action, preflight.UNIT], check=True, capture_output=True, timeout=30)

    def reload(self):
        self.artifacts()
        subprocess.run(['/usr/bin/systemctl', 'daemon-reload'], check=True, capture_output=True, timeout=15)

    def typed(self, interface, prop):
        if (interface, prop) not in (('Service', 'Restart'), ('Unit', 'Conditions')):
            raise Refused('fixed-unit-property-required')
        path = '/org/freedesktop/systemd1/unit/' + ''.join(c if c.isascii() and c.isalnum() else '_' + format(ord(c), '02x') for c in preflight.UNIT)
        result = subprocess.run(['/usr/bin/busctl', '--json=short', 'get-property', 'org.freedesktop.systemd1', path,
            'org.freedesktop.systemd1.' + interface, prop], check=True, capture_output=True, timeout=10)
        return decode(result.stdout)

    def loaded(self, held=False):
        self.artifacts()
        value = preflight.properties(preflight.UNIT, ('FragmentPath', 'DropInPaths', 'User', 'Group', 'Type', 'Restart',
                                                    'PrivateTmp', 'RuntimeDirectory', 'MainPID', 'ActiveState'))
        expected = dict(FragmentPath=str(preflight.UNIT_PATH), User='root', Group='root', Type='simple', PrivateTmp='yes',
                        RuntimeDirectory='ods-pixel-access', Restart='no' if held else 'on-failure',
                        DropInPaths=str(self.policy) if held else '')
        if any(value.get(key) != wanted for key, wanted in expected.items()):
            raise Refused('loaded-coordinator-changed')
        if self.typed('Service', 'Restart') != dict(type='s', data=expected['Restart']):
            raise Refused('restart-policy-unconfirmed')
        if held:
            conditions = self.typed('Unit', 'Conditions')
            rows = conditions.get('data')
            if (conditions.get('type') != 'a(sbbsi)' or type(rows) is not list or len(rows) != 1
                    or rows[0][:4] != ['ConditionPathExists', False, True, str(self.marker)]):
                raise Refused('quarantine-condition-unconfirmed')
        return value

    def hold(self):
        self.artifacts()
        try:
            self.record()
        except FileNotFoundError:
            self.loaded()
            if self.marker.exists() or self.directory.exists():
                raise Refused('unclaimed-quarantine')
        self.record('intent')
        with Directory(self.directory.parent) as parent:
            if not self.directory.exists():
                parent.mkdir(self.directory.name, mode=0o755).close()
        with Directory(self.directory) as directory:
            directory.write(self.policy.name, self.bytes, mode=0o644)
        self.records.write('quarantine', (self.transaction + '\n').encode())
        self.reload()
        self.loaded(True)
        self.record('held')
        self.command('stop')
        if not self.stopped():
            raise Refused('coordinator-stop-unconfirmed')

    def stopped(self):
        value = self.loaded(True)
        return (self.records.read('quarantine') == (self.transaction + '\n').encode()
                and value['MainPID'] == '0' and value['ActiveState'] in ('inactive', 'failed'))

    def restore(self, terminal, socket):
        # Caller supplies a fixed internal proof function, never a wire boolean.
        terminal()
        if socket is not None:
            socket.close()
        terminal()  # joining active work cannot silently age the terminal proof
        self.record('restoring')
        try:
            self.records.unlink('quarantine', (self.transaction + '\n').encode())
        except FileNotFoundError:
            pass
        try:
            with Directory(self.directory) as directory:
                directory.unlink(self.policy.name, self.bytes)
            with Directory(self.directory.parent) as parent:
                os.rmdir(self.directory.name, dir_fd=parent.fd)
                os.fsync(parent.fd)
        except FileNotFoundError:
            pass
        self.reload()
        self.loaded()
        self.record('restored')
        self.command('start')
        deadline = time.monotonic() + 10
        while True:
            value = self.loaded()
            if value['ActiveState'] == 'active' and int(value['MainPID']) > 0:
                return
            if time.monotonic() >= deadline:
                raise Refused('coordinator-start-unconfirmed')
            time.sleep(.1)
