"""Read installed executor identity inside its immutable image, never the host.

Only the service's background worker calls the fixed offline probe and retries
transient failures. Tool requests read cached evidence; they cannot select
commands, images or project paths.
"""
import json
import re
import subprocess
import threading
import time
import uuid

MAX_BYTES = 64 * 1024
PROBE_LABEL = 'org.osmantic.ods.project-capability'


class ProbeCleanupPending(RuntimeError):
    """The service must resolve this exact resource before another probe."""
    def __init__(self, image, name):
        super().__init__('capability probe cleanup is unconfirmed')
        self.image, self.name = image, name


def cleanup_pending_probe(image, name):
    """Confirm absence or inspect/reap only the resource created by our probe."""
    probe_arguments(image, name)
    deadline = time.monotonic() + 5
    def run(arguments):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(arguments, 5)
        return subprocess.run(arguments, capture_output=True, timeout=min(2, remaining), check=True)
    found = run(['docker', 'container', 'ls', '-aq', '--no-trunc', '--filter', 'name=^/' + name + '$'])
    if not found.stdout.strip():
        return
    container_id = found.stdout.decode('ascii').strip()
    if not re.fullmatch(r'[a-f0-9]{64}', container_id):
        raise ValueError('ambiguous capability probe identity')
    inspected = run(['docker', 'inspect', '--format',
        '{{json .Config.Image}} {{json .Name}} {{json (index .Config.Labels "' + PROBE_LABEL + '")}}',
        container_id])
    expected = ' '.join(json.dumps(value) for value in (image, '/' + name, name))
    if inspected.stdout.decode('ascii').strip() != expected:
        raise ValueError('capability probe cleanup identity mismatch')
    run(['docker', 'rm', '-f', container_id])


PROBE = """import json, platform, sys, sysconfig
from pip._vendor.packaging.tags import sys_tags
groups = {}
count = 0
for tag in sys_tags():
    count += 1
    if count > 4096:
        raise RuntimeError('wheel compatibility exceeds evidence limit')
    groups.setdefault((tag.interpreter, tag.abi), []).append(tag.platform)
libc_name, libc_version = platform.libc_ver()
value = {'python': {'version': platform.python_version(), 'implementation': sys.implementation.name,
    'cacheTag': sys.implementation.cache_tag, 'soabi': sysconfig.get_config_var('SOABI')},
    'platform': {'os': sys.platform, 'machine': platform.machine(),
        'libc': {'name': libc_name, 'version': libc_version}},
    'wheelCompatibility': {'source': 'pip._vendor.packaging.tags.sys_tags', 'complete': True,
        'tagCount': count, 'groups': [{'pythonTag': python, 'abiTag': abi, 'platformTags': platforms}
            for (python, abi), platforms in groups.items()]}}
payload = json.dumps(value, separators=(',', ':')).encode('ascii')
if len(payload) > 65536:
    raise RuntimeError('runtime evidence exceeds output limit')
sys.stdout.buffer.write(payload)
"""


def _text(value, *, empty=False):
    return (isinstance(value, str) and (empty or bool(value)) and len(value) <= 128
            and all(32 <= ord(char) <= 126 for char in value))


