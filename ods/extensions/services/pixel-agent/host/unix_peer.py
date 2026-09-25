"""Kernel-authenticated identities for local stream clients, never request data."""
import ctypes
import errno
import os
import socket
import struct
import sys


def peer_ids(connection):
    if connection.family != socket.AF_UNIX or connection.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM:
        raise OSError(errno.EPROTOTYPE, "local stream socket required")
    connection.getpeername()  # Reject listeners and disconnected sockets.
    if sys.platform.startswith("linux"):
        size = struct.calcsize("iII")
        credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, size)
        if len(credentials) != size:
            raise OSError(errno.EIO, "invalid peer credentials")
        _pid, uid, gid = struct.unpack("iII", credentials)
    elif sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        try:
            getpeereid = libc.getpeereid
        except AttributeError:
            raise OSError(errno.ENOSYS, "peer credentials unavailable") from None
        getpeereid.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
        getpeereid.restype = ctypes.c_int
        uid_value, gid_value = ctypes.c_uint32(), ctypes.c_uint32()
        if getpeereid(connection.fileno(), ctypes.byref(uid_value), ctypes.byref(gid_value)) != 0:
            code = ctypes.get_errno() or errno.EIO
            raise OSError(code, os.strerror(code))
        uid, gid = uid_value.value, gid_value.value
    else:
        raise OSError(errno.ENOSYS, "peer credentials unavailable")
    if uid == 0xFFFFFFFF or gid == 0xFFFFFFFF:
        raise OSError(errno.EIO, "invalid peer credentials")
    return uid, gid
