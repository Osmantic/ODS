#!/usr/bin/env python3
"""Closed native Source payload and inert publication; never starts a job."""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import stat
import subprocess
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'bin'))

COMPONENT = 'native-source'
ROLES = ('sync', 'direct', 'action', 'reconcile', 'exchange', 'revoke')
PROFILES = tuple('source-' + role + '.sb' for role in ROLES)
PAYLOADS = ('broker.py', 'source-broker.env', *PROFILES)
FILES = ('component.json', *PAYLOADS)
SOURCE_INPUTS = ('deploy/source-broker/broker.py', 'scripts/configure.mjs', '.env.example')
# Reviewed authority, independent of the owner-produced component manifest.
# These three blobs/default bytes derive from the EXISTING exact Source bundle
# pin in pixel-native-bootstrap.py, not from a new archive or a runtime Git run.
# Pin/table synchronization and the actual configure defaults are tested below
# the owner boundary. Unknown refs cannot acquire this new native capability.
SOURCE_AUTHORITIES = {
    'acc5550888fe034a9a42e90307c9f7a664726315': {
        'bundleSha256': '477f974f7d85c43b8e4e7a79ff3411da8ee938a9d597033c4f53323a5569d922',
        'tree': '754f9cf764a800c79a6c2e5f619dc910ff5c3c07',
        'inputs': {
            'deploy/source-broker/broker.py': '0b0c5a9727505e3b8af0196df2fc922eec42d0977c3ab48515dbee416687b948',
            'scripts/configure.mjs': 'c512eb6cdc0056c03e1d07ccfaf76030e7917a5819322ba8e24e8a7ade0d728d',
            '.env.example': '9f616246f4d44a7fc5b4503a0cc7b0921acf145c902a301b2434776588be2e15',
        },
        'templateSha256': '2f0c2700542d295272c70f47c52cef116101b01c7ce45a353eb0c1d93d1a39be',
    },
}
ROOT_SOURCE_FILES = {
    'bin/access_mode_config.py': 'extensions/services/pixel-agent/host/access_mode_config.py',
    'bin/access_mode_server.py': 'extensions/services/pixel-agent/host/access_mode_server.py',
    'bin/access_mode_worker.py': 'extensions/services/pixel-agent/host/access_mode_worker.py',
    'bin/access_release_transaction.py': 'extensions/services/pixel-agent/host/access_release_transaction.py',
    'bin/connection_transaction.py': 'extensions/services/pixel-agent/host/connection_transaction.py',
    'bin/model_transaction.py': 'extensions/services/pixel-agent/host/model_transaction.py',
    'bin/native_mcp_oauth.mjs': 'extensions/services/pixel-agent/host/native_mcp_oauth.mjs',
    'bin/native_mcp_owner.py': 'extensions/services/pixel-agent/host/native_mcp_owner.py',
    'bin/ods-pixel-route-lease': 'bin/ods-pixel-route-lease',
    'bin/pixel_access_bridge.py': 'bin/pixel_access_bridge.py',
    'bin/pixel_access_client.py': 'bin/pixel_access_client.py',
    'bin/pixel_access_mode.py': 'extensions/services/pixel-agent/host/pixel_access_mode.py',
    'bin/pixel_access_protocol.py': 'bin/pixel_access_protocol.py',
    'bin/pixel_access_reconcile.py': 'bin/pixel_access_reconcile.py',
    'bin/pixel_connections/__init__.py': 'bin/pixel_connections/__init__.py',
    'bin/pixel_connections/activity.py': 'bin/pixel_connections/activity.py',
    'bin/pixel_connections/approvals.py': 'bin/pixel_connections/approvals.py',
    'bin/pixel_connections/catalog.py': 'bin/pixel_connections/catalog.py',
    'bin/pixel_connections/converge.py': 'bin/pixel_connections/converge.py',
    'bin/pixel_connections/coordinator.py': 'bin/pixel_connections/coordinator.py',
    'bin/pixel_connections/custody.py': 'bin/pixel_connections/custody.py',
    'bin/pixel_connections/drafts.py': 'bin/pixel_connections/drafts.py',
    'bin/pixel_connections/google_app.py': 'bin/pixel_connections/google_app.py',
    'bin/pixel_connections/host_api.py': 'bin/pixel_connections/host_api.py',
    'bin/pixel_connections/lifecycle.py': 'bin/pixel_connections/lifecycle.py',
    'bin/pixel_connections/native_access.py': 'bin/pixel_connections/native_access.py',
    'bin/pixel_connections/native_apps.py': 'bin/pixel_connections/native_apps.py',
    'bin/pixel_connections/native_contract.py': 'bin/pixel_connections/native_contract.py',
    'bin/pixel_connections/native_enrollment.py': 'bin/pixel_connections/native_enrollment.py',
    'bin/pixel_connections/native_host_api.py': 'bin/pixel_connections/native_host_api.py',
    'bin/pixel_connections/native_mcp.py': 'bin/pixel_connections/native_mcp.py',
    'bin/pixel_connections/native_public.py': 'bin/pixel_connections/native_public.py',
    'bin/pixel_connections/oauth.py': 'bin/pixel_connections/oauth.py',
    'bin/pixel_connections/planner.py': 'bin/pixel_connections/planner.py',
    'bin/pixel_connections/plugins.py': 'bin/pixel_connections/plugins.py',
    'bin/pixel_connections/state.py': 'bin/pixel_connections/state.py',
    'bin/pixel_connections/status.py': 'bin/pixel_connections/status.py',
    'bin/pixel_gateway_service.py': 'bin/pixel_gateway_service.py',
    'bin/pixel_macos_custody.py': 'bin/pixel_macos_custody.py',
    'bin/pixel_macos_policy.py': 'bin/pixel_macos_policy.py',
    'bin/pixel_macos_process.py': 'bin/pixel_macos_process.py',
    'bin/pixel_model_contract.py': 'bin/pixel_model_contract.py',
    'bin/pixel_model_coordinator.py': 'bin/pixel_model_coordinator.py',
    'bin/pixel_model_transition.py': 'bin/pixel_model_transition.py',
    'bin/pixel_provider/__init__.py': 'bin/pixel_provider/__init__.py',
    'bin/pixel_provider/activation_config.py': 'bin/pixel_provider/activation_config.py',
    'bin/pixel_provider/activity.py': 'bin/pixel_provider/activity.py',
    'bin/pixel_provider/advice.py': 'bin/pixel_provider/advice.py',
    'bin/pixel_provider/advice_call.py': 'bin/pixel_provider/advice_call.py',
    'bin/pixel_provider/advice_frames.py': 'bin/pixel_provider/advice_frames.py',
    'bin/pixel_provider/advice_jobs.py': 'bin/pixel_provider/advice_jobs.py',
    'bin/pixel_provider/advice_process.py': 'bin/pixel_provider/advice_process.py',
    'bin/pixel_provider/advice_python.py': 'bin/pixel_provider/advice_python.py',
    'bin/pixel_provider/advice_runtime.py': 'bin/pixel_provider/advice_runtime.py',
    'bin/pixel_provider/advice_setup.py': 'bin/pixel_provider/advice_setup.py',
    'bin/pixel_provider/advice_setup_worker.py': 'bin/pixel_provider/advice_setup_worker.py',
    'bin/pixel_provider/advice_worker.py': 'bin/pixel_provider/advice_worker.py',
    'bin/pixel_provider/client.py': 'bin/pixel_provider/client.py',
    'bin/pixel_provider/config.py': 'bin/pixel_provider/config.py',
    'bin/pixel_provider/connection.py': 'bin/pixel_provider/connection.py',
    'bin/pixel_provider/connection_import.py': 'bin/pixel_provider/connection_import.py',
    'bin/pixel_provider/connection_transport.py': 'bin/pixel_provider/connection_transport.py',
    'bin/pixel_provider/connection_tunnel.py': 'bin/pixel_provider/connection_tunnel.py',
    'bin/pixel_provider/coordinator.py': 'bin/pixel_provider/coordinator.py',
    'bin/pixel_provider/handoff_approvals.py': 'bin/pixel_provider/handoff_approvals.py',
    'bin/pixel_provider/handoff_worker.py': 'bin/pixel_provider/handoff_worker.py',
    'bin/pixel_provider/health.py': 'bin/pixel_provider/health.py',
    'bin/pixel_provider/host_api.py': 'bin/pixel_provider/host_api.py',
    'bin/pixel_provider/lease_claim.py': 'bin/pixel_provider/lease_claim.py',
    'bin/pixel_provider/managed_deployment.py': 'bin/pixel_provider/managed_deployment.py',
    'bin/pixel_provider/public.py': 'bin/pixel_provider/public.py',
    'bin/pixel_provider/route_worker.py': 'bin/pixel_provider/route_worker.py',
    'bin/pixel_provider/runtime_custody.py': 'bin/pixel_provider/runtime_custody.py',
    'bin/pixel_provider/runtime_gateway.py': 'bin/pixel_provider/runtime_gateway.py',
    'bin/pixel_provider/runtime_policy.py': 'bin/pixel_provider/runtime_policy.py',
    'bin/pixel_provider/runtime_session.py': 'bin/pixel_provider/runtime_session.py',
    'bin/pixel_provider/scopes.py': 'bin/pixel_provider/scopes.py',
    'bin/pixel_provider/service_activation.py': 'bin/pixel_provider/service_activation.py',
    'bin/pixel_provider/service_environment.py': 'bin/pixel_provider/service_environment.py',
    'bin/pixel_provider/sharing.py': 'bin/pixel_provider/sharing.py',
    'bin/pixel_provider/sharing_host_api.py': 'bin/pixel_provider/sharing_host_api.py',
    'bin/pixel_provider/sharing_service.py': 'bin/pixel_provider/sharing_service.py',
    'bin/pixel_provider/store.py': 'bin/pixel_provider/store.py',
    'bin/pixel_provider/store_factory.py': 'bin/pixel_provider/store_factory.py',
    'bin/pixel_provider/vault.py': 'bin/pixel_provider/vault.py',
    'bin/pixel_provider/windows_bootstrap.py': 'bin/pixel_provider/windows_bootstrap.py',
    'bin/pixel_provider/windows_custody.py': 'bin/pixel_provider/windows_custody.py',
    'bin/pixel_provider/windows_store.py': 'bin/pixel_provider/windows_store.py',
    'bin/pixel_provider/windows_transactions.py': 'bin/pixel_provider/windows_transactions.py',
    'bin/pixel_provider/windows_vault.py': 'bin/pixel_provider/windows_vault.py',
    'bin/pixel_schedules.py': 'bin/pixel_schedules.py',
    'bin/pixel_settings/__init__.py': 'bin/pixel_settings/__init__.py',
    'bin/pixel_settings/contract.py': 'bin/pixel_settings/contract.py',
    'bin/pixel_settings/coordinator.py': 'bin/pixel_settings/coordinator.py',
    'bin/pixel_settings/host_api.py': 'bin/pixel_settings/host_api.py',
    'bin/pixel_settings/projection.py': 'bin/pixel_settings/projection.py',
    'bin/pixel_settings/public.py': 'bin/pixel_settings/public.py',
    'bin/pixel_settings/runtime.py': 'bin/pixel_settings/runtime.py',
    'bin/pixel_settings/store.py': 'bin/pixel_settings/store.py',
    'bin/pixel_source_upgrade.py': 'bin/pixel_source_upgrade.py',
    'bin/provider_transaction.py': 'extensions/services/pixel-agent/host/provider_transaction.py',
    'bin/scheduled_tasks_gateway.mjs': 'extensions/services/pixel-agent/host/scheduled_tasks_gateway.mjs',
    'bin/scheduled_tasks_owner.py': 'extensions/services/pixel-agent/host/scheduled_tasks_owner.py',
    'bin/settings_transaction.py': 'extensions/services/pixel-agent/host/settings_transaction.py',
    'bin/unix_peer.py': 'extensions/services/pixel-agent/host/unix_peer.py',
    'installers/macos/lib/pixel-native-source-broker.py': 'installers/macos/lib/pixel-native-source-broker.py',
    'installers/macos/lib/pixel-runtime-bundle.py': 'installers/macos/lib/pixel-runtime-bundle.py',
}
ENV_KEYS = ('PIXEL_SOURCE_TOKEN_PATH', 'PIXEL_SOURCE_OAUTH_DIR', 'PIXEL_SOURCE_PROJECTION_DIR',
    'PIXEL_ACTION_PROPOSAL_DIR', 'PIXEL_ACTION_RESULT_DIR', 'PIXEL_SOURCE_CALENDAR_ID',
    'PIXEL_SOURCE_TIME_ZONE', 'PIXEL_SOURCE_GMAIL_QUERY', 'PIXEL_SOURCE_GMAIL_PAGE_SIZE',
    'PIXEL_SOURCE_GMAIL_MAX_PAGES', 'PIXEL_SOURCE_GMAIL_SENT_QUERY', 'PIXEL_SOURCE_GMAIL_SENT_PAGE_SIZE',
    'PIXEL_SOURCE_GMAIL_SENT_MAX_PAGES', 'PIXEL_SOURCE_CALENDAR_PAST_DAYS',
    'PIXEL_SOURCE_CALENDAR_FUTURE_DAYS', 'PIXEL_SOURCE_CALENDAR_MAX_PAGES', 'PIXEL_LIMB_EMAIL_ENABLED',
    'PIXEL_LIMB_EMAIL_DRAFTS_ENABLED', 'PIXEL_LIMB_CALENDAR_ENABLED', 'PIXEL_LIMB_CALENDAR_WRITE_ENABLED',
    'PIXEL_LIMB_SOCIAL_ENABLED', 'PIXEL_CALENDAR_DIRECT_ENABLED', 'PIXEL_CALENDAR_DIRECT_MAX_PER_HOUR',
    'PYTHONDONTWRITEBYTECODE')
