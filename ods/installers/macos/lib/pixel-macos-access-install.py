#!/usr/bin/env python3
"""Install the native macOS Pixel gateway/access LaunchDaemons.

The default is a plan-only run. A real commit requires root, Darwin, an
explicitly ODS-shaped gateway definition, and no conflicting system daemon.
Migration requires an existing owner gateway; --initial-install instead requires
an unloaded staging template, absence of the owner job and free loopback ports.
Sources and all destinations are checked before writing. Existing
files are never force-overwritten: identical files are adopted, while drift
aborts. Migration failures restore the previous gateway only after the new
jobs are confirmed stopped. Initial failures never create a previous service;
they retain admission hold, disabled jobs and a journal for explicit recovery.
"""
from __future__ import annotations

import argparse
import ast
import base64
import grp
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import pwd
import re
import shlex
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error


HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("pixel_macos_launchd", HERE / "pixel-access-launchd.py")
_launchd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_launchd)
_policy_spec = importlib.util.spec_from_file_location("pixel_macos_policy", HERE.parents[2] / "bin/pixel_macos_policy.py")
_policy = importlib.util.module_from_spec(_policy_spec)
_policy_spec.loader.exec_module(_policy)
_bundle_spec = importlib.util.spec_from_file_location('pixel_runtime_bundle', HERE / 'pixel-runtime-bundle.py')
_bundle = importlib.util.module_from_spec(_bundle_spec)
_bundle_spec.loader.exec_module(_bundle)
_upgrade_spec = importlib.util.spec_from_file_location('pixel_runtime_upgrade', HERE / 'pixel-runtime-upgrade.py')
_upgrade = importlib.util.module_from_spec(_upgrade_spec)
_upgrade_spec.loader.exec_module(_upgrade)
_services_spec = importlib.util.spec_from_file_location('pixel_native_services', HERE / 'pixel-native-services.py')
_native_services = importlib.util.module_from_spec(_services_spec)
_services_spec.loader.exec_module(_native_services)
_env_spec = importlib.util.spec_from_file_location('native_env_values',
    HERE.parents[2] / 'extensions/services/dashboard-api/env_values.py')
_env_values = importlib.util.module_from_spec(_env_spec)
_env_spec.loader.exec_module(_env_values)

GATEWAY_LAUNCHER = Path("/usr/local/libexec/ods-pixel-access/openclaw-gateway-launcher")
PROFILE = Path("/etc/ods/pixel-gateway.sb")
ACCESS_PROGRAM_ROOT = Path("/usr/local/libexec/ods-pixel-access")
RUNTIME_CONFIG_ROOT = Path('/private/var/lib/ods-pixel-native-config')
HOST_FILES = (
    "access_mode_server.py", "unix_peer.py", "access_mode_worker.py", "pixel_access_mode.py",
    "access_mode_config.py", "settings_transaction.py", "provider_transaction.py",
    "model_transaction.py", "pixel_access_bridge.py", "pixel_gateway_service.py",
    "pixel_access_client.py", "pixel_access_reconcile.py", "pixel_model_transition.py",
    "pixel_access_protocol.py", "pixel_macos_custody.py", "pixel_macos_process.py",
    "pixel_model_contract.py", "pixel_model_coordinator.py", "pixel_macos_policy.py",
)
SETTINGS_FILES = ("__init__.py", "contract.py", "projection.py", "runtime.py", "coordinator.py")
PROVIDER_FILES = ("__init__.py", "config.py", "store.py", "activation_config.py", "managed_deployment.py",
                  "service_environment.py", "service_activation.py", "runtime_custody.py", "coordinator.py")
ACCESS_FILES = {
    "config": _launchd.ACCESS_CONFIG,
    "key": _launchd.ACCESS_KEY,
    "plist": _launchd.ACCESS_PLIST,
}


class InstallError(ValueError):
    def __init__(self, code, *, returncode=None):
        self.code, self.returncode = code, returncode
        super().__init__(code)


def _path(value, code="invalid-path"):
    value = os.fspath(value)
    if (not isinstance(value, str) or not value.startswith("/") or value == "/"
            or any(part in ("", ".", "..") for part in value.split("/")[1:])
            or any(c in value for c in "\0\n\r\t")):
        raise InstallError(code)
    return Path(value)


def _read_plist(path):
    try:
        return plistlib.loads(Path(path).read_bytes())
    except (OSError, ValueError, TypeError, plistlib.InvalidFileException):
        raise InstallError("native-gateway-plist-unavailable") from None


def _env_assignments(arguments):
    if (type(arguments) is not list or len(arguments) < 4
            or arguments[:2] != ["/usr/bin/env", "-i"]
            or any(not isinstance(value, str) or not value for value in arguments)):
        raise InstallError("native-gateway-env-boundary-required")
    values, command = {}, []
    for value in arguments[2:]:
        if not command and "=" in value:
            key, _, item = value.partition("=")
            if not key or not key.replace("_", "a").isalnum() or not (key[0].isalpha() or key[0] == "_"):
                raise InstallError("native-gateway-env-invalid")
            if key in values:
                raise InstallError("native-gateway-env-invalid")
            values[key] = item
        else:
            command.append(value)
    if not command:
        raise InstallError("native-gateway-command-unavailable")
    return values, command


def _source_gateway(source, owner, port):
    document = _read_plist(source)
    if (type(document) is not dict or document.get("Label") != _launchd.GATEWAY_LABEL
            or document.get("UserName") not in (None, owner)):
        raise InstallError("native-gateway-plist-not-ods")
    environment, command = _env_assignments(document.get("ProgramArguments"))
    if (len(command) >= 8 and command[:2] == ['/usr/bin/sandbox-exec', '-f']
            and command[3:6] == [str(GATEWAY_LAUNCHER), 'gateway', 'run']):
        root = Path(environment.get('PATH', '').split(':')[0])
        if (root.parent != _bundle.INSTALL_ROOT or not re.fullmatch('[a-f0-9]{64}', root.name)
                or Path(source) != _launchd.GATEWAY_PLIST or document.get('UserName') != owner):
            raise InstallError('native-system-runtime-unqualified')
        node, entrypoint = root / 'node', root / 'runtime/openclaw.mjs'
        if GATEWAY_LAUNCHER.read_bytes() != _launcher_bytes(node, entrypoint):
            raise InstallError('native-system-launcher-mismatch')
        command = command[:3] + [str(node), str(entrypoint)] + command[4:]
    if (len(command) < 9 or command[:2] != ["/usr/bin/sandbox-exec", "-f"]
            or command[5:7] != ["gateway", "run"]
            or command.count("--port") != 1):
        raise InstallError("native-gateway-command-not-ods")
    try:
        profile, node, entrypoint = map(_path, command[2:5])
        port_index = command.index("--port")
        actual_port = int(command[port_index + 1])
    except (IndexError, TypeError, ValueError):
        raise InstallError("native-gateway-command-not-ods") from None
    if actual_port != port:
        raise InstallError("native-gateway-port-mismatch")
    if not node.is_file() or not os.access(node, os.X_OK) or not entrypoint.is_file() or not profile.is_file():
        raise InstallError("native-gateway-runtime-unavailable")
    for key in ("WorkingDirectory", "StandardOutPath", "StandardErrorPath"):
        try:
            _path(document.get(key))
        except (TypeError, ValueError):
            raise InstallError("native-gateway-paths-unavailable") from None
    return document, environment, command, profile, node, entrypoint


def _launcher_bytes(node, entrypoint):
    return ("#!/bin/sh\nexec /usr/bin/env -u NODE_OPTIONS -u NODE_PATH "
            + shlex.quote(str(node)) + " " + shlex.quote(str(entrypoint)) + " \"$@\"\n").encode()


def _promoted_gateway(source_document, environment, command, profile, node, entrypoint, owner, group):
    root_command = ["/usr/bin/sandbox-exec", "-f", str(PROFILE), str(GATEWAY_LAUNCHER), *command[5:]]
    return _launchd.native_gateway_document(
        owner=owner, group=group,
        program_arguments=["/usr/bin/env", "-i",
                           *(key + "=" + value for key, value in environment.items()), *root_command],
        working_directory=source_document["WorkingDirectory"], environment=source_document.get("EnvironmentVariables", {}),
        stdout_path=source_document["StandardOutPath"], stderr_path=source_document["StandardErrorPath"])


def _env_file(path):
    values = {}
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        raise InstallError("managed-environment-unavailable") from None
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = _env_values.parse_env_value(value)
    return values


def _policy_deployment(environment, document, node, entrypoint, owner, *, bundle_plan=None,
                       native_services=False):
    def runtime_path(key):
        try:
            return _path(environment[key]).resolve(strict=True)
        except (KeyError, TypeError, OSError, ValueError):
            raise InstallError('native-policy-runtime-path-required') from None

    home = runtime_path('HOME')
    for directory in (home, home / '.openclaw'):
        try:
            info = directory.lstat()
        except OSError:
            raise InstallError('native-admission-home-required') from None
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != owner.pw_uid
                or info.st_mode & 0o077):
            raise InstallError('native-admission-home-required')
    config_path = runtime_path('OPENCLAW_CONFIG_PATH')
    try:
        config = json.loads(config_path.read_bytes())
        plugins = config.get('plugins', {}).get('load', {}).get('paths', [])
        if type(plugins) is not list:
            raise ValueError()
        plugins = [_path(path).resolve(strict=True) for path in plugins]
    except (OSError, ValueError, TypeError, AttributeError):
        raise InstallError('native-policy-plugin-paths-unavailable') from None
    docker = environment.get('DOCKER_HOST', '')
    if not docker.startswith('unix:///'):
        raise InstallError('native-policy-docker-socket-required')
    socket = _path(docker[len('unix://'):]).resolve(strict=True)
    writable = [runtime_path(key) for key in ('HOME', 'OPENCLAW_STATE_DIR', 'TMPDIR', 'DOCKER_CONFIG')]
    writable.extend([config_path.parent, *(_path(document[key]).parent.resolve(strict=True)
                    for key in ('StandardOutPath', 'StandardErrorPath'))])
    protected = [ACCESS_PROGRAM_ROOT, Path('/private/etc/ods'), _launchd.ACCESS_STATE,
                 _launchd.ACCESS_PLIST, _launchd.GATEWAY_PLIST,
                 node.resolve(strict=True), entrypoint.parent.resolve(strict=True), *plugins]
    if bundle_plan:
        protected.append(Path(bundle_plan['destination']))
        writable.append(Path(bundle_plan['config_path']).parent)
    readable = [_path(document['WorkingDirectory']).resolve(strict=True)]
    sockets = [socket]
    if native_services:
        state = Path('/private/var/lib/pixel-ops-broker')
        layout = _native_services.helper('ops-state')
        writable.extend(state / name for name in layout.SUBMISSIONS)
        protected.extend(state / name for name in layout.PRIVATE + layout.PROJECTIONS + layout.STORAGE)
        protected.extend([Path('/usr/local/libexec/ods-pixel-services'), state / 'inventory.json'])
        readable.extend(state / name for name in (*layout.PROJECTIONS, 'inventory.json'))
        sockets.extend([Path('/private/var/lib/ods-pixel-manager/extension-manager.sock'),
                        Path('/private/var/lib/ods-pixel-artifact-promoter/promoter.sock')])
        if environment.get('PIXEL_HISTORY_DOCKER'):
            docker = _path(environment['PIXEL_HISTORY_DOCKER']).resolve(strict=True)
            readable.append(docker.parent)
            protected.append(docker)
    policies = {mode: _policy.render_policy(
        mode=mode, writable=writable, protected=protected,
        readable=readable, sockets=sockets,
        probe=Path('/private/var/lib/ods-pixel-access-probes') / str(owner.pw_uid))
        for mode in _policy.MODES}
    receipt = _policy.deployment_receipt('/private/etc/ods', policies)
    return receipt, policies


