"""Claim-bound Unix socket publication with durable intent before bind."""
import os
from pathlib import Path
import socket
import stat
import struct
import threading

from custody import Directory, Refused, decode, encode, identity, process_start


class OwnerSocket:
    def __init__(self, runtime, records, transaction, owner, dispatch, quarantine, scope):
        self.runtime, self.records = Path(runtime), records
        self.transaction, self.owner, self.dispatch = transaction, owner, dispatch
        self.quarantine, self.scope = quarantine, scope
        self.directory = None
        self.listener = None
        self.active = None
        self.thread = None
        self.closed = threading.Event()
        self.record = None

    def save(self, phase):
        self.record['phase'] = phase
        self.records.write('socket.json', encode(self.record))

    def recover(self):
        self.scope.require_current()
        if not self.quarantine():
            raise Refused('socket-requires-stopped-quarantine')
        try:
            value = decode(self.records.read('socket.json'))
        except FileNotFoundError:
            if os.path.lexists(self.runtime):
                raise Refused('unclaimed-runtime')
            return
        if (type(value) is not dict or set(value) != {'version', 'transaction', 'runtime', 'parent', 'pid', 'start', 'directory', 'socket', 'phase'}
                or value['version'] != 1 or type(value['pid']) is not int or value['pid'] <= 0
                or type(value['start']) is not str or not value['start'].isdecimal()
                or any(type(value[key]) is not list or len(value[key]) != 2
                       or any(type(number) is not int or number < 0 for number in value[key])
                       for key in ('parent', 'directory', 'socket') if value[key] is not None)
                or value['parent'] is None
                or value.get('transaction') != self.transaction or value.get('runtime') != str(self.runtime)
                or value.get('phase') not in ('creating', 'binding', 'listening', 'closed')
                or process_start(value['pid']) == value['start']):
            raise Refused('socket-intent-not-recoverable')
        with Directory(self.runtime.parent) as parent:
            if list(identity(parent.validate())) != value['parent']:
                raise Refused('runtime-parent-replaced')
        if not os.path.lexists(self.runtime):
            return
        with Directory(self.runtime) as directory:
            info = directory.validate()
            if value['phase'] == 'creating':
                if stat.S_IMODE(info.st_mode) != 0o700 or os.listdir(directory.fd):
                    raise Refused('partial-runtime-custody')
            else:
                if value.get('directory') != list(identity(info)):
                    raise Refused('runtime-directory-replaced')
                names = os.listdir(directory.fd)
                if names not in ([], ['control.sock']):
                    raise Refused('runtime-content-conflict')
                if names:
                    item = directory.stat('control.sock')
                    if (not stat.S_ISSOCK(item.st_mode) or item.st_uid != 0 or item.st_nlink != 1
                            or value['phase'] == 'closed'
                            or value.get('socket') is not None and list(identity(item)) != value['socket']):
                        raise Refused('socket-custody-conflict')
                    # In binding phase the root-private directory and durable
                    # pre-bind intent establish the sole fixed creator, even
                    # if death prevented persisting the socket inode receipt.
                    os.unlink('control.sock', dir_fd=directory.fd)
                    os.fsync(directory.fd)
            original = identity(info)
        with Directory(self.runtime.parent) as parent:
            if identity(parent.stat(self.runtime.name)) != original:
                raise Refused('runtime-directory-replaced')
            os.rmdir(self.runtime.name, dir_fd=parent.fd)
            os.fsync(parent.fd)

    def start(self, checkpoint=lambda _: None):
        self.recover()
        with Directory(self.runtime.parent) as parent:
            self.record = dict(version=1, transaction=self.transaction, runtime=str(self.runtime),
                               parent=list(identity(parent.validate())), pid=os.getpid(), start=process_start(os.getpid()),
                               directory=None, socket=None)
            self.save('creating')
            checkpoint('socket-creation-intent')
            self.directory = parent.mkdir(self.runtime.name)
        checkpoint('socket-directory-created')
        self.record['directory'] = list(identity(self.directory.validate()))
        self.save('binding')
        checkpoint('socket-binding-intent')
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.settimeout(.2)
        # Linux resolves this magic link to the pinned directory, so neither
        # bind nor subsequent chmod/chown follows a replaceable ancestor path.
        self.listener.bind('/proc/self/fd/' + str(self.directory.fd) + '/control.sock')
        checkpoint('socket-bound-before-receipt')
        item = self.directory.stat('control.sock')
        if not stat.S_ISSOCK(item.st_mode) or item.st_uid != 0 or item.st_nlink != 1:
            raise Refused('socket-custody-conflict')
        self.record['socket'] = list(identity(item))
        os.chown('control.sock', 0, self.owner['gid'], dir_fd=self.directory.fd, follow_symlinks=False)
        os.chmod('control.sock', 0o660, dir_fd=self.directory.fd, follow_symlinks=False)
        os.fsync(self.directory.fd)
        self.save('listening')
        os.fchmod(self.directory.fd, 0o711)
        os.fsync(self.directory.fd)
        self.listener.listen(8)
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        while not self.closed.is_set():
            try:
                connection, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                if self.closed.is_set():
                    return
                raise
            self.active = connection
            try:
                with connection:
                    connection.settimeout(1)
                    _, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                    status, body = 403, {'error': 'owner-required'}
                    if uid in (0, self.owner['uid']):
                        try:
                            raw = b''
                            while b'\n' not in raw and len(raw) <= 2048:
                                block = connection.recv(2049-len(raw))
                                if not block:
                                    break
                                raw += block
                            if len(raw) > 2048 or not raw.endswith(b'\n'):
                                raise Refused('invalid-frame')
                            request = decode(raw)
                            if request == {'operation': 'model-status'}:
                                operation, value = 'model-status', None
                            elif request == {'operation': 'installer-model-verify', 'request': {'transaction_id': self.transaction}}:
                                operation, value = 'installer-model-verify', request['request']
                            else:
                                raise Refused('fixed-operation-required')
                            body, status = self.dispatch(uid, operation, value), 200
                        except Exception:
                            status, body = 409, {'error': 'recovery-claim-refused'}
                    connection.sendall(encode(dict(status=status, body=body)) + b'\n')
            except OSError:
                pass
            finally:
                self.active = None

    def close(self):
        self.closed.set()
        if self.active is not None:
            try:
                self.active.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if self.listener is not None:
            self.listener.close()
        if self.thread is not None:
            self.thread.join(5)
            if self.thread.is_alive():
                raise Refused('socket-handler-still-active')
        if self.directory is not None:
            item = self.directory.stat('control.sock')
            if list(identity(item)) != self.record['socket'] or not stat.S_ISSOCK(item.st_mode):
                raise Refused('socket-path-replaced')
            os.unlink('control.sock', dir_fd=self.directory.fd)
            os.fsync(self.directory.fd)
            self.save('closed')
            self.directory.close()
            self.directory = None
