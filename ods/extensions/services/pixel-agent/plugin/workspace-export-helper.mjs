// Fixed serializer executed only by an ordinary, separately admitted exec call.
export const WORKSPACE_EXPORT_HELPER = String.raw`
import base64, hashlib, json, os, signal, stat, sys, unicodedata, zlib

MAX_FILE = 1024 * 1024
MAX_TOTAL = 4 * MAX_FILE
NOFOLLOW = os.O_NOFOLLOW
DIRECTORY = os.O_DIRECTORY
fds = []
created = []
root = None

class ExportError(Exception):
    pass

def fail(reason):
    raise ExportError(reason)

def valid_path(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 512:
        fail('invalid-relative-path')
    if value != unicodedata.normalize('NFC', value) or '\\' in value:
        fail('invalid-relative-path')
    parts = value.split('/')
    if len(parts) > 12 or any(not p or p in ('.', '..') or len(p) > 128 or
        any(ord(c) < 32 or ord(c) == 127 for c in p) for p in parts):
        fail('invalid-relative-path')
    return parts

def keep(fd):
    fds.append(fd)
    return fd

def parent(value):
    parts = valid_path(value)
    fd = os.dup(root)
    try:
        for component in parts[:-1]:
            child = os.open(component, os.O_RDONLY | DIRECTORY | NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd, parts[-1]
    except BaseException:
        os.close(fd)
        raise

def identity(st):
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)

def source(value, limit=MAX_FILE):
    directory, name = parent(value)
    try:
        fd = keep(os.open(name, os.O_RDONLY | NOFOLLOW | os.O_NONBLOCK, dir_fd=directory))
    finally:
        os.close(directory)
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
        fail('source-not-bounded-private-regular-file')
    chunks = []
    total = 0
    while True:
        chunk = os.read(fd, min(65536, limit + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > limit:
            fail('source-too-large')
    if identity(os.fstat(fd)) != identity(before) or total != before.st_size:
        fail('source-changed')
    return fd, identity(before), b''.join(chunks)

def digest(data):
    return hashlib.sha256(data).hexdigest()

def absent(directory, name):
    try:
        os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return
    fail('destination-exists')

def validate_sources(inputs):
    for path, fd, original in inputs:
        if identity(os.fstat(fd)) != original:
            fail('source-changed')
        directory, name = parent(path)
        try:
            current = os.stat(name, dir_fd=directory, follow_symlinks=False)
        finally:
            os.close(directory)
        if not stat.S_ISREG(current.st_mode) or identity(current) != original:
            fail('source-path-changed')

def write_output(path, data):
    directory, name = parent(path)
    keep(directory)
    fd = keep(os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | NOFOLLOW, 0o600, dir_fd=directory))
    own = os.fstat(fd)
    created.append((directory, name, own.st_dev, own.st_ino))
    remaining = memoryview(data)
    while remaining:
        count = os.write(fd, remaining)
        if count <= 0:
            fail('output-write-stalled')
        remaining = remaining[count:]
    os.fsync(fd)
    directory2, name2 = parent(path)
    try:
        current = os.stat(name2, dir_fd=directory2, follow_symlinks=False)
    finally:
        os.close(directory2)
    if not stat.S_ISREG(current.st_mode) or (current.st_dev, current.st_ino) != (own.st_dev, own.st_ino) or current.st_size != len(data):
        fail('output-readback-changed')
    return {'path': path, 'bytes': len(data), 'sha256': digest(data), 'readbackVerified': True}

def validate_outputs(records):
    for record, (_directory, _name, dev, ino) in zip(records, created):
        _fd, observed, data = source(record['path'], record['bytes'])
        if observed[:2] != (dev, ino) or len(data) != record['bytes'] or digest(data) != record['sha256']:
            fail('output-readback-changed')

def run():
    global root
    encoded = sys.argv[1]
    if len(encoded) > 4096:
        fail('request-too-large')
    decoder = zlib.decompressobj()
    decoded = decoder.decompress(base64.b85decode(encoded), 2049)
    if len(decoded) > 2048 or not decoder.eof or decoder.unused_data:
        fail('request-too-large')
    plan = json.loads(decoded.decode('utf-8'))
    if not isinstance(plan, dict) or set(plan) - {'files', 'textMap'} or not isinstance(plan.get('files'), list) or not 1 <= len(plan['files']) <= 16:
        fail('invalid-plan')
    root = keep(os.open('.', os.O_RDONLY | DIRECTORY | NOFOLLOW))
    inputs, outputs, rows, aliases, keys = [], [], [], set(), set()
    mapping = {}
    total = 0
    for item in plan['files']:
        if not isinstance(item, dict) or set(item) - {'source', 'destination', 'key'} or 'source' not in item:
            fail('invalid-file-entry')
        valid_path(item['source'])
        if 'destination' not in item and 'textMap' not in plan:
            fail('missing-output')
        if 'textMap' in plan:
            key = item.get('key')
            if not isinstance(key, str) or not 1 <= len(key) <= 128 or any(ord(c) < 32 or ord(c) == 127 for c in key) or key in keys:
                fail('invalid-or-duplicate-map-key')
            keys.add(key)
        elif 'key' in item:
            fail('map-key-without-map')
        fd, original, data = source(item['source'])
        if original[:2] in aliases:
            fail('source-alias')
        aliases.add(original[:2])
        inputs.append((item['source'], fd, original))
        total += len(data)
        if total > MAX_TOTAL:
            fail('sources-too-large')
        rows.append({'source': item['source'], 'bytes': len(data), 'sha256': digest(data)})
        if 'destination' in item:
            outputs.append((item['destination'], data))
        if 'textMap' in plan:
            mapping[item['key']] = data.decode('utf-8', errors='strict')
    if 'textMap' in plan:
        outputs.append((plan['textMap'], (json.dumps(mapping, ensure_ascii=False, indent=2) + '\n').encode('utf-8')))
    seen = set()
    for path, data in outputs:
        valid_path(path)
        alias = path.casefold()
        if alias in seen:
            fail('destination-alias')
        seen.add(alias)
        directory, name = parent(path)
        try:
            absent(directory, name)
        finally:
            os.close(directory)
    validate_sources(inputs)
    planned = [{'path': path, 'bytes': len(data), 'sha256': digest(data), 'readbackVerified': True} for path, data in outputs]
    receipt = {'schemaVersion': 1, 'kind': 'ods-workspace-export', 'status': 'succeeded', 'sources': rows, 'outputs': planned,
            'boundary': 'Exact file copies and optional UTF-8 text map only; no tests, code execution, publication or task completion verified.'}
    if len(json.dumps(receipt, ensure_ascii=False).encode('utf-8')) > 2400:
        fail('receipt-too-large')
    written = [write_output(path, data) for path, data in outputs]
    validate_outputs(written)
    validate_sources(inputs)
    return receipt

def cancelled(_signum, _frame):
    fail('cancelled')

signal.signal(signal.SIGTERM, cancelled)
signal.signal(signal.SIGINT, cancelled)
try:
    receipt = run()
except BaseException as error:
    for directory, name, dev, ino in reversed(created):
        try:
            current = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if (current.st_dev, current.st_ino) == (dev, ino):
                os.unlink(name, dir_fd=directory)
        except OSError:
            pass
    reason = str(error) if isinstance(error, ExportError) else 'invalid-input-or-filesystem-error'
    print(json.dumps({'schemaVersion': 1, 'kind': 'ods-workspace-export', 'status': 'failed', 'reason': reason}), file=sys.stderr)
    sys.exit(2)
finally:
    for fd in reversed(fds):
        os.close(fd)
print(json.dumps(receipt, ensure_ascii=False))
`;
