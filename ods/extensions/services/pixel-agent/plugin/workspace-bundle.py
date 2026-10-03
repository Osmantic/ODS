"""Fixed workspace-only byte bundler; invoked by the trusted plugin adapter.

The model supplies paths and keys, never code or file contents. Each
execution creates a unique generation and never replaces an existing output.
A failed generation is retained as incomplete, never reported as verified.
"""
import base64
import hashlib
import json
import os
import re
import secrets
import stat
import sys

MAX_FILE = 256 * 1024
MAX_TOTAL = 1024 * 1024
FLAGS = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._ -]{0,127}")
SHA = re.compile(r"[a-f0-9]{64}")


class BundleError(Exception):
    pass


def require(condition, reason):
    if not condition:
        raise BundleError(reason)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def parts(value):
    require(isinstance(value, str) and 0 < len(value) <= 512, "invalid-path")
    result = value.split('/')
    require(len(result) <= 16 and all(COMPONENT.fullmatch(x) and not x.endswith(('.', ' '))
            and x not in ('.', '..') for x in result), "invalid-path")
    return result


def validate(request):
    require(isinstance(request, dict) and set(request) == {'files', 'mappingPath', 'outputRoot'}, "invalid-request")
    files = request['files']
    require(isinstance(files, list) and 1 <= len(files) <= 32, "invalid-file-count")
    parts(request['mappingPath'])
    parts(request['outputRoot'])
    sources, outputs, keys = set(), {request['mappingPath'].casefold()}, set()
    for item in files:
        require(isinstance(item, dict) and set(item) == {'source', 'key', 'copyTo'}, "invalid-file")
        parts(item['source'])
        parts(item['copyTo'])
        require(isinstance(item['key'], str) and 0 < len(item['key']) <= 256
                and not any(ord(c) < 32 for c in item['key']), "invalid-key")
        require(item['key'] not in keys and item['source'].casefold() not in sources
                and item['copyTo'].casefold() not in outputs, "duplicate-path-or-key")
        keys.add(item['key'])
        sources.add(item['source'].casefold())
        outputs.add(item['copyTo'].casefold())
    all_paths = sorted(outputs)
    require(not any(b.startswith(a + '/') for a, b in zip(all_paths, all_paths[1:])), "nested-file-path")
    return files


