"""Native Windows handle-based private reads; not a storage/activation adapter.

Currently qualified only for local fixed NTFS and explicit owner-only DACLs on
both the requested file and its containing directory (or the requested directory).
Does not create/repair ACLs, grant privileges, lock transactions or promise write
durability. Other processes running as this owner, administrators and existing
privileged handles are outside this custody boundary.
"""
import ctypes as C
from contextlib import contextmanager
import os
import re

U32, U16, U8, PTR = C.c_uint32, C.c_uint16, C.c_ubyte, C.c_void_p
BOOL, WCHAR = C.c_int, C.c_wchar_p
READ_CONTROL, READ_ATTRIBUTES = 0x20000, 0x80
LIST_DIRECTORY = 1
DIRECTORY, REPARSE = 0x10, 0x400
INVALID_HANDLE = PTR(-1).value


class WindowsCustodyError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class _FileInfo(C.Structure):
    _fields_ = [('attributes', U32), ('times', U32 * 6), ('volume', U32),
                ('size_high', U32), ('size_low', U32), ('links', U32),
                ('index_high', U32), ('index_low', U32)]


class _AclInfo(C.Structure):
    _fields_ = [('count', U32), ('used', U32), ('free', U32)]


class _Ace(C.Structure):
    _fields_ = [('kind', U8), ('flags', U8), ('size', U16), ('mask', U32)]


class _Api:
    def __init__(self):
        self.k = C.WinDLL('kernel32', use_last_error=True)
        self.a = C.WinDLL('advapi32', use_last_error=True)
        def bind(dll, name, result, *args):
            call = getattr(dll, name)
            call.argtypes, call.restype = args, result
            setattr(self, name, call)
        bind(self.k, 'CreateFileW', PTR, WCHAR, U32, U32, PTR, U32, U32, PTR)
        bind(self.k, 'CloseHandle', BOOL, PTR)
        bind(self.k, 'LocalFree', PTR, PTR)
        bind(self.k, 'GetCurrentProcess', PTR)
        bind(self.k, 'GetCurrentThread', PTR)
        bind(self.k, 'GetFileType', U32, PTR)
        bind(self.k, 'GetFileInformationByHandle', BOOL, PTR, C.POINTER(_FileInfo))
        bind(self.k, 'GetDriveTypeW', U32, WCHAR)
        bind(self.k, 'GetVolumeInformationW', BOOL, WCHAR, PTR, U32,
             C.POINTER(U32), C.POINTER(U32), C.POINTER(U32), PTR, U32)
        bind(self.k, 'ReadFile', BOOL, PTR, PTR, U32, C.POINTER(U32), PTR)
        bind(self.a, 'OpenThreadToken', BOOL, PTR, U32, BOOL, C.POINTER(PTR))
        bind(self.a, 'OpenProcessToken', BOOL, PTR, U32, C.POINTER(PTR))
        bind(self.a, 'GetTokenInformation', BOOL, PTR, U32, PTR, U32, C.POINTER(U32))
        bind(self.a, 'IsValidSid', BOOL, PTR)
        bind(self.a, 'EqualSid', BOOL, PTR, PTR)
        bind(self.a, 'GetLengthSid', U32, PTR)
        bind(self.a, 'GetSecurityInfo', U32, PTR, U32, U32, C.POINTER(PTR),
             PTR, C.POINTER(PTR), PTR, C.POINTER(PTR))
        bind(self.a, 'IsValidSecurityDescriptor', BOOL, PTR)
        bind(self.a, 'GetSecurityDescriptorControl', BOOL, PTR,
             C.POINTER(U16), C.POINTER(U32))
        bind(self.a, 'IsValidAcl', BOOL, PTR)
        bind(self.a, 'GetAclInformation', BOOL, PTR, PTR, U32, U32)
        bind(self.a, 'GetAce', BOOL, PTR, U32, C.POINTER(PTR))


_API = None


def _api():
    global _API
    if os.name != 'nt':
        raise WindowsCustodyError('unsupported-platform')
    if _API is None:
        _API = _Api()
    return _API


def _check(value):
    if not value:
        raise WindowsCustodyError('native-check-failed')