def validate_evidence(payload):
    if not isinstance(payload, bytes) or len(payload) > MAX_BYTES:
        raise ValueError('invalid runtime evidence length')
    try:
        value = json.loads(payload)
    except (ValueError, RecursionError) as error:
        raise ValueError('invalid runtime evidence JSON') from error
    if not isinstance(value, dict) or set(value) != {'python', 'platform', 'wheelCompatibility'}:
        raise ValueError('invalid runtime evidence')
    python, platform, wheel = value['python'], value['platform'], value['wheelCompatibility']
    if (not isinstance(python, dict) or set(python) != {'version', 'implementation', 'cacheTag', 'soabi'}
            or not all(_text(item) for item in python.values())
            or not isinstance(platform, dict) or set(platform) != {'os', 'machine', 'libc'}
            or not _text(platform['os']) or not _text(platform['machine'])
            or not isinstance(platform['libc'], dict) or set(platform['libc']) != {'name', 'version'}
            or not all(_text(item, empty=True) for item in platform['libc'].values())):
        raise ValueError('invalid Python platform evidence')
    if (not isinstance(wheel, dict) or set(wheel) != {'source', 'complete', 'tagCount', 'groups'}
            or wheel['source'] != 'pip._vendor.packaging.tags.sys_tags' or wheel['complete'] is not True
            or type(wheel['tagCount']) is not int or not 0 < wheel['tagCount'] <= 4096
            or not isinstance(wheel['groups'], list) or not 0 < len(wheel['groups']) <= 128):
        raise ValueError('invalid wheel compatibility evidence')
    tags, pairs = set(), set()
    for group in wheel['groups']:
        if (not isinstance(group, dict) or set(group) != {'pythonTag', 'abiTag', 'platformTags'}
                or not all(_text(group[key]) for key in ('pythonTag', 'abiTag'))
                or not isinstance(group['platformTags'], list) or not 0 < len(group['platformTags']) <= 256):
            raise ValueError('invalid wheel compatibility group')
        pair = (group['pythonTag'], group['abiTag'])
        if pair in pairs:
            raise ValueError('duplicate wheel compatibility group')
        pairs.add(pair)
        for tag in group['platformTags']:
            if (not _text(tag) or not re.fullmatch(r'[A-Za-z0-9_.]+', tag)
                    or not all(re.fullmatch(r'[A-Za-z0-9_]+', component) for component in pair)
                    or (*pair, tag) in tags):
                raise ValueError('invalid or duplicate wheel tag')
            tags.add((*pair, tag))
    if len(tags) != wheel['tagCount']:
        raise ValueError('incomplete wheel compatibility evidence')
    return value


def probe_arguments(image, name):
    if not isinstance(image, str) or not re.fullmatch(r'sha256:[a-f0-9]{64}', image):
        raise ValueError('immutable configured Python image required')
    if not re.fullmatch(r'ods-project-capabilities-[a-f0-9]{24}', name):
        raise ValueError('invalid probe identity')
    # Docker owns removal after process exit even if this service crashes.
    # During an outage we cannot assert absence: the live service retains the
    # exact pending receipt and creates no further probes until it is resolved.
    return ['docker', 'run', '--rm', '--name', name, '--label', PROBE_LABEL + '=' + name,
            '--pull', 'never', '--network', 'none',
            '--read-only', '--user', '1000:1000', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges', '--pids-limit', '32',
            '--memory', '128m', '--cpus', '1', '--log-driver', 'none',
            '--workdir', '/', '--entrypoint', '/usr/local/bin/python',
            image, '-I', '-c', PROBE]


def probe_python_runtime(image, *, timeout=20, cancel=None):
    if not 0 < timeout <= 30:
        raise ValueError('invalid capability probe timeout')
    if cancel is not None and cancel.is_set():
        raise InterruptedError('capability probe cancelled')
    name = 'ods-project-capabilities-' + uuid.uuid4().hex[:24]
    arguments = probe_arguments(image, name)
    buffers = [bytearray(), bytearray()]
    overflow = threading.Event()

    def drain(index, stream):
        with stream:
            while chunk := stream.read(4096):
                room = MAX_BYTES - len(buffers[index])
                buffers[index].extend(chunk[:room])
                if len(chunk) > room:
                    overflow.set()

    process = subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    readers = [threading.Thread(target=drain, args=(index, stream), daemon=True)
               for index, stream in enumerate((process.stdout, process.stderr))]
    for reader in readers:
        reader.start()
    try:
        try:
            deadline = time.monotonic() + timeout
            while True:
                if cancel is not None and cancel.is_set():
                    raise InterruptedError('capability probe cancelled')
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(arguments, timeout)
                try:
                    code = process.wait(timeout=min(remaining, 0.1))
                    break
                except subprocess.TimeoutExpired:
                    continue
        except (subprocess.TimeoutExpired, InterruptedError):
            process.kill()
            process.wait(timeout=1)
            raise
        for reader in readers:
            reader.join(timeout=0.25)
        if code != 0 or overflow.is_set() or any(reader.is_alive() for reader in readers):
            raise ValueError('Python runtime probe did not provide bounded complete evidence')
        return validate_evidence(bytes(buffers[0]))
    finally:
        # This unpredictable name was generated here, never provided by a tool.
        # Removing the exact container also stops it after client timeout.
        try:
            cleanup_pending_probe(image, name)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            raise ProbeCleanupPending(image, name) from error
        finally:
            for reader in readers:
                reader.join(timeout=0.25)