STATE = '/private/var/lib/pixel-source-broker'
KIT = '/usr/local/libexec/ods-pixel-access/pixel_connections/source-broker-kit'
LOGS = '/private/var/log/pixel-source-broker'
RUNTIME_ROOT = Path('/usr/local/libexec/ods-pixel-runtimes')
PROGRAM_ROOT = Path('/usr/local/libexec/ods-pixel-access')
ROOT_META = Path('/private/var/lib/ods-pixel-access')
DESCRIPTOR = Path('/private/etc/ods/pixel-provider-runtime.json')
RUNTIME_MANIFEST = Path('/private/etc/ods/pixel-provider-runtime-manifest.json')
LIMIT = 2 * 1024 * 1024
HEX = re.compile('[a-f0-9]{64}\\Z')


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def sha(body):
    return hashlib.sha256(body).hexdigest()


def _pairs(rows):
    result = {}
    for key, value in rows:
        if key in result:
            raise ValueError('native-source-duplicate-key')
        result[key] = value
    return result


def _read_at(directory, name, *, uid=0, gid=0, mode=0o644, limit=LIMIT, no_acl=None):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != uid or before.st_gid != gid
                or stat.S_IMODE(before.st_mode) != mode or before.st_nlink != 1 or before.st_size > limit):
            raise ValueError('native-source-file-unsafe')
        if no_acl:
            no_acl(stream.fileno())
        body = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        stamp = lambda v: (v.st_dev, v.st_ino, v.st_size, v.st_mtime_ns, v.st_ctime_ns)
        if len(body) > limit or stamp(before) != stamp(after):
            raise ValueError('native-source-file-changed')
        return body, [before.st_dev, before.st_ino]


def _path(value):
    value = os.fspath(value)
    if (not value.startswith('/') or any(part in ('', '.', '..') for part in value.split('/')[1:])
            or any(c in value for c in '\0\r\n') or value == '/'):
        raise ValueError('native-source-path-invalid')
    return value


