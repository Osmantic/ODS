#!/usr/bin/env python3
# Dependency-free guest byte pump for ODS Windows->WSL loopback bridge.
# Holder argv: --port <1..65535> --root <install-root>. Host fixed to 127.0.0.1.
# Port-only operation supports isolated transport fixtures. Ordinary uid, no root.
# No payload/header/private-config logging. stdlib only.
#
# Protocol (both directions on owned stdio only; external TCP stays raw):
#   header: [type:1][len:4 big-endian]
#   type 1 DATA      len 1..65536
#   type 2 END_WRITE len 0   (TCP input EOF; keep child stdin open)
#   type 3 ABORT     len 0   (controller death; guest exits immediately)
# An END frame signals upstream read-half close without waiting for wsl.exe
# to close its stdout handle or for the input half to finish.

import os
import importlib.util
import pwd
from pathlib import Path
import socket
import sys
import threading

BUF = 65536
MAX_FRAME = 65536


def admit_root(root, port):
    """Verify the installation and actual user-service listener without logging payloads."""
    spec = importlib.util.spec_from_file_location(
        'ods_opencode_owner', Path(__file__).with_name('opencode-ownership.py'))
    owner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(owner)
    proof = owner.verify(root, pwd.getpwuid(os.getuid()).pw_dir, os.getuid())
    if proof['port'] != port:
        raise ValueError('registered port mismatch')
    return proof['ownerUid'], proof['mainPid'], proof['startTicks']


def parse_port(argv):
    if len(argv) != 2 or argv[0] != "--port":
        return None
    port = argv[1]
    if not port or len(port) > 5 or any(c not in "0123456789" for c in port):
        return None
    n = int(port)
    if n < 1 or n > 65535:
        return None
    return n


def read_exact(fd, n):
    """Read exactly n bytes from fd. Returns bytes or None on EOF/error."""
    chunks = []
    remaining = n
    while remaining > 0:
        try:
            b = os.read(fd, remaining)
        except OSError:
            return None
        if not b:
            return None
        chunks.append(b)
        remaining -= len(b)
    return b"".join(chunks)


def write_all(fd, data):
    """Write all bytes to fd. Returns True on success, False on error."""
    view = memoryview(data)
    while view:
        try:
            n = os.write(fd, view)
        except OSError:
            return False
        if n <= 0:
            return False
        view = view[n:]
    return True


def write_frame(fd, kind, payload=b""):
    header = bytes((kind,)) + len(payload).to_bytes(4, "big")
    return write_all(fd, header) and (not payload or write_all(fd, payload))


def main():
    if os.getuid() == 0:
        sys.stderr.write("must not run as root\n")
        return 4

    args = sys.argv[1:]
    root = None
    if len(args) == 4 and args[2] == '--root':
        root = args[3]
        args = args[:2]
    port = parse_port(args)
    if port is None:
        sys.stderr.write("invalid arguments\n")
        return 2

    before = None
    if root is not None:
        try:
            before = admit_root(root, port)
        except Exception:
            sys.stderr.write('OpenCode ownership admission failed\n')
            return 5
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        sock.connect(("127.0.0.1", port))
        if root is not None:
            try:
                if admit_root(root, port) != before:
                    raise ValueError('service changed during connect')
            except Exception:
                sock.close()
                sys.stderr.write('OpenCode ownership changed during connect\n')
                return 5
        sock.settimeout(None)
    except OSError:
        sys.stderr.write("connect failed\n")
        return 3

    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()

    # Flags: set by worker threads, read by main.
    abort_flag = threading.Event()      # controller death / protocol error
    input_ended = threading.Event()     # legitimate TCP write-half close
    sock_done = threading.Event()       # socket->stdout thread finished

    def stdin_to_socket():
        """Read framed messages from stdin, write payload to socket."""
        try:
            while True:
                hdr = read_exact(stdin_fd, 5)
                if hdr is None:
                    # Unexpected child stdin EOF (controller/job killed).
                    abort_flag.set()
                    return

                mtype = hdr[0]
                length = (hdr[1] << 24) | (hdr[2] << 16) | (hdr[3] << 8) | hdr[4]

                if mtype == 1:  # DATA
                    if input_ended.is_set() or length < 1 or length > MAX_FRAME:
                        abort_flag.set()
                        return
                    payload = read_exact(stdin_fd, length)
                    if payload is None:
                        abort_flag.set()
                        return
                    try:
                        sock.sendall(payload)
                    except OSError:
                        abort_flag.set()
                        return
                elif mtype == 2:  # END_WRITE
                    if length != 0 or input_ended.is_set():
                        abort_flag.set()
                        return
                    # Legitimate TCP half-close: shutdown socket write side,
                    # keep reading replies from socket.
                    try:
                        sock.shutdown(socket.SHUT_WR)
                    except OSError:
                        abort_flag.set()
                        return
                    input_ended.set()
                    # Keep watching the parent pipe for ABORT or unexpected EOF.
                elif mtype == 3:  # ABORT
                    if length != 0:
                        abort_flag.set()
                        return
                    abort_flag.set()
                    return
                else:
                    abort_flag.set()
                    return
        finally:
            if not (input_ended.is_set() and sock_done.is_set()):
                abort_flag.set()

    def socket_to_stdout():
        """Frame upstream bytes and read-half EOF on the owned output pipe."""
        try:
            while True:
                try:
                    data = sock.recv(BUF)
                except OSError:
                    abort_flag.set()
                    return
                if not data:
                    if not write_frame(stdout_fd, 2):
                        abort_flag.set()
                    return
                if not write_frame(stdout_fd, 1, data):
                    abort_flag.set()
                    return
        finally:
            try:
                sys.stdout.close()
            except OSError:
                pass
            # Closing the wrapper alone can retain fd1; explicitly close it.
            try:
                os.close(stdout_fd)
            except OSError:
                pass
            sock_done.set()

    t_in = threading.Thread(target=stdin_to_socket, name="stdin->sock")
    t_out = threading.Thread(target=socket_to_stdout, name="sock->stdout")
    t_in.daemon = True
    t_out.daemon = True
    t_in.start()
    t_out.start()

    # Main waits for either direction to finish or abort.
    while True:
        if abort_flag.is_set():
            break
        if input_ended.is_set() and sock_done.is_set():
            break
        # Poll with short sleep; threads are daemon so process exit is safe.
        abort_flag.wait(0.1)

    # On abort or completion, unblock any remaining worker.
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass

    # Give workers a brief moment to observe shutdown.
    t_out.join(1.0)

    return 0


if __name__ == "__main__":
    sys.exit(main())