def _configuration_bytes(path, uid):
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as handle:
        info = os.fstat(handle.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_nlink != 1
                or info.st_mode & 0o077 or info.st_size > 8 * 1024 * 1024):
            raise InstallError('private-native-config-required')
        if sys.platform == 'darwin':
            sys.path.insert(0, str(HERE.parents[2] / 'bin'))
            from pixel_macos_custody import _require_no_acl
            _require_no_acl(handle.fileno())
        body = handle.read(8 * 1024 * 1024 + 1)
        after = os.fstat(handle.fileno())
        if (len(body) > 8 * 1024 * 1024
                or (info.st_size, info.st_mtime_ns, info.st_ctime_ns) !=
                   (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise InstallError('native-config-changed')
        return body


def _bundle_plan(source, digest, environment, owner, *, upgrade=None):
    if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
        raise InstallError('approved-bundle-digest-required')
    source = _path(source).resolve(strict=True)
    manifest, checksum = _bundle.verify(source, expected_digest=digest)
    if manifest['openclawVersion'] != '2026.6.33':
        raise InstallError('unqualified-openclaw-version')
    baseline = manifest
    current = None
    if upgrade is not None:
        current_digest, kind = upgrade
        if not isinstance(current_digest, str) or not re.fullmatch('[a-f0-9]{64}', current_digest):
            raise InstallError('approved-current-bundle-digest-required')
        current = _bundle.INSTALL_ROOT / current_digest
        _qualify_upgrade(kind, current, source, current_digest=current_digest, candidate_digest=digest)
        baseline, _ = _bundle.verify(current, expected_digest=current_digest)
    config_path = _path(environment.get('OPENCLAW_CONFIG_PATH'))
    body = _configuration_bytes(config_path, owner.pw_uid)
    config = json.loads(body)
    plugins = config.get('plugins', {})
    paths = plugins.get('load', {}).get('paths', [])
    if (type(paths) is not list or len(paths) != len(manifest['plugins'])
            or plugins.get('installs')):
        raise InstallError('bundle-plugin-mapping-unqualified')
    # Match the approved plugin bytes to the configured paths, not only the
    # number or package names. Plugin order and all entry settings are retained.
    for path, relative in zip(paths, manifest['plugins']):
        if current is not None and _path(path) != current / relative:
            raise InstallError('active-plugin-upgrade-source-mismatch')
        actual = _bundle.inventory(_path(path).resolve(strict=True))
        expected = {name[len(relative) + 1:]: record for name, record in baseline['entries'].items()
                    if name.startswith(relative + '/')}
        if actual != expected:
            raise InstallError('bundle-plugin-content-mismatch')
    destination = _bundle.INSTALL_ROOT / checksum
    if paths:
        plugins['load']['paths'] = [str(destination / path) for path in manifest['plugins']]
    config_target = RUNTIME_CONFIG_ROOT / str(owner.pw_uid) / 'openclaw.json'
    return {'source': str(source), 'digest': checksum, 'destination': str(destination),
            'source_config': str(config_path), 'source_config_bytes': body,
            'config_path': str(config_target),
            'config_bytes': (json.dumps(config, indent=2, ensure_ascii=True) + '\n').encode()}


def make_plan(*, install_dir, owner_name, source_plist, openclaw_bin, gateway_port,
              runtime_bundle=None, bundle_digest=None, access_port=18790, _upgrade=None,
              initial_install=False, _migration=None):
    if _migration is not None and (initial_install or _upgrade is not None or runtime_bundle is None):
        raise InstallError('joint-native-migration-plan-required')
    if type(initial_install) is not bool or initial_install and (_upgrade is not None or not runtime_bundle):
        raise InstallError('initial-install-requires-new-protected-runtime')
    owner = pwd.getpwnam(owner_name)
    if owner.pw_uid == 0:
        raise InstallError("native-gateway-owner-invalid")
    group = grp.getgrgid(owner.pw_gid).gr_name
    install_dir = _path(install_dir)
    openclaw_bin = _path(openclaw_bin)
    if not openclaw_bin.is_file() or not os.access(openclaw_bin, os.X_OK):
        raise InstallError("native-openclaw-validator-unavailable")
    source_plist = _path(source_plist)
    if initial_install and source_plist == Path(owner.pw_dir) / 'Library/LaunchAgents' / (_launchd.GATEWAY_LABEL + '.plist'):
        raise InstallError('initial-install-requires-unloaded-template')
    source_document, environment, command, profile, node, entrypoint = _source_gateway(
        source_plist, owner.pw_name, gateway_port)
    source_bytes = source_plist.read_bytes()
    if plistlib.loads(source_bytes) != source_document:
        raise InstallError('native-source-changed')
    if (runtime_bundle is None) != (bundle_digest is None):
        raise InstallError('runtime-bundle-and-digest-required')
    if _upgrade is not None and runtime_bundle is None:
        raise InstallError('upgrade-bundle-required')
    policy_environment = dict(environment)
    if _migration is not None:
        if (source_plist != _launchd.GATEWAY_PLIST
                or environment.get('OPENCLAW_CONFIG_PATH') != _migration['previousConfig']
                or _configuration_bytes(_migration['previousConfig'], owner.pw_uid) != _migration['previousConfigBytes']
                or environment.get('OPENCLAW_STATE_DIR') != _migration['preservation']['stateDir']):
            raise InstallError('native-migration-selection-changed')
        policy_environment['OPENCLAW_CONFIG_PATH'] = _migration['runtime']['source_config']
    bundle_plan = _bundle_plan(runtime_bundle, bundle_digest, policy_environment, owner,
                               upgrade=_upgrade) if runtime_bundle else None
    if _migration is not None and bundle_plan != _migration['runtime']:
        raise InstallError('native-migration-selection-changed')
    policy_receipt, policies = _policy_deployment(policy_environment, source_document, node, entrypoint, owner,
                                                 bundle_plan=bundle_plan)
    source_process = {'uid': owner.pw_uid, 'gid': owner.pw_gid, 'executable': str(node)}
    runtime_node, runtime_entrypoint = node, entrypoint
    gateway_environment = dict(environment)
    if _migration is not None:
        gateway_environment['PIXEL_OPS_STATE_DIR'] = '/private/var/lib/pixel-ops-broker'
    if bundle_plan:
        runtime_root = Path(bundle_plan['destination'])
        runtime_node, runtime_entrypoint = runtime_root / 'node', runtime_root / 'runtime/openclaw.mjs'
        gateway_environment['OPENCLAW_CONFIG_PATH'] = bundle_plan['config_path']
        gateway_environment['OPENCLAW_WRAPPER'] = str(GATEWAY_LAUNCHER)
        paths = environment.get('PATH', '/usr/bin:/bin').split(':')
        if any(not item.startswith('/') or '..' in Path(item).parts for item in paths):
            raise InstallError('native-runtime-search-path-unqualified')
        gateway_environment['PATH'] = ':'.join([str(runtime_root), *(p for p in paths if p != str(node.parent))])
    gateway = _promoted_gateway(source_document, gateway_environment, command, profile, runtime_node, runtime_entrypoint,
                                owner.pw_name, group)
    binding = _launchd.binding(gateway, owner=owner.pw_name, executable=str(runtime_node),
                               uid=owner.pw_uid, gid=owner.pw_gid)
    values = _env_file(install_dir / ".env")
    key = values.get("DASHBOARD_API_KEY", "")
    if (not 32 <= len(key) <= 4096 or any(ord(char) < 33 or ord(char) > 126 for char in key)
            or key == values.get("PIXEL_OPENWEBUI_KEY")):
        raise InstallError("distinct-dashboard-owner-credential-required")
    settings_dir = values.get("ODS_DATA_DIR") or str(install_dir / "data")
    settings_dir = _path(settings_dir)
    settings = _launchd.access_settings(
        install_dir=str(install_dir), owner=owner.pw_name,
        openclaw_bin=str(GATEWAY_LAUNCHER if bundle_plan else openclaw_bin),
        gateway_port=gateway_port, binding_value=binding, settings_data_dir=str(settings_dir), state=_launchd.ACCESS_STATE,
        edge_owner_key_sha256=hashlib.sha256(key.encode("ascii")).hexdigest())
    settings['gateway_policy'] = policy_receipt
    access = _launchd.access_daemon_document()
    if type(access_port) is not int or not 1 <= access_port <= 65535 or access_port == gateway_port:
        raise InstallError('invalid-native-access-port')
    relay = None
    if bundle_plan:
        relay = _launchd.access_relay_document(owner=owner.pw_name, group=group,
            node=str(runtime_node), port=access_port,
            log=str(Path(source_document['StandardOutPath']).parent / 'access-relay.log'))
    return {
        "owner": owner, "source": source_plist, "profile": profile, "node": node,
        "entrypoint": entrypoint, "key": key.encode("ascii"), "launcher": _launcher_bytes(runtime_node, runtime_entrypoint),
        "gateway": gateway, "gateway_binding": binding, "access": access, "access_settings": settings,
        "policies": policies,
        "source_bytes": source_bytes,
        "source_process": source_process, "runtime_bundle": bundle_plan,
        "access_relay": relay, "access_port": access_port,
        "source_environment": dict(environment),
        "initial_install": initial_install,
    }


def bind_initial_services(plan, *, bundle, digest, source_ref):
    if not plan.get('initial_install') or not plan.get('runtime_bundle'):
        raise InstallError('native-services-require-initial-install')
    _bind_services(plan, bundle=bundle, digest=digest, source_ref=source_ref)


def _bind_services(plan, *, bundle, digest, source_ref):
    _bundle.verify_service_binding(plan['runtime_bundle']['source'], digest)
    selection = {'bundle': str(_path(bundle).resolve(strict=True)), 'expected_digest': digest,
        'expected_ref': source_ref, 'expected_config_digest': hashlib.sha256(
            plan['runtime_bundle']['source_config_bytes']).hexdigest()}
    _native_services.helper('config').verified_services(selection['bundle'],
        **{key: value for key, value in selection.items() if key != 'bundle'})
    values = _env_file(Path(plan['access_settings']['install_dir']) / '.env')
    port = values.get('DASHBOARD_API_PORT', '3002')
    if not re.fullmatch('[0-9]{1,5}', port) or not 1 <= int(port) <= 65535:
        raise InstallError('invalid-native-manager-api-port')
    plan['native_services'] = selection
    plan['native_manager_port'] = int(port)
    environment, _ = _env_assignments(plan['gateway']['ProgramArguments'])
    environment['OPENCLAW_CONFIG_PATH'] = plan['runtime_bundle']['source_config']
    receipt, policies = _policy_deployment(environment,
        plistlib.loads(plan['source_bytes']), plan['node'], plan['entrypoint'], plan['owner'],
        bundle_plan=plan['runtime_bundle'], native_services=True)
    plan['access_settings']['gateway_policy'] = receipt
    plan['policies'] = policies


def _activate_initial_services(plan):
    if not plan.get('initial_install'):
        raise InstallError('native-services-require-initial-install')
    return _activate_new_services(plan)


def _activate_new_services(plan):
    if not (plan.get('initial_install') or plan.get('migration_qualification')):
        raise InstallError('native-services-require-joint-plan')
    sys.path.insert(0, str(HERE.parents[2] / 'bin'))
    from pixel_access_bridge import atomic_json, private_json
    journal = Path(_launchd.ACCESS_STATE) / 'service-installation.json'
    if os.path.lexists(journal):
        raise InstallError('native-service-journal-requires-review')
    selection = plan['native_services']
    recovery, witnesses, attempted = None, {}, []
    def checkpoint(value):
        nonlocal recovery, attempted
        if 'recovery' in value:
            recovery = value['recovery']
        if 'attempted' in value:
            attempted = list(value['attempted'])
        atomic_json(journal, {'schemaVersion': 1, 'owner': plan['owner'].pw_uid,
            'selection': selection, 'progress': value, 'requiresGatewayProof': True,
            'recovery': recovery, 'stopWitnesses': dict(witnesses), 'attempted': list(attempted)})
    def save_stop(name, value):
        witnesses[name] = value
        checkpoint({'phase': 'stopping-service', 'service': name, 'requiresRecovery': True})
    def load_stop(name):
        record = private_json(journal, 0, 2 * 1024 * 1024)
        if record.get('selection') != selection or record.get('owner') != plan['owner'].pw_uid:
            raise InstallError('native-service-recovery-selection-changed')
        return record['stopWitnesses'][name]
    checkpoint({'phase': 'starting'})
    try:
        document = json.loads(plan['runtime_bundle']['source_config_bytes'])
        agents = [item for item in document['agents']['list'] if item.get('id') == 'pixel']
        if len(agents) != 1:
            raise InstallError('native-service-workspace-required')
        services = _native_services.install_new(selection=selection, owner=plan['owner'].pw_name,
            environment=Path(plan['access_settings']['install_dir']) / '.env',
            workspace=_path(agents[0]['workspace']), port=plan['native_manager_port'], checkpoint=checkpoint,
            save_stop=save_stop, load_stop=load_stop)
    except BaseException:
        # Preserve the more detailed last checkpoint for recovery diagnostics.
        value = json.loads(journal.read_bytes())
        value['requiresRecovery'] = True
        atomic_json(journal, value)
        raise
    def rollback():
        _native_services.stop_new(services=services, attempted=['manager', 'promoter', 'operations'],
            checkpoint=checkpoint)
    return rollback


def _verify_new_services(plan):
    from pixel_access_bridge import private_json
    journal = Path(_launchd.ACCESS_STATE) / 'service-installation.json'
    record = private_json(journal, 0, 2 * 1024 * 1024)
    if (record.get('owner') != plan['owner'].pw_uid or record.get('selection') != plan['native_services']
            or record.get('progress', {}).get('phase') != 'services-active'):
        raise InstallError('native-services-not-active')
    recovery = record['recovery']
    adapters = _native_services.recovery_adapters(recovery, owner=plan['owner'].pw_name,
        save_stop=None, load_stop=None)
    checks = _native_services.readiness_checks(owner=plan['owner'].pw_name,
        identity=recovery['identity'], python=recovery['python'])
    for name, service in adapters.items():
        service.process_identity()
        if checks[name]() is not True:
            raise InstallError('native-services-not-ready')
    if private_json(journal, 0, 2 * 1024 * 1024) != record:
        raise InstallError('native-service-recovery-journal-changed')


def _restore_new_services(plan):
    """Stop the journaled new service set under the caller's deployment lock."""
    if sys.platform != 'darwin' or os.geteuid() != 0:
        raise InstallError('macos-root-install-required')
    sys.path.insert(0, str(HERE.parents[2] / 'bin'))
    from pixel_access_bridge import atomic_json, private_json
    journal = Path(_launchd.ACCESS_STATE) / 'service-installation.json'
    if not os.path.lexists(journal):
        return
    record = private_json(journal, 0, 2 * 1024 * 1024)
    if (type(record.get('schemaVersion')) is not int or record['schemaVersion'] != 1
            or record.get('owner') != plan['owner'].pw_uid
            or type(record.get('attempted')) is not list or type(record.get('stopWitnesses')) is not dict):
        raise InstallError('native-service-recovery-selection-changed')
    if record.get('selection') != plan['native_services']:
        # Activation refuses an existing journal before touching these services.
        # A healthy prior deployment must remain running during gateway rollback.
        if (not plan.get('migration_qualification') or record.get('requiresRecovery')
                or record.get('progress', {}).get('phase') != 'services-active'
                or record['attempted'] != ['manager', 'promoter', 'operations']):
            raise InstallError('native-service-recovery-selection-changed')
        _verify_new_services(dict(plan, native_services=record['selection']))
        if private_json(journal, 0, 2 * 1024 * 1024) != record:
            raise InstallError('native-service-recovery-journal-changed')
        return
    attempted = record['attempted']
    if attempted != ['manager', 'promoter', 'operations'][:len(attempted)]:
        raise InstallError('native-service-recovery-attempts-invalid')
    if not attempted:
        return
    def verify_record():
        if private_json(journal, 0, 2 * 1024 * 1024) != record:
            raise InstallError('native-service-recovery-journal-changed')
    def update(**changes):
        nonlocal record
        verify_record()
        candidate = dict(record, **changes)
        atomic_json(journal, candidate)
        record = candidate
    def checkpoint(value):
        update(progress=value, requiresRecovery=True)
    def save_stop(name, value):
        update(stopWitnesses=dict(record['stopWitnesses'], **{name: value}))
    def load_stop(name):
        verify_record()
        return record['stopWitnesses'][name]
    services = _native_services.recovery_adapters(record.get('recovery'),
        owner=plan['owner'].pw_name, save_stop=save_stop, load_stop=load_stop)
    verify_record()
    _native_services.stop_new(services=services, attempted=attempted, checkpoint=checkpoint)


def _qualify_upgrade(kind, current, candidate, *, current_digest, candidate_digest):
    qualifiers = {'stream-progress': _bundle.qualify_stream_progress_upgrade,
                  'workspace-root': _bundle.qualify_workspace_root_upgrade}
    if kind not in qualifiers:
        raise InstallError('unsupported-runtime-upgrade-kind')
    return qualifiers[kind](current, candidate, current_digest=current_digest,
                            candidate_digest=candidate_digest)


def qualify_migration_selection(*, owner_name, current_digest, gateway_port, candidate,
                                runtime_bundle, bundle_digest, services_bundle, services_digest, source_ref):
    """Read-only binding of migration artifacts to the selected active deployment.

    This is not an activation plan: live process custody, locking, policy
    transition, service replacement and rollback still require a joint executor.
    """
    if not isinstance(current_digest, str) or not re.fullmatch('[a-f0-9]{64}', current_digest):
        raise InstallError('approved-current-bundle-digest-required')
    owner = pwd.getpwnam(owner_name)
    if owner.pw_uid <= 0:
        raise InstallError('native-gateway-owner-invalid')
    document, environment, _, _, node, entrypoint = _source_gateway(
        _launchd.GATEWAY_PLIST, owner_name, gateway_port)
    current = _bundle.INSTALL_ROOT / current_digest
    config_parent = RUNTIME_CONFIG_ROOT / str(owner.pw_uid)
    previous_path = environment.get('OPENCLAW_CONFIG_PATH')
    if (document.get('UserName') != owner_name or node != current / 'node'
            or entrypoint != current / 'runtime/openclaw.mjs'
            or not _source_runtime_config(previous_path, config_parent, current_digest)):
        raise InstallError('active-runtime-migration-source-mismatch')
    _bundle.verify(current, expected_digest=current_digest)
    before = _configuration_bytes(previous_path, owner.pw_uid)
    candidate = _path(candidate).resolve(strict=True)
    candidate_path = candidate / 'openclaw.json'
    candidate_body = _configuration_bytes(candidate_path, owner.pw_uid)
    record_body = _configuration_bytes(candidate / 'migration.json', owner.pw_uid)
    migration_spec = importlib.util.spec_from_file_location('native_migration_selection',
        HERE / 'pixel-native-migration.py')
    migration = importlib.util.module_from_spec(migration_spec)
    migration_spec.loader.exec_module(migration)
    state = environment.get('OPENCLAW_STATE_DIR')
    if not isinstance(state, str) or not Path(state).is_absolute():
        raise InstallError('active-native-state-directory-required')
    preservation = migration.verify_state_preservation(json.loads(before), candidate_body,
        json.loads(record_body), state_dir=state)
    selected_runtime = _bundle_plan(runtime_bundle, bundle_digest,
        {'OPENCLAW_CONFIG_PATH': str(candidate_path)}, owner)
    _bundle.verify_service_binding(runtime_bundle, services_digest)
    if selected_runtime['source_config_bytes'] != candidate_body or bundle_digest == current_digest:
        raise InstallError('native-migration-candidate-changed')
    selected_services = {'bundle': str(_path(services_bundle).resolve(strict=True)),
        'expected_digest': services_digest, 'expected_ref': source_ref,
        'expected_config_digest': hashlib.sha256(candidate_body).hexdigest()}
    _native_services.helper('config').verified_services(selected_services['bundle'],
        expected_digest=services_digest, expected_ref=source_ref,
        expected_config_digest=selected_services['expected_config_digest'])
    if (_configuration_bytes(previous_path, owner.pw_uid) != before
            or _configuration_bytes(candidate / 'migration.json', owner.pw_uid) != record_body
            or _configuration_bytes(candidate_path, owner.pw_uid) != candidate_body):
        raise InstallError('native-migration-selection-changed')
    return {'currentDigest': current_digest, 'runtime': selected_runtime, 'services': selected_services,
        'preservation': preservation, 'previousConfig': previous_path, 'previousConfigBytes': before}


def _native_transport_environment(transport, owner):
    if (type(transport) is not dict or set(transport) != {'docker', 'project', 'image', 'user'}
            or any(type(value) is not str for value in transport.values())
            or not re.fullmatch('[a-z0-9][a-z0-9_-]{0,127}', transport['project'])
            or not re.fullmatch('sha256:[a-f0-9]{64}', transport['image'])
            or not re.fullmatch(str(owner.pw_uid) + r':[0-9]{1,10}', transport['user'])):
        raise InstallError('complete-native-docker-transport-required')
    docker = str(_path(transport['docker']))
    return {'PIXEL_HISTORY_TRANSPORT': 'docker-exec', 'PIXEL_HISTORY_DOCKER': docker,
        'PIXEL_PREVIEW_DOCKER': docker, 'PIXEL_HISTORY_PROJECT': transport['project'],
        'PIXEL_HISTORY_IMAGE': transport['image'], 'PIXEL_HISTORY_USER': transport['user']}


def make_migration_plan(*, install_dir, owner_name, openclaw_bin, gateway_port, candidate,
                        runtime_bundle, bundle_digest, current_digest, services_bundle,
                        services_digest, source_ref, access_port=18790, native_transport=None):
    """Build the joint deployment without mutating the active gateway or state.

    The activation executor must requalify this selection under its lock and
    bind service/environment recovery before publication. Neither the initial
    installer nor the narrow runtime-patch executor may apply this plan.
    """
    selection = qualify_migration_selection(owner_name=owner_name, current_digest=current_digest,
        gateway_port=gateway_port, candidate=candidate, runtime_bundle=runtime_bundle,
        bundle_digest=bundle_digest, services_bundle=services_bundle,
        services_digest=services_digest, source_ref=source_ref)
    plan = make_plan(install_dir=install_dir, owner_name=owner_name,
        source_plist=_launchd.GATEWAY_PLIST, openclaw_bin=openclaw_bin, gateway_port=gateway_port,
        runtime_bundle=runtime_bundle, bundle_digest=bundle_digest, access_port=access_port,
        _migration=selection)
    plan['migration_qualification'] = {
        'currentDigest': current_digest, 'candidateDigest': bundle_digest,
        'serviceDigest': services_digest, 'pixelSourceRef': source_ref,
        'preservation': selection['preservation'], 'candidate': str(_path(candidate).resolve(strict=True)),
        'transport': native_transport}
    plan['migration_source_config_bytes'] = selection['previousConfigBytes']
    plan['upgrade_kind'] = 'native-migration'
    plan['upgrade_qualification'] = {'currentDigest': current_digest, 'candidateDigest': bundle_digest,
        'kind': 'native-migration'}
    if native_transport is not None:
        environment, command = _env_assignments(plan['gateway']['ProgramArguments'])
        environment.update(_native_transport_environment(native_transport, plan['owner']))
        plan['gateway']['ProgramArguments'] = ['/usr/bin/env', '-i',
            *(key + '=' + value for key, value in environment.items()), *command]
    _version_upgrade_config(plan)
    _bind_services(plan, bundle=services_bundle, digest=services_digest, source_ref=source_ref)
    if qualify_migration_selection(owner_name=owner_name, current_digest=current_digest,
            gateway_port=gateway_port, candidate=candidate, runtime_bundle=runtime_bundle,
            bundle_digest=bundle_digest, services_bundle=services_bundle,
            services_digest=services_digest, source_ref=source_ref) != selection:
        raise InstallError('native-migration-selection-changed')
    return plan


def make_upgrade_plan(*, install_dir, owner_name, openclaw_bin, gateway_port,
                      runtime_bundle, bundle_digest, current_digest, access_port=18790,
                      upgrade_kind='stream-progress'):
    """Plan against the active system definition, never the retired user job.

    Read-only: loaded-process custody, transaction locking, publication and
    rollback must still be enforced by the activation path.
    """
    if not isinstance(current_digest, str) or not re.fullmatch('[a-f0-9]{64}', current_digest):
        raise InstallError('approved-current-bundle-digest-required')
    source = _launchd.GATEWAY_PLIST
    document, environment, _, _, node, entrypoint = _source_gateway(source, owner_name, gateway_port)
    current = _bundle.INSTALL_ROOT / current_digest
    owner = pwd.getpwnam(owner_name)
    config_parent = RUNTIME_CONFIG_ROOT / str(owner.pw_uid)
    if (document.get('UserName') != owner_name
            or node != current / 'node' or entrypoint != current / 'runtime/openclaw.mjs'
            or not _source_runtime_config(environment.get('OPENCLAW_CONFIG_PATH'), config_parent, current_digest)):
        raise InstallError('active-runtime-upgrade-source-mismatch')
    qualification = _qualify_upgrade(upgrade_kind, current, runtime_bundle,
        current_digest=current_digest, candidate_digest=bundle_digest)
    plan = make_plan(install_dir=install_dir, owner_name=owner_name, source_plist=source,
        openclaw_bin=openclaw_bin, gateway_port=gateway_port, runtime_bundle=runtime_bundle,
        bundle_digest=bundle_digest, access_port=access_port,
        _upgrade=(current_digest, upgrade_kind))
    if plistlib.loads(plan['source_bytes']) != document:
        raise InstallError('active-runtime-upgrade-source-changed')
    _version_upgrade_config(plan)
    plan['upgrade_qualification'] = qualification
    plan['upgrade_kind'] = upgrade_kind
    return plan


def _source_runtime_config(path, parent, digest):
    if not isinstance(path, (str, Path)) or Path(path).parent != parent:
        return False
    name = Path(path).name
    return name == 'openclaw.json' or bool(re.fullmatch('openclaw-' + re.escape(digest)
        + r'(?:-[a-f0-9]{64})?\.json', name))


def _candidate_config_names(plan):
    selection = plan['runtime_bundle']
    legacy = 'openclaw-' + selection['digest'] + '.json'
    if not plan.get('migration_qualification'):
        return (legacy,)
    revision = hashlib.sha256(selection['config_bytes']).hexdigest()
    # Keep legacy names readable for recovery of already-journaled attempts.
    return ('openclaw-' + selection['digest'] + '-' + revision + '.json', legacy)


def _version_upgrade_config(plan):
    selection, owner = plan['runtime_bundle'], plan['owner']
    old_path = selection['config_path']
    new_path = str(RUNTIME_CONFIG_ROOT / str(owner.pw_uid) / _candidate_config_names(plan)[0])
    if new_path == selection['source_config']:
        raise InstallError('native-upgrade-config-already-selected')
    arguments = plan['gateway']['ProgramArguments']
    assignment = 'OPENCLAW_CONFIG_PATH=' + old_path
    if arguments.count(assignment) != 1:
        raise InstallError('native-upgrade-config-binding-invalid')
    arguments[arguments.index(assignment)] = 'OPENCLAW_CONFIG_PATH=' + new_path
    selection['config_path'] = new_path
    binding = _launchd.binding(plan['gateway'], owner=owner.pw_name,
        executable=plan['gateway_binding']['executable'], uid=owner.pw_uid, gid=owner.pw_gid)
    plan['gateway_binding'] = binding
    plan['access_settings']['gateway_binding'] = binding


def _check_directory(path, *, private=False):
    path = Path(path)
    for item in (path, *path.parents):
        info = item.lstat()
        forbidden = 0o077 if item == path and private else 0o022
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
                or info.st_mode & forbidden):
            raise InstallError("access-directory-custody-unavailable")


def _destination(path):
    path = Path(path)
    # /etc is an OS-provided alias on Darwin; inspect its canonical parent
    # explicitly without resolving arbitrary symlinks in deployment paths.
    if path.parts[:2] == ("/", "etc"):
        path = Path("/private/etc").joinpath(*path.parts[2:])
    return path


def _check_existing(path, data, *, mode, uid=0, gid=0):
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != data:
            raise InstallError("existing-ods-file-drift")
        info = path.stat()
        if (info.st_uid != uid or info.st_gid != gid or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != mode):
            raise InstallError("existing-ods-file-unsafe")
        return True
    return False


def _preflight_directory(path, *, private=False):
    path = Path(path)
    # Inspect the nearest existing ancestor before mkdir can follow a symlink
    # or create anything underneath a writable deployment directory.
    ancestor = path
    while not ancestor.exists() and not ancestor.is_symlink():
        ancestor = ancestor.parent
    _check_directory(ancestor, private=private and ancestor == path)


def _preflight_file(path, data, *, mode, uid=0, gid=0):
    path = _destination(path)
    _preflight_directory(path.parent)
    _check_existing(path, data, mode=mode, uid=uid, gid=gid)


def _write_exact(path, data, *, mode, uid=0, gid=0):
    path = _destination(path)
    _preflight_file(path, data, mode=mode, uid=uid, gid=gid)
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    _check_directory(path.parent)
    if _check_existing(path, data, mode=mode, uid=uid, gid=gid):
        return
    fd, temporary = tempfile.mkstemp(prefix=".ods-pixel-install-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        os.fchown(fd, uid, gid)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _deployment_files(source, plan):
    """Snapshot every input before the first deployment write or job change."""
    files = []
    def add(path, data, mode=0o644, uid=0, gid=0):
        files.append((_destination(path), data, dict(mode=mode, uid=uid, gid=gid)))

    target = ACCESS_PROGRAM_ROOT
    host = Path(source) / "extensions/services/pixel-agent/host"
    bin_root = Path(source) / "bin"
    for name in HOST_FILES:
        origin = host / name if (host / name).exists() else bin_root / name
        add(target / name, origin.read_bytes())
    for name in SETTINGS_FILES:
        add(target / "pixel_settings" / name, (bin_root / "pixel_settings" / name).read_bytes())
    for name in PROVIDER_FILES:
        add(target / "pixel_provider" / name, (bin_root / "pixel_provider" / name).read_bytes())
    add(PROFILE, plan['policies']['sandboxed'])
    for mode, record in plan['access_settings']['gateway_policy']['profiles'].items():
        add(record['path'], plan['policies'][mode])
    add(GATEWAY_LAUNCHER, plan["launcher"], mode=0o755)
    add(_launchd.GATEWAY_PLIST, _launchd.encode(plan["gateway"]))
    add(ACCESS_FILES["key"], plan["key"], mode=0o600,
        uid=plan["owner"].pw_uid, gid=plan["owner"].pw_gid)
    add(ACCESS_FILES["config"], json.dumps(plan["access_settings"], sort_keys=True, separators=(",", ":")).encode(), mode=0o600)
    add(ACCESS_FILES["plist"], _launchd.encode(plan["access"]))
    if plan.get('access_relay'):
        for name in ('access_mode_http.mjs', 'access_mode_relay.mjs'):
            add(target / name, (host / name).read_bytes())
        # No owner workspace, Docker socket or writable runtime is granted to
        # this transport. It only reads the fixed key and contacts the root UDS.
        profile = ('(version 1)\n(deny default)\n(import "system.sb")\n'
                   '(allow process-exec signal sysctl-read network*)\n'
                   '(allow file-read-metadata)\n'
                   '(allow file-read* (subpath "/System") (subpath "/usr") '
                   '(subpath "/bin") (subpath "/private/etc") '
                   '(literal "/private/var/run/ods-pixel-access/control.sock"))\n'
                   '(allow file-read* file-write* (literal "/dev/null") '
                   '(literal "/dev/urandom") (literal "/dev/random"))\n')
        add(_launchd.RELAY_PROFILE, profile.encode())
        add(_launchd.RELAY_PLIST, _launchd.encode(plan['access_relay']))
    if len({path for path, _, _ in files}) != len(files):
        raise InstallError("duplicate-deployment-destination")
    return files


def _preflight_jobs():
    for target in (_launchd.GATEWAY_TARGET, _launchd.ACCESS_TARGET, _launchd.RELAY_TARGET):
        result = subprocess.run(["/bin/launchctl", "print", target],
                                capture_output=True, timeout=10, check=False)
        if result.returncode == 0:
            raise InstallError("existing-system-daemon-requires-upgrade")
        if result.returncode != 113:
            raise InstallError("system-daemon-absence-unconfirmed")


def _command(arguments, *, timeout=20):
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise InstallError('host-command-failed', returncode=result.returncode)
    return result.stdout


def _job_disabled(target):
    domain, label = target.rsplit('/', 1)
    raw = _command(['/bin/launchctl', 'print-disabled', domain])
    lines = raw.strip().splitlines()
    if not lines or lines[0].strip() != 'disabled services = {' or lines[-1].strip() != '}':
        raise InstallError('launchd-disabled-state-unavailable')
    entries = {}
    for line in lines[1:-1]:
        match = re.fullmatch(r'\s*"([^"\n]+)" => (enabled|disabled)\s*', line)
        if not match or match[1] in entries:
            raise InstallError('launchd-disabled-state-unavailable')
        entries[match[1]] = match[2] == 'disabled'
    return entries.get(label, False)


def _activation_services(plan):
    # These are the same adapters used by runtime transitions. The installer
    # is already executing the explicitly selected checkout, not chat input.
    sys.path.insert(0, str(HERE.parents[2] / 'bin'))
    from pixel_gateway_service import LaunchdGatewayService
    from pixel_macos_custody import protected_bytes, verify_loaded_launchd_definition

    def service(target, path, expected, *, source=False, process=None):
        def verify_definition():
            if source:
                info = path.lstat()
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != plan['owner'].pw_uid
                        or info.st_nlink != 1 or info.st_mode & 0o022
                        or path.read_bytes() != expected):
                    raise InstallError('native-source-changed')
            elif protected_bytes(_destination(path)) != expected:
                raise InstallError('native-deployment-changed')

        def verify():
            verify_definition()
            verify_loaded_launchd_definition(_command(['/bin/launchctl', 'print', target]),
                                             target, path, plistlib.loads(expected))
        return LaunchdGatewayService(_command, InstallError, target, verify,
            plist=path, verify_definition=verify_definition, process=process)

    process = plan['gateway_binding']['process']
    old = service(f"gui/{plan['owner'].pw_uid}/{_launchd.GATEWAY_LABEL}",
                  plan['source'], plan['source_bytes'], source=True, process=plan['source_process'])
    gateway = service(_launchd.GATEWAY_TARGET, _launchd.GATEWAY_PLIST,
                      _launchd.encode(plan['gateway']), process=process)
    access = service(_launchd.ACCESS_TARGET, _launchd.ACCESS_PLIST, _launchd.encode(plan['access']))
    if plan.get('access_relay'):
        relay = service(_launchd.RELAY_TARGET, _launchd.RELAY_PLIST,
                        _launchd.encode(plan['access_relay']), process=process)
        return old, gateway, access, relay
    return old, gateway, access


def _upgrade_policy_mode(plan):
    sys.path.insert(0, str(HERE.parents[2] / 'bin'))
    from pixel_macos_custody import protected_bytes
    settings = json.loads(protected_bytes(_destination(ACCESS_FILES['config'])))
    owner = plan['owner']
    expected = _launchd.binding(plistlib.loads(plan['source_bytes']), owner=owner.pw_name,
        executable=plan['source_process']['executable'], uid=owner.pw_uid, gid=owner.pw_gid)
    if (settings.get('owner') != owner.pw_name
            or settings.get('install_dir') != plan['access_settings']['install_dir']
            or settings.get('gateway_binding') != expected
            or settings.get('gateway_process') != expected['process']):
        raise InstallError('runtime-upgrade-controller-binding-changed')
    return _policy.policy_state(settings.get('gateway_policy'))['activeMode']


def _upgrade_file_snapshots(plan, source):
    """Capture existing root files; only new immutable policy files may be added."""
    sys.path.insert(0, str(HERE.parents[2] / 'bin'))
    from pixel_macos_custody import protected_bytes
    if not plan.get('upgrade_qualification'):
        raise InstallError('runtime-upgrade-plan-required')
    mode = _upgrade_policy_mode(plan)
    existing, additions = [], []
    policy_paths = {_destination(record['path'])
                    for record in plan['access_settings']['gateway_policy']['profiles'].values()}
    for path, after, attributes in _deployment_files(source, plan):
        if path == _destination(PROFILE):
            after = plan['policies'][mode]
        if attributes.get('uid', 0) != 0:
            # The owner credential is not rotated by a runtime update.
            if path != _destination(ACCESS_FILES['key']):
                raise InstallError('runtime-upgrade-owner-file-unexpected')
            _check_existing(path, after, **attributes)
            if not os.path.lexists(path):
                raise InstallError('runtime-upgrade-owner-key-missing')
            continue
        _preflight_directory(path.parent)
        if not os.path.lexists(path):
            if path not in policy_paths:
                raise InstallError('runtime-upgrade-existing-file-missing')
            additions.append((path, after, attributes))
            continue
        before = protected_bytes(path, limit=8 * 1024 * 1024)
        _check_existing(path, before, **attributes)
        existing.append(dict(path=str(path), before=before, after=after,
                             mode=attributes['mode'], gid=attributes.get('gid', 0)))
    baseline_path = _launchd.ACCESS_STATE / 'service-baseline.json'
    if os.path.lexists(baseline_path):
        before = protected_bytes(baseline_path, limit=8192)
        _check_existing(baseline_path, before, mode=0o600, gid=0)
        existing.append(dict(path=str(baseline_path), before=before,
                             after=_upgrade_service_baseline(before, existing), mode=0o600, gid=0))
    return existing, additions


def _upgrade_service_baseline(body, records):
    """Rebind the sandbox baseline only from the approved deployment pair."""
    from pixel_gateway_service import launchd_definition_digest
    by_path = {item['path']: item for item in records}
    plist_record = by_path[str(_destination(_launchd.GATEWAY_PLIST))]
    config_record = by_path[str(_destination(ACCESS_FILES['config']))]
    def boundary(version):
        settings = json.loads(config_record[version])
        policy = _policy.validate_receipt(settings['gateway_policy'])
        return {'schemaVersion': 1, 'platform': 'macos-launchd',
                'target': _launchd.GATEWAY_TARGET, 'plist': str(_launchd.GATEWAY_PLIST),
                'definition': launchd_definition_digest(plistlib.loads(plist_record[version]), InstallError),
                'policy': dict(policy, activeMode='sandboxed')}
    value = json.loads(body)
    if (type(value) is not dict or set(value) != {'boundary'}
            or type(value['boundary']) is not str or json.loads(value['boundary']) != boundary('before')):
        raise InstallError('runtime-upgrade-service-baseline-drift')
    return (json.dumps({'boundary': json.dumps(boundary('after'), sort_keys=True,
                         separators=(',', ':'))}, sort_keys=True) + '\n').encode()


def _upgrade_services(plan, records):
    """Bind both versions to protected snapshots and durable stop witnesses."""
    sys.path.insert(0, str(HERE.parents[2] / 'bin'))
    from pixel_gateway_service import LaunchdGatewayService, launchd_definition_digest
    from pixel_macos_custody import protected_bytes, verify_loaded_launchd_definition
    from pixel_access_bridge import atomic_json, private_json
    selection = plan.get('upgrade_qualification')
    if not selection or selection['candidateDigest'] != plan['runtime_bundle']['digest']:
        raise InstallError('runtime-upgrade-plan-required')
    by_path = {str(item['path']): item for item in records}
    if len(by_path) != len(records):
        raise InstallError('runtime-upgrade-duplicate-file')
    definitions = {
        'gateway': (_launchd.GATEWAY_TARGET, _launchd.GATEWAY_PLIST),
        'access': (_launchd.ACCESS_TARGET, _launchd.ACCESS_PLIST),
        'relay': (_launchd.RELAY_TARGET, _launchd.RELAY_PLIST),
    }

    def create(role, version, target, path):
        try:
            expected = by_path[str(_destination(path))]['before' if version == 'previous' else 'after']
            document = plistlib.loads(expected)
        except (KeyError, ValueError, TypeError):
            raise InstallError('runtime-upgrade-service-snapshot-invalid') from None
        if type(document) is not dict or document.get('Label') != target.rsplit('/', 1)[1]:
            raise InstallError('runtime-upgrade-service-label-mismatch')
        # Stop receipts need a supported identity for every old/new service.
        # Qualify snapshots before staging or acquiring the admission hold.
        launchd_definition_digest(document, InstallError)
        def verify_definition():
            if protected_bytes(_destination(path)) != expected:
                raise InstallError('native-deployment-changed')
        def verify():
            verify_definition()
            verify_loaded_launchd_definition(_command(['/bin/launchctl', 'print', target]),
                                             target, path, document)
        process = None
        if role in ('gateway', 'relay'):
            process = plan['source_process'] if version == 'previous' else plan['gateway_binding']['process']
        witness = _runtime_upgrade_stop_witness(selection['candidateDigest'], version, role)
        def save(value):
            atomic_json(witness, value)
        def load():
            return private_json(witness, 0, 1024 * 1024)
        return LaunchdGatewayService(_command, InstallError, target, verify,
            plist=path, verify_definition=verify_definition, process=process,
            save_stop=save, load_stop=load)

    return tuple({role: create(role, version, *definition) for role, definition in definitions.items()}
                 for version in ('previous', 'candidate'))


def _runtime_upgrade_stop_witness(candidate_digest, version, role):
    if (not isinstance(candidate_digest, str)
            or not re.fullmatch('[a-f0-9]{64}', candidate_digest)
            or version not in ('previous', 'candidate')
            or role not in ('gateway', 'access', 'relay')):
        raise InstallError('runtime-upgrade-stop-witness-invalid')
    return Path(_launchd.ACCESS_STATE) / (
        'runtime-upgrade-stop-' + candidate_digest + '-' + version + '-' + role + '.json')


def _clear_candidate_stop_witnesses(plan):
    """Remove only the candidate receipts after the transaction is terminal.

    These files are transaction-local state, not deployment inputs. A failed
    replacement must not leave a valid-looking receipt that can block a later
    retry or be mistaken for proof that the candidate ever bootstrapped.
    """
    selection = plan.get('runtime_bundle')
    candidate_digest = selection.get('digest') if isinstance(selection, dict) else None
    paths = [_runtime_upgrade_stop_witness(candidate_digest, 'candidate', role)
             for role in ('gateway', 'access', 'relay')]
    parent = Path(_launchd.ACCESS_STATE)
    from pixel_macos_custody import protected_directory, _require_no_acl
    with protected_directory(parent) as directory_fd:
        if stat.S_IMODE(os.fstat(directory_fd).st_mode) != 0o700:
            raise InstallError('runtime-upgrade-stop-state-unsafe')
        pending = []
        for path in paths:
            try:
                descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                     dir_fd=directory_fd)
            except FileNotFoundError:
                continue
            try:
                info = os.fstat(descriptor)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                        or stat.S_IMODE(info.st_mode) != 0o600):
                    raise InstallError('runtime-upgrade-stop-witness-unsafe')
                _require_no_acl(descriptor)
                pending.append(path)
            finally:
                os.close(descriptor)
        try:
            for path in pending:
                os.unlink(path.name, dir_fd=directory_fd)
        finally:
            if pending:
                os.fsync(directory_fd)