def _user_sid(api):
    # Recheck on every inspection; a cached SID must not mask impersonation.
    token = PTR()
    if api.OpenThreadToken(api.GetCurrentThread(), 8, True, C.byref(token)):
        api.CloseHandle(token)
        raise WindowsCustodyError('impersonation-not-supported')
    if C.get_last_error() != 1008:  # ERROR_NO_TOKEN
        raise WindowsCustodyError('identity-unavailable')
    _check(api.OpenProcessToken(api.GetCurrentProcess(), 8, C.byref(token)))
    try:
        needed = U32()
        ok = api.GetTokenInformation(token, 1, None, 0, C.byref(needed))
        if ok or C.get_last_error() != 122 or not 16 <= needed.value <= 1048576:
            raise WindowsCustodyError('identity-unavailable')
        raw = C.create_string_buffer(needed.value)
        _check(api.GetTokenInformation(token, 1, raw, needed, C.byref(needed)))
        sid = C.cast(raw, C.POINTER(PTR))[0]
        _check(api.IsValidSid(sid))
        return raw, sid  # Keep TOKEN_USER/SID backing memory alive.
    finally:
        api.CloseHandle(token)


def _info(api, handle, directory):
    if api.GetFileType(handle) != 1:
        raise WindowsCustodyError('unsafe-file-type')
    info = _FileInfo()
    _check(api.GetFileInformationByHandle(handle, C.byref(info)))
    if (info.attributes & REPARSE or bool(info.attributes & DIRECTORY) != directory
            or (not directory and info.links != 1)):
        raise WindowsCustodyError('unsafe-file-type')
    return info


def inspect_private(handle, *, directory=False):
    api = _api()
    if type(directory) is not bool or type(handle) is not int or handle in (0, INVALID_HANDLE):
        raise WindowsCustodyError('invalid-request')
    info = _info(api, handle, directory)
    backing, current_sid = _user_sid(api)
    owner, dacl, descriptor = PTR(), PTR(), PTR()
    try:
        error = api.GetSecurityInfo(handle, 1, 1 | 4, C.byref(owner), None,
                                    C.byref(dacl), None, C.byref(descriptor))
        if error:
            raise WindowsCustodyError('security-unavailable')
        control, revision = U16(), U32()
        _check(api.IsValidSecurityDescriptor(descriptor))
        _check(api.GetSecurityDescriptorControl(descriptor, C.byref(control), C.byref(revision)))
        if (not owner or not api.IsValidSid(owner) or not api.EqualSid(owner, current_sid)
                or not dacl or not control.value & 0x1000 or not api.IsValidAcl(dacl)):
            raise WindowsCustodyError('unsafe-acl')
        acl = _AclInfo()
        _check(api.GetAclInformation(dacl, C.byref(acl), C.sizeof(acl), 2))
        if acl.count != 1:
            raise WindowsCustodyError('unsafe-acl')
        ace_pointer = PTR()
        _check(api.GetAce(dacl, 0, C.byref(ace_pointer)))
        ace = C.cast(ace_pointer, C.POINTER(_Ace)).contents
        if ace.kind != 0 or ace.flags != 0 or ace.mask != 0x001F01FF or ace.size < 16:
            raise WindowsCustodyError('unsafe-acl')
        sid = ace_pointer.value + C.sizeof(_Ace)
        if (not api.IsValidSid(sid) or api.GetLengthSid(sid) + C.sizeof(_Ace) != ace.size
                or not api.EqualSid(sid, current_sid)):
            raise WindowsCustodyError('unsafe-acl')
        return info.volume, (info.index_high << 32) | info.index_low
    finally:
        if descriptor:
            api.LocalFree(descriptor)
        del backing


