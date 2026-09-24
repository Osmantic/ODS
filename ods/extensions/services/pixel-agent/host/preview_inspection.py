#!/usr/bin/env python3
"""Trusted fixed-purpose preview inspection broker. Never executes site bytes here."""
import base64
import json
import os
import pathlib
import pwd
import re
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import uuid
from preview_inspection_protocol import *

SOCKET = pathlib.Path('/run/ods-pixel-inspection/control.sock')
CONFIG = pathlib.Path('/usr/local/libexec/ods-pixel-services/helpers/preview-inspection.json' if sys.platform == 'darwin' else '/etc/ods-pixel-inspection.json')
DOCKER_PATHS = ('/usr/bin/docker', '/Applications/Docker.app/Contents/Resources/bin/docker')

def load_config():
    info = CONFIG.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise Invalid('unsafe configuration')
    config = strict_json(CONFIG.read_bytes())
    exact(config, ('imageId','docker','snapshotRoot','ownerUid','transport'))
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', config['imageId']) or config['docker'] not in DOCKER_PATHS or type(config['ownerUid']) is not int or config['ownerUid'] <= 0 or config['transport'] not in ('local','docker-desktop'):
        raise Invalid('invalid configuration')
    root = pathlib.Path(config['snapshotRoot'])
    if not root.is_absolute() or '..' in root.parts or root == pathlib.Path('/'):
        raise Invalid('invalid snapshot root')
    # Installation controls these binaries, never caller data or environment.
    binary = pathlib.Path(config['docker']).resolve(strict=True)
    info = binary.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise Invalid('unsafe Docker binary')
    return config

def snapshot_bundle(root, request, owner_uid=None):
    import workspace_preview as preview
    validate_request(request)
    owner_uid = os.getuid() if owner_uid is None else owner_uid
    files = []
    # Reopen with the publisher's no-symlink/stable-fd checks, then rehash the
    # exact transported bytes. A race cannot substitute a second snapshot.
    for name, source, info in preview._source_files(pathlib.Path(root), request['siteId'], owner_uid):
        if stat.S_IMODE(info.st_mode) != 0o400:
            raise Invalid('unsafe snapshot')
        data = preview._read_stable(source, info)
        files.append({'path':name,'base64':base64.b64encode(data).decode()})
    bundle = {'schemaVersion':1,'request':request,'files':files}
    validate_bundle(bundle)
    return bundle

def bounded_process(argv, body, *, timeout, limit, cancelled=None):
    # Only fixed trusted executables reach this function; no shell or inherited
    # API/SSH/Docker environment. Docker's ordinary current-user config is not
    # mounted in, or forwarded to, the capsule.
    env = {'PATH':'/usr/bin:/bin','HOME':os.path.expanduser('~')}
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, env=env)
    output, errors = bytearray(), []
    def feed():
        try:
            process.stdin.write(body); process.stdin.close()
        except (OSError, ValueError):
            pass
    def drain():
        try:
            while True:
                chunk = process.stdout.read(4096)
                if not chunk:
                    break
                if len(output) + len(chunk) > limit:
                    errors.append('output_limit'); process.kill(); break
                output.extend(chunk)
        except OSError:
            errors.append('unavailable')
    writer, reader = threading.Thread(target=feed, daemon=True), threading.Thread(target=drain, daemon=True)
    writer.start(); reader.start()
    deadline = time.monotonic() + timeout
    try:
        while process.poll() is None:
            if cancelled and cancelled.is_set():
                raise Invalid('cancelled')
            if time.monotonic() >= deadline:
                raise Invalid('timeout')
            time.sleep(.025)
        reader.join(2)
        if reader.is_alive() or errors or process.returncode:
            raise Invalid(errors[0] if errors else 'unavailable')
        return bytes(output)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        writer.join(1); reader.join(1)
        process.stdout.close()

def docker_prefix(config):
    endpoint = '/var/run/docker.sock'
    if config['transport'] == 'docker-desktop':
        home = pathlib.Path(pwd.getpwuid(config['ownerUid']).pw_dir)
        if not home.is_absolute() or '..' in home.parts:
            raise Invalid('invalid owner home')
        endpoint = str(home / '.docker/run/docker.sock')
    return [config['docker'], '--host', 'unix://' + endpoint]

def capsule_argv(config, name):
    return [*docker_prefix(config),'run' ,'--rm','-i','--pull=never','--name',name,
            '--network=none','--read-only','--user','65534:65534',
            '--cap-drop=ALL','--security-opt=no-new-privileges',
            '--pids-limit=128','--memory=1g','--memory-swap=1g','--cpus=1',
            '--ipc=private','--shm-size=128m',
            '--tmpfs','/tmp:rw,nosuid,nodev,size=256m,mode=1777',
            '--entrypoint','python3',config['imageId'],'/source/preview_inspection_capsule.py']