def _upgrade_service_identity(service):
    if service.target != _launchd.ACCESS_TARGET:
        return service.process_identity()
    # The root Python coordinator is bound by its protected/loaded plist,
    # not the owner Node executable specification used by gateway and relay.
    from pixel_macos_process import process_birth
    pid = service.pid(require_running=True)
    birth = process_birth(pid)
    if service.pid(require_running=True) != pid or process_birth(pid) != birth:
        raise InstallError('native-access-process-changed')
    return (pid, *birth)


def _observe_upgrade_service(previous, candidate):
    if previous.target != candidate.target:
        raise InstallError('runtime-upgrade-target-changed')
    try:
        _command(['/bin/launchctl', 'print', previous.target])
    except InstallError as error:
        if error.code == 'host-command-failed' and error.returncode == 113:
            return 'absent'
        raise
    for version, service in (('previous', previous), ('candidate', candidate)):
        try:
            service.verify_definition()
        except InstallError as error:
            if error.code == 'native-deployment-changed':
                continue
            raise
        # A matching on-disk plist alone cannot qualify a loaded process.
        _upgrade_service_identity(service)
        return version
    raise InstallError('runtime-upgrade-service-unqualified')


def _start_upgrade_service(service):
    service.verify_definition()
    if _job_disabled(service.target):
        raise InstallError('runtime-upgrade-disabled-service')
    # Never kickstart a job whose loaded definition still refers to the old
    # runtime. Replacement requires a confirmed unload followed by bootstrap.
    try:
        _command(['/bin/launchctl', 'print', service.target])
    except InstallError as error:
        if error.code != 'host-command-failed' or error.returncode != 113:
            raise
    else:
        raise InstallError('runtime-upgrade-service-still-loaded')
    if service.save_stop is None:
        raise InstallError('native-stop-witness-store-required')
    service.save_stop(None)
    service._stopping_tree = None
    _command(['/bin/launchctl', 'bootstrap', service.target.rsplit('/', 1)[0], str(service.plist)])
    _wait_running(service)
    _wait_upgrade_identity(service)


