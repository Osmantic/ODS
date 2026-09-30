"""Hard per-job tmpfs bounds and durable aggregate reservations.

This is internal service policy, never a project/tool argument. Reservations
survive controller death; only confirmed removal releases their capacity.
"""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

MIB = 1024 * 1024
DEFAULT_JOB_BYTES = 1024 * MIB
DEFAULT_TOTAL_BYTES = 2048 * MIB
DEFAULT_MAX_JOBS = 2
STAGE_MEMORY = 2048 * MIB
HEADROOM = 512 * MIB
KEEPER_SECONDS = 1200
MAX_INODES = 65536


class StorageAdmissionError(ValueError):
    """Closed diagnostic reason; never includes Docker output or paths."""
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def validate_limits(job_bytes, total_bytes, max_jobs):
    if (type(job_bytes) is not int or not 32 * MIB <= job_bytes <= 4096 * MIB
            or type(total_bytes) is not int or not job_bytes <= total_bytes <= 8192 * MIB
            or type(max_jobs) is not int or not 1 <= max_jobs <= 8):
        raise ValueError('invalid project storage limits')


def volume_options(size):
    validate_limits(size, max(size, DEFAULT_TOTAL_BYTES), 1)
    return {'type': 'tmpfs', 'device': 'tmpfs',
            'o': f'size={size},nr_inodes={MAX_INODES},uid=1000,gid=1000,nosuid,nodev'}


def verify_volume(value, job, size=None):
    options = value.get('Options') or {}
    found = re.fullmatch(r'size=([0-9]+),nr_inodes=65536,uid=1000,gid=1000,nosuid,nodev', options.get('o', ''))
    if not found:
        return False
    actual_size = int(found[1])
    try:
        expected = volume_options(actual_size)
    except ValueError:
        return False
    return (value.get('Name') == job and value.get('Driver') == 'local'
            and value.get('Scope') == 'local' and options == expected
            and (size is None or size == actual_size)
            and value.get('Labels') == {'org.osmantic.ods.project-job': job,
                                       'org.osmantic.ods.project-storage': 'tmpfs-v1'})


def engine_headroom(image):
    """Read the Linux engine's VM memory, not the CLI host's memory."""
    result = subprocess.run([
        'docker', 'run', '--rm', '--network', 'none', '--read-only', '--user', '1000:1000',
        '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--pids-limit', '16',
        '--memory', '32m', '--memory-swap', '32m', '--log-driver', 'none',
        image, 'cat', '/proc/meminfo'], capture_output=True, timeout=10, check=True)
    if len(result.stdout) > 16384:
        raise ValueError('engine memory evidence invalid')
    found = {}
    for line in result.stdout.decode('ascii').splitlines():
        match = re.fullmatch(r'(MemTotal|MemAvailable):\s+([0-9]+) kB', line)
        if match:
            found[match[1]] = int(match[2]) * 1024
    capacity = subprocess.run(['docker', 'info', '--format', '{{.MemTotal}}'],
                              capture_output=True, timeout=5, check=True)
    total = int(capacity.stdout)
    if set(found) != {'MemTotal', 'MemAvailable'} or not 0 < found['MemAvailable'] <= found['MemTotal']:
        raise ValueError('engine memory evidence unavailable')
    return min(total, found['MemTotal']), min(total, found['MemAvailable'])


def engine_storage_jobs():
    result = subprocess.run(['docker', 'volume', 'ls', '--format', '{{.Name}}', '--filter',
                             'label=org.osmantic.ods.project-job'],
                            capture_output=True, timeout=5, check=True)
    if len(result.stdout) > 4096:
        raise ValueError('project storage inventory exceeds bound')
    names = result.stdout.decode('ascii').split()
    if any(not re.fullmatch(r'ods-project-[a-f0-9]{24}', name) for name in names):
        raise ValueError('unknown bounded project volume')
    return set(names)