def profiles(python):
    """Fixed role policy renderer. Actual kernel/Python/TLS closure is a Mac gate."""
    python = _path(python)
    literal = lambda v: '(literal ' + json.dumps(v) + ')'
    subtree = lambda v: '(subpath ' + json.dumps(v) + ')'
    common = ('(version 1)\n(deny default)\n(import "system.sb")\n'
        '(allow sysctl-read)\n'
        '(allow mach-lookup (global-name "com.apple.mDNSResponder"))\n'
        '(allow process-exec ' + literal(python) + ')\n'
        '(allow file-read-metadata)\n'
        '(allow file-read* ' + ' '.join(subtree(v) for v in ('/System', '/usr', '/Library/Frameworks', '/Library/Developer'))
        + ' ' + subtree(KIT) + ' ' + literal(STATE + '/custody.json')
        + ' ' + literal(STATE + '/source-broker.env') + ')\n'
        '(allow file-read* (subpath "/private/etc/ssl") (literal "/private/etc/resolv.conf") '
        '(literal "/private/etc/hosts") (literal "/private/etc/services"))\n'
        '(allow file-read* file-write* (literal "/dev/null") (literal "/dev/urandom") (literal "/dev/random"))\n'
        '(allow network-outbound)\n')
    result = {}
    for role in ROLES:
        reads = [literal(STATE + '/private/google-token.json')]
        writes = []
        if role == 'sync':
            reads.append(subtree(STATE + '/projection'))
            writes.append(subtree(STATE + '/projection'))
        elif role in ('action', 'reconcile', 'direct'):
            reads.extend((subtree(STATE + '/proposals'), subtree(STATE + '/results')))
            writes.append(subtree(STATE + '/results'))
        else:
            reads.append(subtree(STATE + '/private/oauth'))
            writes.extend((subtree(STATE + '/private/oauth'), literal(STATE + '/private/google-token.json')))
            # atomic_json stages the replacement token in its same private directory.
            if role == 'exchange':
                writes.append(subtree(STATE + '/private'))
        body = common + '(allow file-read* ' + ' '.join(reads) + ')\n'
        body += '(allow file-write* ' + ' '.join(writes) + ')\n'
        body += '(allow file-write* ' + ' '.join(literal(LOGS + '/' + role + '.' + stream + '.log')
            for stream in ('stdout', 'stderr')) + ')\n'
        result['source-' + role + '.sb'] = body.encode()
    return result


def disabled_definition(role, *, identity, python, identifier=None):
    """Render ONLY an inert definition, never validate admission or publish it.

    The broker entrypoint deliberately refuses until Root approves the launch
    generation/cancellation handoff. No periodic/path trigger is emitted here.
    """
    from pixel_connections.custody import (_native_identity_intent, native_child_arguments,
        NATIVE_BROKER, NATIVE_LABEL)
    _native_identity_intent(identity)
    native_child_arguments(role, identifier)
    python = _path(python)
    label = NATIVE_LABEL + role + ('.' + identifier if identifier is not None else '')
    environment = {'HOME': '/private/var/empty', 'USER': NATIVE_BROKER, 'LOGNAME': NATIVE_BROKER,
        'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1'}
    document = {
        'Label': label, 'Disabled': True, 'RunAtLoad': False, 'KeepAlive': False,
        'UserName': NATIVE_BROKER, 'GroupName': NATIVE_BROKER, 'InitGroups': False,
        'ProgramArguments': ['/usr/bin/env', '-i', *(key + '=' + value for key, value in environment.items()),
            python, '-I', '-B', str(PROGRAM_ROOT / 'pixel_connections/custody.py'),
            '--native-broker-child', role, *([identifier] if identifier is not None else [])],
        'WorkingDirectory': STATE, 'Umask': 0o077, 'ProcessType': 'Background',
        'AbandonProcessGroup': False, 'ExitTimeOut': 5, 'StandardInPath': '/dev/null',
        'StandardOutPath': LOGS + '/' + role + '.stdout.log',
        'StandardErrorPath': LOGS + '/' + role + '.stderr.log',
    }
    return Path('/Library/LaunchDaemons') / (label + '.plist'), plistlib.dumps(document, sort_keys=True), 0o644


def source_payloads(source, ref, python):
    """Owner-side build from exact tracked objects, not owner-generated defaults."""
    if os.geteuid() == 0 or not re.fullmatch('[a-f0-9]{40}', ref):
        raise ValueError('native-source-owner-build-required')
    if ref not in SOURCE_AUTHORITIES:
        raise ValueError('native-source-ref-unqualified')
    source = Path(source).resolve(strict=True)
    inputs = {}
    for relative in SOURCE_INPUTS:
        result = subprocess.run(['git', '-C', str(source), 'cat-file', 'blob', ref + ':' + relative],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15, check=True)
        if not 0 < len(result.stdout) <= LIMIT:
            raise ValueError('native-source-input-invalid')
        inputs[relative] = result.stdout
    if {name: sha(body) for name, body in inputs.items()} != SOURCE_AUTHORITIES[ref]['inputs']:
        raise ValueError('native-source-input-unqualified')
    from pixel_connections.custody import parse_env, env_text
    defaults = dict(parse_env('\n'.join(line for line in inputs['.env.example'].decode().splitlines()
        if line and not line.startswith('#'))))
    paths = dict(zip(ENV_KEYS[:5], (STATE + '/private/google-token.json', STATE + '/private/oauth',
        STATE + '/projection', STATE + '/proposals', STATE + '/results')))
    fixed = {**paths, 'PIXEL_SOURCE_CALENDAR_ID': 'primary', 'PIXEL_SOURCE_TIME_ZONE': 'America/New_York',
        'PYTHONDONTWRITEBYTECODE': '1'}
    values = [(key, fixed[key] if key in fixed else defaults[key]) for key in ENV_KEYS]
    payloads = {'broker.py': inputs[SOURCE_INPUTS[0]], 'source-broker.env': env_text(values, {}).encode(),
        **profiles(python)}
    manifest = {'schemaVersion': 1, 'kind': 'ods-native-source-component', 'sourceRef': ref,
        'hostPython': _path(python), 'inputs': {key: sha(value) for key, value in inputs.items()},
        'files': {key: sha(value) for key, value in payloads.items()}}
    result = {'component.json': encoded(manifest), **payloads}
    validate_payloads(result, ref=ref, python=python)
    return result


def validate_payloads(files, *, ref, python):
    if type(ref) is not str or ref not in SOURCE_AUTHORITIES:
        raise ValueError('native-source-ref-unqualified')
    authority = SOURCE_AUTHORITIES[ref]
    if (type(files) is not dict or set(files) != set(FILES)
            or any(type(value) is not bytes or not 0 < len(value) <= LIMIT for value in files.values())):
        raise ValueError('native-source-component-incomplete')
    try:
        manifest = json.loads(files['component.json'], object_pairs_hook=_pairs)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError('native-source-component-invalid') from None
    if (type(manifest) is not dict or set(manifest) != {'schemaVersion', 'kind', 'sourceRef', 'hostPython', 'inputs', 'files'}
            or type(manifest['schemaVersion']) is not int or manifest['schemaVersion'] != 1
            or manifest['kind'] != 'ods-native-source-component' or manifest['sourceRef'] != ref
            or manifest['hostPython'] != _path(python) or type(manifest['inputs']) is not dict
            or set(manifest['inputs']) != set(SOURCE_INPUTS) or type(manifest['files']) is not dict
            or set(manifest['files']) != set(PAYLOADS)
            or any(type(value) is not str or not HEX.fullmatch(value) for value in manifest['inputs'].values())
            or manifest['inputs'][SOURCE_INPUTS[0]] != sha(files['broker.py'])
            or manifest['inputs'] != authority['inputs']
            or sha(files['source-broker.env']) != authority['templateSha256']
            or manifest['files'] != {key: sha(files[key]) for key in PAYLOADS}
            or any(files[key] != value for key, value in profiles(python).items())):
        raise ValueError('native-source-component-changed')
    from pixel_connections.custody import parse_env
    lines = parse_env(files['source-broker.env'].decode())
    if tuple(key for key, _ in lines) != ENV_KEYS:
        raise ValueError('native-source-template-invalid')
    return manifest


def bundle_payloads(root, *, ref, python, protected=False):
    """The caller first verifies the enclosing exact bundle digest/custody."""
    directory = Path(root) / COMPONENT
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if set(os.listdir(fd)) != set(FILES):
            raise ValueError('native-source-component-incomplete')
        no_acl = None
        if protected:
            from pixel_macos_custody import _require_no_acl
            no_acl = _require_no_acl
        owner = 0 if protected else os.getuid()
        result = {name: _read_at(fd, name, uid=owner, gid=0 if protected else os.getgid(),
            mode=0o755 if name == 'broker.py' else 0o644,
            no_acl=no_acl)[0] for name in FILES}
    finally:
        os.close(fd)
    validate_payloads(result, ref=ref, python=python)
    return result