def _wait_upgrade_identity(service, *, timeout=30):
    # launchd publishes a PID while env/sandbox/the launcher still precede
    # Node. Only a fully verified final identity admits the started service.
    deadline = time.monotonic() + timeout
    while True:
        try:
            return _upgrade_service_identity(service)
        except InstallError as error:
            if error.code not in ('gateway-process-executable-mismatch',
                                  'gateway-process-unavailable', 'gateway-process-changed',
                                  'runtime-unavailable-or-busy'):
                raise
            if time.monotonic() >= deadline:
                raise InstallError('native-service-identity-timeout') from error
        time.sleep(0.25)


def _assert_upgrade_absent(previous, candidate):
    """Require a stop witness for the version currently installed on disk.

    An old version's witness cannot prove a later instance stopped. Ambiguous
    crash windows stay held for recovery rather than falling back to that proof.
    """
    if previous.target != candidate.target:
        raise InstallError('runtime-upgrade-target-changed')
    for service in (candidate, previous):
        try:
            service.verify_definition()
        except InstallError as error:
            if error.code == 'native-deployment-changed':
                continue
            raise
        service.assert_stopped()
        return
    raise InstallError('runtime-upgrade-service-unqualified')


def _stop_upgrade_service(service):
    _stop_loaded(service)
    # Missing job alone is not sufficient: assert_stopped also checks the
    # persisted process tree and boot identity from the stop witness.
    service.assert_stopped()


def _ready_gateway(service, port, *, timeout=60):
    http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            identity = service.process_identity(timeout=3)
            with http.open(f'http://127.0.0.1:{port}/healthz', timeout=3) as response:
                if response.status == 200 and service.process_identity(timeout=3) == identity:
                    return
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        time.sleep(0.25)
    raise InstallError('native-gateway-readiness-failed')


def _stop_loaded(service):
    try:
        service.pid(require_running=True)
    except InstallError as error:
        if error.code == 'host-command-failed' and error.returncode == 113:
            return
        raise
    service.stop()
    service.assert_stopped()


def _wait_running(service, *, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if service.pid(require_running=False) > 0:
                return
        except InstallError as error:
            # launchd may be between spawn states immediately after bootstrap.
            # Observe the same job; custody/malformed output errors still fail.
            if error.code != 'runtime-unavailable-or-busy':
                raise
        time.sleep(0.25)
    raise InstallError('native-service-startup-unconfirmed')


def _access_response_ready(value, *, upgrade_guard=False):
    if type(value) is not dict:
        return False
    if not upgrade_guard:
        return value.get('available') is True
    # This proves only that the controller is serving its upgrade guard.
    # Gateway health, process custody and policy need independent proof.
    return (value.get('available') is False
            and value.get('surface') == 'darwin'
            and value.get('pending') is True
            and value.get('runtime_verified') is False
            and value.get('effective_mode') == 'unknown'
            and value.get('reason') == 'runtime-upgrade-recovery-required')


def _ready_access(service, *, upgrade_guard=False):
    pid = service.pid(require_running=True)
    address = _launchd.ACCESS_SOCKET
    deadline = time.monotonic() + 10
    while not address.exists() and time.monotonic() < deadline:
        time.sleep(0.25)
    info = address.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o660:
        raise InstallError('native-access-socket-unavailable')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(20)
        connection.connect(str(address))
        connection.sendall(b'{"operation":"status"}\n')
        with connection.makefile('rb') as stream:
            raw = stream.readline(65537)
    if len(raw) > 65536 or not raw.endswith(b'\n'):
        raise InstallError('native-access-readiness-failed')
    value = json.loads(raw)
    if (type(value) is not dict or value.get('status') != 200
            or not _access_response_ready(value.get('body'), upgrade_guard=upgrade_guard)
            or service.pid(require_running=True) != pid):
        raise InstallError('native-access-readiness-failed')


def _migration_phase(plan, journal, value):
    sys.path.insert(0, str(HERE.parents[2] / 'bin'))
    from pixel_access_bridge import atomic_json
    atomic_json(journal, {'schemaVersion': 1, 'phase': value,
                         'source': str(plan['source']), 'owner': plan['owner'].pw_uid,
                         'operation': 'initial-install' if plan.get('initial_install') else 'migration'})


def _require_initial_absence(plan, source):
    """Prove initial-state assumptions, never infer a migration from absence."""
    source.verify_definition()
    agent = Path(plan['owner'].pw_dir) / 'Library/LaunchAgents' / (_launchd.GATEWAY_LABEL + '.plist')
    if os.path.lexists(agent):
        raise InstallError('initial-install-existing-user-definition')
    try:
        _command(['/bin/launchctl', 'print', source.target])
    except InstallError as error:
        if error.code != 'host-command-failed' or error.returncode != 113:
            raise
    else:
        raise InstallError('initial-install-existing-user-service')
    sockets = []
    try:
        for port in (plan['access_settings']['gateway_port'], plan['access_port']):
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sockets.append(listener)
            listener.bind(('127.0.0.1', port))
    except OSError:
        raise InstallError('initial-install-port-unavailable') from None
    finally:
        for listener in sockets:
            listener.close()


def _migration_edge_context(plan):
    owner = pwd.getpwnam(plan['owner'].pw_name)
    if (owner.pw_uid <= 0 or owner.pw_uid != plan['owner'].pw_uid
            or owner.pw_gid != plan['owner'].pw_gid or os.geteuid() != 0):
        raise InstallError('native-migration-owner-changed')
    source = plan['source_environment']
    environment = {key: source[key] for key in ('HOME', 'PATH', 'DOCKER_HOST', 'DOCKER_CONFIG') if key in source}
    if (not environment.get('PATH') or not environment.get('DOCKER_HOST', '').startswith('unix:///')):
        raise InstallError('native-migration-docker-environment-required')
    return dict(cwd='/', env=environment, user=owner.pw_uid, group=owner.pw_gid,
                extra_groups=os.getgrouplist(owner.pw_name, owner.pw_gid))


def _migration_edge_request(plan, container, operation=None, binding=None):
    sys.path.insert(0, str(HERE.parents[2] / 'bin'))
    from pixel_access_bridge import _edge_container_request
    return _edge_container_request(container, '/v1/transition' + ('/' + operation if operation else ''),
        plan['key'].decode('ascii'), binding, timeout=20, process_context=_migration_edge_context(plan))


def _edge_hold_journal(plan):
    selection = plan.get('upgrade_qualification')
    if selection is None:
        return Path(_launchd.ACCESS_STATE) / 'installation-edge.json'
    digest = selection.get('candidateDigest')
    if (not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest)
            or digest != plan.get('runtime_bundle', {}).get('digest')):
        raise InstallError('runtime-upgrade-plan-required')
    return Path(_launchd.ACCESS_STATE) / ('runtime-upgrade-edge-' + digest + '.json')


def _acquire_migration_hold(plan):
    path = _edge_hold_journal(plan)
    context = _migration_edge_context(plan)
    result = subprocess.run(['docker', 'inspect', 'ods-pixel-edge', '--format',
        '{{.Id}} {{.State.Running}} {{index .Config.Labels "com.docker.compose.service"}}'],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=10, **context)
    identity = result.stdout.strip().split()
    if (len(identity) != 3 or not re.fullmatch('[a-f0-9]{64}', identity[0])
            or identity[1:] != ['true', 'pixel-edge']):
        raise InstallError('native-migration-edge-unqualified')
    before = _migration_edge_request(plan, identity[0])
    if (before.get('capability') != 'available' or before.get('phase') != 'idle'
            or before.get('streams') != 0 or before.get('admission_blocked') is not False
            or not re.fullmatch('[a-f0-9]{64}', str(before.get('revision', '')))):
        raise InstallError('native-migration-edge-not-idle')
    binding = dict(token=os.urandom(32).hex(), revision=before['revision'])
    # Persist before acquire: a lost reply is not permission to switch or to
    # guess another token. The operator can reconcile this exact hold.
    sys.path.insert(0, str(HERE.parents[2] / 'bin'))
    from pixel_access_bridge import atomic_json
    record = dict(schemaVersion=1, container=identity[0], binding=binding, phase='acquiring')
    if os.path.lexists(path):
        raise InstallError('native-migration-edge-journal-requires-review')
    atomic_json(path, record)
    held = _migration_edge_request(plan, identity[0], 'acquire', binding)
    if (held.get('capability') != 'available' or held.get('phase') != 'held'
            or held.get('streams') != 0 or held.get('admission_blocked') is not True
            or held.get('revision') != binding['revision']):
        raise InstallError('native-migration-edge-hold-unconfirmed')
    record.update(phase='held', status=held)
    atomic_json(path, record)
    return record


def _resume_migration_hold(plan):
    """Reacquire only the persisted container/token/revision under caller lock."""
    from pixel_access_bridge import private_json, atomic_json
    path = _edge_hold_journal(plan)
    record = private_json(path, 0, 65536)
    if (not isinstance(record, dict) or record.get('schemaVersion') != 1
            or record.get('phase') not in ('acquiring', 'held')
            or set(record) != ({'schemaVersion', 'container', 'binding', 'phase'}
                               | ({'status'} if record.get('phase') == 'held' else set()))
            or not re.fullmatch('[a-f0-9]{64}', str(record.get('container', '')))
            or not isinstance(record.get('binding'), dict)
            or set(record['binding']) != {'token', 'revision'}
            or any(not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value)
                   for value in record['binding'].values())):
        raise InstallError('native-migration-edge-journal-invalid')
    context = _migration_edge_context(plan)
    result = subprocess.run(['docker', 'inspect', record['container'], '--format',
        '{{.Id}} {{.State.Running}} {{index .Config.Labels "com.docker.compose.service"}}'],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=10, **context)
    if result.stdout.strip().split() != [record['container'], 'true', 'pixel-edge']:
        raise InstallError('native-migration-edge-unqualified')
    if private_json(path, 0, 65536) != record:
        raise InstallError('native-migration-edge-journal-changed')
    held = _migration_edge_request(plan, record['container'], 'acquire', record['binding'])
    if (held.get('capability') != 'available' or held.get('phase') != 'held'
            or held.get('streams') != 0 or held.get('admission_blocked') is not True
            or held.get('revision') != record['binding']['revision']):
        raise InstallError('native-migration-edge-hold-unconfirmed')
    if private_json(path, 0, 65536) != record:
        raise InstallError('native-migration-edge-journal-changed')
    record = dict(record, phase='held', status=held)
    atomic_json(path, record)
    return record


def _finish_migration_hold(plan, record):
    path = _edge_hold_journal(plan)
    from pixel_access_bridge import atomic_json, private_json
    if private_json(path, 0, 65536) != record or record.get('phase') not in ('held', 'releasing'):
        raise InstallError('native-migration-edge-journal-changed')
    if record['phase'] == 'held':
        current = _migration_edge_request(plan, record['container'], 'acquire', record['binding'])
        if current != record['status']:
            raise InstallError('native-migration-edge-hold-changed')
        if private_json(path, 0, 65536) != record:
            raise InstallError('native-migration-edge-journal-changed')
        record = dict(record, phase='releasing')
        atomic_json(path, record)
    # A lost release reply must retry release with the original token. Acquire
    # would conflict with the new revision after a successful release.
    released = _migration_edge_request(plan, record['container'], 'release', record['binding'])
    if (released.get('capability') != 'available' or released.get('phase') != 'idle'
            or released.get('streams') != 0 or released.get('admission_blocked') is not False):
        raise InstallError('native-migration-edge-release-unconfirmed')
    if private_json(path, 0, 65536) != record:
        raise InstallError('native-migration-edge-journal-changed')
    atomic_json(path, dict(record, phase='released'))