def stable(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def parent(root, relative, create=False):
    fd = os.dup(root)
    try:
        for component in parts(relative)[:-1]:
            if create:
                try:
                    os.mkdir(component, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(component, FLAGS | os.O_DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def read(root, relative, limit=MAX_FILE, missing=False, with_identity=False):
    try:
        directory = parent(root, relative)
    except FileNotFoundError:
        if missing:
            return None
        raise
    try:
        try:
            fd = os.open(parts(relative)[-1], FLAGS, dir_fd=directory)
        except FileNotFoundError:
            if missing:
                return None
            raise
        try:
            before = os.fstat(fd)
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= limit, "unsafe-file")
            data = bytearray()
            while len(data) <= limit:
                block = os.read(fd, min(65536, limit + 1 - len(data)))
                if not block:
                    break
                data.extend(block)
            require(len(data) <= limit and stable(before) == stable(os.fstat(fd)), "changed-or-large-file")
            require(stable(before) == stable(os.stat(parts(relative)[-1], dir_fd=directory, follow_symlinks=False)), 'source-path-changed')
            return (bytes(data), stable(before)) if with_identity else bytes(data)
        finally:
            os.close(fd)
    finally:
        os.close(directory)


def derive(root, request):
    files = validate(request)
    sources, outputs, mapping, identities, total = [], {}, {}, [], 0
    for item in files:
        data, identity = read(root, item['source'], with_identity=True)
        identities.append(identity)
        total += len(data)
        require(total <= MAX_TOTAL, "source-total-too-large")
        try:
            text = data.decode('utf-8', errors='strict')
        except UnicodeDecodeError:
            raise BundleError('source-not-utf8')
        require(text.encode('utf-8') == data, "source-roundtrip-failed")
        sources.append({'path': item['source'], 'bytes': len(data), 'sha256': digest(data)})
        mapping[item['key']] = text
        outputs[item['copyTo']] = data
    # JSON whitespace is deterministic; the values preserve every original byte.
    encoded = (json.dumps(mapping, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')
    parsed = json.loads(encoded)
    require(all(parsed[item['key']].encode('utf-8') == outputs[item['copyTo']] for item in files), "mapping-roundtrip-failed")
    outputs[request['mappingPath']] = encoded
    return sources, outputs, identities


def atomic_write(root, name, data):
    directory = parent(root, name, create=True)
    temporary = '.ods-bundle-' + secrets.token_hex(12)
    fd = None
    try:
        fd = os.open(temporary, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        offset = 0
        while offset < len(data):
            offset += os.write(fd, data[offset:])
        os.fsync(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        observed = bytearray()
        while len(observed) < len(data):
            chunk = os.read(fd, min(65536, len(data) - len(observed)))
            require(bool(chunk), "staged-readback-failed")
            observed.extend(chunk)
        require(bytes(observed) == data, "staged-readback-failed")
        os.close(fd)
        fd = None
        rebound = parent(root, name)
        try:
            current_parent, original_parent = os.fstat(rebound), os.fstat(directory)
            require((current_parent.st_dev, current_parent.st_ino) ==
                    (original_parent.st_dev, original_parent.st_ino), "output-parent-changed")
        finally:
            os.close(rebound)
        # Atomic create-only publication: a concurrent owner/editor-created
        # destination wins, even when it appears after all prior checks.
        os.link(temporary, parts(name)[-1], src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
        os.unlink(temporary, dir_fd=directory)
        os.fsync(directory)
    finally:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass
        os.close(directory)


def bundle(payload, workspace='.'):
    require(os.name == 'posix' and hasattr(os, 'O_NOFOLLOW'), 'posix-workspace-required')
    require(isinstance(payload, dict) and set(payload) == {'request', 'generation'}, "invalid-operation")
    request, generation = payload['request'], payload['generation']
    validate(request)
    require(isinstance(generation, str) and re.fullmatch(r'bundle-[a-f0-9]{32}', generation), 'invalid-generation')
    generation_path = request['outputRoot'] + '/' + generation
    parts(generation_path)
    root = os.open(workspace, FLAGS | os.O_DIRECTORY)
    written, created, directory = [], False, None
    try:
        sources, outputs, identities = derive(root, request)
        holder = parent(root, generation_path, create=True)
        try:
            os.mkdir(generation, 0o700, dir_fd=holder)
            created = True
            directory = os.open(generation, FLAGS | os.O_DIRECTORY, dir_fd=holder)
        finally:
            os.close(holder)
        for name, data in outputs.items():
            atomic_write(directory, name, data)
            written.append(generation_path + '/' + name)
        require(all(read(directory, name, MAX_TOTAL * 6 + 4096) == data for name, data in outputs.items()), 'output-readback-failed')
        final_sources, _, final_identities = derive(root, request)
        require(final_sources == sources and final_identities == identities, 'source-changed-during-generation')
        # Reopen through the workspace path too; an externally renamed parent
        # must never turn an unreachable generation into a success receipt.
        require(all(read(root, generation_path + '/' + name, MAX_TOTAL * 6 + 4096) == data for name, data in outputs.items()), 'generation-path-changed')
        return {'schemaVersion': 2, 'kind': 'ods-pixel-workspace-bundle', 'status': 'succeeded',
                'generationPath': generation_path, 'mappingPath': generation_path + '/' + request['mappingPath'],
                'sources': sources, 'outputs': [{'path': generation_path + '/' + name, 'bytes': len(data), 'sha256': digest(data)} for name, data in sorted(outputs.items())],
                'written': written, 'readbackVerified': True, 'decodedAndRawBytesEqual': True,
                'boundary': 'Immutable derived bytes only; no execution, publication, or task-correctness proof.'}
    except BaseException as error:
        return {'schemaVersion': 2, 'kind': 'ods-pixel-workspace-bundle', 'status': 'failed',
                'generationPath': generation_path if created else None,
                'written': written, 'readbackVerified': False,
                'error': str(error) if isinstance(error, BundleError) else 'workspace-io-failed'}
    finally:
        if directory is not None:
            os.close(directory)
        os.close(root)


if __name__ == '__main__':
    try:
        require(len(sys.argv) == 2 and len(sys.argv[1]) <= 131072, 'invalid-payload')
        value = bundle(json.loads(base64.b64decode(sys.argv[1], validate=True)))
    except BaseException:
        value = {'schemaVersion': 2, 'kind': 'ods-pixel-workspace-bundle', 'status': 'failed', 'error': 'invalid-payload', 'readbackVerified': False}
    print(json.dumps(value, ensure_ascii=True, separators=(',', ':')))
    raise SystemExit(0 if value['status'] == 'succeeded' else 1)
