"""Owner process for managed project jobs; no implicit permission grants."""
import argparse
import fcntl
import os
from pathlib import Path
import signal
import socket
import stat
import threading

from project_authority import ManagedFullAccessPolicy
from project_controller import ProjectController
from project_transport import serve_project_connection
from project_runtime import verify_runtime
from project_storage import DEFAULT_JOB_BYTES, DEFAULT_TOTAL_BYTES, DEFAULT_MAX_JOBS


def serve(controller, stop, *, ready=None):
    """Use the already validated private state directory for socket and lock."""
    verify_runtime(controller.image)
    if controller.python_image:
        verify_runtime(controller.python_image, runtime="python")
    root = controller.jobs.path.parent
    socket_path = root / "control.sock"
    lock = os.open(root / "service.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    bound = None
    owns_lock = False
    try:
        info = os.fstat(lock)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or info.st_mode & 0o077):
            raise ValueError("unsafe project service lock")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        owns_lock = True
        # No earlier service or this process can be executing queued callbacks
        # while we relabel interrupted records. Never replay accepted work.
        with controller.lock:
            if controller.futures:
                raise ValueError("project service requires an idle controller")
            controller.jobs.recover_interrupted()
        # Only one service can own this private state; stale socket cleanup is
        # allowed only after acquiring its lifetime lock and checking ownership.
        try:
            info = socket_path.lstat()
        except FileNotFoundError:
            info = None
        if info is not None:
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                raise ValueError("unsafe existing project socket")
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(1)
                try:
                    probe.connect(str(socket_path))
                except ConnectionRefusedError:
                    socket_path.unlink()
                else:
                    raise ValueError("project socket is active")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(socket_path))
            os.chmod(socket_path, 0o600)
            bound = socket_path.lstat()
            listener.listen(8)
            listener.settimeout(0.2)
            controller.start_capability_probe()
            if ready is not None:
                ready.set()
            while not stop.is_set():
                try:
                    connection, _ = listener.accept()
                except TimeoutError:
                    continue
                with connection:
                    try:
                        serve_project_connection(connection, controller=controller, owner_uid=os.getuid())
                    except OSError:
                        # A disconnected client does not cancel or replay a job.
                        # Its durable state remains available for observation.
                        continue
    finally:
        if bound is not None:
            try:
                current = socket_path.lstat()
                if (current.st_dev, current.st_ino) == (bound.st_dev, bound.st_ino):
                    socket_path.unlink()
            except FileNotFoundError:
                pass
        try:
            if owns_lock:
                # Retain exclusive ownership while accepted work drains, so a
                # replacement cannot relabel jobs still executing here.
                controller.close()
        finally:
            os.close(lock)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--python-image")
    parser.add_argument('--storage-bytes', type=int, default=DEFAULT_JOB_BYTES)
    parser.add_argument('--storage-total-bytes', type=int, default=DEFAULT_TOTAL_BYTES)
    parser.add_argument('--storage-max-jobs', type=int, default=DEFAULT_MAX_JOBS)
    options = parser.parse_args()
    if os.getuid() == 0:
        parser.error("run as the configured non-root workspace owner")
    if not Path(options.workspace).is_absolute() or not Path(options.state_root).is_absolute():
        parser.error("absolute configured directories required")
    stop = threading.Event()
    for number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(number, lambda *_: stop.set())
    controller = ProjectController(options.workspace, options.state_root, options.image,
                                   authorize=ManagedFullAccessPolicy(), python_image=options.python_image, storage_limits={
                                       'job_bytes': options.storage_bytes,
                                       'total_bytes': options.storage_total_bytes,
                                       'max_jobs': options.storage_max_jobs})
    try:
        serve(controller, stop)
    finally:
        # Drain accepted work; no new request is admitted after listener closure.
        controller.close()


if __name__ == "__main__":
    main()