def _activate_with_admission(plan, services, journal):
    hold = _acquire_migration_hold(plan)
    rollback_services = None
    try:
        if plan.get('native_services'):
            rollback_services = _activate_initial_services(plan)
        _activate(plan, services, journal)
    except BaseException:
        if rollback_services is not None:
            try:
                rollback_services()
            except BaseException:
                _migration_phase(plan, journal, 'recovery-required')
                raise InstallError('native-service-stop-unconfirmed') from None
        # Only the activation engine's verified restoration may reopen the
        # old service. An incomplete rollback retains the admission hold.
        value = json.loads(Path(journal).read_bytes())
        if value.get('phase') == 'restored':
            _finish_migration_hold(plan, hold)
        raise
    _finish_migration_hold(plan, hold)


def _ready_access_relay(service, plan, *, upgrade_guard=False):
    identity = service.process_identity()
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(f"http://127.0.0.1:{plan['access_port']}/v1/access-mode",
                                    headers={'Authorization': 'Bearer ' + plan['key'].decode('ascii')})
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if service.process_identity() != identity:
            raise InstallError('native-access-relay-process-changed')
        try:
            with client.open(request, timeout=min(22, max(1, deadline - time.monotonic()))) as response:
                raw = response.read(65537)
                if response.status != 200 or len(raw) > 65536:
                    raise InstallError('native-access-relay-readiness-failed')
            value = json.loads(raw)
            if (not _access_response_ready(value, upgrade_guard=upgrade_guard)
                    or service.process_identity() != identity):
                raise InstallError('native-access-relay-readiness-failed')
            return
        except urllib.error.HTTPError:
            raise InstallError('native-access-relay-readiness-failed') from None
        except (urllib.error.URLError, TimeoutError):
            time.sleep(1)
    raise InstallError('native-access-relay-readiness-failed')


def _activate(plan, services, journal):
    old, gateway, access, *relays = services
    candidates = (gateway, access, *relays)
    def phase(value):
        _migration_phase(plan, journal, value)

    initial = plan.get('initial_install', False)
    phase('checking-initial-state' if initial else 'disabling-source')
    try:
        if plan.get('runtime_bundle'):
            _verify_bundle_selection(plan)
        if initial:
            _require_initial_absence(plan, old)
        else:
            _command(['/bin/launchctl', 'disable', old.target])
            phase('stopping-source')
            old.stop()
            old.assert_stopped()
        for service in candidates:
            phase('starting-' + ('gateway' if service is gateway else 'access' if service is access else 'access-relay'))
            _command(['/bin/launchctl', 'enable', service.target])
            _command(['/bin/launchctl', 'bootstrap', service.target.rsplit('/', 1)[0], str(service.plist)])
            _wait_running(service)
        _ready_gateway(gateway, plan['access_settings']['gateway_port'])
        _ready_access(access)
        for service in relays:
            _ready_access_relay(service, plan)
        if plan.get('native_services'):
            _verify_new_services(plan)
        phase('active')
    except BaseException:
        phase('rolling-back')
        try:
            # Prevent either newly staged daemon from returning at reboot,
            # even if stopping a process fails and requires manual recovery.
            for service in reversed(candidates):
                _command(['/bin/launchctl', 'disable', service.target])
            for service in reversed(candidates):
                _stop_loaded(service)
            if initial:
                # There was no previous gateway to restore. Keep admission held
                # and all staged jobs disabled until explicit operator recovery.
                phase('initial-stopped')
            else:
                # Source bytes must still be the approved image. Never bootstrap
                # a newly edited user plist as part of automatic restoration.
                old.verify_definition()
                bootstrap_old = False
                try:
                    old.pid(require_running=True)
                except InstallError as error:
                    if error.code != 'host-command-failed' or error.returncode != 113:
                        raise
                    old.assert_stopped()
                    bootstrap_old = True
                _command(['/bin/launchctl', 'enable', old.target])
                if bootstrap_old:
                    _command(['/bin/launchctl', 'bootstrap', old.target.rsplit('/', 1)[0], str(old.plist)])
                _ready_gateway(old, plan['access_settings']['gateway_port'])
                phase('restored')
        except BaseException:
            phase('recovery-required')
            raise InstallError('native-migration-recovery-required') from None
        raise


def _previous_upgrade_guard(records):
    """Recognize reviewed guard methods without executing archived Python."""
    path = str(_destination(ACCESS_PROGRAM_ROOT / 'pixel_access_bridge.py'))
    bodies = [item['before'] for item in records if item['path'] == path]
    if len(bodies) != 1:
        raise InstallError('runtime-upgrade-previous-guard-unqualified')
    def methods(body):
        tree = ast.parse(body)
        classes = [node for node in tree.body if isinstance(node, ast.ClassDef)
                   and node.name == 'LaunchdAccessBridge']
        if len(classes) != 1:
            raise ValueError()
        found = {}
        for node in classes[0].body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in ('locked', 'status'):
                if node.name in found:
                    raise ValueError()
                found[node.name] = ast.dump(node, include_attributes=False)
        return found
    try:
        previous = methods(bodies[0])
        if not previous:
            return False
        current = methods((HERE.parents[2] / 'bin/pixel_access_bridge.py').read_bytes())
        if set(current) == {'locked', 'status'} and previous == current:
            return True
    except (SyntaxError, ValueError, TypeError):
        pass
    raise InstallError('runtime-upgrade-previous-guard-unqualified')


def _prepare_candidate_stop_witnesses(previous, candidate, records):
    """Carry proven absence across plist replacement, before any bootstrap.

    _start_upgrade_service durably clears this witness before bootstrap. A
    reboot also invalidates it through the boot identity in assert_stopped.
    """
    from pixel_gateway_service import launchd_definition_digest
    by_path = {item['path']: item for item in records}
    pending = []
    for role in ('gateway', 'access', 'relay'):
        old, new = previous[role], candidate[role]
        if old.target != new.target or old.plist != new.plist or new.save_stop is None or new.load_stop is None:
            raise InstallError('runtime-upgrade-stop-binding-changed')
        old.verify_definition()
        old.assert_stopped()
        try:
            new.load_stop()
        except FileNotFoundError:
            pass
        else:
            raise InstallError('runtime-upgrade-candidate-witness-exists')
        witness = old.load_stop()
        if witness['definition'] != old.definition() or witness['boot'] != old._boot_identity():
            raise InstallError('runtime-upgrade-stop-binding-changed')
        record = by_path[str(_destination(new.plist))]
        if launchd_definition_digest(plistlib.loads(record['before']), InstallError) != witness['definition']:
            raise InstallError('runtime-upgrade-stop-binding-changed')
        pending.append((new, dict(witness, definition=launchd_definition_digest(
            plistlib.loads(record['after']), InstallError))))
    # Validate every service before publishing any derived absence receipt.
    for service, witness in pending:
        service.save_stop(witness)


def _relocate_upgrade_receipt(plan, previous, candidate, *, restore=False):
    """Run owner-state mutation only after proving every installed job stopped."""
    for role in ('gateway', 'access', 'relay'):
        _assert_upgrade_absent(previous[role], candidate[role])
    hold = _resume_migration_hold(plan)
    selection = plan['runtime_bundle']
    source, target = selection['source_config'], selection['config_path']
    source_body = selection['source_config_bytes']
    if plan.get('migration_qualification'):
        source = plan['source_environment']['OPENCLAW_CONFIG_PATH']
        source_body = plan['migration_source_config_bytes']
    source_hash = hashlib.sha256(source_body).hexdigest()
    target_hash = hashlib.sha256(selection['config_bytes']).hexdigest()
    if restore:
        source, target, source_hash, target_hash = target, source, target_hash, source_hash
    owner = pwd.getpwnam(plan['owner'].pw_name)
    if owner.pw_uid <= 0 or (owner.pw_uid, owner.pw_gid) != (plan['owner'].pw_uid, plan['owner'].pw_gid):
        raise InstallError('native-migration-owner-changed')
    env = {key: plan['source_environment'][key] for key in ('HOME', 'XDG_STATE_HOME')
           if key in plan['source_environment']}
    env['PATH'] = '/usr/bin:/bin'
    result = subprocess.run(['/usr/bin/python3', '-I', str(HERE / 'pixel-runtime-receipt.py')],
        input=json.dumps(dict(source=source, target=target, sourceHash=source_hash, targetHash=target_hash)) + '\n',
        text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30, check=False,
        cwd='/', env=env, user=owner.pw_uid, group=owner.pw_gid,
        extra_groups=os.getgrouplist(owner.pw_name, owner.pw_gid))
    if result.returncode != 0 or len(result.stdout) > 1024:
        raise InstallError('runtime-upgrade-owner-relocation-failed')
    response = json.loads(result.stdout)
    if type(response) is not dict or set(response) != {'relocated'} or type(response['relocated']) is not bool:
        raise InstallError('runtime-upgrade-owner-relocation-failed')
    for role in ('gateway', 'access', 'relay'):
        _assert_upgrade_absent(previous[role], candidate[role])
    if _resume_migration_hold(plan) != hold:
        raise InstallError('runtime-upgrade-edge-hold-changed')


def _restore_upgrade_owner(plan, previous, candidate):
    if plan.get('migration_qualification'):
        _restore_new_services(plan)
    _relocate_upgrade_receipt(plan, previous, candidate, restore=True)


def _activate_upgrade(plan, records, journal, *, previous_guard=False):
    """Activate staged files under the caller's controller lock and journal.

    Publication and interrupted-run recovery remain the caller's responsibility.
    Admission stays closed if restoration or final live verification fails.
    """
    sys.path.insert(0, str(HERE.parents[2] / 'bin'))
    from pixel_macos_custody import protected_bytes, replace_protected_bytes
    previous, candidate = _upgrade_services(plan, records)
    mode = _upgrade_policy_mode(plan)

    def verify_files(version):
        for item in records:
            if protected_bytes(item['path'], limit=8 * 1024 * 1024) != item[version]:
                raise InstallError('runtime-upgrade-file-drift')

    def verify():
        verify_files('before')
        _verify_bundle_selection(plan)

    def ready(services):
        is_candidate = services is candidate
        if is_candidate and plan.get('migration_qualification'):
            _verify_new_services(plan)
        verify_files('after' if is_candidate else 'before')
        for service in services.values():
            _upgrade_service_identity(service)
        settings = json.loads(protected_bytes(_destination(ACCESS_FILES['config'])))
        if _policy.policy_state(settings['gateway_policy'])['activeMode'] != mode:
            raise InstallError('runtime-upgrade-policy-mode-changed')
        _ready_gateway(services['gateway'], plan['access_settings']['gateway_port'])
        guard = is_candidate or previous_guard
        _ready_access(services['access'], upgrade_guard=guard)
        _ready_access_relay(services['relay'], plan, upgrade_guard=guard)

    def read(path):
        return protected_bytes(path, limit=8 * 1024 * 1024)

    def replace_files():
        _prepare_candidate_stop_witnesses(previous, candidate, records)
        _upgrade.replace_deployment_files(records, read=read, replace=replace_protected_bytes)

    def migrate_owner():
        _relocate_upgrade_receipt(plan, previous, candidate)
        if plan.get('migration_qualification'):
            _activate_new_services(plan)

    def restore_owner():
        _restore_upgrade_owner(plan, previous, candidate)

    # Validate before acquiring admission: a failure here cannot strand a hold.
    verify()
    hold = _acquire_migration_hold(plan)
    try:
        _upgrade.activate(previous=previous, candidate=candidate, phase=journal.phase,
            verify=verify,
            replace_files=replace_files,
            migrate_owner=migrate_owner, restore_owner=restore_owner,
            restore_files=lambda: _upgrade.restore_deployment_files(
                records, read=read, replace=replace_protected_bytes),
            start=_start_upgrade_service, stop=_stop_upgrade_service, ready=ready)
    except BaseException:
        if journal.value['phase'] == 'restored':
            journal.finish(lambda: ready(previous))
            _ready_access(previous['access'])
            _ready_access_relay(previous['relay'], plan)
            _clear_candidate_stop_witnesses(plan)
            _finish_migration_hold(plan, hold)
        raise
    journal.finish(lambda: ready(candidate))
    # Once the guard is removed, both endpoints must report normal availability
    # before reopening requests. Failure retains the durable edge hold.
    _ready_access(candidate['access'])
    _ready_access_relay(candidate['relay'], plan)
    _clear_candidate_stop_witnesses(plan)
    _finish_migration_hold(plan, hold)


def _recover_unstarted_upgrade(plan, records, journal, previous, candidate, *, verify_snapshots, previous_guard):
    """Finish a staging-only interruption without stopping healthy old jobs."""
    from pixel_macos_custody import protected_bytes
    hold_path = _edge_hold_journal(plan)
    if os.path.lexists(hold_path):
        return False
    if journal.value['phase'] not in ('prepared', 'restored'):
        raise InstallError('runtime-upgrade-missing-edge-hold')

    def verify():
        verify_snapshots()
        _verify_recovery_runtime(plan)
        if os.path.lexists(hold_path):
            raise InstallError('runtime-upgrade-edge-hold-changed')
        for item in records:
            if protected_bytes(item['path'], limit=8 * 1024 * 1024) != item['before']:
                raise InstallError('runtime-upgrade-rollback-file-drift')
        for role, service in previous.items():
            if _observe_upgrade_service(service, candidate[role]) != 'previous':
                raise InstallError('runtime-upgrade-original-service-required')
        _upgrade_policy_mode(plan)
        _ready_gateway(previous['gateway'], plan['access_settings']['gateway_port'])
        _ready_access(previous['access'], upgrade_guard=previous_guard)
        _ready_access_relay(previous['relay'], plan, upgrade_guard=previous_guard)

    verify()
    journal.phase('restored')
    _clear_candidate_stop_witnesses(plan)
    journal.finish(verify)
    _ready_access(previous['access'])
    _ready_access_relay(previous['relay'], plan)
    return True


def _recover_upgrade(plan, records, journal, *, verify_snapshots, previous_guard=False):
    """Restore an approved recovery journal while caller holds controller lock.

    The caller must load/authorize records with recovery_upgrade and supply
    immutable bundle/config verification. Unknown stop witnesses stay blocked.
    """
    from pixel_macos_custody import protected_bytes, replace_protected_bytes
    previous, candidate = _upgrade_services(plan, records)
    if _recover_unstarted_upgrade(plan, records, journal, previous, candidate,
            verify_snapshots=verify_snapshots, previous_guard=previous_guard):
        return

    def read(path):
        return protected_bytes(path, limit=8 * 1024 * 1024)

    def verify():
        verify_snapshots()
        _verify_recovery_runtime(plan)
        for item in records:
            if read(item['path']) not in (item['before'], item['after']):
                raise InstallError('runtime-upgrade-rollback-file-drift')

    def ready(services):
        if services is not previous:
            raise InstallError('runtime-upgrade-recovery-target-invalid')
        verify_snapshots()
        _verify_recovery_runtime(plan)
        for item in records:
            if read(item['path']) != item['before']:
                raise InstallError('runtime-upgrade-rollback-file-drift')
        for service in services.values():
            _upgrade_service_identity(service)
        # Now the old controller configuration and profile have been restored;
        # binding and active policy must qualify against the original plan.
        _upgrade_policy_mode(plan)
        _ready_gateway(previous['gateway'], plan['access_settings']['gateway_port'])
        _ready_access(previous['access'], upgrade_guard=previous_guard)
        _ready_access_relay(previous['relay'], plan, upgrade_guard=previous_guard)

    verify()
    hold = _resume_migration_hold(plan)
    _upgrade.recover_previous(previous=previous, candidate=candidate,
        phase=journal.phase, verify_snapshots=verify,
        observe=_observe_upgrade_service, assert_absent=_assert_upgrade_absent,
        restore_owner=lambda: _restore_upgrade_owner(plan, previous, candidate),
        restore_files=lambda: _upgrade.restore_deployment_files(
            records, read=read, replace=replace_protected_bytes),
        start=_start_upgrade_service, stop=_stop_upgrade_service, ready=ready)
    _clear_candidate_stop_witnesses(plan)
    journal.finish(lambda: ready(previous))
    _ready_access(previous['access'])
    _ready_access_relay(previous['relay'], plan)
    _finish_migration_hold(plan, hold)


def _verify_bundle_selection(plan):
    selection = plan['runtime_bundle']
    if _configuration_bytes(selection['source_config'], plan['owner'].pw_uid) != selection['source_config_bytes']:
        raise InstallError('native-config-changed')
    sys.path.insert(0, str(HERE.parents[2] / 'bin'))
    from pixel_macos_custody import protected_tree_metadata
    protected_tree_metadata(selection['destination'])
    _bundle.verify(selection['destination'], expected_digest=selection['digest'])
    if _configuration_bytes(selection['config_path'], plan['owner'].pw_uid) != selection['config_bytes']:
        raise InstallError('native-candidate-config-changed')