def inspect_request(request, config, cancelled=None):
    validate_request(request)
    if os.getuid() not in (0, config['ownerUid']):
        raise Invalid('unauthorized')
    if config['transport'] == 'local':
        bundle = snapshot_bundle(config['snapshotRoot'], request, config['ownerUid'])
    else:
        raw = bounded_process([*docker_prefix(config),'exec','-i','ods-pixel-workspace-preview',
            'python3','/source/preview_inspection.py','export'], canonical(request),
            timeout=15, limit=MAX_BUNDLE, cancelled=cancelled)
        bundle = strict_json(raw)
        bound, _files = validate_bundle(bundle)
        if bound != request:
            raise Invalid('snapshot mismatch')
    name = 'ods-preview-inspection-' + uuid.uuid4().hex
    try:
        raw = bounded_process(capsule_argv(config, name), canonical(bundle),
                              timeout=45, limit=MAX_RESULT, cancelled=cancelled)
        result = strict_json(raw)
        if not isinstance(result, dict) or result.get('kind') != KIND or result.get('schemaVersion') != 1 or result.get('siteId') != request['siteId'] or result.get('sha256') != request['sha256'] or result.get('planSha256') != plan_hash(request) or result.get('status') not in ('passed','failed') or result.get('scope') != SCOPE:
            raise Invalid('invalid capsule receipt')
        return result
    finally:
        # Killing `docker run` alone does not stop its container. This random
        # name belongs only to this call. Cleanup executes on every exit path.
        try:
            bounded_process([*docker_prefix(config),'rm','-f',name], b'', timeout=5, limit=1024)
        except (OSError, ValueError):
            pass

def read_request(stream):
    raw = stream.read(MAX_REQUEST + 1)
    if len(raw) > MAX_REQUEST:
        raise Invalid('request too large')
    return validate_request(strict_json(raw))

def handle(connection, config):
    from unix_peer import peer_ids
    request = None
    cancelled = threading.Event()
    try:
        if peer_ids(connection)[0] != config['ownerUid']:
            raise Invalid('unauthorized')
        connection.settimeout(5)
        raw = bytearray()
        while len(raw) <= MAX_REQUEST:
            chunk = connection.recv(1024)
            if not chunk:
                raise Invalid('invalid framing')
            raw.extend(chunk)
            if b'\n' in raw:
                break
        if len(raw) > MAX_REQUEST or raw.count(b'\n') != 1 or not raw.endswith(b'\n'):
            raise Invalid('invalid framing')
        request = validate_request(strict_json(raw))
        def watch_disconnect():
            connection.settimeout(.25)
            while not cancelled.is_set():
                try:
                    connection.recv(1)
                    cancelled.set(); break
                except socket.timeout:
                    continue
                except OSError:
                    cancelled.set(); break
        watcher = threading.Thread(target=watch_disconnect, daemon=True)
        watcher.start()
        result = inspect_request(request, config, cancelled)
    except Exception:
        result = failure('unavailable', request)
    finally:
        cancelled.set()
    try:
        connection.sendall(canonical(result) + b'\n')
    except OSError:
        pass

def serve(config):
    from unix_peer import peer_ids  # require kernel peer-identity support early
    if os.getuid() not in (0, config['ownerUid']):
        raise Invalid('unauthorized')
    parent = SOCKET.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid() or parent.st_mode & 0o022:
        raise Invalid('unsafe socket directory')
    if SOCKET.exists() or SOCKET.is_symlink():
        info = SOCKET.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
            raise Invalid('unsafe socket')
        SOCKET.unlink()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(SOCKET)); os.chmod(SOCKET, 0o660); listener.listen(2)
        try:
            while True:
                connection, _ = listener.accept()
                with connection:
                    handle(connection, config)
        finally:
            SOCKET.unlink(missing_ok=True)

def health(config):
    raw = bounded_process([*docker_prefix(config),'image','inspect','--format','{{.Id}}',config['imageId']], b'', timeout=5, limit=256)
    if raw.decode().strip() != config['imageId']:
        raise Invalid('image unavailable')
    return {'schemaVersion':1,'kind':KIND,'status':'ready','imageId':config['imageId']}

def main():
    request = None
    try:
        if sys.argv[1:] == ['export']:
            request = read_request(sys.stdin.buffer)
            result = snapshot_bundle('/previews', request)
        elif sys.argv[1:] == ['health']:
            result = health(load_config())
        elif sys.argv[1:] == ['serve']:
            def stop(*_):
                raise KeyboardInterrupt()
            for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                signal.signal(sig, stop)
            serve(load_config()); return
        elif sys.argv[1:] == ['request']:
            request = read_request(sys.stdin.buffer)
            cancelled = threading.Event()
            for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                signal.signal(sig, lambda *_: cancelled.set())
            result = inspect_request(request, load_config(), cancelled)
        else:
            raise Invalid('invalid invocation')
    except Exception:
        result = failure('unavailable', request)
    sys.stdout.buffer.write(canonical(result) + b'\n')

if __name__ == '__main__':
    main()
