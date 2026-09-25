"""Scoped, cooperating-writer NTFS transactions; no automatic activation.

The stable lock serializes callers using this API, not arbitrary same-user code.
Publication uses same-directory MoveFileExW WRITE_THROUGH and readback. This is
not a directory-fsync emulation or a claim of qualified power-loss recovery.
An attempted publication with uncertain outcome retains state and fails closed.
"""
import ctypes as C
from contextlib import contextmanager
import math
import os
import secrets
import threading
import time

from . import windows_custody as W

LOCK_NAME = '.provider-config.lock'
MAX_BYTES = 1048576


class _Attributes(C.Structure):
    _fields_ = [('length', W.U32), ('descriptor', W.PTR), ('inherit', W.BOOL)]


class _Overlapped(C.Structure):
    _fields_ = [('internal', C.c_size_t), ('internal_high', C.c_size_t),
                ('offset', W.U32), ('offset_high', W.U32), ('event', W.PTR)]


def _apis():
    api = W._api()
    def bind(dll, name, result, *args):
        call = getattr(dll, name)
        call.argtypes, call.restype = args, result
        return call
    calls = {
        'sid_string': bind(api.a, 'ConvertSidToStringSidW', W.BOOL, W.PTR, C.POINTER(W.PTR)),
        'descriptor': bind(api.a, 'ConvertStringSecurityDescriptorToSecurityDescriptorW',
                           W.BOOL, C.c_wchar_p, W.U32, C.POINTER(W.PTR), C.POINTER(W.U32)),
        'lock': bind(api.k, 'LockFileEx', W.BOOL, W.PTR, W.U32, W.U32, W.U32, W.U32,
                     C.POINTER(_Overlapped)),
        'unlock': bind(api.k, 'UnlockFileEx', W.BOOL, W.PTR, W.U32, W.U32, W.U32,
                       C.POINTER(_Overlapped)),
        'write': bind(api.k, 'WriteFile', W.BOOL, W.PTR, W.PTR, W.U32, C.POINTER(W.U32), W.PTR),
        'flush': bind(api.k, 'FlushFileBuffers', W.BOOL, W.PTR),
        'move': bind(api.k, 'MoveFileExW', W.BOOL, C.c_wchar_p, C.c_wchar_p, W.U32),
        'disposition': bind(api.k, 'SetFileInformationByHandle', W.BOOL, W.PTR, W.U32, W.PTR, W.U32),
    }
    return api, calls


@contextmanager
def _security(api, calls):
    backing, sid = W._user_sid(api)
    text, descriptor, size = W.PTR(), W.PTR(), W.U32()
    try:
        W._check(calls['sid_string'](sid, C.byref(text)))
        sddl = 'O:' + C.wstring_at(text) + 'D:P(A;;FA;;;' + C.wstring_at(text) + ')'
        W._check(calls['descriptor'](sddl, 1, C.byref(descriptor), C.byref(size)))
        attributes = _Attributes(C.sizeof(_Attributes), descriptor, False)
        yield C.byref(attributes)
    finally:
        if descriptor:
            api.LocalFree(descriptor)
        if text:
            api.LocalFree(text)
        del backing


def _child(directory, name):
    if not isinstance(name, str) or not name or '/' in name or '\\' in name:
        raise W.WindowsCustodyError('invalid-path')
    path = directory + '\\' + name
    W._path(path)  # Validate BEFORE any Path normalization could erase traversal.
    return path


def _open_new(api, calls, path):
    with _security(api, calls) as attributes:
        handle = api.CreateFileW(path, 0xC0000000 | W.READ_CONTROL | 0x10000,
                                3, attributes, 1, 0x02200000, None)
    if handle in (None, W.INVALID_HANDLE):
        raise W.WindowsCustodyError('create-failed')
    return handle