def runtime_files(bundle_root, bundle_digest, launcher_body, python, *, previous_policy=None):
    """Predict exact fixed Root files from a verified bundle, without publishing.

    The existing installer publishes its bundle/launcher first, these metadata
    files last, then reopens RuntimeCustody.inspect. No request selects paths.
    """
    spec_path = Path(__file__).with_name('pixel-runtime-bundle.py')
    import importlib.util
    spec = importlib.util.spec_from_file_location('source_runtime_bundle', spec_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bundle_root = Path(bundle_root)
    manifest, checksum = module.verify(bundle_root, expected_digest=bundle_digest)
    selected = module._read_release_selection(bundle_root, manifest['entries'])
    component = {}
    for name in FILES:
        relative = COMPONENT + '/' + name
        with os.fdopen(module._open_file(bundle_root, relative), 'rb') as stream:
            component[name] = stream.read(LIMIT + 1)
        if sha(component[name]) != manifest['entries'][relative][3]:
            raise ValueError('native-source-component-changed')
    validate_payloads(component, ref=selected['pixelSourceRevision'], python=python)
    destination = RUNTIME_ROOT / checksum
    launcher = PROGRAM_ROOT / 'openclaw-gateway-launcher'
    node, runtime = destination / 'node', destination / 'runtime'
    if any(not re.fullmatch(r'/[A-Za-z0-9_./-]+', str(value)) for value in (node, runtime)):
        raise ValueError('native-source-launcher-path-invalid')
    expected = ('#!/bin/sh\nexec /usr/bin/env -u NODE_OPTIONS -u NODE_PATH '
        + str(node) + ' ' + str(runtime) + '/openclaw.mjs "$@"\n').encode()
    if launcher_body != expected:
        raise ValueError('native-source-launcher-changed')
    from pixel_provider.runtime_custody import _regular
    from pixel_provider.managed_deployment import required_policy
    python_record = _regular(Path(python))
    entries = manifest['entries']
    current = {'runtime': {key[len('runtime/'):]: value for key, value in entries.items() if key.startswith('runtime/')},
        'source': {key[len('ods-source/'):]: value for key, value in entries.items() if key.startswith('ods-source/')},
        'node': entries['node'], 'launcher': ['file', 0o755, len(expected), sha(expected)],
        'hostPython': python_record}
    state = ROOT_META
    body = encoded(current)
    descriptor = {'schemaVersion': 1, 'runtimeRoot': str(runtime), 'sourceRoot': str(destination / 'ods-source'),
        'node': str(node), 'launcher': str(launcher), 'hostPython': _path(python),
        'manifest': str(RUNTIME_MANIFEST), 'manifestSha256': sha(body),
        'policy': required_policy(previous_policy)}
    desc_body = encoded(descriptor)
    receipt = encoded({'schemaVersion': 1, 'kind': 'ods-native-source-runtime-publication',
        'bundleSha256': checksum, 'sourceRef': selected['pixelSourceRevision'],
        'odsSourceCommit': selected['odsSource']['commit'], 'componentSha256': sha(component['component.json']),
        'manifestSha256': sha(body), 'descriptorSha256': sha(desc_body), 'launcherSha256': sha(expected)})
    # Return descriptor LAST. A matching operator file with no receipt is not adopted.
    return [(RUNTIME_MANIFEST, body, 0o644),
        (state / 'source-runtime-installation.json', receipt, 0o600),
        (DESCRIPTOR, desc_body, 0o644)]


def publish_runtime_metadata(bundle_root, bundle_digest, launcher_body, python, *, installation,
                             rollback=False, verify=False):
    """Initial static metadata only, under the existing Root install intent.

    Journal every new inode before writing. Publish the descriptor last and
    retain exact interrupted/rolled-back objects. There is no active-runtime
    rollback or different-deployment migration in this first inert slice.
    """
    from pixel_macos_custody import protected_directory, protected_tree_metadata, _require_no_acl
    if (sys.platform != 'darwin' or os.geteuid() != 0 or type(rollback) is not bool or type(verify) is not bool
            or rollback and verify
            or type(installation) is not dict or set(installation) != {'source', 'owner', 'operation'}
            or type(installation['source']) is not str or type(installation['owner']) is not int
            or installation['owner'] <= 0 or installation['operation'] != 'initial-install'
            or Path(bundle_root) != RUNTIME_ROOT / bundle_digest):
        raise ValueError('native-source-initial-runtime-publication-required')
    protected_tree_metadata(Path(bundle_root))
    files = runtime_files(bundle_root, bundle_digest, launcher_body, python)
    paths = [path for path, _body, _mode in files]
    if paths != [RUNTIME_MANIFEST, ROOT_META / 'source-runtime-installation.json', DESCRIPTOR]:
        raise ValueError('native-source-runtime-file-contract-changed')
    opened = []
    try:
        for parent in dict.fromkeys(path.parent for path in paths):
            context = protected_directory(parent)
            opened.append((parent, context, context.__enter__()))
        directories = {path: fd for path, _context, fd in opened}
        metadata = directories[ROOT_META]
        record_name = 'source-runtime-publication.json'
        def read(directory, name, *, mode):
            return _read_at(directory, name, mode=mode, limit=32 * 1024 * 1024, no_acl=_require_no_acl)[0]
        def read_record():
            try:
                return _read_at(metadata, record_name, mode=0o600, limit=16384, no_acl=_require_no_acl)[0]
            except FileNotFoundError:
                return None
        def require_installation():
            current = json.loads(_read_at(metadata, 'installation.json', mode=0o600, limit=8192,
                                         no_acl=_require_no_acl)[0], object_pairs_hook=_pairs)
            if (type(current) is not dict or set(current) != {'schemaVersion', 'phase', *installation}
                    or type(current['schemaVersion']) is not int or current['schemaVersion'] != 1
                    or current['phase'] not in (('active',) if verify else ('staging', 'staging-failed'))
                    or any(type(current[key]) is not type(value) or current[key] != value
                           for key, value in installation.items())):
                raise ValueError('native-source-runtime-installation-changed')
        require_installation()
        previous = read_record()
        contract = {'schemaVersion': 1, 'kind': 'ods-native-source-runtime-publication-intent',
            'installation': installation, 'bundleSha256': bundle_digest,
            'files': {str(path): {'sha256': sha(body), 'mode': mode} for path, body, mode in files},
            'parents': {str(parent): [os.fstat(fd).st_dev, os.fstat(fd).st_ino]
                        for parent, fd in directories.items()}}
        if previous is None:
            if rollback or verify or any(_at_inode(directories[path.parent], path.name) is not None for path in paths):
                raise ValueError('native-source-runtime-prior-state-unbound')
            record = {**contract, 'nonce': uuid.uuid4().hex, 'phase': 'staging',
                'children': {str(path): {'inode': None, 'ready': False} for path in paths}}
        else:
            record = json.loads(previous, object_pairs_hook=_pairs)
        def validate():
            if (type(record) is not dict or set(record) != {*contract, 'nonce', 'phase', 'children'}
                    or any(type(record[key]) is not type(value) or record[key] != value for key, value in contract.items())
                    or type(record['nonce']) is not str or not re.fullmatch('[a-f0-9]{32}', record['nonce'])
                    or record['phase'] not in ('staging', 'publishing', 'published-no-jobs', 'restoring', 'restored')
                    or type(record['children']) is not dict or set(record['children']) != set(contract['files'])):
                raise ValueError('native-source-runtime-publication-record-changed')
            for child in record['children'].values():
                if (type(child) is not dict or set(child) != {'inode', 'ready'} or type(child['ready']) is not bool
                        or child['inode'] is not None and not _inode(child['inode'])
                        or child['ready'] and child['inode'] is None):
                    raise ValueError('native-source-runtime-publication-record-changed')
            if record['phase'] in ('publishing', 'published-no-jobs') and any(
                    not child['ready'] for child in record['children'].values()):
                raise ValueError('native-source-runtime-publication-record-changed')
        validate()
        if verify and record['phase'] != 'published-no-jobs':
            raise ValueError('native-source-runtime-publication-incomplete')
        def save():
            nonlocal previous
            validate()
            require_installation()
            if read_record() != previous:
                raise ValueError('native-source-runtime-publication-record-changed')
            temporary = '.runtime-intent-' + uuid.uuid4().hex
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=metadata)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    info = os.fstat(stream.fileno())
                    _require_no_acl(stream.fileno())
                    if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode), info.st_nlink) != (0, 0, 0o600, 1):
                        raise ValueError('native-source-runtime-publication-record-unsafe')
                    stream.write(encoded(record))
                    stream.flush()
                    os.fsync(stream.fileno())
                if read_record() != previous:
                    raise ValueError('native-source-runtime-publication-record-changed')
                if previous is None:
                    os.link(temporary, record_name, src_dir_fd=metadata, dst_dir_fd=metadata, follow_symlinks=False)
                else:
                    os.replace(temporary, record_name, src_dir_fd=metadata, dst_dir_fd=metadata)
                os.fsync(metadata)
                previous = encoded(record)
            finally:
                try:
                    os.unlink(temporary, dir_fd=metadata)
                except FileNotFoundError:
                    pass
        if previous is None:
            save()
        if record['phase'] == 'restored':
            if not rollback:
                raise ValueError('native-source-runtime-restored-intent-retained')
            return 'restored'
        for path, body, mode in files:
            directory = directories[path.parent]
            child = record['children'][str(path)]
            temporary = '.source-runtime-' + record['nonce'] + '-' + path.name
            final = _at_inode(directory, path.name)
            staged = _at_inode(directory, temporary)
            if final is not None and (final != child['inode'] or not child['ready'] or staged is not None):
                raise ValueError('native-source-runtime-postimage-changed')
            if staged is not None and staged != child['inode']:
                raise ValueError('native-source-runtime-staging-changed')
            if staged is not None:
                current_mode = stat.S_IMODE(os.stat(temporary, dir_fd=directory, follow_symlinks=False).st_mode)
                if current_mode not in (0o600, mode):
                    raise ValueError('native-source-runtime-staging-unsafe')
                existing = read(directory, temporary, mode=current_mode)
                if (existing != body) if child['ready'] else (not body.startswith(existing)):
                    raise ValueError('native-source-runtime-staging-changed')
            if final is not None:
                if read(directory, path.name, mode=mode) != body:
                    raise ValueError('native-source-runtime-postimage-changed')
            elif child['inode'] is not None and staged is None:
                raise ValueError('native-source-runtime-recorded-inode-missing')
            if verify and (final != child['inode'] or staged is not None):
                raise ValueError('native-source-runtime-publication-incomplete')
        if verify:
            return 'published-no-jobs'
        if rollback:
            record['phase'] = 'restoring'
            save()
            for path, body, mode in reversed(files):
                directory = directories[path.parent]
                temporary = '.source-runtime-' + record['nonce'] + '-' + path.name
                child = record['children'][str(path)]
                if _at_inode(directory, path.name) is not None:
                    if read(directory, path.name, mode=mode) != body:
                        raise ValueError('native-source-runtime-postimage-changed')
                    os.rename(path.name, temporary, src_dir_fd=directory, dst_dir_fd=directory)
                    os.fsync(directory)
                if _at_inode(directory, path.name) is not None:
                    raise ValueError('native-source-runtime-prior-absence-changed')
                if _at_inode(directory, temporary) != child['inode']:
                    raise ValueError('native-source-runtime-staging-changed')
            record['phase'] = 'restored'
            save()
            return 'restored'
        if record['phase'] == 'restoring':
            raise ValueError('native-source-runtime-rollback-requires-resume')
        if record['phase'] == 'staging':
            for path, body, mode in files:
                directory = directories[path.parent]
                temporary = '.source-runtime-' + record['nonce'] + '-' + path.name
                child = record['children'][str(path)]
                flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
                if child['inode'] is None:
                    flags |= os.O_CREAT | os.O_EXCL
                fd = os.open(temporary, flags, 0o600, dir_fd=directory)
                with os.fdopen(fd, 'r+b') as stream:
                    info = os.fstat(stream.fileno())
                    _require_no_acl(stream.fileno())
                    if (not stat.S_ISREG(info.st_mode) or (info.st_uid, info.st_gid) != (0, 0)
                            or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) not in (0o600, mode)
                            or info.st_size > len(body)):
                        raise ValueError('native-source-runtime-staging-unsafe')
                    identity = [info.st_dev, info.st_ino]
                    if child['inode'] is None:
                        child['inode'] = identity
                        save()
                    elif child['inode'] != identity:
                        raise ValueError('native-source-runtime-staging-changed')
                    existing = stream.read(len(body) + 1)
                    if (existing != body) if child['ready'] else (not body.startswith(existing)):
                        raise ValueError('native-source-runtime-staging-changed')
                    if not child['ready']:
                        stream.seek(0, os.SEEK_END)
                        stream.write(body[len(existing):])
                        stream.flush()
                        os.fsync(stream.fileno())
                        os.fchmod(stream.fileno(), mode)
                        os.fsync(stream.fileno())
                        child['ready'] = True
                        save()
                os.fsync(directory)
            record['phase'] = 'publishing'
            save()
        for path, body, mode in files:  # Descriptor is the last fixed path.
            directory = directories[path.parent]
            temporary = '.source-runtime-' + record['nonce'] + '-' + path.name
            if _at_inode(directory, path.name) is None:
                if read(directory, temporary, mode=mode) != body:
                    raise ValueError('native-source-runtime-staging-changed')
                os.rename(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory)
                os.fsync(directory)
            if read(directory, path.name, mode=mode) != body:
                raise ValueError('native-source-runtime-postimage-changed')
        record['phase'] = 'published-no-jobs'
        save()
        return 'published-no-jobs'
    finally:
        for _path, context, _fd in reversed(opened):
            context.__exit__(None, None, None)


