"""Provision a new owner-private Pixel home and an unloaded gateway template.

This does not start jobs, grant broker access, or expose a network listener.
The protected installer generates the actual Seatbelt policy during planning.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import pwd
import re
import shlex
import stat
import sys
import tempfile


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('native_layout_config', HERE / 'pixel-native-config.py')
config = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(config)


def absolute(value):
    path = Path(value)
    if (not path.is_absolute() or path == Path('/') or '..' in path.parts
            or any(ord(char) < 32 for char in str(path))):
        raise ValueError('absolute-native-layout-path-required')
    return path


def write_private(path, body, mode=0o600):
    with path.open('xb') as stream:
        os.fchmod(stream.fileno(), mode)
        stream.write(body)


def prepare(*, candidate, home, node, runtime, docker, docker_socket, ods_source,
            ingress_image, compose_project, ingress_gid):
    if sys.platform != 'darwin' or os.geteuid() == 0:
        raise ValueError('native-macos-owner-required')
    owner = pwd.getpwuid(os.getuid())
    if (not isinstance(ingress_image, str) or not re.fullmatch(r'sha256:[a-f0-9]{64}', ingress_image)
            or not isinstance(compose_project, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,127}', compose_project)
            or type(ingress_gid) is not int or not 0 <= ingress_gid <= 2147483647):
        raise ValueError('qualified-native-ingress-identity-required')
    candidate, runtime, ods_source = [absolute(path).resolve(strict=True)
                                     for path in (candidate, runtime, ods_source)]
    if os.path.lexists(candidate / 'migration.json'):
        raise ValueError('native-migration-requires-joint-activation')
    home = absolute(home)
    home = home.parent.resolve(strict=True) / home.name
    if os.path.lexists(home):
        raise ValueError('new-native-home-required')
    for source in (candidate / 'workspace', runtime):
        if source == home or source in home.parents:
            raise ValueError('native-home-inside-source')
    node, docker, docker_socket = [absolute(path).resolve(strict=True)
                                  for path in (node, docker, docker_socket)]
    if (not all(path.is_file() and os.access(path, os.X_OK) for path in (node, docker))
            or not stat.S_ISSOCK(docker_socket.stat().st_mode)):
        raise ValueError('native-executable-and-docker-socket-required')
    receipt = config.private_json(candidate / 'candidate.json')
    config_body = config.service_snapshot(candidate, 'openclaw.json', private=True)
    document = json.loads(config_body)
    if (type(receipt) is not dict or receipt.get('status') != 'staged'
            or receipt.get('requiresServiceQualification') is not True):
        raise ValueError('native-staged-candidate-required')
    selected = [item for item in document['agents']['list'] if item.get('id') == 'pixel']
    workspace = home / '.openclaw/workspace-pixel'
    bind = str(home / '.openclaw/.ods-exec-control') + ':/run/pixel-ods-control:ro'
    port = document['gateway']['port']
    if (len(selected) != 1 or selected[0].get('workspace') != str(workspace)
            or document['agents']['defaults']['sandbox']['docker'].get('binds') != [bind]
            or type(port) is not int or not 1 <= port <= 65535):
        raise ValueError('native-layout-candidate-home-mismatch')
    entrypoint = runtime / 'node_modules/openclaw/openclaw.mjs'
    if not entrypoint.is_file():
        raise ValueError('native-layout-runtime-unavailable')
    wrapper = ods_source / 'extensions/services/pixel-agent/host/cancellable-exec.sh'
    wrapper_record = config.bundle._file(wrapper.parent, wrapper.name)
    snapshot = config.bundle.inventory(candidate / 'workspace')
    if any(record[0] == 'link' for record in snapshot.values()):
        raise ValueError('native-workspace-symlink-refused')
    with tempfile.TemporaryDirectory(prefix='.pixel-layout-', dir=home.parent) as temporary:
        staged = Path(temporary) / 'home'
        staged.mkdir(mode=0o700)
        for relative in ('.openclaw', '.openclaw/.ods-exec-control', 'tmp', 'logs', 'docker-config'):
            (staged / relative).mkdir(mode=0o700)
        config.bundle._copy_tree(candidate / 'workspace', staged / '.openclaw/workspace-pixel', snapshot)
        for directory, _, files in os.walk(staged / '.openclaw/workspace-pixel'):
            Path(directory).chmod(0o700)
            for name in files:
                path = Path(directory) / name
                path.chmod(0o700 if path.stat().st_mode & 0o111 else 0o600)
        write_private(staged / '.openclaw/openclaw.json', config_body)
        with (staged / '.openclaw/.ods-exec-control/cancellable-exec.sh').open('xb') as output:
            if config.bundle._file(wrapper.parent, wrapper.name, output) != wrapper_record:
                raise ValueError('native-exec-wrapper-changed')
            os.fchmod(output.fileno(), 0o500)
        launcher = ('#!/bin/sh\nexec /usr/bin/env -u NODE_OPTIONS -u NODE_PATH '
                    + shlex.quote(str(node)) + ' ' + shlex.quote(str(entrypoint)) + ' "$@"\n')
        write_private(staged / 'openclaw', launcher.encode(), 0o500)
        # Never run the staging template as an unprotected owner gateway.
        write_private(staged / 'gateway-template.sb', b'(version 1)\n(deny default)\n')
        environment = {
            'HOME': str(home), 'USER': owner.pw_name, 'LOGNAME': owner.pw_name,
            'PATH': ':'.join(dict.fromkeys([str(node.parent), str(docker.parent), '/usr/bin', '/bin', '/usr/sbin', '/sbin'])),
            'OPENCLAW_CONFIG_PATH': str(home / '.openclaw/openclaw.json'),
            'OPENCLAW_STATE_DIR': str(home / '.openclaw'), 'OPENCLAW_WRAPPER': str(home / 'openclaw'),
            'TMPDIR': str(home / 'tmp'), 'DOCKER_HOST': 'unix://' + str(docker_socket),
            'DOCKER_CONFIG': str(home / 'docker-config'), 'PIXEL_AGENT_ID': 'pixel',
            'PIXEL_HISTORY_TRANSPORT': 'docker-exec', 'PIXEL_HISTORY_DOCKER': str(docker),
            'PIXEL_PREVIEW_DOCKER': str(docker),
            'PIXEL_HISTORY_IMAGE': ingress_image, 'PIXEL_HISTORY_PROJECT': compose_project,
            'PIXEL_HISTORY_USER': str(owner.pw_uid) + ':' + str(ingress_gid),
            'PIXEL_OPS_STATE_DIR': '/private/var/lib/pixel-ops-broker',
        }
        template = {
            'Label': 'com.ods.pixel-native-gateway',
            'ProgramArguments': ['/usr/bin/env', '-i', *(key + '=' + value for key, value in environment.items()),
                '/usr/bin/sandbox-exec', '-f', str(home / 'gateway-template.sb'), str(node), str(entrypoint),
                'gateway', 'run', '--port', str(port)],
            'WorkingDirectory': str(workspace),
            'StandardOutPath': str(home / 'logs/gateway.log'), 'StandardErrorPath': str(home / 'logs/gateway.log'),
        }
        write_private(staged / 'gateway.plist', plistlib.dumps(template))
        write_private(staged / 'layout.json', (json.dumps({'schemaVersion': 1,
            'status': 'staged', 'requiresServiceQualification': True,
            'pixelSourceRef': receipt['pixelSourceRef'], 'gatewayPort': port}) + '\n').encode())
        if config.service_snapshot(candidate, 'openclaw.json', private=True) != config_body or os.path.lexists(home):
            raise ValueError('native-layout-input-changed')
        os.rename(staged, home)
    return home / 'gateway.plist'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('candidate', 'home', 'node', 'runtime', 'docker', 'docker-socket', 'ods-source',
                 'ingress-image', 'compose-project'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--ingress-gid', type=int, required=True)
    args = parser.parse_args()
    try:
        path = prepare(candidate=args.candidate, home=args.home, node=args.node, runtime=args.runtime,
                       docker=args.docker, docker_socket=args.docker_socket, ods_source=args.ods_source,
                       ingress_image=args.ingress_image, compose_project=args.compose_project, ingress_gid=args.ingress_gid)
    except (OSError, ValueError, KeyError, TypeError):
        print('error: native-pixel-layout-staging-failed', file=sys.stderr)
        return 1
    print(json.dumps({'gatewayTemplate': str(path), 'status': 'staged', 'requiresServiceQualification': True}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