def _runtime_config(plan, *, write=False):
    """Stage a new owner-writable config below a root-controlled parent.

    The old config is never edited. Existing candidate files must be exact;
    symlink or configuration drift never authorizes overwrite/adoption.
    """
    selection, owner = plan['runtime_bundle'], plan['owner']
    path = Path(selection['config_path'])
    names = (_candidate_config_names(plan) if plan.get('upgrade_qualification') or plan.get('migration_qualification')
        else ('openclaw.json',))
    if path not in {RUNTIME_CONFIG_ROOT / str(owner.pw_uid) / name for name in names}:
        raise InstallError('native-candidate-config-path-invalid')
    name = path.name
    if _configuration_bytes(selection['source_config'], owner.pw_uid) != selection['source_config_bytes']:
        raise InstallError('native-config-changed')
    sys.path.insert(0, str(HERE.parents[2] / 'bin'))
    from pixel_macos_custody import protected_directory, _require_no_acl
    if not write and not os.path.lexists(RUNTIME_CONFIG_ROOT):
        ancestor = RUNTIME_CONFIG_ROOT.parent
        while not os.path.lexists(ancestor):
            ancestor = ancestor.parent
        with protected_directory(ancestor):
            return
    with protected_directory(RUNTIME_CONFIG_ROOT, create=write) as root_fd:
        directory = str(owner.pw_uid)
        if write:
            try:
                os.mkdir(directory, 0o700, dir_fd=root_fd)
                created = True
            except FileExistsError:
                created = False
        else:
            try:
                os.stat(directory, dir_fd=root_fd, follow_symlinks=False)
            except FileNotFoundError:
                return
            created = False
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        try:
            info = os.fstat(fd)
            _require_no_acl(fd)
            if created:
                if info.st_uid != 0 or info.st_mode & 0o077:
                    raise InstallError('native-candidate-config-directory-unsafe')
                os.fchown(fd, owner.pw_uid, owner.pw_gid)
                os.fsync(root_fd)
            elif info.st_uid != owner.pw_uid or info.st_gid != owner.pw_gid or info.st_mode & 0o077:
                raise InstallError('native-candidate-config-directory-unsafe')
            if os.path.lexists(path):
                if _configuration_bytes(path, owner.pw_uid) != selection['config_bytes']:
                    raise InstallError('native-candidate-config-changed')
                return
            if not write:
                return
            temporary = '.config-' + os.urandom(16).hex()
            output = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=fd)
            try:
                with os.fdopen(output, 'wb') as handle:
                    handle.write(selection['config_bytes'])
                    handle.flush()
                    os.fchown(handle.fileno(), owner.pw_uid, owner.pw_gid)
                    os.fsync(handle.fileno())
                # link is an exclusive publication, never an overwrite.
                os.link(temporary, path.name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
            finally:
                os.unlink(temporary, dir_fd=fd)
            os.fsync(fd)
            if _configuration_bytes(path, owner.pw_uid) != selection['config_bytes']:
                raise InstallError('native-candidate-config-changed')
        finally:
            os.close(fd)


RECOVERY_PLAN_FIELDS = ('source_environment', 'source_process', 'gateway_binding',
                        'access_settings', 'access_port', 'upgrade_qualification', 'upgrade_kind')
MIGRATION_RECOVERY_FIELDS = ('migration_qualification', 'migration_source_config_bytes',
                            'native_services', 'native_manager_port')


def _recovery_context(plan):
    """JSON-only recovery inputs; executable Python objects are never serialized."""
    owner = plan['owner']
    selection = dict(plan['runtime_bundle'])
    for field in ('source_config_bytes', 'config_bytes'):
        selection[field] = base64.b64encode(selection[field]).decode('ascii')
    value = {field: plan[field] for field in RECOVERY_PLAN_FIELDS}
    value.update(schemaVersion=1, owner={'name': owner.pw_name, 'uid': owner.pw_uid,
        'gid': owner.pw_gid, 'home': owner.pw_dir}, runtime_bundle=selection,
        source_bytes=base64.b64encode(plan['source_bytes']).decode('ascii'),
        key=base64.b64encode(plan['key']).decode('ascii'))
    if plan.get('migration_qualification'):
        value.update({field: plan[field] for field in MIGRATION_RECOVERY_FIELDS})
        value['migration_source_config_bytes'] = base64.b64encode(plan['migration_source_config_bytes']).decode('ascii')
    return value


def _load_recovery_context(path, *, current_digest, candidate_digest, owner_name):
    from pixel_access_bridge import private_json
    value = private_json(path, 0, _upgrade.JOURNAL_LIMIT)
    plan = _decode_recovery_context(value, current_digest=current_digest,
                                    candidate_digest=candidate_digest, owner_name=owner_name)
    if private_json(path, 0, _upgrade.JOURNAL_LIMIT) != value:
        raise InstallError('runtime-upgrade-context-changed')
    return plan


def _decode_recovery_context(value, *, current_digest, candidate_digest, owner_name):
    expected = set(RECOVERY_PLAN_FIELDS) | {'schemaVersion', 'owner', 'runtime_bundle', 'source_bytes', 'key'}
    migrating = isinstance(value, dict) and value.get('upgrade_kind') == 'native-migration'
    if migrating:
        expected.update(MIGRATION_RECOVERY_FIELDS)
    if type(value) is not dict or set(value) != expected or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1:
        raise InstallError('runtime-upgrade-context-invalid')
    owner = pwd.getpwnam(owner_name)
    if owner.pw_uid == 0 or value['owner'] != {'name': owner.pw_name, 'uid': owner.pw_uid,
                                            'gid': owner.pw_gid, 'home': owner.pw_dir}:
        raise InstallError('runtime-upgrade-context-owner-changed')
    qualification = value['upgrade_qualification']
    if (type(qualification) is not dict or type(value['runtime_bundle']) is not dict
            or any(not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest)
                   for digest in (current_digest, candidate_digest))
            or current_digest == candidate_digest
            or qualification.get('currentDigest') != current_digest
            or qualification.get('candidateDigest') != candidate_digest
            or value['runtime_bundle'].get('digest') != candidate_digest
            or value['upgrade_kind'] not in ('workspace-root', 'stream-progress', 'native-migration')):
        raise InstallError('runtime-upgrade-context-selection-changed')
    plan = {field: value[field] for field in RECOVERY_PLAN_FIELDS}
    plan['owner'] = owner
    plan['runtime_bundle'] = dict(value['runtime_bundle'])
    try:
        for field in ('source_bytes', 'key'):
            plan[field] = base64.b64decode(value[field], validate=True)
        for field in ('source_config_bytes', 'config_bytes'):
            plan['runtime_bundle'][field] = base64.b64decode(value['runtime_bundle'][field], validate=True)
        if migrating:
            plan.update({field: value[field] for field in MIGRATION_RECOVERY_FIELDS})
            plan['migration_source_config_bytes'] = base64.b64decode(value['migration_source_config_bytes'], validate=True)
    except (ValueError, TypeError):
        raise InstallError('runtime-upgrade-context-encoding-invalid') from None
    if migrating:
        _validate_migration_recovery_context(plan)
    return plan


def _validate_migration_recovery_context(plan):
    """Validate joint selection in protected recovery data without staging reads."""
    try:
        selection = plan['migration_qualification']
        fields = {'currentDigest', 'candidateDigest', 'serviceDigest', 'pixelSourceRef', 'preservation', 'candidate', 'transport'}
        if type(selection) is not dict or set(selection) != fields:
            raise ValueError()
        if selection['transport'] is not None:
            _native_transport_environment(selection['transport'], plan['owner'])
        if plan['upgrade_qualification'] != {'currentDigest': selection['currentDigest'],
                'candidateDigest': selection['candidateDigest'], 'kind': 'native-migration'}:
            raise ValueError()
        if (not re.fullmatch('[a-f0-9]{64}', selection['serviceDigest'])
                or not re.fullmatch('[a-f0-9]{40}', selection['pixelSourceRef'])
                or type(plan['native_manager_port']) is not int or not 1 <= plan['native_manager_port'] <= 65535):
            raise ValueError()
        runtime = plan['runtime_bundle']
        if _path(runtime['source_config']) != _path(selection['candidate']) / 'openclaw.json':
            raise ValueError()
        services = plan['native_services']
        if (type(services) is not dict or set(services) != {
                'bundle', 'expected_digest', 'expected_ref', 'expected_config_digest'}
                or services['expected_digest'] != selection['serviceDigest']
                or services['expected_ref'] != selection['pixelSourceRef']
                or services['expected_config_digest'] != hashlib.sha256(runtime['source_config_bytes']).hexdigest()):
            raise ValueError()
        _path(services['bundle'])
        preservation = selection['preservation']
        if type(preservation) is not dict or set(preservation) != {
                'stateDir', 'workspace', 'previousConfigSha256', 'candidateConfigSha256'}:
            raise ValueError()
        spec = importlib.util.spec_from_file_location('native_recovery_preservation', HERE / 'pixel-native-migration.py')
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        migration.verify_state_preservation(json.loads(plan['migration_source_config_bytes']),
            runtime['source_config_bytes'], dict(preservation, schemaVersion=1, kind='legacy-native',
                agentId='pixel', requiresJointActivation=True), state_dir=plan['source_environment']['OPENCLAW_STATE_DIR'])
    except (KeyError, TypeError, ValueError):
        raise InstallError('native-migration-recovery-context-invalid') from None


def _retirement_plan(snapshots, *, current_digest, candidate_digest, owner_name):
    """Rebuild authority from protected history, including after partial cleanup."""
    archive = _upgrade.validate_retirement_snapshots(snapshots,
        current_digest=current_digest, candidate_digest=candidate_digest)
    value = json.loads(snapshots[archive])
    required, optional = _recovery_file_contract()
    contract = {**required, **optional}
    files = value.get('files')
    if (type(files) is not list or any(type(item) is not dict
            or type(item.get('path')) is not str for item in files)):
        raise InstallError('runtime-upgrade-recovery-file-contract-invalid')
    selected = {item['path'] for item in files}
    if not set(required).issubset(selected) or not selected.issubset(contract):
        raise InstallError('runtime-upgrade-recovery-file-contract-invalid')
    records = _upgrade.decode_recovery(value, current_digest=current_digest,
        candidate_digest=candidate_digest, allowed_paths=selected)
    if any((item['mode'], item['gid']) != contract[item['path']] for item in records):
        raise InstallError('runtime-upgrade-recovery-file-contract-invalid')
    plan = _decode_recovery_context(json.loads(snapshots[
        'runtime-upgrade-context-' + candidate_digest + '.json']),
        current_digest=current_digest, candidate_digest=candidate_digest, owner_name=owner_name)
    _verify_recovery_bindings(plan, records)
    _verify_recovery_runtime(plan)
    return plan, records


def _verify_retirement_live(plan, records):
    """Check the restored deployment without relying on removed journal files."""
    from pixel_macos_custody import protected_bytes
    if any(os.path.lexists(_launchd.ACCESS_STATE / name) for name in
           ('transition.json', 'policy-activation.json', 'runtime-upgrade.json')):
        raise InstallError('runtime-upgrade-pending-recovery')
    for item in records:
        if protected_bytes(item['path'], limit=8 * 1024 * 1024) != item['before']:
            raise InstallError('runtime-upgrade-rollback-file-drift')
    previous, _ = _upgrade_services(plan, records)
    identities = {role: _upgrade_service_identity(service) for role, service in previous.items()}
    _upgrade_policy_mode(plan)
    _ready_gateway(previous['gateway'], plan['access_settings']['gateway_port'])
    _ready_access(previous['access'])
    _ready_access_relay(previous['relay'], plan)
    if any(_upgrade_service_identity(service) != identities[role] for role, service in previous.items()):
        raise InstallError('runtime-upgrade-restored-process-changed')


def _archive_verified_retirement(bridge, snapshots, *, current_digest, candidate_digest, owner_name):
    """Archive a restored attempt using caller-selected protected snapshots."""
    from pixel_access_bridge import LaunchdAccessBridge
    if sys.platform != 'darwin' or os.geteuid() != 0:
        raise InstallError('macos-root-install-required')
    if not isinstance(bridge, LaunchdAccessBridge) or bridge.state != _launchd.ACCESS_STATE:
        raise InstallError('runtime-upgrade-controller-state-changed')
    # Normal locking permits both a terminal attempt and partial retirement;
    # the live check below independently rejects other pending transitions.
    with bridge.locked():
        def verify():
            plan, records = _retirement_plan(snapshots, current_digest=current_digest,
                candidate_digest=candidate_digest, owner_name=owner_name)
            _verify_retirement_live(plan, records)
        return _upgrade.archive_restored_attempt(bridge.state, current_digest=current_digest,
            candidate_digest=candidate_digest, snapshots=snapshots, verify_restored=verify)


def _recovery_file_contract():
    """Fixed native deployment destinations, independent of journal contents."""
    paths = [ACCESS_PROGRAM_ROOT / name for name in HOST_FILES]
    paths += [ACCESS_PROGRAM_ROOT / 'pixel_settings' / name for name in SETTINGS_FILES]
    paths += [ACCESS_PROGRAM_ROOT / 'pixel_provider' / name for name in PROVIDER_FILES]
    paths += [ACCESS_PROGRAM_ROOT / name for name in ('access_mode_http.mjs', 'access_mode_relay.mjs')]
    paths += [PROFILE, _launchd.GATEWAY_PLIST, _launchd.ACCESS_PLIST,
              _launchd.RELAY_PROFILE, _launchd.RELAY_PLIST]
    required = {str(_destination(path)): (0o644, 0) for path in paths}
    required[str(_destination(GATEWAY_LAUNCHER))] = (0o755, 0)
    required[str(_destination(ACCESS_FILES['config']))] = (0o600, 0)
    optional = {str(_destination(PROFILE.with_name('pixel-gateway.' + mode + '.sb'))): (0o644, 0)
                for mode in _policy.MODES}
    optional[str(_launchd.ACCESS_STATE / 'service-baseline.json')] = (0o600, 0)
    return required, optional


def _load_upgrade_recovery(*, current_digest, candidate_digest, owner_name, completed=False):
    """Read recovery authority under the caller's controller lock; no mutation."""
    from pixel_access_bridge import private_json
    # Validate before using the externally selected digest in a filename.
    if any(type(value) is not str or not re.fullmatch('[a-f0-9]{64}', value)
           for value in (current_digest, candidate_digest)) or current_digest == candidate_digest:
        raise InstallError('runtime-upgrade-context-selection-changed')
    required, optional = _recovery_file_contract()
    contract = {**required, **optional}
    path = _launchd.ACCESS_STATE / ('runtime-upgrade-' + candidate_digest + '.completed.json'
                                    if completed else 'runtime-upgrade.json')
    value = private_json(path, 0, _upgrade.JOURNAL_LIMIT)
    if completed and (type(value) is not dict or value.get('phase') not in ('active', 'restored')):
        raise InstallError('runtime-upgrade-archive-not-terminal')
    files = value.get('files') if type(value) is dict else None
    if (type(files) is not list or any(type(item) is not dict
            or type(item.get('path')) is not str for item in files)):
        raise InstallError('runtime-upgrade-recovery-file-contract-invalid')
    selected = {item['path'] for item in files}
    if not set(required).issubset(selected) or not selected.issubset(contract):
        raise InstallError('runtime-upgrade-recovery-file-contract-invalid')
    journal, records = _upgrade.RecoveryJournal.load(
        path, current_digest=current_digest,
        candidate_digest=candidate_digest, allowed_paths=selected)
    if journal.value != value:
        raise InstallError('runtime-upgrade-journal-changed')
    if (not set(required).issubset({item['path'] for item in records})
            or any((item['mode'], item['gid']) != contract[item['path']] for item in records)):
        raise InstallError('runtime-upgrade-recovery-file-contract-invalid')
    plan = _load_recovery_context(
        _launchd.ACCESS_STATE / ('runtime-upgrade-context-' + candidate_digest + '.json'),
        current_digest=current_digest, candidate_digest=candidate_digest, owner_name=owner_name)
    _verify_recovery_runtime(plan)
    return plan, journal, records


def _verify_recovery_bindings(plan, records):
    """Require the context and both journaled service definitions to agree."""
    try:
        files = {item['path']: item for item in records}
        gateway = files[str(_destination(_launchd.GATEWAY_PLIST))]
        settings = files[str(_destination(ACCESS_FILES['config']))]
        owner = plan['owner']
        if gateway['before'] != plan['source_bytes']:
            raise ValueError()
        before = json.loads(settings['before'])
        after = json.loads(settings['after'])
        old_binding = _launchd.binding(plistlib.loads(gateway['before']), owner=owner.pw_name,
            executable=plan['source_process']['executable'], uid=owner.pw_uid, gid=owner.pw_gid)
        new_binding = _launchd.binding(plistlib.loads(gateway['after']), owner=owner.pw_name,
            executable=str(_bundle.INSTALL_ROOT / plan['runtime_bundle']['digest'] / 'node'),
            uid=owner.pw_uid, gid=owner.pw_gid)
        if plan.get('migration_qualification'):
            expected_environment = _native_transport_environment(plan['migration_qualification']['transport'], owner)
            actual_environment, _ = _env_assignments(plistlib.loads(gateway['after'])['ProgramArguments'])
            if any(actual_environment.get(key) != value for key, value in expected_environment.items()):
                raise ValueError()
        if (after != plan['access_settings'] or new_binding != plan['gateway_binding']
                or after.get('gateway_binding') != new_binding
                or before.get('gateway_binding') != old_binding
                or before.get('gateway_process') != plan['source_process']
                or before.get('owner') != owner.pw_name
                or before.get('install_dir') != after.get('install_dir')):
            raise ValueError()
        baseline = files.get(str(_launchd.ACCESS_STATE / 'service-baseline.json'))
        if baseline is not None and baseline['after'] != _upgrade_service_baseline(baseline['before'], records):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise InstallError('runtime-upgrade-recovery-binding-mismatch') from None


