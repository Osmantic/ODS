"""Create one NEW owner-private NTFS provider root, without adoption/activation.

NtCreateFile(FILE_CREATE, FILE_DIRECTORY_FILE) applies the descriptor and returns
a pinned handle in the create operation. Parent handles and the new root deny
delete sharing. Existing paths are never opened as the new root or repaired.
Failures after an uncertain create retain state, never delete by path. This is
not power-loss durability, a store migration, or same-user/admin isolation.
"""
import ctypes as C

from . import windows_custody as W
from . import windows_transactions as T


class _Unicode(C.Structure):
    _fields_ = [('length', W.U16), ('maximum', W.U16), ('buffer', W.PTR)]


class _ObjectAttributes(C.Structure):
    _fields_ = [('length', W.U32), ('root', W.PTR), ('name', C.POINTER(_Unicode)),
                ('attributes', W.U32), ('security', W.PTR), ('quality', W.PTR)]


class _Status(C.Union):
    _fields_ = [('status', C.c_int32), ('pointer', W.PTR)]


class _IoStatus(C.Structure):
    _fields_ = [('result', _Status), ('information', C.c_size_t)]


def _create_api():
    # User-mode documented NtCreateFile, not a privileged driver/Zw entrypoint.
    call = C.WinDLL('ntdll').NtCreateFile
    call.argtypes = [C.POINTER(W.PTR), W.U32, C.POINTER(_ObjectAttributes),
                     C.POINTER(_IoStatus), W.PTR, W.U32, W.U32, W.U32, W.U32, W.PTR, W.U32]
    call.restype = C.c_int32
    return call


def _identity(api, handle):
    info = W._info(api, handle, True)
    return info.volume, (info.index_high << 32) | info.index_low


def create_private_root(path):
    """Create one final component; return its verified (volume, file-id).

    Caller must choose the explicit path. Ancestors must already exist; normal
    inherited ancestor ACLs are not changed. Every existing final target is a
    collision, including an already-private directory. Follow-on store access
    must independently reopen/check custody; this returns no enduring authority.
    """
    api, calls = T._apis()  # Non-Windows refuses before touching the filesystem.
    value, root, parts = W._path(path)
    if not parts:
        raise W.WindowsCustodyError('invalid-path')
    # Reserve the actual transaction temporary filename in the 240 UTF-16-unit
    # path budget. A root that cannot hold a provider transaction is not ready.
    child = value + '\\.ods-txn-' + 'f' * 32 + '.tmp'
    W._path(child)
    name_bytes = parts[-1].encode('utf-16-le')
    if api.GetDriveTypeW(root) != 3:
        raise W.WindowsCustodyError('unsupported-volume')
    fs, serial, maximum, flags = C.create_unicode_buffer(64), W.U32(), W.U32(), W.U32()
    W._check(api.GetVolumeInformationW(root, None, 0, C.byref(serial), C.byref(maximum),
                                     C.byref(flags), fs, len(fs)))
    if fs.value != 'NTFS' or not flags.value & 8:
        raise W.WindowsCustodyError('unsupported-volume')
    create = _create_api()
    handles, identities = [], []
    created, attempted = W.PTR(), False
    try:
        ancestors = [root] + [root + '\\'.join(parts[:i]) for i in range(1, len(parts))]
        for ancestor in ancestors:
            handle = api.CreateFileW(ancestor, W.READ_ATTRIBUTES | W.LIST_DIRECTORY,
                                     3, None, 3, 0x02200000, None)
            if handle in (None, W.INVALID_HANDLE):
                raise W.WindowsCustodyError('ancestor-unavailable')
            handles.append(handle)
            identity = _identity(api, handle)
            if identity[0] != serial.value:
                raise W.WindowsCustodyError('volume-drift')
            identities.append(identity)
        # ObjectName is only the validated leaf, relative to the pinned parent.
        buffer = C.create_string_buffer(name_bytes + b'\0\0')
        name = _Unicode(len(name_bytes), len(name_bytes) + 2, C.cast(buffer, W.PTR))
        status = _IoStatus()
        with T._security(api, calls) as attributes:
            descriptor = C.cast(attributes, C.POINTER(T._Attributes)).contents.descriptor
            obj = _ObjectAttributes(C.sizeof(_ObjectAttributes), handles[-1], C.pointer(name),
                                    0x40 | 0x1000, descriptor, None)  # CASE_INSENSITIVE, DONT_REPARSE
            attempted = True
            result = create(C.byref(created), W.READ_CONTROL | W.READ_ATTRIBUTES | 0x100000 | W.LIST_DIRECTORY,
                            C.byref(obj), C.byref(status), None, 0x80, 3, 2, 0x21, None, 0)
            # FILE_CREATE=2, DIRECTORY_FILE|SYNCHRONOUS_IO_NONALERT=0x21.
            # No OPEN_IF, overwrite, delete-on-close or inherited handle.
            if (result & 0xffffffff) == 0xc0000035 and not created.value:
                attempted = False  # Documented name collision: no create/open.
                raise W.WindowsCustodyError('already-exists')
            if result != 0 or created.value in (None, W.INVALID_HANDLE) or status.information != 2:
                raise W.WindowsCustodyError('creation-outcome-unknown')
        identity = W.inspect_private(created.value, directory=True)
        if identity[0] != serial.value:
            raise W.WindowsCustodyError('volume-drift')
        # Check the path still resolves to the exact handle returned at create.
        with W.open_private(value, directory=True) as readback:
            if W.inspect_private(readback, directory=True) != identity:
                raise W.WindowsCustodyError('identity-drift')
            if [_identity(api, handle) for handle in handles] != identities:
                raise W.WindowsCustodyError('identity-drift')
            if W.inspect_private(created.value, directory=True) != identity:
                raise W.WindowsCustodyError('identity-drift')
        return identity
    except Exception:
        if attempted:
            raise W.WindowsCustodyError('creation-outcome-unknown') from None
        raise
    finally:
        if created.value not in (None, W.INVALID_HANDLE):
            api.CloseHandle(created)
        for handle in reversed(handles):
            api.CloseHandle(handle)