class ProjectStorage:
    def __init__(self, state, *, job_bytes=DEFAULT_JOB_BYTES, total_bytes=DEFAULT_TOTAL_BYTES,
                 max_jobs=DEFAULT_MAX_JOBS):
        validate_limits(job_bytes, total_bytes, max_jobs)
        self.state = Path(state)
        self.job_bytes, self.total_bytes, self.max_jobs = job_bytes, total_bytes, max_jobs

    @contextmanager
    def locked(self):
        info = self.state.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('private project state required')
        fd = os.open(self.state / 'storage.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError('unsafe project storage lock')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)

    def _read(self):
        try:
            fd = os.open(self.state / 'storage.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return {}
        with os.fdopen(fd, 'rb') as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError('unsafe project storage reservations')
            value = json.loads(source.read(8193))
        if (type(value) is not dict or len(value) > 8
                or any(not re.fullmatch(r'ods-project-[a-f0-9]{24}', key)
                       or type(size) is not int or not 32 * MIB <= size <= 4096 * MIB
                       for key, size in value.items())):
            raise ValueError('invalid project storage reservations')
        return value

    def _write(self, value):
        fd, temporary = tempfile.mkstemp(prefix='.storage-', dir=self.state)
        try:
            with os.fdopen(fd, 'w') as target:
                json.dump(value, target, sort_keys=True)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self.state / 'storage.json')
            directory = os.open(self.state, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def reserve(self, image, job):
        if not re.fullmatch(r'ods-project-[a-f0-9]{24}', job):
            raise ValueError('invalid project job')
        with self.locked():
            value = self._read()
            if job in value:
                raise ValueError('project storage reservation already exists')
            if engine_storage_jobs() - value.keys():
                raise StorageAdmissionError('storage-recovery-required', 'unaccounted project storage requires recovery')
            if len(value) >= self.max_jobs or sum(value.values()) + self.job_bytes > self.total_bytes:
                raise StorageAdmissionError('storage-capacity-reserved', 'project storage capacity reserved; recover prior jobs first')
            total, available = engine_headroom(image)
            # An orphaned stage can still grow to its cgroup maximum. Reserve
            # its worst case as well as tmpfs bytes; current usage alone is not
            # a promise that an older job will remain small.
            required = sum(value.values()) + self.job_bytes + (len(value) + 1) * (STAGE_MEMORY + HEADROOM)
            if required > total or required > available:
                raise StorageAdmissionError('engine-headroom-insufficient', 'insufficient verified engine memory headroom')
            # Intent precedes Docker resources, so a crash cannot erase their budget.
            value[job] = self.job_bytes
            self._write(value)

    def create_volume(self, job):
        with self.locked():
            size = self._read().get(job)
            if size is None:
                raise ValueError('project storage reservation missing')
            found = subprocess.run(['docker', 'volume', 'inspect', job], capture_output=True, timeout=10)
            if found.returncode == 0:
                raise ValueError('project volume already exists')
            options = volume_options(size)
            args = ['docker', 'volume', 'create', '--driver', 'local', '--label',
                    'org.osmantic.ods.project-job=' + job, '--label', 'org.osmantic.ods.project-storage=tmpfs-v1']
            for key, value in options.items():
                args += ['--opt', key + '=' + value]
            subprocess.run([*args, job], capture_output=True, timeout=10, check=True)
            observed = subprocess.run(['docker', 'volume', 'inspect', job], capture_output=True, timeout=10, check=True)
            if not verify_volume(json.loads(observed.stdout)[0], job, size):
                raise ValueError('project tmpfs identity unconfirmed')

    def release_removed(self, job):
        """Only absence of every mount/container and the volume releases capacity."""
        with self.locked():
            value = self._read()
            if job not in value:
                return
            for selector in ('volume=' + job, 'label=org.osmantic.ods.project-job=' + job,
                             'name=^/' + job + '-'):
                result = subprocess.run(['docker', 'ps', '-aq', '--filter', selector], capture_output=True, timeout=5, check=True)
                if result.stdout.strip():
                    raise ValueError('project resources remain; storage reservation retained')
            result = subprocess.run(['docker', 'volume', 'ls', '--format', '{{.Name}}', '--filter', 'name=^' + job + '$'],
                                    capture_output=True, timeout=5, check=True)
            if result.stdout.strip():
                raise ValueError('project volume remains; storage reservation retained')
            del value[job]
            self._write(value)