def _source_authority(selection):
    from pixel_connections import custody
    return custody, custody._native_selected(selection, _publication=True)


def _no_actors(selection, custody, metadata):
    """Read-only proof for inert B3.1. A loaded job is not silently stopped."""
    if os.path.lexists(custody.NATIVE_META / 'native-source-jobs'):
        raise ValueError('native-source-job-state-requires-lifecycle')
    raw = selection.bridge.command(['/bin/launchctl', 'print', 'system'], timeout=10)
    if not raw.startswith('system = {') or not raw.rstrip().endswith('}') or len(raw) > 1024 * 1024:
        raise ValueError('native-source-job-observation-unavailable')
    if custody.NATIVE_LABEL in raw:
        raise ValueError('native-source-loaded-job-preserved')
    identity, _body = custody._native_json_at(metadata, 'source-identity.json')
    if identity is not None:
        identity = custody._native_identity_intent(identity)
        processes = selection.bridge.command(['/bin/ps', '-axo', 'pid=,uid='], timeout=10)
        if len(processes) > 1024 * 1024:
            raise ValueError('native-source-process-observation-unavailable')
        for line in processes.splitlines():
            match = re.fullmatch(r'\s*([1-9][0-9]*)\s+([0-9]+)\s*', line)
            if not match:
                raise ValueError('native-source-process-observation-unavailable')
            if int(match[2]) == identity['id']:
                raise ValueError('native-source-live-process-preserved')
    boot = selection.bridge.command(['/usr/sbin/sysctl', '-n', 'kern.bootsessionuuid'], timeout=10).strip()
    try:
        boot = str(uuid.UUID(boot)).upper()
    except ValueError:
        raise ValueError('native-source-boot-observation-unavailable') from None
    return {'kind': 'never-started-observed', 'boot': boot, 'loadedSourceJobs': 0,
            'brokerProcesses': 0, 'brokerUid': identity['id'] if identity else None}