def recover_install(bridge, *, current_digest, candidate_digest, owner_name):
    """Restore an interrupted runtime upgrade; CLI remains gated separately."""
    if sys.platform != 'darwin' or os.geteuid() != 0:
        raise InstallError('macos-root-install-required')
    from pixel_access_bridge import LaunchdAccessBridge
    from pixel_macos_custody import protected_bytes
    if not isinstance(bridge, LaunchdAccessBridge) or bridge.state != _launchd.ACCESS_STATE:
        raise InstallError('runtime-upgrade-controller-state-changed')
    if not os.path.lexists(bridge.state / 'runtime-upgrade.json'):
        return _finish_archived_upgrade(bridge, current_digest=current_digest,
            candidate_digest=candidate_digest, owner_name=owner_name)
    with bridge.recovery_locked():
        selection = dict(current_digest=current_digest, candidate_digest=candidate_digest,
                         owner_name=owner_name)
        plan, journal, records = _load_upgrade_recovery(**selection)
        _verify_recovery_bindings(plan, records)
        def verify():
            fresh_plan, fresh_journal, fresh_records = _load_upgrade_recovery(**selection)
            if (fresh_plan != plan or fresh_records != records or fresh_journal.value != journal.value):
                raise InstallError('runtime-upgrade-recovery-authority-changed')
            _verify_recovery_bindings(plan, records)
            for item in records:
                if protected_bytes(item['path'], limit=8 * 1024 * 1024) not in (item['before'], item['after']):
                    raise InstallError('runtime-upgrade-rollback-file-drift')
        verify()
        _recover_upgrade(plan, records, journal, verify_snapshots=verify,
                         previous_guard=_previous_upgrade_guard(records))
    return 'restored'


def _finish_archived_upgrade(bridge, *, current_digest, candidate_digest, owner_name):
    """Finish only admission release, never replay an archived deployment."""
    from pixel_access_bridge import private_json
    from pixel_macos_custody import protected_bytes
    with bridge.recovery_locked(completed_digest=candidate_digest):
        selection = dict(current_digest=current_digest, candidate_digest=candidate_digest,
                         owner_name=owner_name, completed=True)
        plan, journal, records = _load_upgrade_recovery(**selection)
        _verify_recovery_bindings(plan, records)
        phase = journal.value['phase']
        previous, candidate = _upgrade_services(plan, records)
        services = candidate if phase == 'active' else previous
        def verify():
            fresh_plan, fresh_journal, fresh_records = _load_upgrade_recovery(**selection)
            if fresh_plan != plan or fresh_journal.value != journal.value or fresh_records != records:
                raise InstallError('runtime-upgrade-recovery-authority-changed')
            for item in records:
                if protected_bytes(item['path'], limit=8 * 1024 * 1024) != item['after' if phase == 'active' else 'before']:
                    raise InstallError('runtime-upgrade-rollback-file-drift')
            if phase == 'active':
                if plan.get('migration_qualification'):
                    _verify_recovery_runtime(plan)
                    _verify_new_services(plan)
                else:
                    _verify_bundle_selection(plan)
            for service in services.values():
                _upgrade_service_identity(service)
            settings = json.loads(protected_bytes(_destination(ACCESS_FILES['config'])))
            _policy.policy_state(settings['gateway_policy'])
            _ready_gateway(services['gateway'], plan['access_settings']['gateway_port'])
            _ready_access(services['access'])
            _ready_access_relay(services['relay'], plan)
        verify()
        _clear_candidate_stop_witnesses(plan)
        path = _edge_hold_journal(plan)
        if not os.path.lexists(path):
            if phase != 'restored':
                raise InstallError('runtime-upgrade-missing-edge-hold')
            return phase
        hold = private_json(path, 0, 65536)
        if (type(hold) is not dict or hold.get('schemaVersion') != 1
                or set(hold) != {'schemaVersion', 'container', 'binding', 'phase', 'status'}
                or hold.get('phase') not in ('held', 'releasing', 'released')
                or type(hold.get('container')) is not str or not re.fullmatch('[a-f0-9]{64}', hold['container'])
                or type(hold.get('binding')) is not dict or set(hold['binding']) != {'token', 'revision'}
                or any(type(value) is not str or not re.fullmatch('[a-f0-9]{64}', value)
                       for value in hold['binding'].values())):
            raise InstallError('native-migration-edge-journal-invalid')
        verify()
        if hold['phase'] != 'released':
            _finish_migration_hold(plan, hold)
        return phase


def _verify_recovery_runtime(plan):
    """Verify installed artifacts without requiring staging to have finished."""
    from pixel_macos_custody import protected_tree_metadata
    selection, qualification = plan['runtime_bundle'], plan['upgrade_qualification']
    current_digest, candidate_digest = qualification['currentDigest'], qualification['candidateDigest']
    if (any(type(value) is not str or not re.fullmatch('[a-f0-9]{64}', value)
            for value in (current_digest, candidate_digest)) or current_digest == candidate_digest
            or selection['digest'] != candidate_digest):
        raise InstallError('runtime-upgrade-context-selection-changed')
    current = _bundle.INSTALL_ROOT / current_digest
    candidate = _bundle.INSTALL_ROOT / candidate_digest
    config_parent = RUNTIME_CONFIG_ROOT / str(plan['owner'].pw_uid)
    source_config = selection['source_config']
    source_body = selection['source_config_bytes']
    migrating = bool(plan.get('migration_qualification'))
    if migrating:
        _validate_migration_recovery_context(plan)
        source_config = plan['source_environment']['OPENCLAW_CONFIG_PATH']
        source_body = plan['migration_source_config_bytes']
    if (selection['destination'] != str(candidate)
            or selection['config_path'] not in {str(config_parent / name) for name in _candidate_config_names(plan)}
            or not _source_runtime_config(source_config, config_parent, current_digest)):
        raise InstallError('runtime-upgrade-recovery-path-invalid')
    protected_tree_metadata(current)
    _bundle.verify(current, expected_digest=current_digest)
    if _configuration_bytes(source_config, plan['owner'].pw_uid) != source_body:
        raise InstallError('native-config-changed')
    # Consult only the fixed installed location, never the original writable
    # staging directory recorded in the plan. Partial publication stays held.
    if os.path.lexists(candidate):
        protected_tree_metadata(candidate)
        if migrating:
            manifest, _ = _bundle.verify(candidate, expected_digest=candidate_digest)
            config = json.loads(selection['source_config_bytes'])
            config['plugins']['load']['paths'] = [str(candidate / path) for path in manifest['plugins']]
            if (json.dumps(config, indent=2, ensure_ascii=True) + '\n').encode() != selection['config_bytes']:
                raise InstallError('native-migration-config-mapping-changed')
        else:
            approved = _qualify_upgrade(plan['upgrade_kind'], current, candidate,
                current_digest=current_digest, candidate_digest=candidate_digest)
            if approved != qualification:
                raise InstallError('runtime-upgrade-qualification-changed')
    if os.path.lexists(selection['config_path']):
        if _configuration_bytes(selection['config_path'], plan['owner'].pw_uid) != selection['config_bytes']:
            raise InstallError('native-candidate-config-changed')


def _reprove_installed_access(plan):
    """Renew the installed controller's proof outside controller locks."""
    from pixel_macos_custody import protected_bytes
    if sys.platform != 'darwin' or os.geteuid() != 0:
        raise InstallError('macos-root-install-required')
    owner = pwd.getpwnam(plan['owner'].pw_name)
    if owner.pw_uid <= 0 or (owner.pw_uid, owner.pw_gid) != (plan['owner'].pw_uid, plan['owner'].pw_gid):
        raise InstallError('native-migration-owner-changed')
    helper = ACCESS_PROGRAM_ROOT / 'pixel_access_reconcile.py'
    protected_bytes(helper)
    mode = _policy.policy_state(plan['access_settings']['gateway_policy'])['activeMode']
    result = subprocess.run(['/usr/bin/python3', '-I', str(helper), '--startup'],
        cwd='/', env={'HOME': owner.pw_dir, 'PATH': '/usr/bin:/bin'},
        user=owner.pw_uid, group=owner.pw_gid,
        extra_groups=os.getgrouplist(owner.pw_name, owner.pw_gid),
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, timeout=360, check=False)
    if result.returncode != 0 or len(result.stdout) > 1024:
        raise InstallError('native-runtime-access-reproof-required')
    try:
        value = json.loads(result.stdout)
        if (type(value) is not dict or set(value) != {'result', 'mode'}
                or value['result'] not in ('already-ready', 'reproved') or value['mode'] != mode):
            raise ValueError()
    except (ValueError, TypeError):
        raise InstallError('native-runtime-access-reproof-required') from None


def upgrade_install(plan, source):
    """Stage and activate a qualified replacement under the controller lock."""
    if plan.get('migration_qualification'):
        raise InstallError('joint-native-migration-activation-required')
    return _execute_upgrade_install(plan, source)


def _requalify_migration(plan):
    _validate_migration_recovery_context(plan)
    approval = plan['migration_qualification']
    runtime, services = plan['runtime_bundle'], plan['native_services']
    selected = qualify_migration_selection(owner_name=plan['owner'].pw_name,
        current_digest=approval['currentDigest'], gateway_port=plan['access_settings']['gateway_port'],
        candidate=approval['candidate'], runtime_bundle=runtime['source'], bundle_digest=runtime['digest'],
        services_bundle=services['bundle'], services_digest=services['expected_digest'],
        source_ref=services['expected_ref'])
    if (selected['preservation'] != approval['preservation'] or selected['services'] != services
            or selected['previousConfigBytes'] != plan['migration_source_config_bytes']
            or selected['previousConfig'] != plan['source_environment']['OPENCLAW_CONFIG_PATH']
            or any(selected['runtime'][key] != runtime[key] for key in (
                'source', 'digest', 'destination', 'source_config', 'source_config_bytes', 'config_bytes'))):
        raise InstallError('native-migration-selection-changed')
    values = _env_file(Path(plan['access_settings']['install_dir']) / '.env')
    keys = [values.get(name, '') for name in ('DASHBOARD_API_KEY', 'PIXEL_OPENWEBUI_KEY', 'PIXEL_MODEL_RELAY_KEY')]
    config = json.loads(runtime['source_config_bytes'])
    if (not all(re.fullmatch('[a-f0-9]{64}', key) for key in keys) or len(set(keys)) != 3
            or keys[0].encode('ascii') != plan['key']
            or keys[2] != config['models']['providers']['ods-gateway']['apiKey']):
        raise InstallError('native-migration-credentials-not-persisted')
    _qualify_migration_ingress(plan)
    return plan['upgrade_qualification']


def _qualify_migration_ingress(plan):
    transport = plan['migration_qualification'].get('transport')
    expected = _native_transport_environment(transport, plan['owner'])
    environment, _ = _env_assignments(plan['gateway']['ProgramArguments'])
    if any(environment.get(key) != value for key, value in expected.items()):
        raise InstallError('native-migration-docker-binding-mismatch')
    context = _migration_edge_context(plan)
    def docker(*args):
        result = subprocess.run([transport['docker'], *args], capture_output=True,
            text=True, timeout=20, **context)
        if result.returncode or len(result.stdout) > 4 * 1024 * 1024:
            raise InstallError('native-migration-ingress-unavailable')
        return result.stdout
    try:
        ids = docker('ps', '--no-trunc', '--filter', 'label=com.docker.compose.project=' + transport['project'],
            '--filter', 'label=com.docker.compose.service=pixel-native-ingress', '--format', '{{.ID}}').strip().splitlines()
        if len(ids) != 1 or not re.fullmatch('[a-f0-9]{64}', ids[0]):
            raise ValueError()
        records = json.loads(docker('inspect', ids[0]))
        if type(records) is not list or len(records) != 1:
            raise ValueError()
        record = records[0]
        labels = record['Config']['Labels']
        if (record['Id'] != ids[0] or record['Image'] != transport['image']
                or record['State']['Running'] is not True or record['Config']['User'] != transport['user']
                or labels.get('com.docker.compose.project') != transport['project']
                or labels.get('com.docker.compose.service') != 'pixel-native-ingress'):
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise InstallError('native-migration-ingress-identity-mismatch') from None
    return ids[0]


def migrate_install(plan, source):
    """Apply a joint migration; caller must also retain the owner environment backup."""
    if not plan.get('migration_qualification') or plan.get('upgrade_kind') != 'native-migration':
        raise InstallError('joint-native-migration-plan-required')
    return _execute_upgrade_install(plan, source)


def _execute_upgrade_install(plan, source):
    if sys.platform != 'darwin' or os.geteuid() != 0:
        raise InstallError('macos-root-install-required')
    if not plan.get('upgrade_qualification') or not plan.get('runtime_bundle'):
        raise InstallError('runtime-upgrade-plan-required')
    sys.path.insert(0, str(HERE.parents[2] / 'bin'))
    from pixel_access_bridge import LaunchdAccessBridge
    from pixel_macos_custody import protected_bytes
    settings = json.loads(protected_bytes(_destination(ACCESS_FILES['config'])))
    if settings.get('state_dir') != str(_launchd.ACCESS_STATE):
        raise InstallError('runtime-upgrade-controller-state-changed')
    bridge = LaunchdAccessBridge(settings['install_dir'], plan['key'].decode('ascii'),
        gateway_target=settings['gateway_target'], gateway_plist=settings['gateway_plist'],
        gateway_process=settings['gateway_process'], state=Path(settings['state_dir']),
        gateway_binding=settings['gateway_binding'], installed_binary=settings['openclaw_bin'],
        gateway_owner=plan['owner'].pw_name, gateway_port=settings.get('gateway_port'),
        settings_data_dir=settings.get('settings_data_dir'), gateway_policy=settings['gateway_policy'])
    # Snapshot and staging share one lock. Do not nest prepared_upgrade's lock:
    # it is the same nonblocking flock used by live controller mutations.
    with bridge.locked():
        if any(os.path.lexists(bridge.state / name) for name in
               ('transition.json', 'policy-activation.json', 'runtime-upgrade.json')):
            raise InstallError('runtime-upgrade-pending-recovery')
        if json.loads(protected_bytes(_destination(ACCESS_FILES['config']))) != settings:
            raise InstallError('runtime-upgrade-controller-binding-changed')
        if plan.get('migration_qualification') and os.path.lexists(bridge.state / 'service-installation.json'):
            raise InstallError('native-managed-service-upgrade-requires-qualification')
        records, additions = _upgrade_file_snapshots(plan, source)
        previous_guard = _previous_upgrade_guard(records)
        previous, _ = _upgrade_services(plan, records)
        for service in previous.values():
            if _job_disabled(service.target):
                raise InstallError('runtime-upgrade-disabled-service')
            _upgrade_service_identity(service)
        _runtime_config(plan)
        selection, qualification = plan['runtime_bundle'], plan['upgrade_qualification']
        # Qualification is repeated inside the lock, before any staging write.
        if plan.get('migration_qualification'):
            approved = _requalify_migration(plan)
        else:
            approved = _qualify_upgrade(plan.get('upgrade_kind', 'stream-progress'),
                _bundle.INSTALL_ROOT / qualification['currentDigest'], selection['source'],
                current_digest=qualification['currentDigest'], candidate_digest=selection['digest'])
        if approved != qualification:
            raise InstallError('runtime-upgrade-qualification-changed')
        # Reject an earlier attempt before publishing a new mutation guard.
        # Its archive and admission receipt remain recovery authority.
        prior_paths = [bridge.state / ('runtime-upgrade-' + selection['digest'] + '.completed.json'),
                       _edge_hold_journal(plan)]
        prior_paths.extend(_runtime_upgrade_stop_witness(selection['digest'], version, role)
                           for version in ('previous', 'candidate')
                           for role in ('gateway', 'access', 'relay'))
        if any(os.path.lexists(path) for path in prior_paths):
            raise InstallError('runtime-upgrade-prior-attempt-requires-archive')
        value = _upgrade.encode_recovery(records,
            current_digest=qualification['currentDigest'], candidate_digest=selection['digest'],
            allowed_paths={item['path'] for item in records})
        context_path = bridge.state / ('runtime-upgrade-context-' + selection['digest'] + '.json')
        _write_exact(context_path,
            (json.dumps(_recovery_context(plan), sort_keys=True) + '\n').encode(), mode=0o600)
        journal = _upgrade.RecoveryJournal.create(bridge.state / 'runtime-upgrade.json', value)
        # Staging changes no live references; failures retain the exact journal
        # and leave recovery to a verified operator command, not a fresh install.
        published = _bundle.publish(selection['source'], expected_digest=selection['digest'])
        if str(published) != selection['destination']:
            raise InstallError('native-bundle-destination-changed')
        _runtime_config(plan, write=True)
        for path, data, attributes in additions:
            _write_exact(path, data, **attributes)
        _activate_upgrade(plan, records, journal, previous_guard=previous_guard)
    # Reconciliation submits a controller transaction, so it must not run
    # while this process holds the same controller lock.
    _reprove_installed_access(plan)
    return 'active'