def _path(path):
    try:
        value = os.fspath(path)
    except TypeError:
        raise WindowsCustodyError('invalid-path') from None
    if not isinstance(value, str) or not re.match(r'^[A-Za-z]:[\\/]', value):
        raise WindowsCustodyError('invalid-path')
    try:
        if len(value.encode('utf-16-le')) // 2 > 240:
            raise WindowsCustodyError('invalid-path')
    except UnicodeError:
        raise WindowsCustodyError('invalid-path') from None
    value = value.replace('/', '\\')
    root, tail = value[:3], value[3:]
    parts = tail.split('\\') if tail else []
    for part in parts:
        if (not part or part in ('.', '..') or part[-1] in '. '
                or re.search(r'[\x00-\x1f<>:"|?*]', part)
                or re.fullmatch(r'CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³]',
                                part.split('.')[0].rstrip(' '), re.IGNORECASE)):
            raise WindowsCustodyError('invalid-path')
    return value, root, parts


@contextmanager
def open_private(path, *, directory=False, missing_ok=False):
    api = _api()
    if type(directory) is not bool or type(missing_ok) is not bool or (missing_ok and not directory):
        raise WindowsCustodyError('invalid-request')
    value, root, parts = _path(path)
    # A missing final directory has no descriptor to inspect. Identity must
    # still be checked so that absence never bypasses impersonation refusal.
    _user_sid(api)
    if api.GetDriveTypeW(root) != 3:
        raise WindowsCustodyError('unsupported-volume')
    fs, serial, maximum, flags = C.create_unicode_buffer(64), U32(), U32(), U32()
    _check(api.GetVolumeInformationW(root, None, 0, C.byref(serial), C.byref(maximum),
                                     C.byref(flags), fs, len(fs)))
    if fs.value != 'NTFS' or not flags.value & 8:
        raise WindowsCustodyError('unsupported-volume')
    handles, private_parent = [], None
    try:
        ancestors = [root] + [root + '\\'.join(parts[:i]) for i in range(1, len(parts))]
        for number, ancestor in enumerate(ancestors):
            is_parent = not directory and number == len(ancestors) - 1
            # Attribute-only opens do not enforce the intended share-delete
            # exclusion. Request directory read access so this handle pins it.
            access = READ_ATTRIBUTES | LIST_DIRECTORY | (READ_CONTROL if is_parent else 0)
            handle = api.CreateFileW(ancestor, access, 3, None, 3, 0x02200000, None)
            if handle in (None, INVALID_HANDLE):
                raise WindowsCustodyError('open-failed')
            handles.append(handle)
            if _info(api, handle, True).volume != serial.value:
                raise WindowsCustodyError('volume-drift')
            if is_parent:
                inspect_private(handle, directory=True)
                private_parent = handle
        access = READ_CONTROL | READ_ATTRIBUTES | (LIST_DIRECTORY if directory else 0x80000000)
        handle = api.CreateFileW(value, access, 3 if directory else 1, None, 3, 0x02200000, None)
        if handle in (None, INVALID_HANDLE):
            if missing_ok and C.get_last_error() == 2:  # ERROR_FILE_NOT_FOUND, final component ONLY
                yield None
                _user_sid(api)
                return
            raise WindowsCustodyError('open-failed')
        handles.append(handle)
        identity = inspect_private(handle, directory=directory)
        if identity[0] != serial.value:
            raise WindowsCustodyError('volume-drift')
        yield handle
        if inspect_private(handle, directory=directory) != identity:
            raise WindowsCustodyError('identity-drift')
        if private_parent is not None:
            inspect_private(private_parent, directory=True)
    finally:
        for handle in reversed(handles):
            api.CloseHandle(handle)


def read_private(path, *, max_bytes=32768):
    _api()
    if type(max_bytes) is not int or not 1 <= max_bytes <= 1048576:
        raise WindowsCustodyError('invalid-request')
    with open_private(path) as handle:
        api = _api()
        identity = inspect_private(handle)
        raw = bytearray()
        while len(raw) <= max_bytes:
            size = min(65536, max_bytes + 1 - len(raw))
            chunk, count = C.create_string_buffer(size), U32()
            _check(api.ReadFile(handle, chunk, size, C.byref(count), None))
            if count.value > size:
                raise WindowsCustodyError('invalid-read')
            if not count.value:
                break
            raw.extend(chunk.raw[:count.value])
        if len(raw) > max_bytes:
            raise WindowsCustodyError('file-too-large')
        if inspect_private(handle) != identity:
            raise WindowsCustodyError('identity-drift')
        return bytes(raw)