def _material(selection):
    custody, doc = _source_authority(selection)
    import importlib.util
    spec = importlib.util.spec_from_file_location('publication_bundle', Path(__file__).with_name('pixel-runtime-bundle.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = Path(doc['nodePath']).parent
    manifest, _checksum = module.verify(root, expected_digest=doc['bundleSha256'])
    selected = module._read_release_selection(root, manifest['entries'])
    files = bundle_payloads(root, ref=selected['pixelSourceRevision'], python=doc['hostPythonPath'], protected=True)
    return custody, doc, files, selected['pixelSourceRevision']


def _kit_image(custody, parent, name, *, expected=None, mode=0o755):
    try:
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    except FileNotFoundError:
        if expected is not None:
            raise ValueError('native-source-kit-image-missing') from None
        return None, None
    try:
        info = os.fstat(fd)
        custody._native_no_acl(fd)
        if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, mode):
            raise ValueError('native-source-kit-directory-unsafe')
        names = set(os.listdir(fd))
        if names not in ({'source-kit.json', 'broker.py', 'source-broker.env'}, {'source-kit.json', *PAYLOADS}):
            raise ValueError('native-source-kit-file-set-invalid')
        bodies, identities = {}, {}
        for leaf in sorted(names):
            bodies[leaf], identities[leaf] = _read_at(fd, leaf,
                mode=0o755 if leaf == 'broker.py' else 0o644, no_acl=custody._native_no_acl)
        image = {'inode': [info.st_dev, info.st_ino], 'files': {
            leaf: {'inode': identities[leaf], 'sha256': sha(bodies[leaf])} for leaf in sorted(names)}}
        if expected is not None and image != expected:
            raise ValueError('native-source-kit-image-changed')
        return image, bodies
    finally:
        os.close(fd)


def _binding_images(selection, custody, metadata, old_sha, new_sha):
    """Only the same deployment/identity, complete A prefix. No token read."""
    names = ('source-custody-prepare.json', 'source-environment.json', 'source-logs-prepare.json')
    values = {name: custody._native_json_at(metadata, name)[0] for name in names}
    if not os.path.lexists(custody.NATIVE_STATE):
        if any(value is not None for value in values.values()):
            raise ValueError('native-source-preparation-incomplete')
        return {}, None
    gate = os.open(custody.NATIVE_STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        identity, binding = custody._native_environment_context(selection, metadata, gate, old_sha, _publication=True)
        if values[names[1]] is not None:
            custody._native_environment_record(values[names[1]], binding)
            if values[names[1]]['pending'] is not None:
                raise ValueError('native-source-environment-publication-pending')
            actual = custody._native_environment_snapshot(gate, custody.NATIVE_ENV.name, identity['id'])
            if actual != values[names[1]]['current']:
                raise ValueError('native-source-environment-journal-changed')
        elif os.path.lexists(custody.NATIVE_ENV):
            raise ValueError('native-source-environment-unbound')
        if values[names[2]] is not None:
            _identity, log_binding = custody._native_log_binding(selection, metadata, old_sha, _publication=True)
            custody._native_log_record(values[names[2]], log_binding)
            directory = os.open(custody.NATIVE_LOGS, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                if custody.native_verify_log_directory(directory, identity['id']) != values[names[2]]['directory']:
                    raise ValueError('native-source-log-intent-changed')
                custody._native_log_files(directory, values[names[2]], identity)
            finally:
                os.close(directory)
        images = {}
        for name, before in values.items():
            if before is None:
                continue
            after = dict(before)
            if after.get('kitSha256') != old_sha:
                raise ValueError('native-source-binding-changed')
            after['kitSha256'] = new_sha
            info = os.stat(name, dir_fd=metadata, follow_symlinks=False)
            images[name] = {'before': before, 'after': after, 'mode': 0o600, 'gid': 0,
                'beforeInode': [info.st_dev, info.st_ino], 'afterInode': None, 'ready': False}
        fd = os.open('custody.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=gate)
        with os.fdopen(fd, 'rb') as stream:
            before = json.loads(stream.read(16385), object_pairs_hook=_pairs)
        after = dict(before)
        if after.get('kitSha256') != old_sha:
            raise ValueError('native-source-binding-changed')
        after['kitSha256'] = new_sha
        info = os.stat('custody.json', dir_fd=gate, follow_symlinks=False)
        images['custody.json'] = {'before': before, 'after': after, 'mode': 0o640, 'gid': identity['id'],
            'beforeInode': [info.st_dev, info.st_ino], 'afterInode': None, 'ready': False}
        return images, identity
    finally:
        os.close(gate)


def _publication_record(value, doc, files):
    keys = {'schemaVersion', 'kind', 'deploymentSha256', 'sourceRef', 'nonce', 'phase', 'old', 'new',
            'candidate', 'backup', 'candidateInode', 'stagedFiles', 'bindings', 'witness'}
    if (type(value) is not dict or set(value) != keys or type(value['schemaVersion']) is not int
            or value['schemaVersion'] != 1 or value['kind'] != 'ods-native-source-publication'
            or value['deploymentSha256'] != doc or not re.fullmatch('[a-f0-9]{32}', str(value['nonce']))
            or value['candidate'] != '.source-kit-' + value['nonce']
            or value['backup'] != '.source-kit-before-' + value['nonce']
            or value['phase'] not in ('staging', 'publishing-kit', 'kit-promoted', 'binding', 'prepared-no-jobs',
                                     'rollback-kit', 'rollback-bindings', 'restored')
            or value['new'] != {name: sha(body) for name, body in files.items()}
            or type(value['stagedFiles']) is not dict or set(value['stagedFiles']) != set(files)):
        raise ValueError('native-source-publication-record-changed')
    for record in value['stagedFiles'].values():
        if (type(record) is not dict or set(record) != {'inode', 'ready'} or type(record['ready']) is not bool
                or record['inode'] is not None and not _inode(record['inode'])):
            raise ValueError('native-source-publication-record-changed')
    if value['candidateInode'] is not None and not _inode(value['candidateInode']):
        raise ValueError('native-source-publication-record-changed')
    if value['phase'] in ('publishing-kit', 'kit-promoted', 'binding', 'prepared-no-jobs') and (
            value['candidateInode'] is None or any(not item['ready'] or item['inode'] is None
                                                  for item in value['stagedFiles'].values())):
        raise ValueError('native-source-publication-record-changed')
    if (value['sourceRef'] != json.loads(files['source-kit.json'])['sourceRef']
            or type(value['bindings']) is not dict or set(value['bindings']) - {
                'custody.json', 'source-custody-prepare.json', 'source-environment.json', 'source-logs-prepare.json'}):
        raise ValueError('native-source-publication-record-changed')
    for name, image in value['bindings'].items():
        if (type(image) is not dict or set(image) != {'before', 'after', 'mode', 'gid', 'beforeInode', 'afterInode', 'ready'}
                or not _inode(image['beforeInode']) or image['afterInode'] is not None and not _inode(image['afterInode'])
                or type(image['ready']) is not bool or image['ready'] and image['afterInode'] is None
                or type(image['before']) is not dict or type(image['after']) is not dict
                or set(image['before']) != set(image['after'])
                or 'kitSha256' not in image['before']
                or any(image['before'][key] != image['after'][key] for key in image['before'] if key != 'kitSha256')
                or image['after']['kitSha256'] != sha(files['source-kit.json'])
                or type(image['gid']) is not int or image['gid'] < 0
                or image['mode'] != (0o640 if name == 'custody.json' else 0o600)
                or name != 'custody.json' and image['gid'] != 0):
            raise ValueError('native-source-publication-record-changed')
    old = value['old']
    if old is not None:
        if (type(old) is not dict or set(old) != {'inode', 'files'} or not _inode(old['inode'])
                or type(old['files']) is not dict or set(old['files']) not in (
                    {'source-kit.json', 'broker.py', 'source-broker.env'}, {'source-kit.json', *PAYLOADS})):
            raise ValueError('native-source-publication-record-changed')
        for item in old['files'].values():
            if (type(item) is not dict or set(item) != {'inode', 'sha256'} or not _inode(item['inode'])
                    or type(item['sha256']) is not str or not HEX.fullmatch(item['sha256'])):
                raise ValueError('native-source-publication-record-changed')
        if any(image['before']['kitSha256'] != old['files']['source-kit.json']['sha256']
               for image in value['bindings'].values()):
            raise ValueError('native-source-publication-record-changed')
    witness = value['witness']
    if (type(witness) is not dict or set(witness) != {'kind', 'boot', 'loadedSourceJobs', 'brokerProcesses', 'brokerUid'}
            or witness['kind'] != 'never-started-observed' or witness['loadedSourceJobs'] != 0
            or witness['brokerProcesses'] != 0 or type(witness['boot']) is not str
            or not re.fullmatch('[A-F0-9-]{36}', witness['boot'])
            or witness['brokerUid'] is not None and (type(witness['brokerUid']) is not int or witness['brokerUid'] <= 0)):
        raise ValueError('native-source-publication-record-changed')


def _inode(value):
    return type(value) is list and len(value) == 2 and all(type(v) is int for v in value) and value[0] >= 0 and value[1] > 0


def _publication_files(component, ref, checksum, retained=None):
    bodies = {name: component[name] for name in PAYLOADS}
    if retained is not None:
        if retained['broker.py'] != bodies['broker.py']:
            raise ValueError('native-source-old-broker-not-selected-source')
        if retained['source-broker.env'] != bodies['source-broker.env']:
            # No new public traversal/publication of private onboarding/query
            # text. A later explicit migration may preserve private overrides;
            # this inert slice neither normalizes nor resets the old template.
            raise ValueError('native-source-private-template-migration-required')
        bodies['source-broker.env'] = retained['source-broker.env']
    manifest = {'schemaVersion': 2, 'kind': 'ods-native-source-kit', 'sourceRef': ref,
        'deploymentSha256': checksum, 'files': {name: sha(body) for name, body in bodies.items()}}
    return {'source-kit.json': encoded(manifest), **bodies}


def _at_inode(directory, name):
    try:
        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return None
    return [info.st_dev, info.st_ino]


def _stage_kit(selection, custody, metadata, parent, record, files, save):
    name = record['candidate']
    if record['candidateInode'] is None:
        os.mkdir(name, 0o700, dir_fd=parent)  # Unknown prior object refuses.
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, 0o700):
                raise ValueError('native-source-candidate-unsafe')
            custody._native_no_acl(fd)
            record['candidateInode'] = [info.st_dev, info.st_ino]
            save()
        finally:
            os.close(fd)
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        info = os.fstat(fd)
        custody._native_no_acl(fd)
        if ([info.st_dev, info.st_ino] != record['candidateInode'] or (info.st_uid, info.st_gid) != (0, 0)
                or stat.S_IMODE(info.st_mode) not in (0o700, 0o755) or set(os.listdir(fd)) - set(files)):
            raise ValueError('native-source-candidate-changed')
        for leaf, body in files.items():
            item = record['stagedFiles'][leaf]
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
            if item['inode'] is None:
                flags |= os.O_CREAT | os.O_EXCL
            output = os.open(leaf, flags, 0o600, dir_fd=fd)
            with os.fdopen(output, 'r+b') as stream:
                before = os.fstat(stream.fileno())
                custody._native_no_acl(stream.fileno())
                final_mode = 0o755 if leaf == 'broker.py' else 0o644
                if (not stat.S_ISREG(before.st_mode) or (before.st_uid, before.st_gid) != (0, 0)
                        or before.st_nlink != 1 or before.st_size > len(body)
                        or stat.S_IMODE(before.st_mode) not in ((final_mode,) if item['ready'] else (0o600, final_mode))):
                    raise ValueError('native-source-candidate-file-unsafe')
                inode = [before.st_dev, before.st_ino]
                if item['inode'] is None:
                    item['inode'] = inode
                    save()  # Before first byte or permission publication.
                elif item['inode'] != inode:
                    raise ValueError('native-source-candidate-file-changed')
                prefix = stream.read(len(body) + 1)
                if (prefix != body) if item['ready'] else (not body.startswith(prefix)):
                    raise ValueError('native-source-candidate-file-changed')
                if not item['ready']:
                    custody._native_selected(selection, _publication=True)
                    _no_actors(selection, custody, metadata)
                    stream.seek(0, os.SEEK_END)
                    stream.write(body[len(prefix):])
                    stream.flush()
                    os.fsync(stream.fileno())
                    os.fchmod(stream.fileno(), final_mode)
                    os.fsync(stream.fileno())
                    item['ready'] = True
                    save()
        if set(os.listdir(fd)) != set(files):
            raise ValueError('native-source-candidate-file-set-changed')
        os.fchmod(fd, 0o755)
        os.fsync(fd)
        os.fsync(parent)
    finally:
        os.close(fd)
    return {'inode': record['candidateInode'], 'files': {leaf: {
        'inode': record['stagedFiles'][leaf]['inode'], 'sha256': sha(body)} for leaf, body in files.items()}}


def _binding_directory(custody, metadata, name):
    if name == 'custody.json':
        fd = os.open(custody.NATIVE_STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        return fd, True
    return metadata, False


def _binding_current(custody, directory, name, image):
    try:
        body, inode = _read_at(directory, name, gid=image['gid'], mode=image['mode'],
                              limit=16384, no_acl=custody._native_no_acl)
    except FileNotFoundError:
        return None
    value = json.loads(body, object_pairs_hook=_pairs)
    if value == image['before'] and inode == image['beforeInode']:
        return 'before'
    if value == image['after'] and inode == image['afterInode']:
        return 'after'
    raise ValueError('native-source-binding-image-changed')


def _apply_bindings(selection, custody, metadata, record, save, *, rollback=False):
    for index, (name, image) in enumerate(sorted(record['bindings'].items())):
        directory, close = _binding_directory(custody, metadata, name)
        candidate = '.source-binding-' + record['nonce'] + '-' + str(index)
        backup = '.source-binding-before-' + record['nonce'] + '-' + str(index)
        try:
            if os.fstat(directory).st_dev != os.fstat(metadata).st_dev:
                raise ValueError('native-source-binding-filesystem-changed')
            current = _binding_current(custody, directory, name, image)
            if rollback:
                if current == 'before':
                    continue
                if current == 'after':
                    if _at_inode(metadata, candidate) is not None:
                        raise ValueError('native-source-binding-candidate-conflict')
                    os.rename(name, candidate, src_dir_fd=directory, dst_dir_fd=metadata)
                    os.fsync(directory)
                    os.fsync(metadata)
                elif current is not None:
                    raise ValueError('native-source-binding-image-changed')
                data, inode = _read_at(metadata, backup, gid=image['gid'], mode=image['mode'],
                    limit=16384, no_acl=custody._native_no_acl)
                if inode != image['beforeInode'] or json.loads(data, object_pairs_hook=_pairs) != image['before']:
                    raise ValueError('native-source-binding-backup-changed')
                os.rename(backup, name, src_dir_fd=metadata, dst_dir_fd=directory)
                os.fsync(directory)
                os.fsync(metadata)
                if _binding_current(custody, directory, name, image) != 'before':
                    raise ValueError('native-source-binding-restore-unconfirmed')
                continue
            if current == 'after':
                continue
            body = encoded(image['after'])
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
            if image['afterInode'] is None:
                flags |= os.O_CREAT | os.O_EXCL
            fd = os.open(candidate, flags, 0o600, dir_fd=metadata)
            with os.fdopen(fd, 'r+b') as stream:
                info = os.fstat(stream.fileno())
                custody._native_no_acl(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                        or info.st_gid not in (0, image['gid']) or info.st_size > len(body)
                        or stat.S_IMODE(info.st_mode) not in (0o600, image['mode'])):
                    raise ValueError('native-source-binding-candidate-unsafe')
                inode = [info.st_dev, info.st_ino]
                if image['afterInode'] is None:
                    image['afterInode'] = inode
                    save()
                elif image['afterInode'] != inode:
                    raise ValueError('native-source-binding-candidate-changed')
                prefix = stream.read(len(body) + 1)
                if not body.startswith(prefix) or image['ready'] and prefix != body:
                    raise ValueError('native-source-binding-candidate-changed')
                if not image['ready']:
                    stream.seek(0, os.SEEK_END)
                    stream.write(body[len(prefix):])
                    stream.flush()
                    os.fsync(stream.fileno())
                    os.fchown(stream.fileno(), 0, image['gid'])
                    os.fchmod(stream.fileno(), image['mode'])
                    os.fsync(stream.fileno())
                    image['ready'] = True
                    save()
            custody._native_selected(selection, _publication=True)
            _no_actors(selection, custody, metadata)
            if current == 'before':
                if _at_inode(metadata, backup) is not None:
                    raise ValueError('native-source-binding-backup-conflict')
                os.rename(name, backup, src_dir_fd=directory, dst_dir_fd=metadata)
                os.fsync(directory)
                os.fsync(metadata)
            elif current is None:
                old_body, old_inode = _read_at(metadata, backup, gid=image['gid'], mode=image['mode'],
                    limit=16384, no_acl=custody._native_no_acl)
                if old_inode != image['beforeInode'] or json.loads(old_body, object_pairs_hook=_pairs) != image['before']:
                    raise ValueError('native-source-binding-backup-changed')
            os.rename(candidate, name, src_dir_fd=metadata, dst_dir_fd=directory)
            os.fsync(directory)
            os.fsync(metadata)
            if _binding_current(custody, directory, name, image) != 'after':
                raise ValueError('native-source-binding-publication-unconfirmed')
        finally:
            if close:
                os.close(directory)


def verify_kit_publication(selection):
    """Read-only initial-install proof; incomplete publication cannot finalize."""
    custody, doc, component, ref = _material(selection)
    from pixel_macos_custody import protected_directory
    with protected_directory(custody.NATIVE_META) as metadata, protected_directory(custody.NATIVE_KIT.parent) as parent:
        record, _raw = custody._native_json_at(metadata, 'source-publication.json')
        files = _publication_files(component, ref, selection.deployment.checksum)
        _publication_record(record, selection.deployment.checksum, files)
        if record['phase'] != 'prepared-no-jobs' or _no_actors(selection, custody, metadata) != record['witness']:
            raise ValueError('native-source-publication-incomplete')
        expected = {'inode': record['candidateInode'], 'files': {name: {
            'inode': record['stagedFiles'][name]['inode'], 'sha256': sha(body)} for name, body in files.items()}}
        _kit_image(custody, parent, custody.NATIVE_KIT.name, expected=expected)
        custody.native_verify_kit(selection, require_profiles=True, _publication=True)
        for name, image in record['bindings'].items():
            directory, close = _binding_directory(custody, metadata, name)
            try:
                if _binding_current(custody, directory, name, image) != 'after':
                    raise ValueError('native-source-binding-publication-unconfirmed')
            finally:
                if close:
                    os.close(directory)
        if record['bindings']:
            gate = os.open(custody.NATIVE_STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                custody._native_environment_context(selection, metadata, gate, sha(files['source-kit.json']), _publication=True)
            finally:
                os.close(gate)
    return {'status': 'prepared-no-jobs', 'kitSha256': sha(files['source-kit.json'])}


def publish_kit(selection, *, rollback=False):
    """Inert same-deployment A->B publication. Never creates an account/job.

    All names are fixed or nonce-derived under the same Root journal. Previous
    metadata/kit inodes are retained, allowing exact restoration without ever
    reading or backing up a refresh token or owner proposal.
    """
    if type(rollback) is not bool:
        raise ValueError('native-source-publication-control-invalid')
    custody, doc, component, ref = _material(selection)
    from pixel_macos_custody import protected_directory
    with protected_directory(custody.NATIVE_META) as metadata, protected_directory(custody.NATIVE_KIT.parent) as parent:
        record, raw = custody._native_json_at(metadata, 'source-publication.json')
        witness = _no_actors(selection, custody, metadata)
        retained = None
        if record is None:
            if rollback:
                raise ValueError('native-source-publication-recovery-missing')
            old, retained = _kit_image(custody, parent, custody.NATIVE_KIT.name)
            if old is not None:
                current, old_sha = custody.native_verify_kit(selection, _publication=True)
                if current['sourceRef'] != ref:
                    raise ValueError('native-source-source-change-requires-upgrade')
            else:
                old_sha = None
                if os.path.lexists(custody.NATIVE_STATE):
                    raise ValueError('native-source-state-without-bound-kit')
            files = _publication_files(component, ref, selection.deployment.checksum, retained)
            if old is not None and set(retained) == set(files) and all(retained[name] == body for name, body in files.items()):
                # Still need a publication receipt; do not adopt a schema2 kit
                # of unknown origin merely because all payload hashes agree.
                raise ValueError('native-source-complete-kit-without-publication-receipt')
            images, _identity = _binding_images(selection, custody, metadata, old_sha, sha(files['source-kit.json'])) if old else ({}, None)
            nonce = uuid.uuid4().hex
            record = {'schemaVersion': 1, 'kind': 'ods-native-source-publication',
                'deploymentSha256': selection.deployment.checksum, 'sourceRef': ref, 'nonce': nonce,
                'phase': 'staging', 'old': old, 'new': {name: sha(body) for name, body in files.items()},
                'candidate': '.source-kit-' + nonce, 'backup': '.source-kit-before-' + nonce,
                'candidateInode': None, 'stagedFiles': {name: {'inode': None, 'ready': False} for name in files},
                'bindings': images, 'witness': witness}
        else:
            # Validate path grammar BEFORE opening even a Root-recorded name.
            if (type(record) is not dict or not re.fullmatch('[a-f0-9]{32}', str(record.get('nonce')))
                    or record.get('backup') != '.source-kit-before-' + record['nonce']
                    or record.get('candidate') != '.source-kit-' + record['nonce']
                    or record.get('old') is not None and (type(record['old']) is not dict
                        or set(record['old']) != {'inode', 'files'} or not _inode(record['old']['inode']))):
                raise ValueError('native-source-publication-record-changed')
            if record.get('old') is not None:
                for name in (custody.NATIVE_KIT.name, record['backup']):
                    if _at_inode(parent, name) == record['old'].get('inode'):
                        _image, retained = _kit_image(custody, parent, name, expected=record['old'])
                        break
                if retained is None:
                    raise ValueError('native-source-old-kit-image-missing')
            files = _publication_files(component, ref, selection.deployment.checksum, retained)
            _publication_record(record, selection.deployment.checksum, files)
            if record['witness'] != witness:
                raise ValueError('native-source-publication-witness-changed')
        _publication_record(record, selection.deployment.checksum, files)
        def save():
            nonlocal raw
            _publication_record(record, selection.deployment.checksum, files)
            if _no_actors(selection, custody, metadata) != record['witness']:
                raise ValueError('native-source-publication-witness-changed')
            raw = custody._native_save_at(selection, metadata, 'source-publication.json', record,
                                         previous=raw, _publication=True)
        if raw is None:
            save()
        if record['phase'] == 'restored':
            if not rollback:
                raise ValueError('native-source-restored-publication-retained-for-review')
            return {'status': 'restored', 'kitSha256': record['old']['files']['source-kit.json']['sha256'] if record['old'] else None}
        if rollback:
            record['phase'] = 'rollback-kit'
            save()
            current = _at_inode(parent, custody.NATIVE_KIT.name)
            if record['candidateInode'] is not None and current == record['candidateInode']:
                expected = {'inode': record['candidateInode'], 'files': {name: {
                    'inode': record['stagedFiles'][name]['inode'], 'sha256': sha(body)} for name, body in files.items()}}
                _kit_image(custody, parent, custody.NATIVE_KIT.name, expected=expected)
                if _at_inode(parent, record['candidate']) is not None:
                    raise ValueError('native-source-candidate-restore-conflict')
                os.rename(custody.NATIVE_KIT.name, record['candidate'], src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
                current = None
            if record['old'] is not None:
                if current is None:
                    _kit_image(custody, parent, record['backup'], expected=record['old'])
                    os.rename(record['backup'], custody.NATIVE_KIT.name, src_dir_fd=parent, dst_dir_fd=parent)
                    os.fsync(parent)
                _kit_image(custody, parent, custody.NATIVE_KIT.name, expected=record['old'])
            elif current is not None:
                raise ValueError('native-source-prior-absence-changed')
            record['phase'] = 'rollback-bindings'
            save()
            _apply_bindings(selection, custody, metadata, record, save, rollback=True)
            record['phase'] = 'restored'
            save()
            return {'status': 'restored', 'kitSha256': record['old']['files']['source-kit.json']['sha256'] if record['old'] else None}
        if record['phase'].startswith('rollback-'):
            raise ValueError('native-source-rollback-requires-explicit-resume')
        if record['phase'] == 'staging':
            expected = _stage_kit(selection, custody, metadata, parent, record, files, save)
            _kit_image(custody, parent, record['candidate'], expected=expected)
            record['phase'] = 'publishing-kit'
            save()
        expected = {'inode': record['candidateInode'], 'files': {name: {
            'inode': record['stagedFiles'][name]['inode'], 'sha256': sha(body)} for name, body in files.items()}}
        if record['phase'] == 'publishing-kit':
            current = _at_inode(parent, custody.NATIVE_KIT.name)
            if record['old'] is not None and current == record['old']['inode']:
                _kit_image(custody, parent, custody.NATIVE_KIT.name, expected=record['old'])
                if _at_inode(parent, record['backup']) is not None:
                    raise ValueError('native-source-old-kit-backup-conflict')
                os.rename(custody.NATIVE_KIT.name, record['backup'], src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
                current = None
            if current is None:
                if record['old'] is not None:
                    _kit_image(custody, parent, record['backup'], expected=record['old'])
                _kit_image(custody, parent, record['candidate'], expected=expected)
                os.rename(record['candidate'], custody.NATIVE_KIT.name, src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
            _kit_image(custody, parent, custody.NATIVE_KIT.name, expected=expected)
            record['phase'] = 'kit-promoted'
            save()
        _kit_image(custody, parent, custody.NATIVE_KIT.name, expected=expected)
        if record['phase'] in ('kit-promoted', 'binding'):
            record['phase'] = 'binding'
            save()
            _apply_bindings(selection, custody, metadata, record, save)
            custody.native_verify_kit(selection, require_profiles=True, _publication=True)
            # Reuse ordinary state/env/log verification after exact postimages.
            if record['bindings']:
                gate = os.open(custody.NATIVE_STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    custody._native_environment_context(selection, metadata, gate, sha(files['source-kit.json']), _publication=True)
                finally:
                    os.close(gate)
            record['phase'] = 'prepared-no-jobs'
            save()
        elif record['phase'] != 'prepared-no-jobs':
            raise ValueError('native-source-publication-phase-invalid')
        for name, image in record['bindings'].items():
            directory, close = _binding_directory(custody, metadata, name)
            try:
                if _binding_current(custody, directory, name, image) != 'after':
                    raise ValueError('native-source-binding-publication-unconfirmed')
            finally:
                if close: os.close(directory)
        return {'status': 'prepared-no-jobs', 'kitSha256': sha(files['source-kit.json'])}