def install(plan, source):
    if plan.get('migration_qualification'):
        raise InstallError('joint-native-migration-activation-required')
    if plan.get('upgrade_qualification'):
        raise InstallError('runtime-upgrade-activation-required')
    if sys.platform != "darwin" or os.geteuid() != 0:
        raise InstallError("macos-root-install-required")
    # Planning the existing user runtime is useful for migration diagnostics,
    # but a protected service must never execute that writable runtime.
    if not plan.get('runtime_bundle'):
        raise InstallError('protected-runtime-bundle-required')
    files = _deployment_files(source, plan)
    for path, data, attributes in files:
        _preflight_file(path, data, **attributes)
    _preflight_directory(_launchd.ACCESS_STATE, private=True)
    _preflight_jobs()
    _runtime_config(plan)
    services = _activation_services(plan)
    old, *candidates = services
    # Disabled jobs are intentional user state, not ours to override.
    if any(_job_disabled(service.target) for service in services):
        raise InstallError('native-migration-disabled-job')
    if plan.get('initial_install'):
        _require_initial_absence(plan, old)
    else:
        old.process_identity()
    journal = Path(_launchd.ACCESS_STATE) / 'installation.json'
    if os.path.lexists(journal):
        raise InstallError('native-migration-journal-requires-review')
    Path(_launchd.ACCESS_STATE).mkdir(mode=0o700, parents=True, exist_ok=True)
    _check_directory(_launchd.ACCESS_STATE, private=True)
    _migration_phase(plan, journal, 'staging')
    # Stage disabled, so even a crash during file publication cannot create
    # a second gateway automatically at the next boot.
    try:
        for service in candidates:
            _command(['/bin/launchctl', 'disable', service.target])
        selection = plan['runtime_bundle']
        published = _bundle.publish(selection['source'], expected_digest=selection['digest'])
        if str(published) != selection['destination']:
            raise InstallError('native-bundle-destination-changed')
        _runtime_config(plan, write=True)
        for path, data, attributes in files:
            _write_exact(path, data, **attributes)
    except BaseException:
        _migration_phase(plan, journal, 'staging-failed')
        raise
    _activate_with_admission(plan, services, journal)
    _reprove_installed_access(plan)


def _recovery_bridge(*, current_digest, candidate_digest, owner_name):
    if any(type(value) is not str or not re.fullmatch('[a-f0-9]{64}', value)
           for value in (current_digest, candidate_digest)) or current_digest == candidate_digest:
        raise InstallError('runtime-upgrade-context-selection-changed')
    plan = _load_recovery_context(
        _launchd.ACCESS_STATE / ('runtime-upgrade-context-' + candidate_digest + '.json'),
        current_digest=current_digest, candidate_digest=candidate_digest, owner_name=owner_name)
    return _bridge_from_recovery_plan(plan, owner_name=owner_name)


def _bridge_from_recovery_plan(plan, *, owner_name):
    from pixel_access_bridge import LaunchdAccessBridge
    settings = plan['access_settings']
    if (settings.get('state_dir') != str(_launchd.ACCESS_STATE)
            or settings.get('gateway_target') != _launchd.GATEWAY_TARGET
            or settings.get('gateway_plist') != str(_launchd.GATEWAY_PLIST)
            or settings.get('owner') != owner_name
            or settings.get('openclaw_bin') != str(GATEWAY_LAUNCHER)):
        raise InstallError('runtime-upgrade-controller-binding-changed')
    # Construction makes no service calls. The context and journal are loaded
    # again inside recover_install's lock before any recovery operation.
    return LaunchdAccessBridge(str(_path(settings['install_dir'])), plan['key'].decode('ascii'),
        gateway_target=settings['gateway_target'], gateway_plist=settings['gateway_plist'],
        gateway_process=settings['gateway_process'], state=_launchd.ACCESS_STATE,
        gateway_binding=settings['gateway_binding'], installed_binary=settings['openclaw_bin'],
        gateway_owner=owner_name, gateway_port=settings['gateway_port'],
        settings_data_dir=settings.get('settings_data_dir'), gateway_policy=settings['gateway_policy'])


def _recovery_main(argv):
    parser = argparse.ArgumentParser(description='Recover a journaled native runtime update; requires root.')
    parser.add_argument('--owner', required=True)
    parser.add_argument('--current-bundle-digest', required=True)
    parser.add_argument('--bundle-digest', required=True)
    args = parser.parse_args(argv)
    from pixel_access_bridge import AccessError
    try:
        if sys.platform != 'darwin' or os.geteuid() != 0:
            raise InstallError('macos-root-install-required')
        selection = dict(current_digest=args.current_bundle_digest,
                         candidate_digest=args.bundle_digest, owner_name=args.owner)
        outcome = recover_install(_recovery_bridge(**selection), **selection)
        # Recovery may restore the previous runtime. Its installed policy, not
        # the candidate plan, determines the mode that must pass the tool proof.
        _reprove_recovered_access(args.owner)
    except InstallError as error:
        print('error: ' + error.code, file=sys.stderr)
        return 1
    except (AccessError, _upgrade.UpgradeError, OSError, ValueError, KeyError, TypeError,
            subprocess.SubprocessError):
        # Environment/configuration errors can carry credentials in their text.
        print('error: native-runtime-recovery-failed', file=sys.stderr)
        return 1
    print(json.dumps({'operation': 'runtime-recovery', 'status': outcome,
                      'runtimeBundleDigest': args.bundle_digest if outcome == 'active' else args.current_bundle_digest}))
    return 0


def _reprove_recovered_access(owner_name):
    from pixel_macos_custody import protected_bytes
    if sys.platform != 'darwin' or os.geteuid() != 0:
        raise InstallError('macos-root-install-required')
    settings = json.loads(protected_bytes(_destination(ACCESS_FILES['config'])))
    if (settings.get('owner') != owner_name
            or settings.get('gateway_target') != _launchd.GATEWAY_TARGET
            or settings.get('state_dir') != str(_launchd.ACCESS_STATE)):
        raise InstallError('runtime-upgrade-controller-binding-changed')
    _reprove_installed_access({'owner': pwd.getpwnam(owner_name), 'access_settings': settings})


def _retirement_snapshots(*, current_digest, candidate_digest, history_digest=None):
    from pixel_macos_custody import protected_bytes
    if any(type(value) is not str or not re.fullmatch('[a-f0-9]{64}', value)
           for value in (current_digest, candidate_digest)) or current_digest == candidate_digest:
        raise InstallError('runtime-upgrade-context-selection-changed')

    def read(name):
        path = _launchd.ACCESS_STATE / name
        body = protected_bytes(path, limit=3 * _upgrade.JOURNAL_LIMIT)
        if stat.S_IMODE(path.lstat().st_mode) != 0o600:
            raise InstallError('runtime-upgrade-history-mode-invalid')
        return body

    if history_digest is not None:
        if type(history_digest) is not str or not re.fullmatch('[a-f0-9]{64}', history_digest):
            raise InstallError('runtime-upgrade-history-selection-invalid')
        return _upgrade.decode_retirement_history(
            read('runtime-upgrade-history-' + history_digest + '.json'),
            history_digest=history_digest, current_digest=current_digest,
            candidate_digest=candidate_digest)
    required = ['runtime-upgrade-' + candidate_digest + '.completed.json',
                'runtime-upgrade-context-' + candidate_digest + '.json']
    optional = ['runtime-upgrade-edge-' + candidate_digest + '.json']
    optional.extend(_runtime_upgrade_stop_witness(candidate_digest, version, role).name
                    for version in ('previous', 'candidate')
                    for role in ('gateway', 'access', 'relay'))
    snapshots = {name: read(name) for name in required}
    for name in optional:
        if os.path.lexists(_launchd.ACCESS_STATE / name):
            snapshots[name] = read(name)
    return snapshots


def _retirement_main(argv):
    parser = argparse.ArgumentParser(description='Archive a verified restored runtime attempt; requires root.')
    parser.add_argument('--owner', required=True)
    parser.add_argument('--current-bundle-digest', required=True)
    parser.add_argument('--bundle-digest', required=True)
    parser.add_argument('--history-digest', help='Resume using a previously preserved history SHA256')
    args = parser.parse_args(argv)
    from pixel_access_bridge import AccessError
    from pixel_macos_custody import CustodyError
    try:
        if sys.platform != 'darwin' or os.geteuid() != 0:
            raise InstallError('macos-root-install-required')
        selection = dict(current_digest=args.current_bundle_digest,
                         candidate_digest=args.bundle_digest)
        snapshots = _retirement_snapshots(**selection, history_digest=args.history_digest)
        context = json.loads(snapshots['runtime-upgrade-context-' + args.bundle_digest + '.json'])
        plan = _decode_recovery_context(context, **selection, owner_name=args.owner)
        bridge = _bridge_from_recovery_plan(plan, owner_name=args.owner)
        history = _archive_verified_retirement(bridge, snapshots, **selection, owner_name=args.owner)
    except InstallError as error:
        print('error: ' + error.code, file=sys.stderr)
        return 1
    except (AccessError, CustodyError, _upgrade.UpgradeError, OSError, ValueError,
            KeyError, TypeError, subprocess.SubprocessError):
        print('error: native-runtime-retirement-failed', file=sys.stderr)
        return 1
    print(json.dumps({'operation': 'runtime-retirement', 'status': 'archived', 'history': history}))
    return 0


def _migration_main(argv):
    parser = argparse.ArgumentParser(description='Plan or explicitly activate a joint native Pixel migration.')
    for name in ('source', 'install-dir', 'owner', 'candidate', 'runtime-bundle', 'bundle-digest',
                 'current-bundle-digest', 'services-bundle', 'services-digest', 'pixel-source-ref'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--openclaw-bin', default=str(GATEWAY_LAUNCHER))
    parser.add_argument('--gateway-port', type=int, default=18789)
    parser.add_argument('--access-port', type=int, default=18790)
    for name in ('docker', 'compose-project', 'ingress-image', 'ingress-user'):
        parser.add_argument('--' + name)
    parser.add_argument('--activate', action='store_true', help='Requires root; default is a read-only plan')
    args = parser.parse_args(argv)
    from pixel_access_bridge import AccessError
    from pixel_macos_custody import CustodyError
    try:
        if args.activate and (sys.platform != 'darwin' or os.geteuid() != 0):
            raise InstallError('macos-root-install-required')
        bindings = (args.docker, args.compose_project, args.ingress_image, args.ingress_user)
        if any(bindings) and not all(bindings):
            raise InstallError('complete-native-docker-transport-required')
        transport = dict(zip(('docker', 'project', 'image', 'user'), bindings)) if all(bindings) else None
        if args.activate and transport is None:
            raise InstallError('complete-native-docker-transport-required')
        plan = make_migration_plan(install_dir=args.install_dir, owner_name=args.owner,
            openclaw_bin=args.openclaw_bin, gateway_port=args.gateway_port, access_port=args.access_port,
            candidate=args.candidate, runtime_bundle=args.runtime_bundle, bundle_digest=args.bundle_digest,
            current_digest=args.current_bundle_digest, services_bundle=args.services_bundle,
            services_digest=args.services_digest, source_ref=args.pixel_source_ref, native_transport=transport)
        outcome = migrate_install(plan, args.source) if args.activate else 'planned'
    except InstallError as error:
        print('error: ' + error.code, file=sys.stderr)
        return 1
    except (AccessError, CustodyError, _upgrade.UpgradeError, OSError, ValueError,
            KeyError, TypeError, subprocess.SubprocessError) as error:
        # Upgrade rollback wraps its cause. Preserve known installer codes, not
        # arbitrary exception text that could contain environment credentials.
        cause, detail = error, ''
        for _ in range(10):
            if isinstance(cause, InstallError):
                detail = ' (' + cause.code + ')'
                break
            cause = getattr(cause, '__cause__', None)
            if cause is None:
                break
        print('error: native-joint-migration-failed' + detail + '; retain the preparation and protected recovery journal', file=sys.stderr)
        return 1
    print(json.dumps({'operation': 'native-migration', 'status': outcome,
        'runtimeBundleDigest': args.bundle_digest, 'serviceBundleDigest': args.services_digest}, sort_keys=True))
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == 'migrate-native':
        sys.path.insert(0, str(HERE.parents[2] / 'bin'))
        return _migration_main(argv[1:])
    if argv and argv[0] == 'retire-runtime':
        sys.path.insert(0, str(HERE.parents[2] / 'bin'))
        return _retirement_main(argv[1:])
    if argv and argv[0] == 'recover-runtime':
        sys.path.insert(0, str(HERE.parents[2] / 'bin'))
        return _recovery_main(argv[1:])
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--install-dir", help="Existing ODS installation; defaults to --source")
    parser.add_argument("--owner", default=os.environ.get("USER", ""))
    parser.add_argument("--gateway-plist")
    parser.add_argument("--openclaw-bin", required=True)
    parser.add_argument("--gateway-port", type=int, default=18789)
    parser.add_argument('--access-port', type=int, default=18790)
    parser.add_argument('--runtime-bundle', help='Staged native runtime bundle; paired with --bundle-digest')
    parser.add_argument('--bundle-digest', help='Previously selected manifest SHA256')
    parser.add_argument('--services-bundle', help='Approved native service bundle for initial installation')
    parser.add_argument('--services-digest', help='Approved service manifest SHA256')
    parser.add_argument('--pixel-source-ref', help='Exact approved Pixel commit for the service bundle')
    parser.add_argument('--current-bundle-digest', help='Plan a replacement of this active protected runtime (dry-run only)')
    parser.add_argument('--upgrade-kind', choices=('stream-progress', 'workspace-root'),
                        help='Exact reviewed change to qualify; requires --current-bundle-digest')
    parser.add_argument("--install", action="store_true")
    parser.add_argument('--initial-install', action='store_true',
                        help='Plan a new deployment from an unloaded --gateway-plist template, not a migration')
    parser.add_argument('--activate-upgrade', action='store_true',
                        help='Commit a qualified runtime replacement; requires --current-bundle-digest')
    args = parser.parse_args(argv)
    try:
        service_options = (args.services_bundle, args.services_digest, args.pixel_source_ref)
        if any(service_options) and (not all(service_options) or not args.initial_install):
            raise InstallError('complete-initial-service-selection-required')
        if args.initial_install and (args.current_bundle_digest or args.activate_upgrade or not args.gateway_plist):
            raise InstallError('initial-install-requires-unloaded-template')
        if args.upgrade_kind and not args.current_bundle_digest:
            raise InstallError('upgrade-kind-requires-current-bundle')
        if args.activate_upgrade and not args.current_bundle_digest:
            raise InstallError('upgrade-activation-requires-current-bundle')
        if args.activate_upgrade and args.install:
            raise InstallError('upgrade-activation-conflicts-with-install')
        if args.current_bundle_digest and args.gateway_plist:
            raise InstallError('runtime-upgrade-dry-run-only')
        if args.current_bundle_digest and not args.activate_upgrade and args.install:
            raise InstallError('runtime-upgrade-dry-run-only')
        owner = pwd.getpwnam(args.owner)
        source_plist = args.gateway_plist or str(Path(owner.pw_dir) / "Library/LaunchAgents" / (_launchd.GATEWAY_LABEL + ".plist"))
        planner = make_upgrade_plan if args.current_bundle_digest else make_plan
        selection = ({'current_digest': args.current_bundle_digest,
                      'upgrade_kind': args.upgrade_kind or 'stream-progress'} if args.current_bundle_digest
                     else {'source_plist': source_plist, 'initial_install': args.initial_install})
        plan = planner(install_dir=args.install_dir or args.source, owner_name=args.owner, **selection,
                         openclaw_bin=args.openclaw_bin, gateway_port=args.gateway_port,
                         runtime_bundle=args.runtime_bundle, bundle_digest=args.bundle_digest,
                         access_port=args.access_port)
        if args.services_bundle:
            bind_initial_services(plan, bundle=args.services_bundle, digest=args.services_digest,
                source_ref=args.pixel_source_ref)
        if args.activate_upgrade:
            outcome = upgrade_install(plan, args.source)
            print(json.dumps({'operation': 'runtime-upgrade', 'status': outcome,
                              'runtimeBundleDigest': plan['runtime_bundle']['digest']},
                             sort_keys=True, separators=(',', ':')))
        elif args.install:
            install(plan, args.source)
        else:
            # Environment assignments may contain credentials. Only emit the
            # nonsecret deployment identity, never the complete launchd argv.
            public = {"mode": "dry-run", "gatewayTarget": _launchd.GATEWAY_TARGET,
                      "accessTarget": _launchd.ACCESS_TARGET, "owner": plan["owner"].pw_name,
                      "gatewayPort": args.gateway_port, "gatewayDefinition": plan["gateway_binding"]["definition"],
                      "installDir": plan["access_settings"]["install_dir"],
                      "socket": str(_launchd.ACCESS_SOCKET)}
            if plan['runtime_bundle']:
                public['runtimeBundle'] = {key: plan['runtime_bundle'][key] for key in ('digest', 'destination')}
                public['accessRelay'] = {'target': _launchd.RELAY_TARGET, 'port': plan['access_port']}
            if args.current_bundle_digest:
                public['operation'] = 'runtime-upgrade'
                public['currentBundleDigest'] = args.current_bundle_digest
                public['upgradeKind'] = plan['upgrade_kind']
            elif args.initial_install:
                public['operation'] = 'initial-install'
            if plan.get('native_services'):
                public['nativeServiceDigest'] = plan['native_services']['expected_digest']
            print(json.dumps(public, sort_keys=True, separators=(",", ":")))
    except _upgrade.UpgradeError as error:
        code = ('runtime-upgrade-recovery-required' if str(error) == 'runtime-upgrade-recovery-required'
                else 'native-runtime-upgrade-failed')
        print('error: ' + code, file=sys.stderr)
        return 1
    except subprocess.SubprocessError:
        print("error: native-service-command-failed", file=sys.stderr)
        return 1
    except (InstallError, KeyError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