class _Transaction:
    def __init__(self, directory, directory_handle, lock_handle, exclusive, api, calls):
        self.directory, self.directory_handle = directory, directory_handle
        self.lock_handle, self.exclusive = lock_handle, exclusive
        self.api, self.calls = api, calls
        self.identity = W.inspect_private(directory_handle, directory=True)
        self.lock_identity = W.inspect_private(lock_handle)
        self.owner = os.getpid(), threading.get_ident()
        self.active, self.uncertain = True, False

    def _check(self, *, writing=False):
        if (not self.active or self.owner != (os.getpid(), threading.get_ident())
                or (writing and not self.exclusive)):
            raise W.WindowsCustodyError('invalid-transaction')
        if self.uncertain:
            raise W.WindowsCustodyError('write-durability-unknown')
        if (W.inspect_private(self.directory_handle, directory=True) != self.identity
                or W.inspect_private(self.lock_handle) != self.lock_identity):
            raise W.WindowsCustodyError('identity-drift')

    def _path(self, name):
        path = _child(self.directory, name)
        if name.casefold() == LOCK_NAME.casefold() or name.casefold().startswith('.ods-txn-'):
            raise W.WindowsCustodyError('reserved-name')
        return path

    def _target_identity(self, path):
        handle = self.api.CreateFileW(path, 0x80000000 | W.READ_CONTROL, 1, None, 3, 0x02200000, None)
        if handle in (None, W.INVALID_HANDLE):
            if C.get_last_error() == 2:
                return None
            raise W.WindowsCustodyError('target-unavailable')
        try:
            identity = W.inspect_private(handle)
            if identity == self.lock_identity:
                raise W.WindowsCustodyError('reserved-name')
            return identity
        finally:
            self.api.CloseHandle(handle)

    def read(self, name, *, max_bytes=MAX_BYTES):
        self._check()
        if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_BYTES:
            raise W.WindowsCustodyError('invalid-request')
        path = self._path(name)
        identity = self._target_identity(path)
        if identity is None:
            return None
        value = W.read_private(path, max_bytes=max_bytes)
        self._check()
        if self._target_identity(path) != identity:
            raise W.WindowsCustodyError('identity-drift')
        return value

    def _discard(self, path, identity):
        # Delete by the exact verified HANDLE, never by an unchecked filename.
        handle = self.api.CreateFileW(path, 0x10000 | W.READ_CONTROL | W.READ_ATTRIBUTES,
                                     0, None, 3, 0x02200000, None)
        if handle in (None, W.INVALID_HANDLE):
            return False
        try:
            if W.inspect_private(handle) != identity:
                return False
            delete = W.U8(1)
            return bool(self.calls['disposition'](handle, 4, C.byref(delete), C.sizeof(delete)))
        except Exception:
            return False
        finally:
            self.api.CloseHandle(handle)

    def _publish(self, name, content, *, replace):
        self._check(writing=True)
        if type(content) is not bytes or len(content) > MAX_BYTES:
            raise W.WindowsCustodyError('invalid-request')
        target = self._path(name)
        previous = self._target_identity(target)
        if previous is not None and not replace:
            raise W.WindowsCustodyError('file-exists')
        temporary = _child(self.directory, '.ods-txn-' + secrets.token_hex(16) + '.tmp')
        handle, identity, attempted = None, None, False
        try:
            handle = _open_new(self.api, self.calls, temporary)
            identity = W.inspect_private(handle)
            if identity[0] != self.identity[0]:
                raise W.WindowsCustodyError('volume-drift')
            offset = 0
            while offset < len(content):
                buffer = C.create_string_buffer(content[offset:offset + 65536])
                count, size = W.U32(), min(65536, len(content) - offset)
                W._check(self.calls['write'](handle, buffer, size, C.byref(count), None))
                if not 0 < count.value <= size:
                    raise W.WindowsCustodyError('write-failed')
                offset += count.value
            W._check(self.calls['flush'](handle))
            if W.inspect_private(handle) != identity:
                raise W.WindowsCustodyError('identity-drift')
            self.api.CloseHandle(handle)
            handle = None
            self._check(writing=True)
            if self._target_identity(target) != previous:
                raise W.WindowsCustodyError('identity-drift')
            attempted = True
            # Never COPY_ALLOWED, DELAY_UNTIL_REBOOT or unsupported ReplaceFile flags.
            W._check(self.calls['move'](temporary, target, 8 | (1 if replace else 0)))
            if self._target_identity(target) != identity or W.read_private(target, max_bytes=MAX_BYTES) != content:
                raise W.WindowsCustodyError('readback-failed')
            self._check(writing=True)
            return identity
        except Exception as error:
            if attempted:
                self.uncertain = True
                raise W.WindowsCustodyError('write-durability-unknown') from None
            if isinstance(error, W.WindowsCustodyError) and error.code != 'native-check-failed':
                raise
            raise W.WindowsCustodyError('write-failed') from None
        finally:
            if handle not in (None, W.INVALID_HANDLE):
                self.api.CloseHandle(handle)
            if not attempted and identity is not None:
                self._discard(temporary, identity)

    def create_immutable(self, name, content):
        return self._publish(name, content, replace=False)

    def replace(self, name, content):
        return self._publish(name, content, replace=True)


@contextmanager
def private_directory_transaction(directory, *, exclusive=True, timeout=5.0):
    api, calls = _apis()
    if (type(exclusive) is not bool or type(timeout) not in (int, float)
            or not math.isfinite(timeout) or not 0 <= timeout <= 60):
        raise W.WindowsCustodyError('invalid-request')
    directory, _, _ = W._path(directory)
    with W.open_private(directory, directory=True) as directory_handle:
        lock_path = _child(directory, LOCK_NAME)
        # OPEN_ALWAYS never changes an existing file's descriptor or contents.
        with _security(api, calls) as attributes:
            handle = api.CreateFileW(lock_path, 0xC0000000 | W.READ_CONTROL, 3,
                                    attributes, 4, 0x02200000, None)
        if handle in (None, W.INVALID_HANDLE):
            raise W.WindowsCustodyError('lock-unavailable')
        overlapped, locked, transaction = _Overlapped(), False, None
        try:
            W.inspect_private(handle)
            deadline = time.monotonic() + timeout
            while True:
                if calls['lock'](handle, 1 | (2 if exclusive else 0), 0, 1, 0, C.byref(overlapped)):
                    locked = True
                    break
                if C.get_last_error() != 33:
                    raise W.WindowsCustodyError('lock-unavailable')
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise W.WindowsCustodyError('lock-timeout')
                time.sleep(min(0.025, remaining))
            transaction = _Transaction(directory, directory_handle, handle, exclusive, api, calls)
            yield transaction
            transaction._check()
        finally:
            if transaction is not None:
                transaction.active = False
            if locked:
                calls['unlock'](handle, 0, 1, 0, C.byref(overlapped))
            api.CloseHandle(handle)  # OS also releases a held lock on handle close.
