#!/usr/bin/env python3
"""Closed native Source payload and inert publication; never starts a job."""
import hashlib
import json
import os
from pathlib import Path
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
    "bf6db06c5b432ec9e8a408d1cac785753693099c": {
        "bundleSha256": "9e5bbd59f0934b615e23a6c2327b170045073fa59323c60228996a89625863d0",
        "tree": "350dcbc922aacd1f60ecdf0619b367eb22765e93",
        "inputs": {
            "deploy/source-broker/broker.py": "0b0c5a9727505e3b8af0196df2fc922eec42d0977c3ab48515dbee416687b948",
            "scripts/configure.mjs": "c512eb6cdc0056c03e1d07ccfaf76030e7917a5819322ba8e24e8a7ade0d728d",
            ".env.example": "fa1bf7a7e6e5863662466c5a9e77ca0fff1c38cced23d7127c31dd6a2397ac1c"
        },
        "templateSha256": "2f0c2700542d295272c70f47c52cef116101b01c7ce45a353eb0c1d93d1a39be"
    },
    "80fd49e6d6f3d54185fe2415ac6b7e003c16a12a": {
        "bundleSha256": "0e39c7fbeb48cc1c9746ea455408a33cef0a9eefe160e6cecbb1245c4a4bb6cb",
        "tree": "03cb6f868cb44c7bc4b078bcb0e5c61bd0116ef0",
        "inputs": {
            "deploy/source-broker/broker.py": "0b0c5a9727505e3b8af0196df2fc922eec42d0977c3ab48515dbee416687b948",
            "scripts/configure.mjs": "c512eb6cdc0056c03e1d07ccfaf76030e7917a5819322ba8e24e8a7ade0d728d",
            ".env.example": "fa1bf7a7e6e5863662466c5a9e77ca0fff1c38cced23d7127c31dd6a2397ac1c"
        },
        "templateSha256": "2f0c2700542d295272c70f47c52cef116101b01c7ce45a353eb0c1d93d1a39be"
    },
    '47706f6863f74ae65c83c925bd6af51395193363': {
        'bundleSha256': '8c9b197a9763c26054dc0f7d920abe11b24f6a1bfbda03abc156aa031a3a8a7c',
        'tree': 'b050fe80e486c62f5d56a4f3f12f5687aadaa0e1',
        'inputs': {
            'deploy/source-broker/broker.py': '0b0c5a9727505e3b8af0196df2fc922eec42d0977c3ab48515dbee416687b948',
            'scripts/configure.mjs': 'c512eb6cdc0056c03e1d07ccfaf76030e7917a5819322ba8e24e8a7ade0d728d',
            '.env.example': '46b2a465c153768169bf1111930a1b98bc6844c6956d377c12bac3af357afe0b',
        },
        'templateSha256': '2f0c2700542d295272c70f47c52cef116101b01c7ce45a353eb0c1d93d1a39be',
    },
    "3fe50f1d75d13295bfbe964bfcd327a91df15814": {
        "bundleSha256": "5e867c52522b14445d4ccc85974ed80a9f1237fa2c2a759359f8c978e00fa273",
        "tree": "f20e9082102d5ba1002c3452f3a29fe4c7840bf5",
        "inputs": {
            "deploy/source-broker/broker.py": "0b0c5a9727505e3b8af0196df2fc922eec42d0977c3ab48515dbee416687b948",
            "scripts/configure.mjs": "c512eb6cdc0056c03e1d07ccfaf76030e7917a5819322ba8e24e8a7ade0d728d",
            ".env.example": "46b2a465c153768169bf1111930a1b98bc6844c6956d377c12bac3af357afe0b"
        },
        "templateSha256": "2f0c2700542d295272c70f47c52cef116101b01c7ce45a353eb0c1d93d1a39be"
    },
    '8afb55abf50633616cd759146366588717534afc': {
        'bundleSha256': '988caa969858f457855e134d6e26d44ff2edb82263aeff08347d9dd8fc115b16',
        'tree': 'd5b97ff209a161781cf842badc54b88daa49b864',
        'inputs': {
            'deploy/source-broker/broker.py': '0b0c5a9727505e3b8af0196df2fc922eec42d0977c3ab48515dbee416687b948',
            'scripts/configure.mjs': 'c512eb6cdc0056c03e1d07ccfaf76030e7917a5819322ba8e24e8a7ade0d728d',
            '.env.example': '56d879bec899a4714479809462ee1c15c63a70124758fce31d59a3072c582d2c',
        },
        'templateSha256': '2f0c2700542d295272c70f47c52cef116101b01c7ce45a353eb0c1d93d1a39be',
    },
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
    '224ed399e38273db0f9d4834ceab55d493a99cf3': {
        'bundleSha256': '2fec2c091b424b74f7109c18ef55310e5e78adad75b73e973e64a4b46613cf15',
        'tree': '4a84cdfeba5b4f71ee476ffb76b0e04d8841c953',
        'inputs': {
            'deploy/source-broker/broker.py': '0b0c5a9727505e3b8af0196df2fc922eec42d0977c3ab48515dbee416687b948',
            'scripts/configure.mjs': 'c512eb6cdc0056c03e1d07ccfaf76030e7917a5819322ba8e24e8a7ade0d728d',
            '.env.example': 'c54674f297a091318db77728a088ceaa6a7c3c0815658296704ea2deb8c63ee4',
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
    'bin/pixel_connections/native_executor.py': 'bin/pixel_connections/native_executor.py',
    'bin/pixel_connections/source_backend.py': 'bin/pixel_connections/source_backend.py',
    'bin/pixel_connections/native_retirement.py': 'bin/pixel_connections/native_retirement.py',
    'bin/pixel_connection_agent_status.py': 'bin/pixel_connection_agent_status.py',
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
        '(allow file-read* (literal "/private/etc/ods/pixel-provider-runtime.json") '
        '(literal "/private/etc/ods/pixel-provider-runtime-manifest.json") '
        '(literal (param "ODS_SOURCE_JOB_PLIST")))\n'
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
    from pixel_connections.custody import native_disabled_definition
    return native_disabled_definition(role, identity=identity, python=python, identifier=identifier)


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


def _no_actors(selection, custody, metadata, *, _generation=False):
    """Read-only proof for inert B3.1. A loaded job is not silently stopped."""
    if not _generation and os.path.lexists(custody.NATIVE_META / 'native-source-jobs'):
        raise ValueError('native-source-job-state-requires-lifecycle')
    loaded = custody.native_launchd_loaded_labels(selection.bridge.command(['/bin/launchctl', 'print', 'system'], timeout=10))
    if any(label.startswith(custody.NATIVE_LABEL) for label in loaded):
        raise ValueError('native-source-loaded-job-preserved')
    identity, _body = custody._native_json_at(metadata, 'source-identity.json')
    if identity is not None:
        identity = custody._native_identity_intent(identity)
        processes = selection.bridge.command(['/bin/ps', '-axo', 'pid=,uid='], timeout=10)
        if len(processes) > 1024 * 1024:
            raise ValueError('native-source-process-observation-unavailable')
        for line in processes.splitlines():
            match = re.fullmatch(r'\s*([1-9][0-9]*)\s+(-?[0-9]+)\s*', line)
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
    from pixel_access_bridge import LaunchdAccessBridge, _native_runtime_bundle_cache
    operation = LaunchdAccessBridge._native_runtime_lease(selection.bridge)
    import inspect
    parameter = inspect.signature(module.verify).parameters.get('_operation')
    if (operation is not None and root == operation.bundle_root
            and parameter is not None and parameter.kind is inspect.Parameter.KEYWORD_ONLY
            and parameter.default is None):
        # Only the already-qualified current Root may reuse its immutable hashes.
        # Other roots/legacy helpers stay cold; verify repeats this exact binding.
        _native_runtime_bundle_cache(operation, root)
        manifest, _checksum = module.verify(root, expected_digest=doc['bundleSha256'], _operation=operation)
    else:
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


def _stage_kit(selection, custody, metadata, parent, record, files, save, *, _guard=None):
    if _guard is not None:
        _guard()
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
                    if _guard is not None:
                        _guard()
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


def _apply_bindings(selection, custody, metadata, record, save, *, rollback=False, _guard=None):
    for index, (name, image) in enumerate(sorted(record['bindings'].items())):
        if _guard is not None:
            _guard()
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
                    if _guard is not None:
                        _guard()
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
            if _guard is not None:
                _guard()
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


def _generation_prefix(custody, directory, name, body, image, *, mode=0o600, gid=0):
    """Validate retained prefixes too; rollback cannot adopt an unknown inode."""
    present = custody._native_at_inode(directory, name)
    if present is None:
        return
    if image['inode'] is None or present != image['inode']:
        raise ValueError('native-source-generation-prefix-changed')
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        custody._native_no_acl(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid not in (0, gid)
                or stat.S_IMODE(info.st_mode) not in (0o600, mode) or info.st_nlink != 1
                or [info.st_dev, info.st_ino] != image['inode'] or not 0 <= info.st_size <= len(body)):
            raise ValueError('native-source-generation-prefix-changed')
        prefix = stream.read(len(body) + 1)
        after = os.fstat(stream.fileno())
        if (not body.startswith(prefix) or image['ready'] and (prefix != body
                or (info.st_gid, stat.S_IMODE(info.st_mode)) != (gid, mode))
                or (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or custody._native_at_inode(directory, name) != image['inode']):
            raise ValueError('native-source-generation-prefix-changed')


def _generation_stage(custody, directory, name, body, image, save, guard, *, mode=0o600, gid=0):
    """Record the created FD before bytes/permissions; resume only its prefix."""
    guard()
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
    if image['inode'] is None:
        flags |= os.O_CREAT | os.O_EXCL
    fd = os.open(name, flags, 0o600, dir_fd=directory)
    with os.fdopen(fd, 'r+b') as stream:
        info = os.fstat(stream.fileno())
        custody._native_no_acl(stream.fileno())
        inode = [info.st_dev, info.st_ino]
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid not in (0, gid)
                or stat.S_IMODE(info.st_mode) not in (0o600, mode) or info.st_nlink != 1
                or not 0 <= info.st_size <= len(body)
                or custody._native_at_inode(directory, name) != inode):
            raise ValueError('native-source-generation-prefix-unsafe')
        if image['inode'] is None:
            image['inode'] = inode
            save()
        elif inode != image['inode']:
            raise ValueError('native-source-generation-prefix-changed')
        prefix = stream.read(len(body) + 1)
        if (prefix != body) if image['ready'] else (not body.startswith(prefix)):
            raise ValueError('native-source-generation-prefix-changed')
        if image['ready']:
            if (info.st_gid, stat.S_IMODE(info.st_mode)) != (gid, mode):
                raise ValueError('native-source-generation-prefix-changed')
            return
        guard()
        if custody._native_at_inode(directory, name) != inode:
            raise ValueError('native-source-generation-prefix-changed')
        stream.seek(0, os.SEEK_END)
        stream.write(body[len(prefix):])
        stream.flush()
        os.fsync(stream.fileno())
        os.fchown(stream.fileno(), 0, gid)
        os.fchmod(stream.fileno(), mode)
        os.fsync(stream.fileno())
        if custody._native_root_file(directory, name, mode=mode, gid=gid)[0] != body:
            raise ValueError('native-source-generation-prefix-changed')
        image['ready'] = True
        save()


def publish_disabled_generation(selection, specifications, *, rollback=False, _generation=None):
    """DATA ONLY, caller holds existing Root admission. No launchctl mutation.

    The protected prepare intent precedes creation; definitions and manifest
    precede the inactive Gate projection. A missing Gate during two renames is
    not an atomic pair and admits no actor. Unknown images are preserved.
    """
    if (type(rollback) is not bool or type(specifications) is not list or not 1 <= len(specifications) <= 64
            or _generation is not None and (type(_generation) is not str or not re.fullmatch('[a-f0-9]{32}', _generation))):
        raise ValueError('native-source-generation-control-invalid')
    custody, doc, _component, _ref = _material(selection)
    from pixel_macos_custody import protected_directory
    with protected_directory(custody.NATIVE_META) as metadata, protected_directory(custody.NATIVE_DEFINITIONS) as units:
        gate = os.open(custody.NATIVE_STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        jobs = None
        try:
            _kit, kit_sha = custody.native_verify_kit(selection, require_profiles=True, _publication=True)
            identity, base_binding, expected = custody._native_environment_binding(selection, metadata, gate, kit_sha, _publication=True)
            publication, _publication_inode = custody._native_root_file(metadata, 'source-publication.json')
            binding = dict(base_binding, publicationSha256=sha(publication))
            env_record, _env_raw = custody._native_json_at(metadata, 'source-environment.json')
            custody._native_environment_record(env_record, base_binding)
            environment = custody._native_environment_snapshot(gate, custody.NATIVE_ENV.name, identity['id'])
            if environment is None or env_record['pending'] is not None or environment != env_record['current']:
                raise ValueError('native-source-generation-environment-unsettled')
            # The exact existing log publication is required, not just paths.
            custody._native_verify_prepared_logs(metadata, identity, base_binding)
            files = {}
            for spec in specifications:
                if type(spec) is not dict or set(spec) != {'role', 'identifier'}:
                    raise ValueError('native-source-generation-control-invalid')
                path, body, _mode = disabled_definition(spec['role'], identity=identity,
                    python=doc['hostPythonPath'], identifier=spec['identifier'])
                if path.stem in files:
                    raise ValueError('native-source-generation-duplicate-definition')
                files[path.stem] = (dict(spec), body)
            record, raw = custody._native_json_at(metadata, 'source-jobs-prepare.json')
            if record is None:
                if rollback:
                    raise ValueError('native-source-generation-recovery-missing')
                # Actual B3.1 protected bundle, old/new images, and never-started
                # witness are verified BEFORE the first generation intent.
                if _generation is None:
                    verify_kit_publication(selection)
                else:
                    _environment_generation_boundary(selection, custody, doc, _component, _ref,
                        metadata=metadata, gate=gate, identity=identity, binding=base_binding,
                        expected=expected, generation=_generation)
                before, before_inode = custody._native_root_file(gate, 'custody.json', mode=0o640, gid=identity['id'], limit=16384)
                if before != expected:
                    raise ValueError('native-source-generation-prior-not-v1')
                if custody._native_at_inode(metadata, 'native-source-jobs') is not None:
                    raise ValueError('native-source-generation-unmanaged-directory')
                if any(name.startswith(custody.NATIVE_LABEL) for name in os.listdir(units)):
                    raise ValueError('native-source-generation-unmanaged-definition')
                if len({os.fstat(fd).st_dev for fd in (metadata, gate, units)}) != 1:
                    raise ValueError('native-source-generation-cross-filesystem')
                record = {'schemaVersion': 1, 'kind': 'ods-native-source-job-prepare',
                    'generation': _generation or uuid.uuid4().hex, 'phase': 'staging', 'binding': binding,
                    'environment': environment, 'before': {'inode': before_inode, 'descriptor': json.loads(expected)},
                    'directory': None, 'definitions': {label: dict(spec, sha256=sha(body), inode=None, ready=False)
                        for label, (spec, body) in files.items()}, 'manifest': {'inode': None, 'ready': False},
                    'gate': {'inode': None, 'ready': False}, 'witness': _no_actors(selection, custody, metadata)}
            custody._native_job_record(record, binding, expected)
            if _generation is not None and record['generation'] != _generation:
                raise ValueError('native-source-generation-hint-changed')
            if record['environment'] != environment or set(record['definitions']) != set(files) or any(
                    {key: item[key] for key in ('role', 'identifier')} != files[label][0]
                    for label, item in record['definitions'].items()):
                raise ValueError('native-source-generation-input-changed')
            retained = '.native-source-jobs-' + record['generation']
            if record['phase'] in ('unknown', 'retired') or record['phase'] in ('restoring', 'restored') and not rollback:
                raise ValueError('native-source-generation-explicit-recovery-required')

            def guard():
                custody._native_selected(selection, _publication=True)
                current_identity, current_binding, current_expected = custody._native_environment_binding(
                    selection, metadata, gate, kit_sha, _publication=True)
                if (current_identity != identity or current_binding != base_binding or current_expected != expected
                        or custody._native_root_file(metadata, 'source-publication.json')[0] != publication
                        or custody._native_environment_snapshot(gate, custody.NATIVE_ENV.name, identity['id']) != environment
                        or custody._native_json_at(metadata, 'source-environment.json')[0] != env_record
                        or _no_actors(selection, custody, metadata, _generation=True) != record['witness']):
                    raise ValueError('native-source-generation-input-changed')
                custody._native_verify_prepared_logs(metadata, identity, base_binding)
                if set(name for name in os.listdir(units) if name.startswith(custody.NATIVE_LABEL)) - {
                        label + '.plist' for label in files}:
                    raise ValueError('native-source-generation-unmanaged-definition')
                for label, (_spec, body) in files.items():
                    item, name = record['definitions'][label], label + '.plist'
                    if custody._native_at_inode(units, name) is not None:
                        actual, inode = custody._native_root_file(units, name, mode=0o644, limit=16384)
                        if not item['ready'] or (actual, inode) != (body, item['inode']):
                            raise ValueError('native-source-generation-definition-changed')
                if jobs is not None:
                    current = os.fstat(jobs)
                    if ([current.st_dev, current.st_ino] != record['directory']
                            or (current.st_uid, current.st_gid, stat.S_IMODE(current.st_mode)) != (0, 0, 0o700)):
                        raise ValueError('native-source-generation-directory-changed')
                    custody._native_no_acl(jobs)
                    located = custody._native_at_inode(metadata, 'native-source-jobs')
                    if located != record['directory'] and not (rollback and located is None
                            and custody._native_at_inode(metadata, retained) == record['directory']):
                        raise ValueError('native-source-generation-directory-changed')
                    allowed = {'manifest.json', '.gate-next', '.gate-before', *[label + '.plist' for label in files]}
                    if set(os.listdir(jobs)) - allowed:
                        raise ValueError('native-source-generation-directory-changed')
                    for label, (_spec, body) in files.items():
                        name = label + '.plist'
                        _generation_prefix(custody, jobs, name, body, record['definitions'][label], mode=0o644)
                        if custody._native_at_inode(jobs, name) is not None and custody._native_at_inode(units, name) is not None:
                            raise ValueError('native-source-generation-definition-conflict')
                    _generation_prefix(custody, jobs, 'manifest.json', custody._native_encoded(custody._native_job_manifest(record)), record['manifest'])
                    _generation_prefix(custody, jobs, '.gate-next', custody._native_job_projection(record), record['gate'], mode=0o640, gid=identity['id'])
                    if custody._native_at_inode(jobs, '.gate-before') is not None:
                        old, old_inode = custody._native_root_file(jobs, '.gate-before', mode=0o640, gid=identity['id'])
                        if (old, old_inode) != (expected, record['before']['inode']):
                            raise ValueError('native-source-generation-gate-backup-changed')
                current_inode = custody._native_at_inode(gate, 'custody.json')
                if current_inode is None:
                    if record['phase'] not in ('moving-gate', 'restoring') or jobs is None or custody._native_at_inode(jobs, '.gate-before') != record['before']['inode']:
                        raise ValueError('native-source-generation-gate-changed')
                else:
                    current_body, current_inode = custody._native_root_file(gate, 'custody.json', mode=0o640, gid=identity['id'], limit=16384)
                    if (current_body, current_inode) != (expected, record['before']['inode']) and not (
                            record['gate']['ready'] and (current_body, current_inode) == (custody._native_job_projection(record), record['gate']['inode'])):
                        raise ValueError('native-source-generation-gate-changed')

            def save():
                nonlocal raw
                guard()
                custody._native_job_record(record, binding, expected)
                raw = custody._native_save_at(selection, metadata, 'source-jobs-prepare.json', record,
                    previous=raw, _publication=True)

            if raw is None:
                save()  # Before mkdir or any disabled definition.
            if record['directory'] is None:
                guard()
                os.mkdir('native-source-jobs', mode=0o700, dir_fd=metadata)
                jobs = os.open('native-source-jobs', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=metadata)
                info = os.fstat(jobs)
                if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, 0o700):
                    raise ValueError('native-source-generation-directory-unsafe')
                custody._native_no_acl(jobs)
                record['directory'] = [info.st_dev, info.st_ino]
                os.fsync(jobs)
                os.fsync(metadata)
                save()
            else:
                if rollback and custody._native_at_inode(metadata, 'native-source-jobs') is None:
                    jobs = os.open(retained, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=metadata)
                else:
                    jobs = custody._native_job_directory(metadata, record)
            guard()
            if record['phase'] == 'restored':
                if (custody._native_at_inode(metadata, 'native-source-jobs') is not None
                        or custody._native_root_file(gate, 'custody.json', mode=0o640, gid=identity['id']) != (expected, record['before']['inode'])
                        or any(custody._native_at_inode(units, label + '.plist') is not None for label in files)):
                    raise ValueError('native-source-generation-restore-unconfirmed')
                return {'status': 'restored', 'generation': record['generation']}

            def current_gate():
                try:
                    body, inode = custody._native_root_file(gate, 'custody.json', mode=0o640, gid=identity['id'], limit=16384)
                except FileNotFoundError:
                    return None
                if (body, inode) == (expected, record['before']['inode']):
                    return 'before'
                if record['gate']['ready'] and (body, inode) == (custody._native_job_projection(record), record['gate']['inode']):
                    return 'after'
                raise ValueError('native-source-generation-gate-changed')

            if rollback:
                record['phase'] = 'restoring'
                save()
                current = current_gate()
                if current == 'after':
                    if custody._native_at_inode(jobs, '.gate-next') is not None:
                        raise ValueError('native-source-generation-gate-conflict')
                    guard()
                    os.rename('custody.json', '.gate-next', src_dir_fd=gate, dst_dir_fd=jobs)
                    os.fsync(gate)
                    os.fsync(jobs)
                    current = None
                if current is None:
                    body, inode = custody._native_root_file(jobs, '.gate-before', mode=0o640, gid=identity['id'])
                    if (body, inode) != (expected, record['before']['inode']):
                        raise ValueError('native-source-generation-gate-backup-changed')
                    guard()
                    os.rename('.gate-before', 'custody.json', src_dir_fd=jobs, dst_dir_fd=gate)
                    os.fsync(gate)
                    os.fsync(jobs)
                for label, item in record['definitions'].items():
                    name = label + '.plist'
                    if custody._native_at_inode(units, name) is None:
                        continue
                    actual, inode = custody._native_root_file(units, name, mode=0o644, limit=16384)
                    if (actual, inode) != (files[label][1], item['inode']) or custody._native_at_inode(jobs, name) is not None:
                        raise ValueError('native-source-generation-definition-changed')
                    guard()
                    os.rename(name, name, src_dir_fd=units, dst_dir_fd=jobs)
                    os.fsync(units)
                    os.fsync(jobs)
                guard()
                if custody._native_at_inode(metadata, 'native-source-jobs') == record['directory']:
                    if custody._native_at_inode(metadata, retained) is not None:
                        raise ValueError('native-source-generation-retained-conflict')
                    os.rename('native-source-jobs', retained, src_dir_fd=metadata, dst_dir_fd=metadata)
                    os.fsync(metadata)
                record['phase'] = 'restored'
                save()
                return {'status': 'restored', 'generation': record['generation']}

            if record['phase'] == 'committed-inactive':
                custody._native_validate_inactive_generation(selection, metadata, gate, identity, base_binding,
                    expected, custody._native_root_file(gate, 'custody.json', mode=0o640, gid=identity['id'])[0],
                    custody._native_at_inode(gate, 'custody.json'))
                return {'status': 'inactive', 'generation': record['generation'], 'definitionCount': len(files)}
            if record['phase'] == 'staging':
                for label, (_spec, body) in files.items():
                    _generation_stage(custody, jobs, label + '.plist', body, record['definitions'][label], save, guard, mode=0o644)
                _generation_stage(custody, jobs, 'manifest.json', custody._native_encoded(custody._native_job_manifest(record)),
                    record['manifest'], save, guard)
                projection = custody._native_job_projection(record)
                if len(projection) > 16384:
                    raise ValueError('native-source-generation-projection-too-large')
                _generation_stage(custody, jobs, '.gate-next', projection, record['gate'], save, guard,
                    mode=0o640, gid=identity['id'])
                record['phase'] = 'publishing'
                save()
            if record['phase'] == 'publishing':
                for label, (_spec, body) in files.items():
                    name = label + '.plist'
                    if custody._native_at_inode(units, name) is None:
                        actual, inode = custody._native_root_file(jobs, name, mode=0o644, limit=16384)
                        if (actual, inode) != (body, record['definitions'][label]['inode']):
                            raise ValueError('native-source-generation-definition-changed')
                        guard()
                        os.rename(name, name, src_dir_fd=jobs, dst_dir_fd=units)
                        os.fsync(jobs)
                        os.fsync(units)
                    actual, inode = custody._native_root_file(units, name, mode=0o644, limit=16384)
                    if (actual, inode) != (body, record['definitions'][label]['inode']):
                        raise ValueError('native-source-generation-definition-changed')
                record['phase'] = 'manifest-ready'
                save()
            # Revalidate every input and full public definition before the
            # broker-readable commit point, including on a lost-ack resume.
            for label, (_spec, body) in files.items():
                actual, inode = custody._native_root_file(units, label + '.plist', mode=0o644, limit=16384)
                if (actual, inode) != (body, record['definitions'][label]['inode']):
                    raise ValueError('native-source-generation-definition-changed')
            actual, inode = custody._native_root_file(jobs, 'manifest.json')
            if actual != custody._native_encoded(custody._native_job_manifest(record)) or inode != record['manifest']['inode']:
                raise ValueError('native-source-generation-manifest-changed')
            record['phase'] = 'moving-gate'
            save()
            current = current_gate()
            if current == 'before':
                if custody._native_at_inode(jobs, '.gate-before') is not None:
                    raise ValueError('native-source-generation-gate-conflict')
                guard()
                os.rename('custody.json', '.gate-before', src_dir_fd=gate, dst_dir_fd=jobs)
                os.fsync(gate)
                os.fsync(jobs)
                current = None
            if current is None:
                old, old_inode = custody._native_root_file(jobs, '.gate-before', mode=0o640, gid=identity['id'])
                new, new_inode = custody._native_root_file(jobs, '.gate-next', mode=0o640, gid=identity['id'])
                if (old, old_inode) != (expected, record['before']['inode']) or (new, new_inode) != (
                        custody._native_job_projection(record), record['gate']['inode']):
                    raise ValueError('native-source-generation-gate-candidate-changed')
                guard()
                os.rename('.gate-next', 'custody.json', src_dir_fd=jobs, dst_dir_fd=gate)
                os.fsync(gate)
                os.fsync(jobs)
            if current_gate() != 'after':
                raise ValueError('native-source-generation-gate-unconfirmed')
            record['phase'] = 'committed-inactive'
            save()
            custody._native_validate_inactive_generation(selection, metadata, gate, identity, base_binding,
                expected, custody._native_job_projection(record), record['gate']['inode'])
            return {'status': 'inactive', 'generation': record['generation'], 'definitionCount': len(files)}
        finally:
            if jobs is not None:
                os.close(jobs)
            os.close(gate)


def _environment_no_actors(selection, custody, metadata):
    witness = _no_actors(selection, custody, metadata, _generation=True)
    # An empty/truncated/duplicate process census is unknown, never absence.
    raw = selection.bridge.command(['/bin/ps', '-axo', 'pid=,uid='], timeout=10)
    rows = [re.fullmatch(r'\s*([1-9][0-9]*)\s+(-?[0-9]+)\s*', row) for row in raw.splitlines()]
    if (not rows or len(raw) > 1024 * 1024 or any(row is None for row in rows)
            or len({row[1] for row in rows}) != len(rows)
            or any(int(row[2]) == witness['brokerUid'] for row in rows)):
        raise ValueError('native-source-environment-actors-unconfirmed')
    return witness


def _environment_image(custody, directory, name, *, mode=0o600, gid=0):
    body, inode = custody._native_root_file(directory, name, mode=mode, gid=gid, limit=524288)
    return {'inode': inode, 'sha256': sha(body), 'mode': mode, 'gid': gid}, body


def _environment_inode(info):
    return [info.st_dev, info.st_ino]


def _environment_legacy_tree(custody, directory, broker_uid):
    """Exact schema-1 receipt check; never reinterpret a prior tree digest."""
    root = os.fstat(directory)
    if (root.st_uid, root.st_gid, stat.S_IMODE(root.st_mode)) != (0, 0, 0o700):
        raise ValueError('native-source-environment-history-unsafe')
    entries, budget = [], [4096, 32 * 1024 * 1024]
    def walk(fd, prefix='', depth=0):
        if depth > 8:
            raise ValueError('native-source-environment-history-too-large')
        custody._native_no_acl(fd)
        for name in sorted(os.listdir(fd)):
            budget[0] -= 1
            if budget[0] < 0:
                raise ValueError('native-source-environment-history-too-large')
            relative = prefix + name
            child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            try:
                info = os.fstat(child)
                mode = stat.S_IMODE(info.st_mode)
                if (info.st_uid != 0 or info.st_dev != root.st_dev
                        or custody._native_at_inode(fd, name) != _environment_inode(info)):
                    raise ValueError('native-source-environment-history-unsafe')
                if stat.S_ISDIR(info.st_mode) and mode == 0o700 and info.st_gid == 0:
                    entries.append([relative, 'directory', _environment_inode(info)])
                    walk(child, relative + '/', depth + 1)
                elif stat.S_ISREG(info.st_mode) and mode in (0o600, 0o640, 0o644) and info.st_gid in (0, broker_uid):
                    image, body = _environment_image(custody, fd, name, mode=mode, gid=info.st_gid)
                    budget[1] -= len(body)
                    if budget[1] < 0:
                        raise ValueError('native-source-environment-history-too-large')
                    entries.append([relative, image])
                else:
                    raise ValueError('native-source-environment-history-unsafe')
            finally:
                os.close(child)
    walk(directory)
    return sha(encoded(entries))


def _environment_jobs_record(custody, value, broker_uid):
    if (type(value) is not dict or set(value) != {'directory', 'files', 'history'}
            or not custody._native_inode(value['directory'])
            or type(value['files']) is not dict or not 1 <= len(value['files']) <= 512):
        raise ValueError('native-source-environment-jobs-record-changed')
    for name, image in value['files'].items():
        if (type(name) is not str or not name or name in ('.', '..', 'history')
                or any(char in name for char in '/\\\0') or len(name) > 255
                or type(image) is not dict or set(image) != {'inode', 'sha256', 'mode', 'gid'}
                or not custody._native_inode(image['inode'])
                or type(image['sha256']) is not str or not HEX.fullmatch(image['sha256'])
                or type(image['mode']) is not int or image['mode'] not in (0o600, 0o640, 0o644)
                or type(image['gid']) is not int or image['gid'] not in (0, broker_uid)):
            raise ValueError('native-source-environment-jobs-record-changed')
    history = value['history']
    if history is not None and (type(history) is not dict or set(history) != {'directory', 'count'}
            or not custody._native_inode(history['directory'])
            or type(history['count']) is not int or history['count'] < 0):
        raise ValueError('native-source-environment-jobs-record-changed')


def _environment_history_directory(custody, jobs, history):
    """Sealed history is retained by inode; descendants grant no live authority."""
    present = custody._native_at_inode(jobs, 'history')
    if history is None:
        if present is not None:
            raise ValueError('native-source-environment-history-unmanaged')
        return
    if present != history['directory']:
        raise ValueError('native-source-environment-history-changed')
    fd = os.open('history', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=jobs)
    try:
        info = os.fstat(fd)
        if (_environment_inode(info) != history['directory']
                or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, 0o700)):
            raise ValueError('native-source-environment-history-unsafe')
        custody._native_no_acl(fd)
    finally:
        os.close(fd)


def _environment_tree(custody, directory, broker_uid, *, origin, active):
    """Freeze only the closed live namespace and the protected history inode.

    The origin and active records have already passed their literal binding
    validators. Prior receipts remain in the same directory, without reading,
    copying or treating their contents as invocation or stop authority.
    """
    info = os.fstat(directory)
    if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, 0o700):
        raise ValueError('native-source-environment-history-unsafe')
    custody._native_no_acl(directory)
    history = None
    allowed = {'manifest.json', '.gate-before'}
    if active is None:
        allowed.update(label + '.plist' for label in origin['definitions'])
    else:
        allowed.update(active['retained'])
        transaction = active['transaction']
        if transaction is not None:
            stem = _active_stem(transaction)
            allowed.update(stem + key for key in transaction['images'])
            allowed.update((stem + 'definition-old', stem + 'manifest-old'))
        archived = active.get('history')
        if archived is not None:
            if archived['pending'] is not None:
                raise ValueError('native-source-environment-active-unsettled')
            if archived['directory'] is not None:
                history = {'directory': list(archived['directory']), 'count': archived['count']}
                allowed.add('history')
    names = set(os.listdir(directory))
    if names - allowed or len(names) > 513:
        raise ValueError('native-source-environment-live-unmanaged')
    _environment_history_directory(custody, directory, history)
    files = {}
    for name in sorted(names - {'history'}):
        child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            file_info = os.fstat(child)
        finally:
            os.close(child)
        mode = stat.S_IMODE(file_info.st_mode)
        if not stat.S_ISREG(file_info.st_mode) or mode not in (0o600, 0o640, 0o644) or file_info.st_gid not in (0, broker_uid):
            raise ValueError('native-source-environment-live-unsafe')
        files[name] = _environment_image(custody, directory, name, mode=mode, gid=file_info.st_gid)[0]
        if active is not None and name in active['retained'] and files[name] != active['retained'][name]:
            raise ValueError('native-source-environment-live-retained-changed')
    value = {'directory': _environment_inode(info), 'files': files, 'history': history}
    _environment_jobs_record(custody, value, broker_uid)
    return value


def _environment_history_matches(custody, directory, broker_uid, record):
    if record['schemaVersion'] == 1:
        return _environment_legacy_tree(custody, directory, broker_uid) == record['old']['treeSha256']
    value = record['old']['jobs']
    _environment_jobs_record(custody, value, broker_uid)
    info = os.fstat(directory)
    if (_environment_inode(info) != value['directory']
            or value['directory'] != record['old']['directory']
            or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, 0o700)):
        return False
    custody._native_no_acl(directory)
    if set(os.listdir(directory)) != set(value['files']) | ({'history'} if value['history'] is not None else set()):
        return False
    _environment_history_directory(custody, directory, value['history'])
    for name, image in value['files'].items():
        if _environment_image(custody, directory, name, mode=image['mode'], gid=image['gid'])[0] != image:
            return False
    return True


def _environment_abandoned_admission(custody, execution, public, witness):
    """A stopped, fully qualified origin may retain an unfinished intent.

    This does not complete or dispatch it. The unchanged execution journal is
    withdrawn with its generation, including any unknown business outcome.
    Earlier partial publisher states never reach this proof.
    """
    pending = execution['admission']
    if pending is None:
        return
    if execution['boot'] != witness['boot'] or public['launch']['boot'] != witness['boot']:
        raise ValueError('native-source-environment-admission-boot-unconfirmed')
    previous = execution['jobs'].get(pending['label'])
    if pending['beforeNonce'] is None:
        if previous is not None:
            raise ValueError('native-source-environment-admission-origin-changed')
    elif (previous is None or previous['nonce'] != pending['beforeNonce']
            or (previous['role'], previous['identifier']) != (pending['role'], pending['identifier'])
            or previous['role'] not in ('sync', 'direct') or previous['phase'] != 'stopped'
            or not previous['bootoutIntent']):
        raise ValueError('native-source-environment-admission-origin-changed')
    current = public['launch'].get('invocations', {}).get(pending['label'])
    if current is not None and (previous is None or current['invocation']['nonce'] != previous['nonce']):
        invocation = custody._native_invocation(current['invocation']).document
        if (current['phase'] not in ('prepared', 'admitted')
                or (invocation['role'], invocation['identifier'], invocation['boot'], invocation['generation']) != (
                    pending['role'], pending['identifier'], witness['boot'], public['launch']['generation'])
                or int(invocation['admissionDeadlineNs']) > int(pending['ceilingNs'])):
            raise ValueError('native-source-environment-admission-slot-changed')


def _environment_origin(selection, custody, doc, component, ref, metadata, gate, units, identity, binding, expected):
    """Validate old authority statically, then obtain fresh actor absence."""
    from pixel_connections import native_executor
    publication, _ = custody._native_root_file(metadata, 'source-publication.json')
    origin, _ = custody._native_json_at(metadata, 'source-jobs-prepare.json')
    bound = dict(binding, publicationSha256=sha(publication))
    custody._native_job_record(origin, bound, expected)
    if origin['phase'] != 'committed-inactive':
        raise ValueError('native-source-environment-origin-unsettled')
    public_raw, public_inode = custody._native_root_file(gate, 'custody.json', mode=0o640, gid=identity['id'], limit=16384)
    public = custody._native_public_json(public_raw)
    active, _ = custody._native_json_at(metadata, 'source-jobs-active.json')
    if active is None:
        custody._native_validate_inactive_generation(selection, metadata, gate, identity, binding, expected, public_raw, public_inode)
        definitions = {label: dict(item, active=False) for label, item in origin['definitions'].items()}
        boot = origin['witness']['boot']
        manifest_body, manifest_inode = custody._native_encoded(custody._native_job_manifest(origin)), origin['manifest']['inode']
    else:
        full_binding = dict(bound, runtimeCustodySha256=doc['runtimeCustodySha256'], bundleSha256=doc['bundleSha256'], sourceRef=ref)
        _active_record(custody, active, origin, full_binding, expected)
        if active['phase'] != 'committed' or (active.get('history') or {}).get('pending') is not None:
            raise ValueError('native-source-environment-active-unsettled')
        state = active['current']
        if (public_raw, public_inode) != (custody._native_encoded(state['gate']['document']), state['gate']['inode']):
            raise ValueError('native-source-environment-gate-changed')
        definitions, boot = state['definitions'], public['launch']['boot']
        manifest_body, manifest_inode = custody._native_encoded(state['manifest']['document']), state['manifest']['inode']
        for item in definitions.values():
            if item['profileSha256'] != sha(component['source-' + item['role'] + '.sb']):
                raise ValueError('native-source-environment-profile-changed')
    if set(name for name in os.listdir(units) if name.startswith(custody.NATIVE_LABEL)) != {label + '.plist' for label in definitions}:
        raise ValueError('native-source-environment-definitions-unmanaged')
    images = {}
    for label, item in definitions.items():
        _label, body = _active_definition(custody, json.loads(expected), item['role'], item['identifier'], item['active'])
        image, actual = _environment_image(custody, units, label + '.plist', mode=0o644)
        if _label != label or actual != body or image['inode'] != item['inode']:
            raise ValueError('native-source-environment-definition-changed')
        images[label + '.plist'] = image
    jobs = custody._native_job_directory(metadata, origin)
    try:
        body, inode = custody._native_root_file(jobs, 'manifest.json')
        if (body, inode) != (manifest_body, manifest_inode):
            raise ValueError('native-source-environment-manifest-changed')
        tree = _environment_tree(custody, jobs, identity['id'], origin=origin, active=active)
    finally:
        os.close(jobs)
    execution, execution_raw = custody._native_json_at(metadata, 'source-execution.json')
    witness = _environment_no_actors(selection, custody, metadata)
    if execution is not None:
        # Static validation is valid across boots; old PIDs are never queried.
        view = dict(public, launch=dict(public['launch'], boot=boot))
        native_executor._validate(execution, doc, view)
        _environment_abandoned_admission(custody, execution, view, witness)
        if boot == witness['boot']:
            for job in execution['jobs'].values():
                native_executor.verify_stopped(selection, job['role'], job['identifier'], job['nonce'])
    if custody._native_json_at(metadata, 'source-execution.json')[1] != execution_raw:
        raise ValueError('native-source-environment-execution-changed')
    _environment_no_actors(selection, custody, metadata)
    return origin, boot, tree, images


def _environment_generation_record(custody, record, binding):
    keys = {'schemaVersion', 'kind', 'binding', 'previousGeneration', 'generation', 'targetSha256',
        'phase', 'archive', 'old', 'copies', 'v1', 'environment', 'boot'}
    def image(value):
        return (type(value) is dict and set(value) == {'inode', 'sha256', 'mode', 'gid'}
            and custody._native_inode(value['inode']) and type(value['sha256']) is str and HEX.fullmatch(value['sha256'])
            and type(value['mode']) is int and value['mode'] in (0o600, 0o640, 0o644)
            and type(value['gid']) is int and value['gid'] in (0, binding['brokerUid']))
    def environment(value):
        return (type(value) is dict and set(value) == {'inode', 'sha256', 'size'}
            and custody._native_inode(value['inode']) and type(value['sha256']) is str and HEX.fullmatch(value['sha256'])
            and type(value['size']) is int and 0 < value['size'] <= custody.MAX_RESULT_BYTES)
    def staged(value):
        return (type(value) is dict and set(value) == {'inode', 'ready'} and type(value['ready']) is bool
            and (value['inode'] is None or custody._native_inode(value['inode']))
            and (not value['ready'] or value['inode'] is not None))
    if (type(record) is not dict or not keys <= set(record) <= keys | {'childBoots', 'installer'} or type(record['schemaVersion']) is not int
            or record['schemaVersion'] not in (1, 2) or record['kind'] != 'ods-native-source-environment-generation'
            or encoded(record['binding']) != encoded(binding)
            or record['phase'] not in ('staging', 'withdrawing', 'environment', 'generation', 'committed', 'withdrawn')
            or any(type(record[key]) is not str or not re.fullmatch('[a-f0-9]{32}', record[key])
                for key in ('generation', 'previousGeneration')) or record['generation'] == record['previousGeneration']
            or type(record['targetSha256']) is not str or not HEX.fullmatch(record['targetSha256'])
            or record['archive'] is not None and not custody._native_inode(record['archive'])
            or type(record['boot']) is not str or not re.fullmatch('[A-F0-9]{8}(?:-[A-F0-9]{4}){3}-[A-F0-9]{12}', record['boot'])
            or type(record['old']) is not dict or set(record['old']) != {'gate', 'directory',
                'treeSha256' if record['schemaVersion'] == 1 else 'jobs', 'files',
                'definitions', 'environment', 'publication', 'environmentJournal'}
            or not custody._native_inode(record['old']['directory'])
            or record['schemaVersion'] == 1 and (type(record['old']['treeSha256']) is not str
                or not HEX.fullmatch(record['old']['treeSha256']))
            or any(not image(record['old'][key]) for key in ('gate', 'publication', 'environmentJournal'))
            or not environment(record['old']['environment']) or not staged(record['v1'])
            or type(record['copies']) is not dict or set(record['copies']) != {
                'environment.before', 'environment-journal.before', 'publication.before'}
            or any(not staged(item) for item in record['copies'].values())
            or record['phase'] != 'staging' and any(not item['ready'] for item in record['copies'].values())
            or record['phase'] in ('environment', 'generation', 'committed', 'withdrawn') and not record['v1']['ready']
            or record['phase'] in ('generation', 'committed') and (
                not environment(record['environment']) or record['environment']['sha256'] != record['targetSha256'])):
        raise ValueError('native-source-environment-generation-record-changed')
    old = record['old']
    if record['schemaVersion'] == 2:
        _environment_jobs_record(custody, old['jobs'], binding['brokerUid'])
        if old['jobs']['directory'] != old['directory']:
            raise ValueError('native-source-environment-jobs-record-changed')
    if (type(old['files']) is not dict or 'source-jobs-prepare.json' not in old['files']
            or set(old['files']) - {'source-jobs-prepare.json', 'source-jobs-active.json', 'source-execution.json'}
            or type(old['definitions']) is not dict or not 1 <= len(old['definitions']) <= 64
            or any(not image(item) or (item['mode'], item['gid']) != (0o600, 0) for item in old['files'].values())
            or any(not image(item) or (item['mode'], item['gid']) != (0o644, 0) for item in old['definitions'].values())):
        raise ValueError('native-source-environment-generation-record-changed')
    for filename in old['definitions']:
        if not filename.endswith('.plist'):
            raise ValueError('native-source-environment-generation-record-changed')
        custody._native_label_parts(filename[:-6])
    boots = record.get('childBoots', [])
    if (type(boots) is not list or len(boots) > 16 or boots and (
            record['phase'] not in ('generation', 'committed') or record['archive'] is None)):
        raise ValueError('native-source-environment-child-boot-record-changed')
    seen = set()
    for index, item in enumerate(boots):
        if (type(item) is not dict or set(item) != {'beforeBoot', 'boot', 'before', 'afterSha256', 'after', 'phase'}
                or item['phase'] not in ('staging', 'committed')
                or item['phase'] == 'staging' and (index != len(boots) - 1 or record['phase'] == 'committed')
                or any(type(item[key]) is not str or not re.fullmatch('[A-F0-9]{8}(?:-[A-F0-9]{4}){3}-[A-F0-9]{12}', item[key])
                    for key in ('beforeBoot', 'boot')) or item['beforeBoot'] == item['boot'] or item['beforeBoot'] in seen
                or not image(item['before']) or (item['before']['mode'], item['before']['gid']) != (0o600, 0)
                or type(item['afterSha256']) is not str or not HEX.fullmatch(item['afterSha256'])
                or not staged(item['after']) or item['phase'] == 'committed' and not item['after']['ready']):
            raise ValueError('native-source-environment-child-boot-record-changed')
        if index and (boots[index - 1]['boot'] != item['beforeBoot']
                or boots[index - 1]['afterSha256'] != item['before']['sha256']
                or boots[index - 1]['after']['inode'] != item['before']['inode']):
            raise ValueError('native-source-environment-child-boot-chain-changed')
        seen.add(item['beforeBoot'])
    if 'installer' in record:
        installer = record['installer']
        if (record['schemaVersion'] != 2 or record['phase'] in ('generation', 'committed') or boots
                or type(installer) is not dict or set(installer) != {'transaction', 'intent', 'receipt', 'boot'}
                or not staged(installer['receipt']) or record['environment'] is not None
                or record['targetSha256'] != record['old']['environment']['sha256']
                or record['phase'] == 'withdrawn' and (not installer['receipt']['ready'] or installer['boot'] is None)):
            raise ValueError('native-source-installer-withdrawal-record-changed')
        _installer_intent_record(custody, installer['intent'], installer['transaction'], binding)
    elif record['phase'] == 'withdrawn':
        raise ValueError('native-source-installer-withdrawal-record-missing')


def _environment_boot_names(item):
    return 'child-before-' + item['beforeBoot'] + '.json', 'child-next-' + item['beforeBoot'] + '.json'


def _environment_boot_child(custody, metadata, archive, record, binding, expected):
    """Recover only the recorded inactive image pair, never a live invocation."""
    child, raw = custody._native_json_at(metadata, 'source-jobs-prepare.json')
    boots = record.get('childBoots', [])
    if not boots:
        return child, raw
    bound = dict(binding, publicationSha256=record['old']['publication']['sha256'])
    for index, item in enumerate(boots):
        before_name, next_name = _environment_boot_names(item)
        saved = custody._native_at_inode(archive, before_name)
        if saved is None:
            if item['phase'] != 'staging' or index != len(boots) - 1:
                raise ValueError('native-source-environment-child-boot-before-missing')
            image, before_raw = _environment_image(custody, metadata, 'source-jobs-prepare.json')
        else:
            image, before_raw = _environment_image(custody, archive, before_name)
        if image != item['before']:
            raise ValueError('native-source-environment-child-boot-before-changed')
        before = custody._native_public_json(before_raw)
        custody._native_job_record(before, bound, expected)
        if (before['phase'] != 'committed-inactive' or before['generation'] != record['generation']
                or before['environment'] != record['environment'] or before['witness']['boot'] != item['beforeBoot']
                or set(before['definitions']) != {custody.NATIVE_LABEL + role for role in ('sync', 'direct')}
                or before_raw != custody._native_encoded(before)):
            raise ValueError('native-source-environment-child-boot-before-changed')
        after = dict(before, witness=dict(before['witness'], boot=item['boot']))
        after_raw = custody._native_encoded(after)
        if sha(after_raw) != item['afterSha256']:
            raise ValueError('native-source-environment-child-boot-after-changed')
        _generation_prefix(custody, archive, next_name, after_raw, item['after'])
        if item['phase'] == 'committed':
            if saved is None or custody._native_at_inode(archive, next_name) is not None:
                raise ValueError('native-source-environment-child-boot-history-changed')
            if index == len(boots) - 1 and (raw != after_raw
                    or custody._native_at_inode(metadata, 'source-jobs-prepare.json') != item['after']['inode']):
                raise ValueError('native-source-environment-child-boot-current-changed')
        elif raw is None:
            if saved is None or not item['after']['ready']:
                raise ValueError('native-source-environment-child-boot-missing')
            child = after  # Metadata is absent: this is only an inert prefix inventory.
        elif saved is None:
            if raw != before_raw or custody._native_at_inode(metadata, 'source-jobs-prepare.json') != item['before']['inode']:
                raise ValueError('native-source-environment-child-boot-current-changed')
        elif (raw != after_raw or not item['after']['ready']
                or custody._native_at_inode(metadata, 'source-jobs-prepare.json') != item['after']['inode']
                or custody._native_at_inode(archive, next_name) is not None):
            raise ValueError('native-source-environment-child-boot-current-changed')
    return child, raw


def _environment_archive_name(record):
    prefix = ('.source-' + _installer_domain(record['installer']['transaction']) + '-withdrawal-'
        if 'installer' in record else '.source-environment-')
    return prefix + record['generation']


def _environment_retained(custody, metadata, record):
    name = _environment_archive_name(record)
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=metadata)
    try:
        info = os.fstat(fd)
        if (custody._native_at_inode(metadata, name) != record['archive'] or _environment_inode(info) != record['archive']
                or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, 0o700)):
            raise ValueError('native-source-environment-generation-archive-changed')
        custody._native_no_acl(fd)
        images = {'custody.before.json': record['old']['gate'], **record['old']['files'], **record['old']['definitions']}
        wanted = {*images, *record['copies'], 'native-source-jobs',
            *[_environment_boot_names(item)[0] for item in record.get('childBoots', [])]}
        if 'installer' in record and record['installer']['receipt']['inode'] is not None:
            wanted.add('withdrawal-receipt.json')
        if set(os.listdir(fd)) != wanted:
            raise ValueError('native-source-environment-generation-archive-changed')
        for filename, image in images.items():
            if _environment_image(custody, fd, filename, mode=image['mode'], gid=image['gid'])[0] != image:
                raise ValueError('native-source-environment-generation-image-changed')
        hashes = {'environment.before': record['old']['environment']['sha256'],
            'environment-journal.before': record['old']['environmentJournal']['sha256'],
            'publication.before': record['old']['publication']['sha256']}
        for filename, checksum in hashes.items():
            body, inode = custody._native_root_file(fd, filename)
            if inode != record['copies'][filename]['inode'] or sha(body) != checksum:
                raise ValueError('native-source-environment-generation-copy-changed')
        binding = {key: value for key, value in record['binding'].items()
            if key not in ('runtimeCustodySha256', 'bundleSha256', 'sourceRef')}
        # The descriptor is the original closed v1 object retained in the old
        # origin; no invocation or archive is ever adopted as new authority.
        origin, _ = custody._native_json_at(fd, 'source-jobs-prepare.json')
        _environment_boot_child(custody, metadata, fd, record, binding,
            custody._native_encoded(origin['before']['descriptor']))
        jobs = os.open('native-source-jobs', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        try:
            if (_environment_inode(os.fstat(jobs)) != record['old']['directory']
                    or not _environment_history_matches(custody, jobs, record['binding']['brokerUid'], record)):
                raise ValueError('native-source-environment-generation-history-changed')
        finally:
            os.close(jobs)
    finally:
        os.close(fd)


def environment_quiesce_snapshot(selection):
    """Read-only pending recovery inventory, never a stop witness or admission.

    The fixed executor must independently inspect each loaded literal definition
    and fresh current-boot process identity before any fixed-label bootout.
    """
    custody, doc, component, ref = _material(selection)
    from pixel_macos_custody import protected_directory
    with protected_directory(custody.NATIVE_META) as metadata, protected_directory(custody.NATIVE_DEFINITIONS) as units:
        record, _raw = custody._native_json_at(metadata, 'source-environment-generation.json')
        if record is None or record.get('phase') in ('committed', 'withdrawn'):
            return None
        gate = os.open(custody.NATIVE_STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        archive = None
        try:
            _kit, kit_sha = custody.native_verify_kit(selection, require_profiles=True, _publication=True)
            identity, binding, expected = custody._native_environment_binding(selection, metadata, gate, kit_sha, _publication=True)
            full_binding = dict(binding, runtimeCustodySha256=doc['runtimeCustodySha256'], bundleSha256=doc['bundleSha256'], sourceRef=ref)
            _environment_generation_record(custody, record, full_binding)
            archive_name = _environment_archive_name(record)
            if record['archive'] is not None:
                archive = os.open(archive_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=metadata)
                info = os.fstat(archive)
                if (_environment_inode(info) != record['archive'] or custody._native_at_inode(metadata, archive_name) != record['archive']
                        or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, 0o700)):
                    raise ValueError('native-source-environment-generation-archive-changed')
                custody._native_no_acl(archive)
                allowed = {'custody.before.json', 'native-source-jobs', '.v1-next', *record['copies'],
                    *record['old']['files'], *record['old']['definitions'],
                    *[name for item in record.get('childBoots', []) for name in _environment_boot_names(item)]}
                if 'installer' in record:
                    allowed.add('withdrawal-receipt.json')
                if set(os.listdir(archive)) - allowed:
                    raise ValueError('native-source-environment-generation-archive-changed')
            elif custody._native_at_inode(metadata, archive_name) is not None:
                raise ValueError('native-source-environment-generation-unmanaged-archive')

            def original(directory, filename, image, saved_name=None):
                saved_name = filename if saved_name is None else saved_name
                saved = archive is not None and custody._native_at_inode(archive, saved_name) is not None
                actual, body = _environment_image(custody, archive if saved else directory,
                    saved_name if saved else filename, mode=image['mode'], gid=image['gid'])
                if actual != image:
                    raise ValueError('native-source-environment-generation-image-changed')
                return body

            if _environment_image(custody, metadata, 'source-publication.json')[0] != record['old']['publication']:
                raise ValueError('native-source-environment-generation-publication-changed')
            documents = {filename: json.loads(original(metadata, filename, image), object_pairs_hook=custody._native_pairs)
                for filename, image in record['old']['files'].items()}
            origin = documents['source-jobs-prepare.json']
            publication = custody._native_root_file(metadata, 'source-publication.json')[0]
            bound = dict(binding, publicationSha256=sha(publication))
            custody._native_job_record(origin, bound, expected)
            if origin['generation'] != record['previousGeneration'] or origin['environment'] != record['old']['environment']:
                raise ValueError('native-source-environment-generation-origin-changed')
            before = original(gate, 'custody.json', record['old']['gate'], 'custody.before.json')
            active = documents.get('source-jobs-active.json')
            if active is None:
                if before != custody._native_job_projection(origin) or origin['phase'] != 'committed-inactive':
                    raise ValueError('native-source-environment-generation-origin-changed')
                definitions = {label: dict(item, active=False) for label, item in origin['definitions'].items()}
            else:
                _active_record(custody, active, origin, dict(bound, runtimeCustodySha256=doc['runtimeCustodySha256'],
                    bundleSha256=doc['bundleSha256'], sourceRef=ref), expected)
                if (active['phase'] != 'committed' or custody._native_encoded(active['current']['gate']['document']) != before
                        or active['current']['gate']['inode'] != record['old']['gate']['inode']):
                    raise ValueError('native-source-environment-generation-origin-changed')
                definitions = active['current']['definitions']
            if set(definitions) != {filename[:-6] for filename in record['old']['definitions']}:
                raise ValueError('native-source-environment-generation-origin-changed')
            for label, item in definitions.items():
                _label, body = _active_definition(custody, json.loads(expected), item['role'], item['identifier'], item['active'])
                actual = original(units, label + '.plist', record['old']['definitions'][label + '.plist'])
                if label != _label or body != actual or item['inode'] != record['old']['definitions'][label + '.plist']['inode']:
                    raise ValueError('native-source-environment-generation-definition-changed')
                if 'profileSha256' in item and item['profileSha256'] != sha(component['source-' + item['role'] + '.sb']):
                    raise ValueError('native-source-environment-profile-changed')
            jobs_parent = archive if archive is not None and custody._native_at_inode(archive, 'native-source-jobs') is not None else metadata
            jobs = custody._native_job_directory(jobs_parent, origin)
            try:
                if not _environment_history_matches(custody, jobs, identity['id'], record):
                    raise ValueError('native-source-environment-generation-history-changed')
            finally:
                os.close(jobs)
            child, child_raw = _environment_boot_child(custody, metadata, archive, record, binding, expected)
            if record['phase'] == 'generation' and child is not None:
                custody._native_job_record(child, bound, expected)
                if (child['generation'] != record['generation'] or child['environment'] != record['environment']
                        or set(child['definitions']) != {custody.NATIVE_LABEL + role for role in ('sync', 'direct')}):
                    raise ValueError('native-source-environment-generation-child-changed')
                definitions = {label: dict(item, active=False) for label, item in child['definitions'].items()}
                phase, generation, boot = 'child', child['generation'], child['witness']['boot']
                if child_raw is None:
                    # A staged candidate is no current-boot control authority.
                    # Quiescence must still inspect the displaced boot's labels.
                    boot = record['childBoots'][-1]['beforeBoot']
            else:
                phase, generation, boot = 'old', origin['generation'], record['boot']
            remaining = {}
            for filename in (item for item in os.listdir(units) if item.startswith(custody.NATIVE_LABEL)):
                label = filename[:-6] if filename.endswith('.plist') else None
                item = definitions.get(label)
                if item is None or item['inode'] is None:
                    raise ValueError('native-source-environment-generation-definition-changed')
                _label, body = _active_definition(custody, json.loads(expected), item['role'], item['identifier'], item['active'])
                if custody._native_root_file(units, filename, mode=0o644) != (body, item['inode']):
                    raise ValueError('native-source-environment-generation-definition-changed')
                remaining[label] = {key: item[key] for key in ('role', 'identifier', 'active', 'inode', 'sha256')}
            return {'phase': phase, 'generation': generation, 'boot': boot, 'definitions': remaining}
        finally:
            if archive is not None:
                os.close(archive)
            os.close(gate)


def _environment_inactive_child(custody, metadata, gate, units, identity, binding, expected, record, child):
    """Full literal proof of a completed child which has never been admitted."""
    publication = custody._native_root_file(metadata, 'source-publication.json')[0]
    custody._native_job_record(child, dict(binding, publicationSha256=sha(publication)), expected)
    if (child['phase'] != 'committed-inactive' or child['generation'] != record['generation']
            or child['environment'] != record['environment'] or child['before']['inode'] != record['v1']['inode']
            or set(child['definitions']) != {custody.NATIVE_LABEL + role for role in ('sync', 'direct')}
            or not child['gate']['ready'] or not child['manifest']['ready']
            or any(not item['ready'] for item in child['definitions'].values())
            or any(custody._native_at_inode(metadata, name) is not None for name in (
                'source-jobs-active.json', 'source-execution.json'))
            or custody._native_root_file(gate, 'custody.json', mode=0o640, gid=identity['id']) != (
                custody._native_job_projection(child), child['gate']['inode'])
            or set(name for name in os.listdir(units) if name.startswith(custody.NATIVE_LABEL)) != {
                label + '.plist' for label in child['definitions']}):
        raise ValueError('native-source-environment-child-boot-not-inactive')
    environment, _ = custody._native_json_at(metadata, 'source-environment.json')
    custody._native_environment_record(environment, binding)
    if (environment['pending'] is not None or environment['current'] != child['environment']
            or custody._native_environment_snapshot(gate, custody.NATIVE_ENV.name, identity['id']) != child['environment']):
        raise ValueError('native-source-environment-child-boot-environment-changed')
    jobs = custody._native_job_directory(metadata, child)
    try:
        if (set(os.listdir(jobs)) != {'manifest.json', '.gate-before'}
                or custody._native_root_file(jobs, 'manifest.json') != (
                    custody._native_encoded(custody._native_job_manifest(child)), child['manifest']['inode'])
                or custody._native_root_file(jobs, '.gate-before', mode=0o640, gid=identity['id']) != (
                    expected, child['before']['inode'])):
            raise ValueError('native-source-environment-child-boot-prefix-changed')
        for label, item in child['definitions'].items():
            _path, body, _mode = disabled_definition(item['role'], identity=identity,
                python=child['before']['descriptor']['hostPython'], identifier=item['identifier'])
            if custody._native_root_file(units, label + '.plist', mode=0o644) != (body, item['inode']):
                raise ValueError('native-source-environment-child-boot-definition-changed')
    finally:
        os.close(jobs)


def _environment_generation_boundary(selection, custody, doc, component, ref, *, metadata, gate,
                                     identity, binding, expected, generation):
    """Closed replacement for the first kit publisher's now-historical v1 images.

    No ordinary caller can select this proof. Its exact preallocated generation
    must belong to the live Root environment transaction, after full withdrawal
    and the ordinary authenticated environment writer have both completed.
    """
    rebind, _rebind_raw = custody._native_json_at(metadata, 'source-installer-rebind.json')
    if rebind is not None and rebind.get('phase') == 'generation' and rebind.get('generation') == generation:
        return _installer_rebind_boundary(selection, custody, doc, metadata, gate, identity, binding, expected, rebind)
    initial, _initial_raw = custody._native_json_at(metadata, 'source-initial-generation.json')
    if initial is not None and initial.get('phase') == 'generation' and initial.get('generation') == generation:
        if verify_initial_generation_child(selection) != {'generation': generation, 'phase': 'absent'}:
            raise ValueError('native-source-initial-boundary-unconfirmed')
        _initial_generation_view(selection)
        return
    record, _raw = custody._native_json_at(metadata, 'source-environment-generation.json')
    full_binding = dict(binding, runtimeCustodySha256=doc['runtimeCustodySha256'], bundleSha256=doc['bundleSha256'], sourceRef=ref)
    _environment_generation_record(custody, record, full_binding)
    if (record['phase'] != 'generation' or record['generation'] != generation
            or custody._native_root_file(gate, 'custody.json', mode=0o640, gid=identity['id']) != (expected, record['v1']['inode'])
            or custody._native_environment_snapshot(gate, custody.NATIVE_ENV.name, identity['id']) != record['environment']
            or any(custody._native_at_inode(metadata, item) is not None for item in (
                'source-jobs-prepare.json', 'source-jobs-active.json', 'source-execution.json', 'native-source-jobs'))
            or any(item.startswith(custody.NATIVE_LABEL) for item in os.listdir(custody.NATIVE_DEFINITIONS))):
        raise ValueError('native-source-environment-generation-boundary-unconfirmed')
    environment, _ = custody._native_json_at(metadata, 'source-environment.json')
    custody._native_environment_record(environment, binding)
    if environment['pending'] is not None or environment['current'] != record['environment']:
        raise ValueError('native-source-environment-generation-boundary-unconfirmed')
    _environment_retained(custody, metadata, record)
    # Re-prove the closed old-generation chain, not only self-recorded hashes.
    environment_quiesce_snapshot(selection)
    publication, publication_raw = custody._native_json_at(metadata, 'source-publication.json')
    files = _publication_files(component, ref, selection.deployment.checksum)
    _publication_record(publication, selection.deployment.checksum, files)
    if (publication['phase'] != 'prepared-no-jobs' or sha(publication_raw) != record['old']['publication']['sha256']):
        raise ValueError('native-source-environment-generation-publication-changed')
    from pixel_macos_custody import protected_directory
    with protected_directory(custody.NATIVE_KIT.parent) as parent:
        image = {'inode': publication['candidateInode'], 'files': {name: {
            'inode': publication['stagedFiles'][name]['inode'], 'sha256': sha(body)} for name, body in files.items()}}
        _kit_image(custody, parent, custody.NATIVE_KIT.name, expected=image)
    custody._native_environment_context(selection, metadata, gate, binding['kitSha256'], _publication=True)
    custody._native_verify_prepared_logs(metadata, identity, binding)
    _environment_no_actors(selection, custody, metadata)


def publish_environment_generation(selection, text, *, expected_generation=None):
    return _publish_environment_generation(selection, text, expected_generation=expected_generation)


def _publish_environment_generation(selection, text, *, _installer=None, expected_generation=None):
    """Root-locked environment rotation; never dispatch, renew, or resurrect work.

    The original generation and target hash/new generation are frozen before
    withdrawal. All old authority is retained, but never restored by recovery.
    The v1 writer is used only after a complete, freshly quiescent v1 boundary.
    """
    if (expected_generation is not None and (type(expected_generation) is not str
            or not re.fullmatch('[a-f0-9]{32}', expected_generation)) or _installer is not None and expected_generation is not None):
        raise ValueError('native-source-environment-expected-generation-invalid')
    custody, doc, component, ref = _material(selection)
    data, kit_sha = custody._native_environment_bytes(selection, text, _publication=True)
    wanted = sha(data)
    from pixel_macos_custody import protected_directory
    name = 'source-environment-generation.json'
    fixed = [{'role': 'sync', 'identifier': None}, {'role': 'direct', 'identifier': None}]
    with protected_directory(custody.NATIVE_META) as metadata, protected_directory(custody.NATIVE_DEFINITIONS) as units:
        gate = os.open(custody.NATIVE_STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        archive = None
        try:
            identity, binding, expected = custody._native_environment_binding(selection, metadata, gate, kit_sha, _publication=True)
            full_binding = dict(binding, runtimeCustodySha256=doc['runtimeCustodySha256'], bundleSha256=doc['bundleSha256'], sourceRef=ref)
            record, raw = custody._native_json_at(metadata, name)
            if record is not None:
                _environment_generation_record(custody, record, full_binding)
                if 'installer' in record and _installer is None:
                    raise ValueError('native-source-installer-withdrawal-pending')
                if record['phase'] not in ('committed', 'withdrawn') and (
                        record['targetSha256'] != wanted or encoded(_installer_request(record.get('installer'))) != encoded(_installer_request(_installer))
                        or expected_generation is not None and expected_generation != record['previousGeneration']):
                    raise ValueError('native-source-environment-generation-target-changed')
                if record['phase'] == 'withdrawn':
                    if encoded(_installer_request(record['installer'])) != encoded(_installer_request(_installer)):
                        raise ValueError('native-source-installer-withdrawal-target-changed')
                    return _installer_withdrawal_complete(selection, custody, doc, metadata, gate, identity,
                        binding, expected, record, verify=True)
                if record['phase'] == 'committed':
                    _environment_retained(custody, metadata, record)
                    current, _ = custody._native_json_at(metadata, 'source-jobs-prepare.json')
                    if (current is None or current['generation'] != record['generation']
                            or custody._native_environment_snapshot(gate, custody.NATIVE_ENV.name, identity['id']) != record['environment']):
                        raise ValueError('native-source-environment-generation-current-changed')
                    fresh = _environment_no_actors(selection, custody, metadata)
                    if expected_generation is not None and expected_generation not in (record['previousGeneration'], record['generation']):
                        raise ValueError('native-source-environment-expected-generation-changed')
                    if (expected_generation == record['previousGeneration']
                            and (record['targetSha256'] != wanted or record['boot'] != fresh['boot'])):
                        raise ValueError('native-source-environment-expected-generation-advanced')
                    if (_installer is None and record['targetSha256'] == wanted and record['boot'] == fresh['boot']
                            and expected_generation != record['generation']):
                        # Lost reply observes its original inactive generation.
                        custody._native_validate_inactive_generation(selection, metadata, gate, identity, binding,
                            expected, custody._native_root_file(gate, 'custody.json', mode=0o640, gid=identity['id'])[0],
                            custody._native_at_inode(gate, 'custody.json'))
                        return {'status': 'inactive', 'previousGeneration': record['previousGeneration'],
                            'generation': record['generation'], 'environment': dict(record['environment'])}
                    retained = 'source-environment-generation-' + record['generation'] + '.json'
                    if custody._native_at_inode(metadata, retained) is not None:
                        raise ValueError('native-source-environment-generation-history-conflict')
                    os.rename(name, retained, src_dir_fd=metadata, dst_dir_fd=metadata)
                    os.fsync(metadata)
                    record, raw = None, None
            if record is None:
                origin, boot, tree, definitions = _environment_origin(selection, custody, doc, component, ref,
                    metadata, gate, units, identity, binding, expected)
                if expected_generation is not None and expected_generation != origin['generation']:
                    raise ValueError('native-source-environment-expected-generation-changed')
                environment = custody._native_environment_snapshot(gate, custody.NATIVE_ENV.name, identity['id'])
                env_record, _ = custody._native_json_at(metadata, 'source-environment.json')
                custody._native_environment_record(env_record, binding)
                if env_record['pending'] is not None or env_record['current'] != environment:
                    raise ValueError('native-source-environment-unsettled')
                files = {}
                for old_name in ('source-jobs-prepare.json', 'source-jobs-active.json', 'source-execution.json'):
                    if custody._native_at_inode(metadata, old_name) is not None:
                        files[old_name] = _environment_image(custody, metadata, old_name)[0]
                record = {'schemaVersion': 2, 'kind': 'ods-native-source-environment-generation',
                    'binding': full_binding, 'previousGeneration': origin['generation'], 'generation': uuid.uuid4().hex,
                    'targetSha256': wanted, 'phase': 'staging', 'archive': None,
                    'old': {'gate': _environment_image(custody, gate, 'custody.json', mode=0o640, gid=identity['id'])[0],
                        'directory': origin['directory'], 'jobs': tree, 'files': files,
                        'definitions': definitions, 'environment': environment,
                        'publication': _environment_image(custody, metadata, 'source-publication.json')[0],
                        'environmentJournal': _environment_image(custody, metadata, 'source-environment.json')[0]},
                    'copies': {key: {'inode': None, 'ready': False} for key in
                        ('environment.before', 'environment-journal.before', 'publication.before')},
                    'v1': {'inode': None, 'ready': False}, 'environment': None, 'boot': boot}
                if _installer is not None:
                    record['installer'] = dict(_installer)
                    _installer_withdrawal_guard(selection, custody, doc, metadata, gate, identity, full_binding, record)
                _environment_generation_record(custody, record, full_binding)
                raw = custody._native_save_at(selection, metadata, name, record, previous=None, _publication=True)
            retained = _environment_archive_name(record)
            if record['archive'] is None:
                if custody._native_at_inode(metadata, retained) is not None:
                    raise ValueError('native-source-environment-generation-unmanaged-archive')
                _environment_no_actors(selection, custody, metadata)
                os.mkdir(retained, mode=0o700, dir_fd=metadata)
                archive = os.open(retained, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=metadata)
                record['archive'] = _environment_inode(os.fstat(archive))
                os.fsync(metadata)
                raw = custody._native_save_at(selection, metadata, name, record, previous=raw, _publication=True)
            else:
                archive = os.open(retained, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=metadata)

            def exact(directory, filename, image):
                actual, _body = _environment_image(custody, directory, filename, mode=image['mode'], gid=image['gid'])
                if actual != image:
                    raise ValueError('native-source-environment-generation-image-changed')

            def guard():
                _environment_generation_record(custody, record, full_binding)
                current_identity, current_binding, current_expected = custody._native_environment_binding(
                    selection, metadata, gate, kit_sha, _publication=True)
                info = os.fstat(archive)
                if (custody._native_selected(selection, _publication=True) != doc or current_identity != identity
                        or current_binding != binding or current_expected != expected
                        or custody._native_json_at(metadata, name)[1] != raw
                        or _environment_inode(info) != record['archive'] or custody._native_at_inode(metadata, retained) != record['archive']
                        or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, 0o700)):
                    raise ValueError('native-source-environment-generation-binding-changed')
                custody._native_no_acl(archive)
                _environment_no_actors(selection, custody, metadata)
                custody._native_verify_prepared_logs(metadata, identity, binding)
                if 'installer' in record:
                    _installer_withdrawal_guard(selection, custody, doc, metadata, gate, identity, full_binding, record)
                exact(metadata, 'source-publication.json', record['old']['publication'])
                if record['phase'] in ('staging', 'withdrawing'):
                    exact(metadata, 'source-environment.json', record['old']['environmentJournal'])
                    if custody._native_environment_snapshot(gate, custody.NATIVE_ENV.name, identity['id']) != record['old']['environment']:
                        raise ValueError('native-source-environment-generation-input-changed')
                allowed = {'custody.before.json', 'native-source-jobs', '.v1-next', *record['copies'],
                    *record['old']['files'], *record['old']['definitions'],
                    *[name for item in record.get('childBoots', []) for name in _environment_boot_names(item)]}
                if 'installer' in record:
                    allowed.add('withdrawal-receipt.json')
                if set(os.listdir(archive)) - allowed:
                    raise ValueError('native-source-environment-generation-archive-changed')
                copy_hashes = {'environment.before': record['old']['environment']['sha256'],
                    'environment-journal.before': record['old']['environmentJournal']['sha256'],
                    'publication.before': record['old']['publication']['sha256']}
                for filename, staged in record['copies'].items():
                    if staged['ready']:
                        body, inode = custody._native_root_file(archive, filename)
                        if inode != staged['inode'] or sha(body) != copy_hashes[filename]:
                            raise ValueError('native-source-environment-generation-copy-changed')
                for filename, image in {**record['old']['files'], **record['old']['definitions']}.items():
                    directory = units if filename.endswith('.plist') else metadata
                    saved = custody._native_at_inode(archive, filename)
                    present = custody._native_at_inode(directory, filename)
                    if saved is not None:
                        exact(archive, filename, image)
                        if present is not None and record['phase'] not in ('generation', 'committed'):
                            raise ValueError('native-source-environment-generation-image-conflict')
                    elif present is None:
                        raise ValueError('native-source-environment-generation-image-missing')
                    else:
                        exact(directory, filename, image)
                child = None
                if record['phase'] in ('generation', 'committed'):
                    if any(custody._native_at_inode(metadata, filename) is not None
                            for filename in ('source-jobs-active.json', 'source-execution.json')):
                        raise ValueError('native-source-environment-generation-unexpected-actor-journal')
                    child, _ = _environment_boot_child(custody, metadata, archive, record, binding, expected)
                    if child is not None:
                        publication = custody._native_root_file(metadata, 'source-publication.json')[0]
                        custody._native_job_record(child, dict(binding, publicationSha256=sha(publication)), expected)
                        wanted_labels = {custody.NATIVE_LABEL + role for role in ('sync', 'direct')}
                        if (child['generation'] != record['generation'] or child['environment'] != record['environment']
                                or set(child['definitions']) != wanted_labels):
                            raise ValueError('native-source-environment-generation-child-changed')
                    for filename in (item for item in os.listdir(units) if item.startswith(custody.NATIVE_LABEL)):
                        item = child and child['definitions'].get(filename[:-6])
                        if not filename.endswith('.plist') or item is None or not item['ready']:
                            raise ValueError('native-source-environment-generation-child-changed')
                        _path, body, _mode = disabled_definition(item['role'], identity=identity,
                            python=doc['hostPythonPath'], identifier=item['identifier'])
                        if custody._native_root_file(units, filename, mode=0o644) != (body, item['inode']):
                            raise ValueError('native-source-environment-generation-child-changed')
                for directory in (metadata, archive):
                    located = custody._native_at_inode(directory, 'native-source-jobs')
                    if located == record['old']['directory']:
                        jobs = os.open('native-source-jobs', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                        try:
                            if not _environment_history_matches(custody, jobs, identity['id'], record):
                                raise ValueError('native-source-environment-generation-history-changed')
                        finally:
                            os.close(jobs)
                        break
                else:
                    raise ValueError('native-source-environment-generation-history-missing')
                old_gate = custody._native_at_inode(archive, 'custody.before.json')
                if old_gate is not None:
                    exact(archive, 'custody.before.json', record['old']['gate'])
                    present = custody._native_at_inode(gate, 'custody.json')
                    if present is not None:
                        current = custody._native_root_file(gate, 'custody.json', mode=0o640, gid=identity['id'])
                        if current != (expected, record['v1']['inode']) and not (
                                child is not None and child['gate']['ready'] and current == (
                                    custody._native_job_projection(child), child['gate']['inode'])):
                            raise ValueError('native-source-environment-generation-gate-changed')
                else:
                    exact(gate, 'custody.json', record['old']['gate'])

            def save():
                nonlocal raw
                guard()
                raw = custody._native_save_at(selection, metadata, name, record, previous=raw, _publication=True)

            guard()
            if record['phase'] == 'staging':
                # These Root-private copies retain prior env/publication evidence;
                # they are never accepted as a live env or execution journal.
                fd = os.open(custody.NATIVE_ENV.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=gate)
                try:
                    custody.native_verify_env_fd(fd, identity['id'])
                    environment_body = os.read(fd, custody.MAX_RESULT_BYTES + 1)
                finally:
                    os.close(fd)
                if sha(environment_body) != record['old']['environment']['sha256']:
                    raise ValueError('native-source-environment-generation-input-changed')
                bodies = {'environment.before': environment_body,
                    'environment-journal.before': custody._native_root_file(metadata, 'source-environment.json')[0],
                    'publication.before': custody._native_root_file(metadata, 'source-publication.json')[0]}
                for filename, body in bodies.items():
                    _generation_stage(custody, archive, filename, body, record['copies'][filename], save, guard)
                record['phase'] = 'withdrawing'
                save()
            if record['phase'] == 'withdrawing':
                # Revoke FIRST. Crashes leave missing authority, never an old
                # invocation paired with new environment bytes.
                moves = [(gate, 'custody.json', 'custody.before.json'),
                    *[(units, item, item) for item in record['old']['definitions']],
                    *[(metadata, item, item) for item in record['old']['files']],
                    (metadata, 'native-source-jobs', 'native-source-jobs')]
                for directory, source_name, target_name in moves:
                    if custody._native_at_inode(archive, target_name) is not None:
                        continue
                    guard()
                    os.rename(source_name, target_name, src_dir_fd=directory, dst_dir_fd=archive)
                    os.fsync(directory)
                    os.fsync(archive)
                if custody._native_at_inode(gate, 'custody.json') is None:
                    _generation_stage(custody, archive, '.v1-next', expected, record['v1'], save, guard,
                        mode=0o640, gid=identity['id'])
                if custody._native_at_inode(gate, 'custody.json') is None:
                    guard()
                    os.rename('.v1-next', 'custody.json', src_dir_fd=archive, dst_dir_fd=gate)
                    os.fsync(archive)
                    os.fsync(gate)
                if custody._native_root_file(gate, 'custody.json', mode=0o640, gid=identity['id']) != (expected, record['v1']['inode']):
                    raise ValueError('native-source-environment-generation-v1-changed')
                record['phase'] = 'environment'
                save()
            if record['phase'] == 'environment':
                guard()
                if any(custody._native_at_inode(metadata, item) is not None for item in record['old']['files']) or any(
                        item.startswith(custody.NATIVE_LABEL) for item in os.listdir(units)):
                    raise ValueError('native-source-environment-generation-withdrawal-incomplete')
                if _installer is not None:
                    # The explicit installer domain ends at a complete v1
                    # boundary. It never changes env or creates another child.
                    result = _installer_withdrawal_complete(selection, custody, doc, metadata, gate, identity,
                        binding, expected, record, verify=False, archive=archive, save=save, guard=guard)
                    record['phase'] = 'withdrawn'
                    save()
                    return result
                record['environment'] = custody.native_publish_environment(selection, text)
                record['phase'] = 'generation'
                save()
            if record['phase'] == 'generation':
                guard()
                child, _child_raw = _environment_boot_child(custody, metadata, archive, record, binding, expected)
                witness = _environment_no_actors(selection, custody, metadata)
                boots = record.get('childBoots', [])
                pending = boots[-1] if boots and boots[-1]['phase'] == 'staging' else None
                if pending is not None or child is not None and child['witness']['boot'] != witness['boot']:
                    # Only a completed, fully-disabled, never-admitted child is
                    # eligible. Incomplete prefixes remain preserved/refused.
                    _environment_inactive_child(custody, metadata, gate, units, identity, binding, expected, record, child)
                    if pending is None:
                        if len(boots) >= 16:
                            raise ValueError('native-source-environment-child-boot-history-full')
                        before_image, before_raw = _environment_image(custody, metadata, 'source-jobs-prepare.json')
                        if before_raw != custody._native_encoded(child):
                            raise ValueError('native-source-environment-child-boot-before-changed')
                        after = dict(child, witness=dict(child['witness'], boot=witness['boot']))
                        pending = {'beforeBoot': child['witness']['boot'], 'boot': witness['boot'],
                            'before': before_image, 'afterSha256': sha(custody._native_encoded(after)),
                            'after': {'inode': None, 'ready': False}, 'phase': 'staging'}
                        record.setdefault('childBoots', []).append(pending)
                        save()  # Bind the original journal and target before allocating a new inode.
                    if witness['boot'] != pending['boot']:
                        raise ValueError('native-source-environment-child-boot-recovery-boot-changed')
                    before_name, next_name = _environment_boot_names(pending)
                    if custody._native_at_inode(archive, before_name) is None:
                        _image, before_raw = _environment_image(custody, metadata, 'source-jobs-prepare.json')
                    else:
                        _image, before_raw = _environment_image(custody, archive, before_name)
                    before = custody._native_public_json(before_raw)
                    after = dict(before, witness=dict(before['witness'], boot=pending['boot']))
                    after_raw = custody._native_encoded(after)
                    def boot_guard():
                        guard()
                        current, _raw = _environment_boot_child(custody, metadata, archive, record, binding, expected)
                        _environment_inactive_child(custody, metadata, gate, units, identity, binding, expected, record, current)
                        if _environment_no_actors(selection, custody, metadata)['boot'] != pending['boot']:
                            raise ValueError('native-source-environment-child-boot-recovery-boot-changed')
                    current_inode = custody._native_at_inode(metadata, 'source-jobs-prepare.json')
                    if current_inode != pending['after']['inode']:
                        _generation_stage(custody, archive, next_name, after_raw, pending['after'], save, boot_guard)
                        if custody._native_at_inode(archive, before_name) is None:
                            boot_guard()
                            os.rename('source-jobs-prepare.json', before_name, src_dir_fd=metadata, dst_dir_fd=archive)
                            os.fsync(metadata)
                            os.fsync(archive)
                        if custody._native_at_inode(metadata, 'source-jobs-prepare.json') is None:
                            boot_guard()
                            os.rename(next_name, 'source-jobs-prepare.json', src_dir_fd=archive, dst_dir_fd=metadata)
                            os.fsync(archive)
                            os.fsync(metadata)
                    boot_guard()
                    if custody._native_root_file(metadata, 'source-jobs-prepare.json') != (after_raw, pending['after']['inode']):
                        raise ValueError('native-source-environment-child-boot-after-changed')
                    pending['phase'] = 'committed'
                    save()
                result = publish_disabled_generation(selection, fixed, _generation=record['generation'])
                if result['generation'] != record['generation']:
                    raise ValueError('native-source-environment-generation-child-changed')
                record['boot'] = _environment_no_actors(selection, custody, metadata)['boot']
                record['phase'] = 'committed'
                save()
            return {'status': 'inactive', 'previousGeneration': record['previousGeneration'],
                'generation': record['generation'], 'environment': dict(record['environment'])}
        finally:
            if archive is not None:
                os.close(archive)
            os.close(gate)


def _installer_domain(transaction):
    if type(transaction) is not dict:
        raise ValueError('native-source-withdrawal-purpose-invalid')
    if transaction.get('kind') == 'native-installer-upgrade':
        return 'installer'
    if transaction.get('kind') == 'native-installer-retirement':
        return 'retirement'
    raise ValueError('native-source-withdrawal-purpose-invalid')


def _installer_validate(custody, kind, value, *, transaction, owner_uid, owner_gid, intent=None):
    name = 'native_' + _installer_domain(transaction) + '_withdrawal_' + kind
    verifier = getattr(custody, name, None)
    if not callable(verifier):
        raise ValueError('native-source-withdrawal-runtime-verifier-unavailable')
    arguments = {'transaction': transaction, 'owner_uid': owner_uid, 'owner_gid': owner_gid}
    if kind == 'receipt':
        arguments['intent'] = intent
    return verifier(value, **arguments)


def _installer_authority(custody, selection, transaction, *, prepared):
    """Only a fixed verifier from the protected Source runtime closure."""
    name = 'native_' + _installer_domain(transaction) + '_withdrawal_' + ('transaction' if prepared else 'request')
    verifier = getattr(custody, name, None)
    if not callable(verifier):
        raise ValueError('native-source-installer-runtime-verifier-unavailable')
    return verifier(selection, transaction=transaction)


def _installer_token_metadata(custody, gate, broker_uid):
    """No token contents are read, copied, normalized or returned."""
    private = custody._native_directory_fd(gate, 'private')
    try:
        try:
            fd = os.open('google-token.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=private)
        except FileNotFoundError:
            return None
        try:
            info = os.fstat(fd)
            custody._native_no_acl(fd)
            if (not stat.S_ISREG(info.st_mode) or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (
                    broker_uid, broker_uid, 0o600) or info.st_nlink != 1 or not 0 < info.st_size <= custody.MAX_RESULT_BYTES
                    or custody._native_at_inode(private, 'google-token.json') != [info.st_dev, info.st_ino]):
                raise ValueError('native-source-installer-token-metadata-unqualified')
            return [info.st_dev, info.st_ino, info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode),
                info.st_size, info.st_nlink, info.st_mtime_ns, info.st_ctime_ns]
        finally:
            os.close(fd)
    finally:
        os.close(private)


def _installer_intent_record(custody, value, transaction, binding):
    fields = {'schemaVersion', 'kind', 'transaction', 'binding', 'runtime', 'owner', 'generation',
        'boot', 'environment', 'gate', 'metadata', 'token'}
    if (type(value) is not dict or set(value) != fields or type(value['schemaVersion']) is not int
            or value['schemaVersion'] != 1 or value['kind'] != 'ods-native-source-' + _installer_domain(transaction) + '-withdrawal-intent'
            or encoded(value['transaction']) != encoded(transaction) or encoded(value['binding']) != encoded(binding)
            or value['runtime'] != {'custodySha256': binding['runtimeCustodySha256'],
                'bundleSha256': binding['bundleSha256'], 'sourceRef': binding['sourceRef']}
            or type(value['owner']) is not dict or set(value['owner']) != {'uid', 'gid'}
            or type(value['owner']['uid']) is not int or value['owner']['uid'] != binding['ownerUid']
            or type(value['owner']['gid']) is not int or value['owner']['gid'] < 0
            or type(value['generation']) is not str or not re.fullmatch('[a-f0-9]{32}', value['generation'])
            or type(value['boot']) is not str or not re.fullmatch('[A-F0-9]{8}(?:-[A-F0-9]{4}){3}-[A-F0-9]{12}', value['boot'])
            or type(value['metadata']) is not dict or set(value['metadata']) != {
                'identitySha256', 'prepareSha256', 'logsSha256', 'publicationSha256'}
            or any(type(item) is not str or not HEX.fullmatch(item) for item in value['metadata'].values())):
        raise ValueError('native-source-installer-withdrawal-intent-changed')
    environment = value['environment']
    image = value['gate']
    if (type(environment) is not dict or set(environment) != {'inode', 'sha256', 'size'}
            or not custody._native_inode(environment['inode']) or type(environment['sha256']) is not str
            or not HEX.fullmatch(environment['sha256']) or type(environment['size']) is not int
            or not 0 < environment['size'] <= custody.MAX_RESULT_BYTES
            or type(image) is not dict or set(image) != {'inode', 'sha256', 'mode', 'gid'}
            or not custody._native_inode(image['inode']) or type(image['sha256']) is not str or not HEX.fullmatch(image['sha256'])
            or type(image['mode']) is not int or image['mode'] != 0o640
            or type(image['gid']) is not int or image['gid'] != binding['brokerUid']):
        raise ValueError('native-source-installer-withdrawal-intent-changed')
    token = value['token']
    if token is not None and (type(token) is not list or len(token) != 9 or any(type(item) is not int for item in token)
            or not custody._native_inode(token[:2]) or token[2:5] != [binding['brokerUid'], binding['brokerUid'], 0o600]
            or not 0 < token[5] <= custody.MAX_RESULT_BYTES or token[6] != 1 or any(item < 0 for item in token[7:])):
        raise ValueError('native-source-installer-withdrawal-token-metadata-changed')


def _installer_request(value):
    return None if value is None else {'transaction': value['transaction'], 'intent': value['intent']}


def _installer_withdrawal_guard(selection, custody, doc, metadata, gate, identity, binding, record):
    value = record['installer']
    if (type(value) is not dict or set(value) != {'transaction', 'intent', 'receipt', 'boot'}
            or type(value['receipt']) is not dict or set(value['receipt']) != {'inode', 'ready'}
            or type(value['receipt']['ready']) is not bool
            or value['receipt']['inode'] is not None and not custody._native_inode(value['receipt']['inode'])
            or value['receipt']['ready'] and value['receipt']['inode'] is None
            or value['boot'] is not None and (type(value['boot']) is not str
                or not re.fullmatch('[A-F0-9]{8}(?:-[A-F0-9]{4}){3}-[A-F0-9]{12}', value['boot']))):
        raise ValueError('native-source-installer-withdrawal-record-changed')
    intent, transaction = value['intent'], value['transaction']
    _installer_intent_record(custody, intent, transaction, binding)
    _installer_validate(custody, 'intent', intent, transaction=transaction, owner_uid=doc['ownerUid'], owner_gid=doc['ownerGid'])
    authority = _installer_authority(custody, selection, transaction, prepared=True)
    proof = authority.get('proof')
    purpose = _installer_domain(transaction)
    qualified_intent = (authority['intent'].get('mode') == 'withdrawal' if purpose == 'installer'
        else authority['intent'].get('kind') == 'ods-native-source-retirement-intent')
    if (not qualified_intent or authority['phase'] != 'prepared'
            or encoded(proof) != encoded(intent)
            or authority['owner']['uid'] != doc['ownerUid'] or authority['owner']['gid'] != doc['ownerGid']
            or intent['owner'] != {'uid': doc['ownerUid'], 'gid': doc['ownerGid']}
            or intent['generation'] != record['previousGeneration'] or intent['gate'] != record['old']['gate']
            or intent['environment'] != record['old']['environment']
            or transaction.get('currentDigest') != doc['bundleSha256']
            or _installer_token_metadata(custody, gate, identity['id']) != intent['token']
            or custody._native_environment_snapshot(gate, custody.NATIVE_ENV.name, identity['id']) != intent['environment']):
        raise ValueError('native-source-installer-withdrawal-authority-changed')
    anchors = {key: sha(custody._native_root_file(metadata, filename)[0]) for key, filename in (
        ('identitySha256', 'source-identity.json'), ('prepareSha256', 'source-custody-prepare.json'),
        ('logsSha256', 'source-logs-prepare.json'), ('publicationSha256', 'source-publication.json'))}
    if anchors != intent['metadata']:
        raise ValueError('native-source-installer-withdrawal-metadata-changed')
    return authority


def _installer_withdrawal_complete(selection, custody, doc, metadata, gate, identity, binding, expected,
                                   record, *, verify, archive=None, save=None, guard=None):
    full_binding = dict(binding, runtimeCustodySha256=doc['runtimeCustodySha256'],
        bundleSha256=doc['bundleSha256'], sourceRef=record['binding']['sourceRef'])
    authority = _installer_withdrawal_guard(selection, custody, doc, metadata, gate, identity, full_binding, record)
    from pixel_macos_custody import protected_directory
    with protected_directory(custody.NATIVE_DEFINITIONS) as units:
        if (custody._native_root_file(gate, 'custody.json', mode=0o640, gid=identity['id']) != (expected, record['v1']['inode'])
                or any(name.startswith(custody.NATIVE_LABEL) for name in os.listdir(units))
                or any(custody._native_at_inode(metadata, name) is not None for name in (
                    'native-source-jobs', 'source-jobs-prepare.json', 'source-jobs-active.json', 'source-execution.json'))):
            raise ValueError('native-source-installer-withdrawal-v1-unconfirmed')
    _environment_retained(custody, metadata, record)
    value, intent = record['installer'], record['installer']['intent']
    witness = _environment_no_actors(selection, custody, metadata)
    if value['boot'] is None:
        if verify or save is None:
            raise ValueError('native-source-installer-withdrawal-receipt-missing')
        value['boot'] = witness['boot']
        save()
    descriptor = {'inode': record['v1']['inode'], 'sha256': sha(expected), 'mode': 0o640, 'gid': identity['id']}
    purpose = _installer_domain(value['transaction'])
    audit = {'schemaVersion': 1, 'kind': 'ods-native-source-' + purpose + '-withdrawal-archive',
        'transaction': value['transaction'], 'intentSha256': sha(encoded(intent)),
        'binding': full_binding, 'old': record['old'], 'descriptor': descriptor,
        'withdrawal': record['generation'], 'boot': value['boot']}
    body = encoded(audit)
    close = False
    if archive is None:
        archive = os.open(_environment_archive_name(record), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=metadata)
        close = True
    try:
        if not verify:
            _generation_stage(custody, archive, 'withdrawal-receipt.json', body, value['receipt'], save, guard)
        if (not value['receipt']['ready'] or custody._native_root_file(archive, 'withdrawal-receipt.json') != (
                body, value['receipt']['inode'])):
            raise ValueError('native-source-installer-withdrawal-receipt-changed')
        receipt = {'schemaVersion': 1, 'kind': 'ods-native-source-' + purpose + '-withdrawal',
            'transaction': value['transaction'], 'intentSha256': sha(encoded(intent)), 'binding': full_binding,
            'runtime': intent['runtime'], 'owner': intent['owner'], 'generation': record['previousGeneration'],
            'boot': value['boot'], 'environment': intent['environment'], 'token': intent['token'],
            'withdrawal': record['generation'], 'archive': {'name': _environment_archive_name(record),
                'inode': record['archive'], 'receiptSha256': sha(body)}, 'descriptor': descriptor, 'metadata': intent['metadata']}
        if authority.get('receipt') is not None and encoded(authority['receipt']) != encoded(receipt):
            raise ValueError('native-source-installer-withdrawal-receipt-binding-changed')
        _installer_validate(custody, 'receipt', receipt, intent=intent, transaction=value['transaction'],
            owner_uid=doc['ownerUid'], owner_gid=doc['ownerGid'])
        _environment_no_actors(selection, custody, metadata)
        return receipt
    finally:
        if close:
            os.close(archive)


def withdraw_installer_generation(selection, *, transaction, intent):
    """Distinct installer transaction, data-only and stopped at complete v1."""
    if _installer_domain(transaction) != 'installer':
        raise ValueError('native-source-withdrawal-purpose-invalid')
    return _withdraw_generation(selection, transaction=transaction, intent=intent)


def withdraw_retirement_generation(selection, *, transaction, intent):
    """Distinct immutable retirement witness; never an upgrade-shaped request."""
    if _installer_domain(transaction) != 'retirement':
        raise ValueError('native-source-withdrawal-purpose-invalid')
    return _withdraw_generation(selection, transaction=transaction, intent=intent)


def _withdraw_generation(selection, *, transaction, intent):
    custody, _doc, _component, _ref = _material(selection)
    gate = os.open(custody.NATIVE_STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        # Environment text is not a token. Validate unchanged fixed fields;
        # this route never writes or normalizes the existing environment.
        fd = os.open(custody.NATIVE_ENV.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=gate)
        try:
            body = os.read(fd, custody.MAX_RESULT_BYTES + 1)
        finally:
            os.close(fd)
    finally:
        os.close(gate)
    return _publish_environment_generation(selection, body.decode('utf-8'), _installer={
        'transaction': dict(transaction), 'intent': intent,
        'receipt': {'inode': None, 'ready': False}, 'boot': None})


def verify_installer_withdrawal(selection, *, transaction, intent, receipt):
    """Fresh prospective-or-bound receipt proof for prepared installer CAS."""
    if _installer_domain(transaction) != 'installer':
        raise ValueError('native-source-withdrawal-purpose-invalid')
    return _verify_withdrawal(selection, transaction=transaction, intent=intent, receipt=receipt)


def verify_retirement_withdrawal(selection, *, transaction, intent, receipt):
    if _installer_domain(transaction) != 'retirement':
        raise ValueError('native-source-withdrawal-purpose-invalid')
    return _verify_withdrawal(selection, transaction=transaction, intent=intent, receipt=receipt)


def _verify_withdrawal(selection, *, transaction, intent, receipt):
    custody, doc, _component, ref = _material(selection)
    from pixel_macos_custody import protected_directory
    with protected_directory(custody.NATIVE_META) as metadata:
        gate = os.open(custody.NATIVE_STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            _kit, kit_sha = custody.native_verify_kit(selection, require_profiles=True, _publication=True)
            identity, binding, expected = custody._native_environment_binding(selection, metadata, gate, kit_sha, _publication=True)
            record, _raw = custody._native_json_at(metadata, 'source-environment-generation.json')
            _environment_generation_record(custody, record, dict(binding,
                runtimeCustodySha256=doc['runtimeCustodySha256'], bundleSha256=doc['bundleSha256'], sourceRef=ref))
            if (record['phase'] != 'withdrawn' or _installer_request(record.get('installer')) != {
                    'transaction': transaction, 'intent': intent}):
                raise ValueError('native-source-installer-withdrawal-receipt-unconfirmed')
            actual = _installer_withdrawal_complete(selection, custody, doc, metadata, gate, identity,
                binding, expected, record, verify=True)
            if encoded(actual) != encoded(receipt):
                raise ValueError('native-source-installer-withdrawal-receipt-changed')
            return actual
        finally:
            os.close(gate)


def installer_withdrawal_intent(selection, *, transaction):
    """Read-only actual before-state for a distinct protected installer intent."""
    if _installer_domain(transaction) != 'installer':
        raise ValueError('native-source-withdrawal-purpose-invalid')
    return _withdrawal_intent(selection, transaction=transaction)


def retirement_withdrawal_intent(selection, *, transaction):
    if _installer_domain(transaction) != 'retirement':
        raise ValueError('native-source-withdrawal-purpose-invalid')
    return _withdrawal_intent(selection, transaction=transaction)


def _withdrawal_intent(selection, *, transaction):
    custody, doc, component, ref = _material(selection)
    authority = _installer_authority(custody, selection, transaction, prepared=False)
    purpose = _installer_domain(transaction)
    qualified_intent = (authority['intent'].get('mode') == 'withdrawal' if purpose == 'installer'
        else authority['intent'].get('kind') == 'ods-native-source-retirement-intent')
    if (not qualified_intent or transaction['currentDigest'] != doc['bundleSha256']
            or authority['owner']['uid'] != doc['ownerUid'] or authority['owner']['gid'] != doc['ownerGid']):
        raise ValueError('native-source-installer-owner-selection-changed')
    from pixel_macos_custody import protected_directory
    with protected_directory(custody.NATIVE_META) as metadata, protected_directory(custody.NATIVE_DEFINITIONS) as units:
        gate = os.open(custody.NATIVE_STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            _manifest, kit_sha = custody.native_verify_kit(selection, require_profiles=True, _publication=True)
            identity, binding, expected = custody._native_environment_binding(selection, metadata, gate, kit_sha, _publication=True)
            origin, boot, _tree, _definitions = _environment_origin(selection, custody, doc, component, ref,
                metadata, gate, units, identity, binding, expected)
            environment = custody._native_environment_snapshot(gate, custody.NATIVE_ENV.name, identity['id'])
            env, _raw = custody._native_json_at(metadata, 'source-environment.json')
            custody._native_environment_record(env, binding)
            if env['pending'] is not None or env['current'] != environment:
                raise ValueError('native-source-installer-environment-unsettled')
            full_binding = dict(binding, runtimeCustodySha256=doc['runtimeCustodySha256'], bundleSha256=doc['bundleSha256'], sourceRef=ref)
            anchors = {key: sha(custody._native_root_file(metadata, filename)[0]) for key, filename in (
                ('identitySha256', 'source-identity.json'), ('prepareSha256', 'source-custody-prepare.json'),
                ('logsSha256', 'source-logs-prepare.json'), ('publicationSha256', 'source-publication.json'))}
            result = {'schemaVersion': 1, 'kind': 'ods-native-source-' + purpose + '-withdrawal-intent',
                'transaction': dict(transaction), 'binding': full_binding,
                'runtime': {'custodySha256': doc['runtimeCustodySha256'], 'bundleSha256': doc['bundleSha256'], 'sourceRef': ref},
                'owner': {'uid': doc['ownerUid'], 'gid': doc['ownerGid']}, 'generation': origin['generation'],
                'boot': boot, 'environment': environment,
                'gate': _environment_image(custody, gate, 'custody.json', mode=0o640, gid=identity['id'])[0],
                'metadata': anchors, 'token': _installer_token_metadata(custody, gate, identity['id'])}
            _installer_intent_record(custody, result, transaction, full_binding)
            _installer_validate(custody, 'intent', result, transaction=transaction, owner_uid=doc['ownerUid'], owner_gid=doc['ownerGid'])
            if _installer_authority(custody, selection, transaction, prepared=False) != authority:
                raise ValueError('native-source-installer-context-changed')
            _environment_no_actors(selection, custody, metadata)
            return result
        finally:
            os.close(gate)


def _installer_rebind_record(custody, record):
    keys = {'schemaVersion', 'kind', 'transaction', 'intentSha256', 'receiptSha256', 'nonce', 'phase',
        'oldBinding', 'binding', 'gate', 'children', 'environment', 'token', 'ownerGuid', 'kitSha256',
        'kitParent', 'oldKit', 'candidate', 'backup', 'candidateInode', 'stagedFiles', 'bindings',
        'publication', 'generation', 'witness', 'withdrawalParent'}
    if (type(record) is not dict or set(record) != keys or type(record['schemaVersion']) is not int
            or record['schemaVersion'] != 1 or record['kind'] != 'ods-native-source-installer-rebind'
            or type(record['nonce']) is not str or not re.fullmatch('[a-f0-9]{32}', record['nonce'])
            or type(record['generation']) is not str or not re.fullmatch('[a-f0-9]{32}', record['generation'])
            or record['phase'] not in ('staging', 'publishing-kit', 'binding', 'publication', 'generation', 'committed')
            or record['candidate'] != '.source-kit-' + record['nonce']
            or record['backup'] != '.source-kit-before-' + record['nonce']
            or any(type(record[key]) is not str or not HEX.fullmatch(record[key])
                for key in ('intentSha256', 'receiptSha256', 'kitSha256'))
            or not custody._native_inode(record['kitParent']) or not custody._native_inode(record['gate'])
            or record['candidateInode'] is not None and not custody._native_inode(record['candidateInode'])
            or type(record['bindings']) is not dict or set(record['bindings']) != {
                'custody.json', 'source-custody-prepare.json', 'source-environment.json', 'source-logs-prepare.json', 'source-sidefiles.json'}
            or type(record['stagedFiles']) is not dict or set(record['stagedFiles']) != {'source-kit.json', *PAYLOADS}):
        raise ValueError('native-source-installer-rebind-record-changed')
    for image in record['stagedFiles'].values():
        if (type(image) is not dict or set(image) != {'inode', 'ready'} or type(image['ready']) is not bool
                or image['inode'] is not None and not custody._native_inode(image['inode'])
                or image['ready'] and image['inode'] is None):
            raise ValueError('native-source-installer-rebind-record-changed')
    for name, image in record['bindings'].items():
        if (type(image) is not dict or set(image) != {'before', 'beforeInode', 'after', 'afterInode', 'ready', 'mode', 'gid'}
                or type(image['before']) is not dict or type(image['after']) is not dict
                or not custody._native_inode(image['beforeInode'])
                or image['afterInode'] is not None and not custody._native_inode(image['afterInode'])
                or type(image['ready']) is not bool or image['ready'] and image['afterInode'] is None
                or type(image['mode']) is not int or image['mode'] != (0o640 if name == 'custody.json' else 0o600)
                or type(image['gid']) is not int or image['gid'] != (record['binding']['brokerUid'] if name == 'custody.json' else 0)):
            raise ValueError('native-source-installer-rebind-record-changed')
    image = record['publication']
    retained = record['withdrawalParent']
    if (type(image) is not dict or set(image) != {'before', 'beforeInode', 'after', 'afterInode', 'ready'}
            or type(image['before']) is not dict or image['after'] is not None and type(image['after']) is not dict
            or not custody._native_inode(image['beforeInode'])
            or image['afterInode'] is not None and not custody._native_inode(image['afterInode'])
            or type(image['ready']) is not bool or image['ready'] and image['afterInode'] is None
            or type(retained) is not dict or set(retained) != {'inode', 'sha256', 'retained'}
            or not custody._native_inode(retained['inode']) or type(retained['sha256']) is not str
            or not HEX.fullmatch(retained['sha256']) or type(retained['retained']) is not bool):
        raise ValueError('native-source-installer-rebind-record-changed')


def _installer_target_publication(record, files):
    return {'schemaVersion': 1, 'kind': 'ods-native-source-publication',
        'deploymentSha256': record['binding']['deploymentSha256'],
        'sourceRef': json.loads(files['source-kit.json'])['sourceRef'], 'nonce': record['nonce'],
        'phase': 'prepared-no-jobs', 'old': None, 'new': {name: sha(body) for name, body in files.items()},
        'candidate': record['candidate'], 'backup': record['backup'], 'candidateInode': record['candidateInode'],
        'stagedFiles': record['stagedFiles'], 'bindings': {}, 'witness': record['witness']}


def verify_installer_rebind_child(selection):
    """Read only the fixed parent and its never-admitted two-slot child.

    This proves physical prefixes, not installer mutation authority. The fixed
    custody verifier independently derives the parent and all target images.
    """
    custody, doc, _component, _ref = _material(selection)
    from pixel_macos_custody import protected_directory
    with protected_directory(custody.NATIVE_META) as metadata, protected_directory(custody.NATIVE_DEFINITIONS) as units:
        parent, parent_raw = custody._native_json_at(metadata, 'source-installer-rebind.json')
        _installer_rebind_record(custody, parent)
        if parent['phase'] not in ('generation', 'committed'):
            raise ValueError('native-source-installer-rebind-child-phase-changed')
        return _verify_inactive_child_prefix(selection, custody, doc, metadata, units, parent, parent_raw,
            parent_name='source-installer-rebind.json')


def _verify_inactive_child_prefix(selection, custody, doc, metadata, units, parent, parent_raw, *, parent_name):
    gate = os.open(custody.NATIVE_STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    jobs = None
    try:
        identity, binding, expected = custody._native_environment_binding(selection, metadata, gate,
            parent['kitSha256'], _publication=True)
        if (binding != parent['binding'] or custody._native_environment_snapshot(gate, custody.NATIVE_ENV.name,
                identity['id']) != parent['environment'] or _environment_no_actors(selection, custody, metadata) != parent['witness']
                or any(custody._native_at_inode(metadata, name) is not None for name in ('source-jobs-active.json', 'source-execution.json'))):
            raise ValueError('native-source-installer-rebind-child-binding-changed')
        custody._native_verify_prepared_logs(metadata, identity, binding)
        publication, pub_inode = custody._native_root_file(metadata, 'source-publication.json')
        if (publication, pub_inode) != (encoded(parent['publication']['after']), parent['publication']['afterInode']):
            raise ValueError('native-source-installer-rebind-child-publication-changed')
        child, _child_raw = custody._native_json_at(metadata, 'source-jobs-prepare.json')
        current = custody._native_at_inode(gate, 'custody.json')
        if child is None:
            if (parent['phase'] == 'committed' or custody._native_at_inode(metadata, 'native-source-jobs') is not None
                    or any(name.startswith(custody.NATIVE_LABEL) for name in os.listdir(units))
                    or custody._native_root_file(gate, 'custody.json', mode=0o640, gid=identity['id']) != (
                        expected, parent['bindings']['custody.json']['afterInode'])):
                raise ValueError('native-source-installer-rebind-child-unconfirmed')
            return {'generation': parent['generation'], 'phase': 'absent'}
        custody._native_job_record(child, dict(binding, publicationSha256=sha(publication)), expected)
        if (child['generation'] != parent['generation'] or child['environment'] != parent['environment']
                or child['witness'] != parent['witness']
                or child['before']['inode'] != parent['bindings']['custody.json']['afterInode']
                or child['phase'] not in ('staging', 'publishing', 'manifest-ready', 'moving-gate', 'committed-inactive')
                or len(child['definitions']) != 2
                or sorted((item['role'], item['identifier']) for item in child['definitions'].values()) != [('direct', None), ('sync', None)]
                or parent['phase'] == 'committed' and child['phase'] != 'committed-inactive'):
            raise ValueError('native-source-installer-rebind-child-changed')
        files = {}
        for label, item in child['definitions'].items():
            path, body, _mode = disabled_definition(item['role'], identity=identity,
                python=doc['hostPythonPath'], identifier=item['identifier'])
            if path.stem != label or item['sha256'] != sha(body):
                raise ValueError('native-source-installer-rebind-child-definition-changed')
            files[label + '.plist'] = (body, item)
        if set(name for name in os.listdir(units) if name.startswith(custody.NATIVE_LABEL)) - set(files):
            raise ValueError('native-source-installer-rebind-child-definition-changed')
        if child['directory'] is None:
            if (custody._native_at_inode(metadata, 'native-source-jobs') is not None or child['phase'] != 'staging'
                    or any(custody._native_at_inode(units, name) is not None for name in files)
                    or custody._native_root_file(gate, 'custody.json', mode=0o640, gid=identity['id']) != (expected, child['before']['inode'])):
                raise ValueError('native-source-installer-rebind-child-prefix-changed')
            return {'generation': child['generation'], 'phase': child['phase']}
        jobs = custody._native_job_directory(metadata, child)
        if set(os.listdir(jobs)) - {'manifest.json', '.gate-next', '.gate-before', *files}:
            raise ValueError('native-source-installer-rebind-child-prefix-changed')
        for name, (body, item) in files.items():
            staged, public = custody._native_at_inode(jobs, name), custody._native_at_inode(units, name)
            if staged is not None and public is not None or item['inode'] is not None and staged is None and public is None:
                raise ValueError('native-source-installer-rebind-child-prefix-changed')
            _generation_prefix(custody, jobs, name, body, item, mode=0o644)
            if public is not None and (not item['ready'] or custody._native_root_file(units, name, mode=0o644) != (body, item['inode'])):
                raise ValueError('native-source-installer-rebind-child-definition-changed')
            if child['phase'] in ('manifest-ready', 'moving-gate', 'committed-inactive') and public != item['inode']:
                raise ValueError('native-source-installer-rebind-child-definition-missing')
        manifest = custody._native_job_manifest(child)
        _generation_prefix(custody, jobs, 'manifest.json', custody._native_encoded(manifest), child['manifest'])
        if child['manifest']['inode'] is not None and custody._native_at_inode(jobs, 'manifest.json') != child['manifest']['inode']:
            raise ValueError('native-source-installer-rebind-child-manifest-changed')
        projection = custody._native_job_projection(child)
        _generation_prefix(custody, jobs, '.gate-next', projection, child['gate'], mode=0o640, gid=identity['id'])
        before = custody._native_at_inode(jobs, '.gate-before')
        if before is not None and custody._native_root_file(jobs, '.gate-before', mode=0o640, gid=identity['id']) != (expected, child['before']['inode']):
            raise ValueError('native-source-installer-rebind-child-gate-changed')
        if current == child['before']['inode']:
            if before is not None or custody._native_root_file(gate, 'custody.json', mode=0o640, gid=identity['id']) != (expected, current):
                raise ValueError('native-source-installer-rebind-child-gate-changed')
        elif current is None:
            if child['phase'] != 'moving-gate' or before != child['before']['inode']:
                raise ValueError('native-source-installer-rebind-child-gate-changed')
        elif (child['phase'] not in ('moving-gate', 'committed-inactive')
                or not child['gate']['ready'] or current != child['gate']['inode'] or before != child['before']['inode']
                or custody._native_root_file(gate, 'custody.json', mode=0o640, gid=identity['id']) != (projection, current)
                or custody._native_at_inode(jobs, '.gate-next') is not None):
            raise ValueError('native-source-installer-rebind-child-gate-changed')
        if child['phase'] == 'committed-inactive':
            custody._native_validate_inactive_generation(selection, metadata, gate, identity, binding, expected,
                projection, child['gate']['inode'])
        if custody._native_json_at(metadata, parent_name)[1] != parent_raw:
            raise ValueError('native-source-installer-rebind-child-parent-changed')
        _environment_no_actors(selection, custody, metadata)
        return {'generation': child['generation'], 'phase': child['phase']}
    finally:
        if jobs is not None:
            os.close(jobs)
        os.close(gate)


def _installer_rebind_boundary(selection, custody, doc, metadata, gate, identity, binding, expected, record):
    _installer_rebind_record(custody, record)
    authority = custody.native_installer_withdrawal_transaction(selection, transaction=record['transaction'])
    receipt = authority.get('receipt')
    if (receipt is None or sha(encoded(receipt)) != record['receiptSha256']
            or record['phase'] != 'generation' or binding != record['binding']
            or record['binding']['deploymentSha256'] != selection.deployment.checksum
            or doc['bundleSha256'] not in (record['transaction']['candidateDigest'], record['transaction']['currentDigest'])):
        raise ValueError('native-source-installer-generation-boundary-unconfirmed')
    custody.native_installer_rebind_plan(selection, transaction=record['transaction'], receipt=receipt)
    image = record['bindings']['custody.json']
    if (custody._native_root_file(gate, 'custody.json', mode=0o640, gid=identity['id']) != (expected, image['afterInode'])
            or custody._native_environment_snapshot(gate, custody.NATIVE_ENV.name, identity['id']) != record['environment']
            or any(custody._native_at_inode(metadata, name) is not None for name in (
                'source-jobs-prepare.json', 'source-jobs-active.json', 'source-execution.json', 'native-source-jobs'))
            or any(name.startswith(custody.NATIVE_LABEL) for name in os.listdir(custody.NATIVE_DEFINITIONS))):
        raise ValueError('native-source-installer-generation-boundary-unconfirmed')
    verify_kit_publication(selection)
    custody._native_environment_context(selection, metadata, gate, binding['kitSha256'], _publication=True)
    custody._native_verify_prepared_logs(metadata, identity, binding)
    _environment_no_actors(selection, custody, metadata)


def _initial_static_sha(record):
    return sha(encoded({key: value for key, value in record.items() if key not in ('phase', 'after')}))


def _initial_boot_names(item):
    nonce = item['nonce']
    return {key: '.source-initial-' + key.replace('_', '-') + '-' + nonce + '.json'
        for key in ('parent_before', 'parent_next', 'child_before', 'child_next')}


def _initial_boot_record(custody, value):
    if (type(value) is not dict or set(value) != {'schemaVersion', 'kind', 'bindingSha256', 'transactions'}
            or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
            or value['kind'] != 'ods-native-source-initial-boot'
            or type(value['bindingSha256']) is not str or not HEX.fullmatch(value['bindingSha256'])
            or type(value['transactions']) is not list or not 1 <= len(value['transactions']) <= 16):
        raise ValueError('native-source-initial-boot-record-invalid')
    def image(item):
        return (type(item) is dict and set(item) == {'inode', 'sha256', 'mode', 'gid'}
            and custody._native_inode(item['inode']) and type(item['sha256']) is str and HEX.fullmatch(item['sha256'])
            and type(item['mode']) is int and item['mode'] == 0o600 and type(item['gid']) is int and item['gid'] == 0)
    def after(item):
        return (type(item) is dict and set(item) == {'inode', 'ready'} and type(item['ready']) is bool
            and (item['inode'] is None or custody._native_inode(item['inode'])) and (not item['ready'] or item['inode'] is not None))
    seen = set()
    for index, item in enumerate(value['transactions']):
        if (type(item) is not dict or set(item) != {'nonce', 'beforeBoot', 'boot', 'witness', 'phase', 'parent', 'child'}
                or type(item['nonce']) is not str or not re.fullmatch('[a-f0-9]{32}', item['nonce']) or item['nonce'] in seen
                or item['phase'] not in ('parent-staging', 'parent-published', 'child-staging', 'committed')
                or index < len(value['transactions']) - 1 and item['phase'] != 'committed'
                or type(item['parent']) is not dict or set(item['parent']) != {'before', 'after'}
                or not image(item['parent']['before']) or not after(item['parent']['after'])
                or item['phase'] != 'parent-staging' and not item['parent']['after']['ready']
                or type(item['witness']) is not dict or set(item['witness']) != {
                    'kind', 'boot', 'loadedSourceJobs', 'brokerProcesses', 'brokerUid'}
                or item['witness']['kind'] != 'never-started-observed' or item['witness']['boot'] != item['boot']
                or any(type(item['witness'][key]) is not int or item['witness'][key] != 0
                    for key in ('loadedSourceJobs', 'brokerProcesses'))
                or type(item['witness']['brokerUid']) is not int or item['witness']['brokerUid'] <= 0):
            raise ValueError('native-source-initial-boot-record-invalid')
        for key in ('beforeBoot', 'boot'):
            try:
                if str(uuid.UUID(item[key])).upper() != item[key]:
                    raise ValueError()
            except (ValueError, TypeError, AttributeError):
                raise ValueError('native-source-initial-boot-record-invalid') from None
        if (item['beforeBoot'] == item['boot'] or index and value['transactions'][index - 1]['boot'] != item['beforeBoot']):
            raise ValueError('native-source-initial-boot-chain-invalid')
        child = item['child']
        if child is not None and (type(child) is not dict or set(child) != {'before', 'afterSha256', 'after'}
                or not image(child['before']) or not after(child['after']) or type(child['afterSha256']) is not str
                or not HEX.fullmatch(child['afterSha256']) or item['phase'] == 'committed' and not child['after']['ready']):
            raise ValueError('native-source-initial-boot-record-invalid')
        seen.add(item['nonce'])


def _initial_boot_before(custody, metadata, current_name, saved_name, image):
    name = current_name if custody._native_at_inode(metadata, current_name) == image['inode'] else saved_name
    actual, body = _environment_image(custody, metadata, name)
    if actual != image:
        raise ValueError('native-source-initial-boot-before-changed')
    return json.loads(body, object_pairs_hook=custody._native_pairs), body


def initial_boot_images(selection):
    """Read-only fixed image locator, never a current-boot stop proof.

    A displaced child remains old-boot inventory; a staged candidate is never
    returned as launch/control authority. The executor re-proves its literals.
    """
    custody, _doc, _component, _ref = _material(selection)
    from pixel_macos_custody import protected_directory
    with protected_directory(custody.NATIVE_META) as metadata:
        parent, parent_raw = custody._native_json_at(metadata, 'source-initial-generation.json')
        ledger, ledger_raw = custody._native_json_at(metadata, 'source-initial-boot.json')
        if parent is None and ledger is None:
            return None
        if parent is not None:
            _initial_generation_record(custody, parent)
            if parent['phase'] == 'committed':
                if ledger is not None:
                    _initial_boot_record(custody, ledger)
                    if ledger['transactions'][-1]['phase'] != 'committed':
                        raise ValueError('native-source-initial-boot-parent-conflict')
                return None
        child, child_raw = custody._native_json_at(metadata, 'source-jobs-prepare.json')
        parent_inode = custody._native_at_inode(metadata, 'source-initial-generation.json')
        child_inode = custody._native_at_inode(metadata, 'source-jobs-prepare.json')
        if ledger is None:
            return {'parent': parent, 'child': child, 'parentImage': {'inode': parent_inode, 'sha256': sha(parent_raw)},
                'childImage': None if child is None else {'inode': child_inode, 'sha256': sha(child_raw)},
                'displacedBoot': child['witness']['boot'] if child is not None else parent['witness']['boot']}
        _initial_boot_record(custody, ledger)
        original_parent, original_child = parent, child
        displaced = None
        for index, item in enumerate(ledger['transactions']):
            names = _initial_boot_names(item)
            before, before_raw = _initial_boot_before(custody, metadata, 'source-initial-generation.json',
                names['parent_before'], item['parent']['before'])
            _initial_generation_record(custody, before)
            if _initial_static_sha(before) != ledger['bindingSha256']:
                raise ValueError('native-source-initial-boot-binding-changed')
            _generation_prefix(custody, metadata, names['parent_next'], before_raw, item['parent']['after'])
            if item['phase'] == 'committed' and (custody._native_at_inode(metadata, names['parent_before']) != item['parent']['before']['inode']
                    or custody._native_at_inode(metadata, names['parent_next']) is not None):
                raise ValueError('native-source-initial-boot-parent-history-changed')
            if index == len(ledger['transactions']) - 1:
                if parent is None:
                    if (item['phase'] != 'parent-staging' or not item['parent']['after']['ready']
                            or custody._native_at_inode(metadata, names['parent_before']) != item['parent']['before']['inode']
                            or custody._native_at_inode(metadata, names['parent_next']) != item['parent']['after']['inode']):
                        raise ValueError('native-source-initial-boot-parent-missing')
                    original_parent, parent_raw, parent_inode = before, before_raw, item['parent']['before']['inode']
                elif (_initial_static_sha(parent) != ledger['bindingSha256']
                        or item['phase'] != 'committed' and parent_raw != before_raw
                        or item['phase'] != 'committed' and parent_inode not in (
                            item['parent']['before']['inode'], item['parent']['after']['inode'])):
                    raise ValueError('native-source-initial-boot-parent-changed')
            image = item['child']
            if image is not None:
                before_child, before_child_raw = _initial_boot_before(custody, metadata, 'source-jobs-prepare.json',
                    names['child_before'], image['before'])
                if (before_child.get('witness', {}).get('boot') != item['beforeBoot']
                        or before_child.get('generation') != before['generation']):
                    raise ValueError('native-source-initial-boot-child-binding-changed')
                after_child = dict(before_child, witness=dict(before_child['witness'], boot=item['boot']))
                after_raw = custody._native_encoded(after_child)
                if sha(after_raw) != image['afterSha256']:
                    raise ValueError('native-source-initial-boot-child-image-changed')
                _generation_prefix(custody, metadata, names['child_next'], after_raw, image['after'])
                if item['phase'] == 'committed' and (custody._native_at_inode(metadata, names['child_before']) != image['before']['inode']
                        or custody._native_at_inode(metadata, names['child_next']) is not None):
                    raise ValueError('native-source-initial-boot-child-history-changed')
                if index == len(ledger['transactions']) - 1:
                    if child is None:
                        if (item['phase'] != 'child-staging' or not image['after']['ready']
                                or custody._native_at_inode(metadata, names['child_before']) != image['before']['inode']
                                or custody._native_at_inode(metadata, names['child_next']) != image['after']['inode']):
                            raise ValueError('native-source-initial-boot-child-missing')
                        original_child, child_raw, child_inode = before_child, before_child_raw, image['before']['inode']
                    elif item['phase'] != 'committed' and not (
                            child_raw == before_child_raw and child_inode == image['before']['inode']
                            or image['after']['ready'] and child_raw == after_raw and child_inode == image['after']['inode']):
                        raise ValueError('native-source-initial-boot-child-changed')
                    elif item['phase'] == 'committed' and child['witness']['boot'] != item['boot']:
                        raise ValueError('native-source-initial-boot-child-changed')
                    if item['phase'] != 'committed':
                        # A just-published candidate still grants no current
                        # control authority until the durable parent commits.
                        original_child, child_raw, child_inode = before_child, before_child_raw, image['before']['inode']
            elif index == len(ledger['transactions']) - 1 and child is not None and (
                    item['phase'] != 'committed' or child['witness']['boot'] != item['boot']):
                raise ValueError('native-source-initial-boot-child-unexpected')
            displaced = item['boot'] if item['phase'] == 'committed' else item['beforeBoot']
        if (custody._native_json_at(metadata, 'source-initial-boot.json')[1] != ledger_raw
                or custody._native_json_at(metadata, 'source-initial-generation.json')[0] != parent
                or custody._native_json_at(metadata, 'source-jobs-prepare.json')[0] != child):
            raise ValueError('native-source-initial-boot-images-changed')
        return {'parent': original_parent, 'child': original_child, 'parentImage': {'inode': parent_inode, 'sha256': sha(parent_raw)},
            'childImage': None if original_child is None else {'inode': child_inode, 'sha256': sha(child_raw)}, 'displacedBoot': displaced}


def initial_boot_names(selection):
    """Exact closed metadata names, never a prefix adoption rule."""
    custody, _doc, _component, _ref = _material(selection)
    from pixel_macos_custody import protected_directory
    with protected_directory(custody.NATIVE_META) as metadata:
        ledger, raw = custody._native_json_at(metadata, 'source-initial-boot.json')
        if ledger is None:
            return set()
        _initial_boot_record(custody, ledger)
        initial_boot_images(selection)
        names = {'source-initial-boot.json'}
        for item in ledger['transactions']:
            values = _initial_boot_names(item)
            names |= {values['parent_before'], values['parent_next']}
            if item['child'] is not None:
                names |= {values['child_before'], values['child_next']}
        if custody._native_json_at(metadata, 'source-initial-boot.json')[1] != raw:
            raise ValueError('native-source-initial-boot-record-changed')
        return names


def initial_generation_boot_witness(selection, record):
    custody, _doc, _component, _ref = _material(selection)
    from pixel_macos_custody import protected_directory
    with protected_directory(custody.NATIVE_META) as metadata:
        ledger, raw = custody._native_json_at(metadata, 'source-initial-boot.json')
        if ledger is None:
            return dict(record['witness'])
        _initial_boot_record(custody, ledger)
        if ledger['bindingSha256'] != _initial_static_sha(record) or ledger['transactions'][-1]['phase'] != 'committed':
            raise ValueError('native-source-initial-boot-proof-pending')
        images = initial_boot_images(selection)
        if (images is not None and _initial_static_sha(images['parent']) != ledger['bindingSha256']
                or custody._native_json_at(metadata, 'source-initial-boot.json')[1] != raw):
            raise ValueError('native-source-initial-boot-proof-changed')
        return dict(ledger['transactions'][-1]['witness'])


def resume_initial_generation_boot(selection):
    """Data-only boot handoff for the same never-admitted initial generation.

    The executor must first unload independently qualified inert definitions.
    This function then freshly proves absence; it never starts or stops jobs.
    """
    custody, doc, _component, _ref = _material(selection)
    from pixel_connections import native_executor
    from pixel_macos_custody import protected_directory
    snapshot = native_executor.initial_quiesce_snapshot(selection)
    if snapshot is None:
        return {'status': 'observed'}  # Historical committed initial is no authority.
    with protected_directory(custody.NATIVE_META) as metadata:
        images = initial_boot_images(selection)
        if images is None:
            raise ValueError('native-source-initial-boot-context-missing')
        parent = images['parent']
        binding_sha = _initial_static_sha(parent)
        fresh = _environment_no_actors(selection, custody, metadata)
        if fresh['brokerUid'] != parent['binding']['brokerUid'] or snapshot['generation'] != parent['generation']:
            raise ValueError('native-source-initial-boot-runtime-changed')
        name = 'source-initial-boot.json'
        ledger, raw = custody._native_json_at(metadata, name)
        if ledger is not None:
            _initial_boot_record(custody, ledger)
            if ledger['bindingSha256'] != binding_sha:
                raise ValueError('native-source-initial-boot-binding-changed')
        else:
            ledger = {'schemaVersion': 1, 'kind': 'ods-native-source-initial-boot',
                'bindingSha256': binding_sha, 'transactions': []}
        if ledger['transactions'] and ledger['transactions'][-1]['phase'] != 'committed':
            item = ledger['transactions'][-1]
            if item['witness'] != fresh:
                raise ValueError('native-source-initial-boot-next-reboot-preserved')
        elif images['displacedBoot'] == fresh['boot']:
            return {'status': 'observed', 'generation': parent['generation']}
        else:
            if len(ledger['transactions']) >= 16:
                raise ValueError('native-source-initial-boot-history-preserved')
            parent_raw, parent_inode = custody._native_root_file(metadata, 'source-initial-generation.json')
            if {'inode': parent_inode, 'sha256': sha(parent_raw)} != images['parentImage']:
                raise ValueError('native-source-initial-boot-parent-changed')
            child = images['child']
            child_image = None
            if child is not None:
                child_raw, child_inode = custody._native_root_file(metadata, 'source-jobs-prepare.json')
                if {'inode': child_inode, 'sha256': sha(child_raw)} != images['childImage']:
                    raise ValueError('native-source-initial-boot-child-changed')
                after_child = dict(child, witness=dict(child['witness'], boot=fresh['boot']))
                child_image = {'before': {'inode': child_inode, 'sha256': sha(child_raw), 'mode': 0o600, 'gid': 0},
                    'afterSha256': sha(custody._native_encoded(after_child)), 'after': {'inode': None, 'ready': False}}
            item = {'nonce': uuid.uuid4().hex, 'beforeBoot': images['displacedBoot'], 'boot': fresh['boot'],
                'witness': fresh, 'phase': 'parent-staging', 'parent': {'before': {
                    'inode': parent_inode, 'sha256': sha(parent_raw), 'mode': 0o600, 'gid': 0},
                    'after': {'inode': None, 'ready': False}}, 'child': child_image}
            ledger['transactions'].append(item)

        def save():
            nonlocal raw
            _initial_boot_record(custody, ledger)
            if (custody._native_selected(selection, _publication=True) != doc
                    or _environment_no_actors(selection, custody, metadata) != fresh):
                raise ValueError('native-source-initial-boot-authority-changed')
            raw = custody._native_save_at(selection, metadata, name, ledger, previous=raw, _publication=True)

        def guard():
            if (custody._native_json_at(metadata, name)[1] != raw
                    or _environment_no_actors(selection, custody, metadata) != fresh):
                raise ValueError('native-source-initial-boot-authority-changed')
            current = initial_boot_images(selection)
            if current is None or _initial_static_sha(current['parent']) != binding_sha:
                raise ValueError('native-source-initial-boot-binding-changed')
            native_executor.initial_quiesce_snapshot(selection)

        save()  # Before any created FD or rename; new intent or exact resumed CAS.
        names = _initial_boot_names(item)
        before, before_raw = _initial_boot_before(custody, metadata, 'source-initial-generation.json',
            names['parent_before'], item['parent']['before'])
        if item['phase'] == 'parent-staging':
            current = custody._native_at_inode(metadata, 'source-initial-generation.json')
            if current != item['parent']['after']['inode'] or item['parent']['after']['inode'] is None:
                _generation_stage(custody, metadata, names['parent_next'], before_raw, item['parent']['after'], save, guard)
                guard()
                if current == item['parent']['before']['inode']:
                    if custody._native_at_inode(metadata, names['parent_before']) is not None:
                        raise ValueError('native-source-initial-boot-parent-conflict')
                    os.rename('source-initial-generation.json', names['parent_before'], src_dir_fd=metadata, dst_dir_fd=metadata)
                    os.fsync(metadata)
                elif current is not None:
                    raise ValueError('native-source-initial-boot-parent-changed')
                guard()
                os.rename(names['parent_next'], 'source-initial-generation.json', src_dir_fd=metadata, dst_dir_fd=metadata)
                os.fsync(metadata)
            item['phase'] = 'parent-published'
            save()
        if item['phase'] == 'parent-published':
            item['phase'] = 'child-staging' if item['child'] is not None else 'committed'
            save()
        if item['phase'] == 'child-staging':
            image = item['child']
            child, child_raw = _initial_boot_before(custody, metadata, 'source-jobs-prepare.json', names['child_before'], image['before'])
            after_child = dict(child, witness=dict(child['witness'], boot=item['boot']))
            after_raw = custody._native_encoded(after_child)
            if sha(after_raw) != image['afterSha256']:
                raise ValueError('native-source-initial-boot-child-changed')
            current = custody._native_at_inode(metadata, 'source-jobs-prepare.json')
            if current != image['after']['inode'] or image['after']['inode'] is None:
                _generation_stage(custody, metadata, names['child_next'], after_raw, image['after'], save, guard)
                guard()
                if current == image['before']['inode']:
                    if custody._native_at_inode(metadata, names['child_before']) is not None:
                        raise ValueError('native-source-initial-boot-child-conflict')
                    os.rename('source-jobs-prepare.json', names['child_before'], src_dir_fd=metadata, dst_dir_fd=metadata)
                    os.fsync(metadata)
                elif current is not None:
                    raise ValueError('native-source-initial-boot-child-changed')
                guard()
                os.rename(names['child_next'], 'source-jobs-prepare.json', src_dir_fd=metadata, dst_dir_fd=metadata)
                os.fsync(metadata)
            item['phase'] = 'committed'
            save()
        guard()
        return {'status': 'rebound', 'generation': before['generation'], 'boot': item['boot']}


def _initial_generation_record(custody, record):
    keys = {'schemaVersion', 'kind', 'binding', 'generation', 'nonce', 'phase', 'before', 'after',
        'gate', 'environment', 'identitySha256', 'prepareSha256', 'logsSha256', 'witness'}
    if (type(record) is not dict or set(record) != keys or type(record['schemaVersion']) is not int
            or record['schemaVersion'] != 1 or record['kind'] != 'ods-native-source-initial-generation'
            or any(type(record[key]) is not str or not re.fullmatch('[a-f0-9]{32}', record[key]) for key in ('generation', 'nonce'))
            or record['phase'] not in ('staging', 'publication', 'generation', 'committed')
            or type(record['before']) is not dict or set(record['before']) != {'document', 'inode'}
            or type(record['before']['document']) is not dict or not custody._native_inode(record['before']['inode'])
            or type(record['after']) is not dict or set(record['after']) != {'inode', 'ready'}
            or type(record['after']['ready']) is not bool
            or record['after']['inode'] is not None and not custody._native_inode(record['after']['inode'])
            or record['after']['ready'] and record['after']['inode'] is None
            or any(type(record[key]) is not str or not HEX.fullmatch(record[key])
                for key in ('identitySha256', 'prepareSha256', 'logsSha256'))):
        raise ValueError('native-source-initial-record-invalid')


def _initial_generation_static_view(selection):
    """Read fixed initial images without absence, current-boot or stop authority.

    The fixed boot locator can supply a displaced before-image during a known
    rename prefix. A staged candidate never becomes process-control authority.
    The mutating view below separately proves fresh preconsent/no-actor state.
    """
    custody, doc, component, ref = _material(selection)
    images = initial_boot_images(selection)
    from pixel_macos_custody import protected_directory
    with protected_directory(custody.NATIVE_META) as metadata, protected_directory(custody.NATIVE_KIT.parent) as kit_parent:
        if images is None:
            record, raw = custody._native_json_at(metadata, 'source-initial-generation.json')
        else:
            record, raw = images['parent'], encoded(images['parent'])
            if sha(raw) != images['parentImage']['sha256']:
                raise ValueError('native-source-initial-parent-changed')
        if record is not None:
            _initial_generation_record(custody, record)
        gate = os.open(custody.NATIVE_STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            _kit, kit_sha = custody.native_verify_kit(selection, require_profiles=True, _publication=True)
            identity, binding, expected = custody._native_environment_binding(selection, metadata, gate, kit_sha, _publication=True)
            full_binding = dict(binding, runtimeCustodySha256=doc['runtimeCustodySha256'], bundleSha256=doc['bundleSha256'], sourceRef=ref)
            publication_raw, publication_inode = custody._native_root_file(metadata, 'source-publication.json') if record is None else (
                encoded(record['before']['document']), record['before']['inode'])
            publication = json.loads(publication_raw, object_pairs_hook=_pairs)
            files = _publication_files(component, ref, selection.deployment.checksum)
            _publication_record(publication, selection.deployment.checksum, files)
            if (publication['phase'] != 'prepared-no-jobs' or publication['old'] is not None or publication['bindings']
                    or publication['witness']['brokerUid'] is not None and (
                        type(publication['witness']['brokerUid']) is not int or publication['witness']['brokerUid'] != identity['id'])
                    or any(type(publication['witness'][key]) is not int for key in ('loadedSourceJobs', 'brokerProcesses'))):
                raise ValueError('native-source-initial-code-publication-required')
            try:
                if str(uuid.UUID(publication['witness']['boot'])).upper() != publication['witness']['boot']:
                    raise ValueError()
            except (ValueError, TypeError, AttributeError):
                raise ValueError('native-source-initial-code-witness-invalid') from None
            image = {'inode': publication['candidateInode'], 'files': {name: {
                'inode': publication['stagedFiles'][name]['inode'], 'sha256': sha(body)} for name, body in files.items()}}
            _kit_image(custody, kit_parent, custody.NATIVE_KIT.name, expected=image)
            history = custody.native_installer_code_only_history(selection)
            if (type(history) is not dict or set(history) != {'metadata', 'kits'}
                    or any(type(history[key]) is not set or any(type(name) is not str for name in history[key])
                        for key in history)):
                raise ValueError('native-source-initial-kit-history-invalid')
            if any(name.startswith('.source-kit-') and name not in history['kits'] for name in os.listdir(kit_parent)):
                raise ValueError('native-source-initial-kit-history-preserved')
            environment = custody._native_environment_snapshot(gate, custody.NATIVE_ENV.name, identity['id'])
            env, _ = custody._native_json_at(metadata, 'source-environment.json')
            custody._native_environment_record(env, binding)
            if environment is None or env['pending'] is not None or env['current'] != environment:
                raise ValueError('native-source-initial-environment-unsettled')
            custody._native_verify_prepared_logs(metadata, identity, binding)
            allowed = {'source-identity.json', 'source-custody-prepare.json', 'source-environment.json',
                'source-logs-prepare.json', 'source-publication.json', 'source-sidefiles.json',
                'source-runtime-installation.json', 'source-runtime-publication.json', 'source-initial-generation.json'}
            if record is not None:
                allowed |= {'.source-initial-publication-' + record['nonce'] + '.json',
                    '.source-initial-publication-next-' + record['nonce']}
                if record['phase'] in ('generation', 'committed'):
                    allowed |= {'source-jobs-prepare.json', 'native-source-jobs'}
            allowed |= history['metadata'] | initial_boot_names(selection)
            from pixel_connections import native_executor
            allowed |= native_executor.initial_quiesce_names(selection)
            if (any(name.startswith(('source-', '.source-', 'native-source-', '.native-source-')) and name not in allowed
                    for name in os.listdir(metadata)) or custody._native_at_inode(metadata, 'retirement.json') is not None):
                raise ValueError('native-source-initial-history-preserved')
            witness = None if record is None else record['witness']
            anchors = {key: sha(custody._native_root_file(metadata, filename)[0]) for key, filename in (
                ('identitySha256', 'source-identity.json'), ('prepareSha256', 'source-custody-prepare.json'),
                ('logsSha256', 'source-logs-prepare.json'))}
            if record is None:
                if custody._native_root_file(gate, 'custody.json', mode=0o640, gid=identity['id'])[0] != expected:
                    raise ValueError('native-source-initial-v1-required')
                return custody, doc, full_binding, identity, expected, environment, anchors, witness, publication, publication_inode
            if (encoded(record['binding']) != encoded(full_binding) or encoded(record['environment']) != encoded(environment)
                    or any(record[key] != value for key, value in anchors.items())
                    or type(record['gate']) is not dict or set(record['gate']) != {'inode', 'sha256', 'mode', 'gid'}
                    or not custody._native_inode(record['gate']['inode'])
                    or record['gate'] != {'inode': record['gate']['inode'], 'sha256': sha(expected), 'mode': 0o640, 'gid': identity['id']}):
                raise ValueError('native-source-initial-input-changed')
            after = dict(publication, witness=witness)
            _publication_record(after, selection.deployment.checksum, files)
            if (witness['brokerUid'] != identity['id'] or type(witness['brokerUid']) is not int
                    or any(type(witness[key]) is not int for key in ('loadedSourceJobs', 'brokerProcesses'))):
                raise ValueError('native-source-initial-code-witness-invalid')
            current = custody._native_at_inode(metadata, 'source-publication.json')
            retained = '.source-initial-publication-' + record['nonce'] + '.json'
            temporary = '.source-initial-publication-next-' + record['nonce']
            if current == publication_inode:
                if (custody._native_root_file(metadata, 'source-publication.json') != (publication_raw, publication_inode)
                        or custody._native_at_inode(metadata, retained) is not None):
                    raise ValueError('native-source-initial-publication-changed')
            else:
                if custody._native_root_file(metadata, retained) != (publication_raw, publication_inode):
                    raise ValueError('native-source-initial-publication-changed')
                if current is not None and (not record['after']['ready'] or custody._native_root_file(metadata,
                        'source-publication.json') != (encoded(after), record['after']['inode'])):
                    raise ValueError('native-source-initial-publication-changed')
            _generation_prefix(custody, metadata, temporary, encoded(after), record['after'])
            if current != record['after']['inode'] and record['after']['inode'] is not None and custody._native_at_inode(metadata, temporary) != record['after']['inode']:
                raise ValueError('native-source-initial-prefix-missing')
            if record['phase'] not in ('generation', 'committed'):
                if custody._native_root_file(gate, 'custody.json', mode=0o640, gid=identity['id']) != (
                        expected, record['gate']['inode']) or record['gate']['sha256'] != sha(expected):
                    raise ValueError('native-source-initial-v1-changed')
            if ((images is None and custody._native_json_at(metadata, 'source-initial-generation.json')[1] != raw)
                    or images is not None and encoded(initial_boot_images(selection)) != encoded(images)):
                raise ValueError('native-source-initial-parent-changed')
            return custody, doc, full_binding, identity, expected, environment, anchors, witness, publication, publication_inode
        finally:
            os.close(gate)


def _initial_generation_view(selection):
    """Fresh preconsent/no-actor proof, separate from static stop inventory."""
    view = _initial_generation_static_view(selection)
    custody, _doc, _binding, identity, _expected, _environment, _anchors, witness, _before, _inode = view
    from pixel_macos_custody import protected_directory
    images = initial_boot_images(selection)
    with protected_directory(custody.NATIVE_META) as metadata:
        record = (images['parent'] if images is not None else
                  custody._native_json_at(metadata, 'source-initial-generation.json')[0])
        gate = os.open(custody.NATIVE_STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            fd = os.open(custody.NATIVE_ENV.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=gate)
            try:
                body = os.read(fd, custody.MAX_RESULT_BYTES + 1)
            finally:
                os.close(fd)
            values = dict(custody.parse_env(body.decode('utf-8')))
            custody._native_environment_bytes(selection, body.decode('utf-8'), _publication=True)
            if any(values.get(flag) != '0' for flag in custody.PREAUTH_FLAGS):
                raise ValueError('native-source-initial-grants-present')
            for name in ('private', 'private/oauth', 'projection', 'proposals', 'results'):
                directory = custody._native_directory_fd(gate, name)
                try:
                    if set(os.listdir(directory)) != ({'oauth'} if name == 'private' else set()):
                        raise ValueError('native-source-initial-private-data-preserved')
                finally:
                    os.close(directory)
            logfd = os.open(custody.NATIVE_LOGS, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                if any(os.stat(name, dir_fd=logfd, follow_symlinks=False).st_size for name in custody._native_log_names()):
                    raise ValueError('native-source-initial-log-history-preserved')
            finally:
                os.close(logfd)
            fresh = _environment_no_actors(selection, custody, metadata)
            if record is not None and fresh != initial_generation_boot_witness(selection, record):
                raise ValueError('native-source-initial-input-changed')
            # PUBHASH stays bound to the original witness after a reboot.
            return (*view[:7], fresh if witness is None else witness, *view[8:])
        finally:
            os.close(gate)


def verify_initial_generation_child(selection):
    custody, doc, full, identity, _expected, environment, _anchors, witness, before, _inode = _initial_generation_view(selection)
    from pixel_macos_custody import protected_directory
    with protected_directory(custody.NATIVE_META) as metadata, protected_directory(custody.NATIVE_DEFINITIONS) as units:
        record, raw = custody._native_json_at(metadata, 'source-initial-generation.json')
        _initial_generation_record(custody, record)
        if record['phase'] not in ('generation', 'committed'):
            raise ValueError('native-source-initial-child-phase-changed')
        binding = {key: value for key, value in full.items() if key not in ('runtimeCustodySha256', 'bundleSha256', 'sourceRef')}
        parent = {'phase': record['phase'], 'binding': binding, 'kitSha256': binding['kitSha256'],
            'environment': environment, 'witness': initial_generation_boot_witness(selection, record), 'generation': record['generation'],
            'bindings': {'custody.json': {'afterInode': record['gate']['inode']}},
            'publication': {'after': dict(before, witness=witness), 'afterInode': record['after']['inode']}}
        return _verify_inactive_child_prefix(selection, custody, doc, metadata, units, parent, raw,
            parent_name='source-initial-generation.json')


def publish_initial_generation(selection):
    """Fresh preconsent v1 only: witness publication then two inactive slots."""
    custody, doc, binding, identity, expected, environment, anchors, witness, before, before_inode = _initial_generation_view(selection)
    from pixel_macos_custody import protected_directory
    with protected_directory(custody.NATIVE_META) as metadata:
        name = 'source-initial-generation.json'
        record, raw = custody._native_json_at(metadata, name)
        if record is None:
            gate = os.open(custody.NATIVE_STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                gate_image = _environment_image(custody, gate, 'custody.json', mode=0o640, gid=identity['id'])[0]
            finally:
                os.close(gate)
            record = {'schemaVersion': 1, 'kind': 'ods-native-source-initial-generation', 'binding': binding,
                'generation': uuid.uuid4().hex, 'nonce': uuid.uuid4().hex, 'phase': 'staging',
                'before': {'document': before, 'inode': before_inode}, 'after': {'inode': None, 'ready': False},
                'gate': gate_image, 'environment': environment, **anchors, 'witness': witness}
        def save():
            nonlocal raw
            _initial_generation_record(custody, record)
            if (custody._native_selected(selection, _publication=True) != doc
                    or _environment_no_actors(selection, custody, metadata) != initial_generation_boot_witness(selection, record)):
                raise ValueError('native-source-initial-binding-changed')
            raw = custody._native_save_at(selection, metadata, name, record, previous=raw, _publication=True)
        def guard():
            _initial_generation_view(selection)
        if raw is None:
            save()
        temporary = '.source-initial-publication-next-' + record['nonce']
        retained = '.source-initial-publication-' + record['nonce'] + '.json'
        body = encoded(dict(before, witness=witness))
        if record['phase'] == 'staging':
            _generation_stage(custody, metadata, temporary, body, record['after'], save, guard)
            record['phase'] = 'publication'
            save()
        if record['phase'] == 'publication':
            guard()
            current = custody._native_at_inode(metadata, 'source-publication.json')
            if current == before_inode:
                os.rename('source-publication.json', retained, src_dir_fd=metadata, dst_dir_fd=metadata)
                os.fsync(metadata)
                current = None
            if current is None:
                guard()
                os.rename(temporary, 'source-publication.json', src_dir_fd=metadata, dst_dir_fd=metadata)
                os.fsync(metadata)
            if custody._native_root_file(metadata, 'source-publication.json') != (body, record['after']['inode']):
                raise ValueError('native-source-initial-publication-changed')
            record['phase'] = 'generation'
            save()
        guard()
        if record['phase'] == 'committed':
            verify_initial_generation_child(selection)
            return {'status': 'inactive', 'generation': record['generation'], 'environment': dict(environment)}
        child = publish_disabled_generation(selection, [{'role': 'sync', 'identifier': None},
            {'role': 'direct', 'identifier': None}], _generation=record['generation'])
        if child['generation'] != record['generation']:
            raise ValueError('native-source-initial-child-changed')
        record['phase'] = 'committed'
        save()
        verify_initial_generation_child(selection)
        return {'status': 'inactive', 'generation': record['generation'], 'environment': dict(environment)}


def _installer_rebind_result(record):
    return {'status': 'inactive', 'generation': record['generation'], 'environment': dict(record['environment']),
        'transaction': dict(record['transaction']), 'kitSha256': record['kitSha256']}


def verify_installer_rebind_generation(selection, *, transaction, receipt):
    """Fresh final lifecycle proof; the initial-kit verifier stays job-free."""
    custody, _doc, _component, _ref = _material(selection)
    from pixel_macos_custody import protected_directory
    with protected_directory(custody.NATIVE_META) as metadata:
        record, raw = custody._native_json_at(metadata, 'source-installer-rebind.json')
        _installer_rebind_record(custody, record)
        if (record['phase'] != 'committed' or record['transaction'] != transaction
                or record['receiptSha256'] != sha(encoded(receipt))):
            raise ValueError('native-source-installer-rebind-unconfirmed')
        custody.native_installer_rebind_plan(selection, transaction=transaction, receipt=receipt)
        child = verify_installer_rebind_child(selection)
        if (child != {'generation': record['generation'], 'phase': 'committed-inactive'}
                or custody._native_json_at(metadata, 'source-installer-rebind.json')[1] != raw):
            raise ValueError('native-source-installer-rebind-unconfirmed')
        return _installer_rebind_result(record)


def rebind_installer_generation(selection, *, transaction, receipt):
    """Data-only explicit target migration after a bound complete withdrawal.

    The fixed runtime verifier independently derives every before/after image
    from the actual protected installer journal. This writer retains original
    inodes, stages each replacement before publication, and never dispatches.
    """
    custody, doc, _component, _ref = _material(selection)
    from pixel_macos_custody import protected_directory
    name = 'source-installer-rebind.json'
    with protected_directory(custody.NATIVE_META) as metadata, protected_directory(custody.NATIVE_KIT.parent) as parent:
        plan = custody.native_installer_rebind_plan(selection, transaction=transaction, receipt=receipt)
        files = plan['targetKit']
        record, raw = custody._native_json_at(metadata, name)
        if record is None:
            nonce = uuid.uuid4().hex
            old_parent, parent_inode = custody._native_root_file(metadata, 'source-environment-generation.json', limit=65536)
            record = {'schemaVersion': 1, 'kind': 'ods-native-source-installer-rebind',
                'transaction': dict(transaction), 'intentSha256': receipt['intentSha256'], 'receiptSha256': sha(encoded(receipt)),
                'nonce': nonce, 'phase': 'staging', 'oldBinding': plan['oldBinding'], 'binding': plan['binding'],
                'gate': plan['gate'], 'children': plan['children'], 'environment': plan['environment'], 'token': plan['token'],
                'ownerGuid': plan['ownerGuid'], 'kitSha256': plan['kitSha256'], 'kitParent': plan['kitParent'], 'oldKit': plan['oldKit'],
                'candidate': '.source-kit-' + nonce, 'backup': '.source-kit-before-' + nonce,
                'candidateInode': None, 'stagedFiles': {leaf: {'inode': None, 'ready': False} for leaf in files},
                'bindings': {leaf: {'before': json.loads(image['before'], object_pairs_hook=_pairs),
                    'beforeInode': image['inode'], 'after': json.loads(image['after'], object_pairs_hook=_pairs),
                    'afterInode': None, 'ready': False, 'mode': image['mode'], 'gid': image['gid']}
                    for leaf, image in plan['images'].items()},
                'publication': {'before': json.loads(plan['publication']['before'], object_pairs_hook=_pairs),
                    'beforeInode': plan['publication']['inode'], 'after': None, 'afterInode': None, 'ready': False},
                'generation': uuid.uuid4().hex, 'witness': plan['witness'],
                'withdrawalParent': {'inode': parent_inode, 'sha256': sha(old_parent), 'retained': False}}
        _installer_rebind_record(custody, record)

        def authority_guard():
            _installer_rebind_record(custody, record)
            actual = custody.native_installer_withdrawal_transaction(selection, transaction=transaction)
            if (sha(encoded(actual.get('receipt'))) != record['receiptSha256'] or actual.get('receipt') != receipt
                    or actual['proof'] != plan['intent'] or actual['phase'] not in ('replacing-files', 'active', 'restored')
                    or custody._native_json_at(metadata, name)[1] != raw
                    or custody._native_selected(selection, _publication=True) != doc
                    or _at_inode(parent, '.') != record['kitParent']):
                raise ValueError('native-source-installer-rebind-authority-changed')
            _environment_no_actors(selection, custody, metadata)

        def guard():
            authority_guard()
            fresh = custody.native_installer_rebind_plan(selection, transaction=transaction, receipt=receipt)
            for key in ('oldBinding', 'binding', 'gate', 'children', 'environment', 'token', 'ownerGuid', 'kitSha256', 'kitParent', 'oldKit'):
                if encoded(fresh[key]) != encoded(record[key]):
                    raise ValueError('native-source-installer-rebind-derivation-changed')
            if fresh['targetKit'] != files or fresh['witness'] != record['witness']:
                raise ValueError('native-source-installer-rebind-derivation-changed')
            for leaf, image in record['bindings'].items():
                expected = fresh['images'][leaf]
                if (encoded(image['before']) != expected['before'] or encoded(image['after']) != expected['after']
                        or image['beforeInode'] != expected['inode'] or (image['mode'], image['gid']) != (expected['mode'], expected['gid'])):
                    raise ValueError('native-source-installer-rebind-derivation-changed')

        def save():
            nonlocal raw
            # Newly created inert candidate inode is recorded before its first
            # byte. Full physical-prefix proof follows the CAS, before writing.
            authority_guard()
            raw = custody._native_save_at(selection, metadata, name, record, previous=raw, _publication=True)

        if raw is None:
            save()
        guard()
        if record['phase'] == 'staging':
            expected = _stage_kit(selection, custody, metadata, parent, record, files, save, _guard=guard)
            _kit_image(custody, parent, record['candidate'], expected=expected)
            record['publication']['after'] = _installer_target_publication(record, files)
            record['phase'] = 'publishing-kit'
            save()
        expected = {'inode': record['candidateInode'], 'files': {leaf: {
            'inode': record['stagedFiles'][leaf]['inode'], 'sha256': sha(body)} for leaf, body in files.items()}}
        if record['phase'] == 'publishing-kit':
            guard()
            current = _at_inode(parent, custody.NATIVE_KIT.name)
            if current == record['oldKit']['inode']:
                _kit_image(custody, parent, custody.NATIVE_KIT.name, expected=record['oldKit'])
                if _at_inode(parent, record['backup']) is not None:
                    raise ValueError('native-source-installer-rebind-backup-conflict')
                os.rename(custody.NATIVE_KIT.name, record['backup'], src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
                current = None
            if current is None:
                guard()
                _kit_image(custody, parent, record['backup'], expected=record['oldKit'])
                _kit_image(custody, parent, record['candidate'], expected=expected)
                os.rename(record['candidate'], custody.NATIVE_KIT.name, src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
            _kit_image(custody, parent, custody.NATIVE_KIT.name, expected=expected)
            record['phase'] = 'binding'
            save()
        if record['phase'] == 'binding':
            _apply_bindings(selection, custody, metadata, record, save, _guard=guard)
            record['phase'] = 'publication'
            save()
        if record['phase'] == 'publication':
            image = record['publication']
            body = encoded(_installer_target_publication(record, files))
            if encoded(image['after']) != body:
                raise ValueError('native-source-installer-rebind-publication-changed')
            temporary = '.source-rebind-publication-' + record['nonce']
            backup = '.source-publication-before-' + record['nonce'] + '.json'
            current = custody._native_at_inode(metadata, 'source-publication.json')
            if current != image['afterInode'] or image['afterInode'] is None:
                stage = {'inode': image['afterInode'], 'ready': image['ready']}
                def publication_save():
                    image.update(afterInode=stage['inode'], ready=stage['ready'])
                    save()
                _generation_stage(custody, metadata, temporary, body, stage, publication_save, guard)
                guard()
                if current == image['beforeInode']:
                    if custody._native_root_file(metadata, 'source-publication.json') != (encoded(image['before']), image['beforeInode']):
                        raise ValueError('native-source-installer-rebind-publication-changed')
                    if custody._native_at_inode(metadata, backup) is not None:
                        raise ValueError('native-source-installer-rebind-publication-conflict')
                    os.rename('source-publication.json', backup, src_dir_fd=metadata, dst_dir_fd=metadata)
                    os.fsync(metadata)
                elif current is not None or custody._native_root_file(metadata, backup) != (encoded(image['before']), image['beforeInode']):
                    raise ValueError('native-source-installer-rebind-publication-changed')
                guard()
                os.rename(temporary, 'source-publication.json', src_dir_fd=metadata, dst_dir_fd=metadata)
                os.fsync(metadata)
            if custody._native_root_file(metadata, 'source-publication.json') != (body, image['afterInode']):
                raise ValueError('native-source-installer-rebind-publication-changed')
            record['phase'] = 'generation'
            save()
        if record['phase'] == 'generation':
            guard()
            retained = 'source-environment-generation-' + receipt['withdrawal'] + '.json'
            old_parent = record['withdrawalParent']
            current = custody._native_at_inode(metadata, 'source-environment-generation.json')
            if current == old_parent['inode']:
                raw_parent, inode = custody._native_root_file(metadata, 'source-environment-generation.json', limit=65536)
                if sha(raw_parent) != old_parent['sha256'] or inode != old_parent['inode'] or custody._native_at_inode(metadata, retained) is not None:
                    raise ValueError('native-source-installer-rebind-withdrawal-parent-changed')
                os.rename('source-environment-generation.json', retained, src_dir_fd=metadata, dst_dir_fd=metadata)
                os.fsync(metadata)
            elif current is not None:
                raise ValueError('native-source-installer-rebind-withdrawal-parent-changed')
            raw_parent, inode = custody._native_root_file(metadata, retained, limit=65536)
            if inode != old_parent['inode'] or sha(raw_parent) != old_parent['sha256']:
                raise ValueError('native-source-installer-rebind-withdrawal-parent-changed')
            old_parent['retained'] = True
            save()
            child = publish_disabled_generation(selection, [{'role': 'sync', 'identifier': None},
                {'role': 'direct', 'identifier': None}], _generation=record['generation'])
            if child['generation'] != record['generation']:
                raise ValueError('native-source-installer-rebind-child-changed')
            record['phase'] = 'committed'
            save()
        guard()
        child = publish_disabled_generation(selection, [{'role': 'sync', 'identifier': None},
            {'role': 'direct', 'identifier': None}], _generation=record['generation'])
        if child['generation'] != record['generation']:
            raise ValueError('native-source-installer-rebind-child-changed')
        return _installer_rebind_result(record)


def _active_definition(custody, descriptor, role, identifier, active):
    path, body, _mode = custody.native_disabled_definition(role, identity=descriptor['broker'],
        python=descriptor['hostPython'], identifier=identifier)
    if active:
        import plistlib
        value = plistlib.loads(body)
        value['Disabled'] = False
        body = plistlib.dumps(value, sort_keys=True)
    return path.stem, body


def _active_manifest(custody, state, binding):
    return {**binding, 'schemaVersion': 2, 'kind': 'ods-native-source-job-generation',
        'projectionSha256': sha(custody._native_encoded(state['gate']['document'])),
        'definitions': state['definitions']}


def _active_projection(custody, before, binding, environment, label, invocation, image, *, phase='prepared'):
    import copy
    old = before['gate']['document']
    base = {key: value for key, value in old.items() if key not in ('launch', 'runtime')}
    value = dict(base, schemaVersion=3, phase='active', runtime={
        'custodySha256': binding['runtimeCustodySha256'], 'bundleSha256': binding['bundleSha256'],
        'sourceRef': binding['sourceRef']})
    launch = copy.deepcopy(old['launch']) if old['schemaVersion'] == 3 else {
        'generation': old['launch']['generation'], 'phase': 'active', 'boot': invocation['boot'],
        'environment': {key: environment[key] for key in ('inode', 'sha256')},
        'definitions': {}, 'invocations': {}}
    launch['phase'] = 'active'
    launch['definitions'][label] = {'inode': image['inode'], 'sha256': image['sha256'],
        'profileSha256': image['profileSha256']}
    launch['invocations'][label] = {'phase': phase, 'invocation': invocation}
    value['launch'] = launch
    return value


def _active_state(custody, state, origin, binding, expected, *, staging=False):
    """Root journal consistency, independent of file self-hashes."""
    if (type(state) is not dict or set(state) != {'gate', 'manifest', 'definitions'}
            or type(state['gate']) is not dict or set(state['gate']) != {'document', 'inode'}
            or type(state['manifest']) is not dict or set(state['manifest']) != {'document', 'inode'}
            or any(item['inode'] is not None and not custody._native_inode(item['inode'])
                for item in (state['gate'], state['manifest']))
            or not staging and any(item['inode'] is None for item in (state['gate'], state['manifest']))
            or type(state['definitions']) is not dict or not 1 <= len(state['definitions']) <= 64):
        raise ValueError('native-source-active-record-changed')
    descriptor = json.loads(expected)
    for label, item in state['definitions'].items():
        if (type(item) is not dict or set(item) != {'role', 'identifier', 'active', 'inode', 'sha256', 'profileSha256'}
                or type(item['active']) is not bool or item['inode'] is not None and not custody._native_inode(item['inode'])
                or not staging and item['inode'] is None):
            raise ValueError('native-source-active-record-changed')
        actual_label, body = _active_definition(custody, descriptor, item['role'], item['identifier'], item['active'])
        if (label != actual_label or sha(body) != item['sha256'] or type(item['profileSha256']) is not str
                or not re.fullmatch('[a-f0-9]{64}', item['profileSha256'])):
            raise ValueError('native-source-active-record-changed')
    gate = state['gate']['document']
    if type(gate) is not dict:
        raise ValueError('native-source-active-record-changed')
    if gate.get('schemaVersion') == 2:
        if (gate != json.loads(custody._native_job_projection(origin))
                or state['manifest']['document'] != custody._native_job_manifest(origin)
                or set(state['definitions']) != set(origin['definitions'])
                or any(item['active'] or item['inode'] != origin['definitions'][label]['inode']
                    for label, item in state['definitions'].items())):
            raise ValueError('native-source-active-record-changed')
        return
    common = {key: value for key, value in gate.items() if key not in ('launch', 'runtime', 'schemaVersion', 'phase')}
    if (common != {key: value for key, value in descriptor.items() if key not in ('schemaVersion', 'phase')}
            or gate.get('runtime') != {'custodySha256': binding['runtimeCustodySha256'],
            'bundleSha256': binding['bundleSha256'], 'sourceRef': binding['sourceRef']}
            or len(custody._native_encoded(gate)) > 16384):
        raise ValueError('native-source-active-record-changed')
    # A not-yet-created definition inode is not public authority. Validate a
    # detached shape placeholder only while its exact Root FD is being staged.
    import copy
    check = copy.deepcopy(gate)
    if staging:
        for definition in check['launch']['definitions'].values():
            if definition['inode'] is None:
                definition['inode'] = [1, 1]
    snapshot = custody._native_broker_projection(check)
    if (snapshot['generation'] != origin['generation']
            or set(check['launch']['definitions']) != {label for label, item in state['definitions'].items() if item['active']}
            or state['manifest']['document'] != _active_manifest(custody, state, binding)):
        raise ValueError('native-source-active-record-changed')
    for label, image in gate['launch']['definitions'].items():
        item = state['definitions'][label]
        if image != {key: item[key] for key in ('inode', 'sha256', 'profileSha256')}:
            raise ValueError('native-source-active-record-changed')


def _active_record(custody, record, origin, binding, expected):
    keys = {'schemaVersion', 'kind', 'binding', 'originSha256', 'phase', 'current', 'transaction', 'retained', 'retired'}
    if (type(record) is not dict or not keys <= set(record) <= keys | {'history'} or record['schemaVersion'] != 1
            or type(record['schemaVersion']) is not int or record['kind'] != 'ods-native-source-active-journal'
            or record['binding'] != binding or record['originSha256'] != sha(custody._native_encoded(origin))
            or record['phase'] not in ('committed', 'staging', 'publishing', 'gate-ready', 'restoring', 'restored', 'unknown', 'retired')
            or type(record['retired']) is not list or len(record['retired']) > 64
            or len(set(record['retired'])) != len(record['retired'])
            or type(record['retained']) is not dict or len(record['retained']) > 320):
        raise ValueError('native-source-active-record-changed')
    if 'history' in record:
        history = record['history']
        if (type(history) is not dict or set(history) != {'directory', 'count', 'pending'}
                or history['directory'] is not None and not custody._native_inode(history['directory'])
                or type(history['count']) is not int or history['count'] < 0):
            raise ValueError('native-source-history-record-changed')
        pending = history['pending']
        if pending is not None:
            if (type(pending) is not dict or set(pending) != {'name', 'directory', 'files', 'receipt', 'image'}
                    or type(pending['name']) is not str or not re.fullmatch('[a-f0-9]{32}', pending['name'])
                    or pending['directory'] is not None and not custody._native_inode(pending['directory'])
                    or type(pending['files']) is not dict or len(pending['files']) > 326
                    or type(pending['receipt']) is not dict
                    or set(pending['receipt']) != {'kind', 'binding', 'currentSha256', 'transaction', 'files'}
                    or pending['receipt']['kind'] != 'ods-native-source-invocation-history'
                    or pending['receipt']['binding'] != binding
                    or pending['receipt']['currentSha256'] != sha(custody._native_encoded(record['current']))
                    or pending['receipt']['transaction'] != record['transaction']
                    or pending['receipt']['files'] != pending['files']
                    or type(pending['image']) is not dict or set(pending['image']) != {'inode', 'ready'}
                    or type(pending['image']['ready']) is not bool
                    or pending['image']['inode'] is not None and not custody._native_inode(pending['image']['inode'])
                    or pending['image']['ready'] and pending['image']['inode'] is None):
                raise ValueError('native-source-history-record-changed')
            for name, item in pending['files'].items():
                if (not re.fullmatch(r'\.active-[a-f0-9]{32}\.(?:definition|manifest|gate|before|definition-old|manifest-old)', name)
                        or type(item) is not dict or set(item) != {'inode', 'sha256', 'mode', 'gid'}
                        or not custody._native_inode(item['inode']) or type(item['sha256']) is not str or not HEX.fullmatch(item['sha256'])
                        or item['mode'] not in (0o600, 0o640, 0o644)
                        or item['gid'] not in (0, json.loads(expected)['broker']['id'])):
                    raise ValueError('native-source-history-record-changed')
    for label in record['retired']:
        custody._native_label_parts(label)
    for name, item in record['retained'].items():
        if (not re.fullmatch(r'\.active-[a-f0-9]{32}\.(?:definition|manifest|gate|before|definition-old|manifest-old)', name)
                or type(item) is not dict or set(item) != {'inode', 'sha256', 'mode', 'gid'}
                or not custody._native_inode(item['inode']) or not re.fullmatch('[a-f0-9]{64}', item['sha256'])
                or item['mode'] not in (0o600, 0o640, 0o644) or item['gid'] not in (0, json.loads(expected)['broker']['id'])):
            raise ValueError('native-source-active-record-changed')
    _active_state(custody, record['current'], origin, binding, expected)
    txn = record['transaction']
    if txn is None:
        if record['phase'] != 'committed':
            raise ValueError('native-source-active-record-changed')
        return
    txn_keys = {'label', 'invocation', 'before', 'after', 'images'}
    change = txn.get('change') if type(txn) is dict else None
    if (type(txn) is not dict or not txn_keys <= set(txn) <= txn_keys | {'change', 'admissionCeilingNs'}
            or type(txn['images']) is not dict or set(txn['images']) != (
                {'manifest', 'gate', 'before'} if change is not None else {'definition', 'manifest', 'gate', 'before'})):
        raise ValueError('native-source-active-record-changed')
    invocation = custody._native_invocation(txn['invocation']).document
    if 'admissionCeilingNs' in txn:
        ceiling = txn['admissionCeilingNs']
        if (type(ceiling) is not str or not re.fullmatch('[1-9][0-9]{0,18}', ceiling) or int(ceiling) >= 2**63
                or int(invocation['admissionDeadlineNs']) > int(ceiling)
                or invocation['role'] != 'sync' and int(invocation['workerDeadlineNs']) > int(ceiling)):
            raise ValueError('native-source-active-ceiling-invalid')
    label, body = _active_definition(custody, json.loads(expected), invocation['role'], invocation['identifier'], True)
    if txn['label'] != label or invocation['generation'] != origin['generation']:
        raise ValueError('native-source-active-record-changed')
    for image in txn['images'].values():
        if (type(image) is not dict or set(image) != {'inode', 'ready'} or type(image['ready']) is not bool
                or image['inode'] is not None and not custody._native_inode(image['inode'])
                or image['ready'] and image['inode'] is None):
            raise ValueError('native-source-active-record-changed')
    if record['phase'] in ('committed', 'publishing', 'gate-ready') and any(not image['ready'] for image in txn['images'].values()):
        raise ValueError('native-source-active-record-changed')
    _active_state(custody, txn['before'], origin, binding, expected)
    _active_state(custody, txn['after'], origin, binding, expected, staging=True)
    before, after = txn['before'], txn['after']
    phase = 'prepared'
    if change is not None:
        phase = _active_change(custody, change, before, label, invocation)
    if (set(after['definitions']) != set(before['definitions']) | {label}
            or any(item != after['definitions'][name] for name, item in before['definitions'].items() if name != label)
            or change is None and before['definitions'].get(label, {}).get('active')
            or after['definitions'][label]['sha256'] != sha(body)
            or change is None and after['definitions'][label]['inode'] != txn['images']['definition']['inode']
            or change is not None and after['definitions'] != before['definitions']
            or after['gate']['inode'] != txn['images']['gate']['inode']
            or after['manifest']['inode'] != txn['images']['manifest']['inode']
            or after['gate']['document'] != _active_projection(custody, before, binding,
                origin['environment'], label, invocation, after['definitions'][label], phase=phase)
            or record['current'] not in (before, after)
            or record['phase'] == 'committed' and record['current'] != after
            or record['phase'] == 'restored' and record['current'] != before):
        raise ValueError('native-source-active-record-changed')


def _active_change(custody, change, before, label, invocation):
    """Closed per-invocation transitions; completion never means a provider effect."""
    change_keys = {'id', 'kind', 'expectedNonce', 'phase'}
    if (type(change) is not dict or not change_keys <= set(change) <= change_keys | {'stopProof'}
            or type(change['id']) is not str or not re.fullmatch('[a-f0-9]{32}', change['id'])
            or type(change['expectedNonce']) is not str or not re.fullmatch('[a-f0-9]{32}', change['expectedNonce'])):
        raise ValueError('native-source-lifecycle-control-invalid')
    row = before['gate']['document'].get('launch', {}).get('invocations', {}).get(label)
    if row is None or row['invocation']['nonce'] != change['expectedNonce']:
        raise ValueError('native-source-lifecycle-nonce-changed')
    old = row['invocation']
    if 'stopProof' in change:
        proof = change['stopProof']
        if (change['kind'] != 'rearm' and change['phase'] not in ('completed', 'retired')
                or type(proof) is not dict or set(proof) != {'job', 'sha256'}
                or type(proof['job']) is not dict or proof['job'].get('nonce') != old['nonce']
                or (proof['job'].get('role'), proof['job'].get('identifier')) != (old['role'], old['identifier'])
                or proof['sha256'] != sha(custody._native_encoded(proof['job']))):
            raise ValueError('native-source-stop-proof-invalid')
    if change['kind'] == 'rearm':
        if (change['phase'] != 'prepared' or old['role'] not in ('sync', 'direct')
                or row['phase'] not in ('completed', 'retired')
                or invocation['nonce'] == old['nonce']
                or any(invocation[key] != old[key] for key in ('role', 'identifier', 'boot', 'clock', 'generation'))
                or int(invocation['issuedNs']) < int(old['issuedNs'])):
            raise ValueError('native-source-lifecycle-rearm-refused')
    elif change['kind'] == 'phase':
        transitions = {'running': ('prepared',), 'quiescing': ('prepared', 'running', 'unknown'),
            'unknown': ('prepared', 'running', 'quiescing'), 'completed': ('running',),
            'retired': ('prepared', 'running', 'quiescing', 'unknown', 'completed')}
        if invocation != old or row['phase'] not in transitions.get(change['phase'], ()):
            raise ValueError('native-source-lifecycle-transition-refused')
    else:
        raise ValueError('native-source-lifecycle-control-invalid')
    return change['phase']


def _active_stem(txn):
    return '.active-' + (txn['change']['id'] if 'change' in txn else txn['invocation']['nonce']) + '.'


def _active_history(custody, jobs, record):
    """Validate only live archival intent; sealed older evidence grants no authority.

    The private history directory retains prior transaction images/receipts.
    Its inode stays bound in the live journal; archived contents are never read
    as a live Gate, stop witness, provider result or permission to replay work.
    """
    history = record.get('history')
    if history is None:
        return None, None
    history_fd = archive_fd = None
    try:
        def directory(parent, name, inode):
            present = custody._native_at_inode(parent, name)
            if inode is None:
                if present is not None:
                    raise ValueError('native-source-history-unrecorded-directory')
                return None
            if present != inode:
                raise ValueError('native-source-history-directory-changed')
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                info = os.fstat(fd)
                if ([info.st_dev, info.st_ino] != inode or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, 0o700)):
                    raise ValueError('native-source-history-directory-changed')
                custody._native_no_acl(fd)
                return fd
            except BaseException:
                os.close(fd)
                raise
        history_fd = directory(jobs, 'history', history['directory'])
        pending = history['pending']
        if pending is not None:
            if history_fd is None:
                raise ValueError('native-source-history-directory-changed')
            archive_fd = directory(history_fd, pending['name'], pending['directory'])
            if archive_fd is not None:
                if set(os.listdir(archive_fd)) - {'receipt.json', *pending['files']}:
                    raise ValueError('native-source-history-foreign-file')
                _generation_prefix(custody, archive_fd, 'receipt.json', custody._native_encoded(pending['receipt']), pending['image'])
                for name, image in pending['files'].items():
                    source, target = custody._native_at_inode(jobs, name), custody._native_at_inode(archive_fd, name)
                    if (source is None) == (target is None):
                        raise ValueError('native-source-history-file-changed')
                    body, inode = custody._native_root_file(jobs if source is not None else archive_fd, name,
                        mode=image['mode'], gid=image['gid'])
                    if inode != image['inode'] or sha(body) != image['sha256']:
                        raise ValueError('native-source-history-file-changed')
        return history_fd, archive_fd
    except BaseException:
        for fd in (archive_fd, history_fd):
            if fd is not None:
                os.close(fd)
        raise


def _archive_active(custody, jobs, record, guard, save):
    """Move exact obsolete publication images, preserving interruption evidence.

    Only a committed transaction may enter. Each rename recovers by the original
    inode/hash in either namespace, never by adopting a filename. Crashes before
    a newly-created directory inode is recorded preserve/refuse that residue,
    consistently with the existing native preparers.
    """
    if (record['phase'] != 'committed' and not (record['phase'] == 'restored'
            and (record.get('transaction') or {}).get('change', {}).get('kind') == 'rearm')):
        raise ValueError('native-source-history-uncommitted')
    if 'history' not in record:
        record['history'] = {'directory': None, 'count': 0, 'pending': None}
        save()
    history = record['history']
    if history['directory'] is None:
        guard()
        os.mkdir('history', mode=0o700, dir_fd=jobs)
        os.fsync(jobs)
        fd = os.open('history', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=jobs)
        try:
            info = os.fstat(fd)
            if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, 0o700) or os.listdir(fd):
                raise ValueError('native-source-history-directory-changed')
            custody._native_no_acl(fd)
            history['directory'] = [info.st_dev, info.st_ino]
            save()
        finally:
            os.close(fd)
    if history['pending'] is None:
        txn = record['transaction']
        if txn is None and not record['retained']:
            return
        files = dict(record['retained'])
        if txn is not None:
            for name in os.listdir(jobs):
                if name.startswith(_active_stem(txn)):
                    mode = 0o640 if name.endswith(('.before', '.gate')) else 0o644 if 'definition' in name else 0o600
                    gid = record['current']['gate']['document']['broker']['id'] if mode == 0o640 else 0
                    body, inode = custody._native_root_file(jobs, name, mode=mode, gid=gid)
                    files[name] = {'inode': inode, 'sha256': sha(body), 'mode': mode, 'gid': gid}
        history['pending'] = {'name': uuid.uuid4().hex, 'directory': None, 'files': files,
            'receipt': {'kind': 'ods-native-source-invocation-history', 'binding': record['binding'],
                'currentSha256': sha(custody._native_encoded(record['current'])), 'transaction': txn, 'files': files},
            'image': {'inode': None, 'ready': False}}
        save()
    pending = history['pending']
    history_fd, archive_fd = _active_history(custody, jobs, record)
    try:
        if pending['directory'] is None:
            guard()
            os.mkdir(pending['name'], mode=0o700, dir_fd=history_fd)
            os.fsync(history_fd)
            archive_fd = os.open(pending['name'], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=history_fd)
            info = os.fstat(archive_fd)
            if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, 0o700) or os.listdir(archive_fd):
                raise ValueError('native-source-history-directory-changed')
            custody._native_no_acl(archive_fd)
            pending['directory'] = [info.st_dev, info.st_ino]
            save()
        _generation_stage(custody, archive_fd, 'receipt.json', custody._native_encoded(pending['receipt']), pending['image'], save, guard)
        for name, image in pending['files'].items():
            guard()
            if custody._native_at_inode(jobs, name) is not None:
                if custody._native_at_inode(archive_fd, name) is not None:
                    raise ValueError('native-source-history-file-changed')
                os.rename(name, name, src_dir_fd=jobs, dst_dir_fd=archive_fd)
                os.fsync(jobs)
                os.fsync(archive_fd)
        guard()
        if set(os.listdir(archive_fd)) != {'receipt.json', *pending['files']}:
            raise ValueError('native-source-history-file-changed')
        record['transaction'] = None
        record['retained'] = {}
        record['phase'] = 'committed'
        history['pending'] = None
        history['count'] += 1
        save()
    finally:
        for fd in (archive_fd, history_fd):
            if fd is not None:
                os.close(fd)


def transition_active_slot(selection, role, identifier=None, *, expected_nonce, phase, per_job=False):
    """Root-only durable handoff/revocation, not launchd control or effect success.

    The executor must call running only after its owned READY/GO handoff. Quiescing
    and unknown revoke dispatch before any stop. Completed/retired require the
    fixed global no-jobs/no-broker-process observation; no caller proof is trusted.
    """
    return _change_active_slot(selection, role, identifier, expected_nonce, 'phase', phase, per_job=per_job)


def rearm_active_slot(selection, role, identifier=None, *, expected_nonce, per_job=False, _admission_ceiling_ns=None):
    """Explicit new admission only for quiescent repeatable sync/direct jobs.

    Effect-bearing one-shots are never replayed here. A lost reply observes the
    exact original new nonce and deadlines. This call requires a live bounded Root
    scope and never borrows a role budget as the admission deadline.
    """
    return _change_active_slot(selection, role, identifier, expected_nonce, 'rearm', 'prepared',
        per_job=per_job, _admission_ceiling_ns=_admission_ceiling_ns)


def abort_rearm_active_slot(selection, role, identifier=None, *, expected_nonce):
    """Explicitly withdraw one new repeatable admission after global quiescence.

    Only its exact original new nonce may abort. The prior Gate must already be
    completed/retired, so recovery cannot restore old dispatch authority. A lost
    abort acknowledgement observes the same restored transaction.
    """
    from pixel_access_bridge import LaunchdAccessBridge
    from pixel_connections import custody
    from pixel_connections.planner import ConnectionDeployment
    if (type(selection) is not custody._NativeSourceSelection or selection.seal is not custody._NATIVE_SEAL
            or type(selection.bridge) is not LaunchdAccessBridge or type(selection.deployment) is not ConnectionDeployment):
        raise custody.CustodyError('native-source-deployment-required')
    if type(expected_nonce) is not str or not re.fullmatch('[a-f0-9]{32}', expected_nonce):
        raise ValueError('native-source-lifecycle-control-invalid')
    with LaunchdAccessBridge._native_runtime_operation(selection.bridge):
        return _publish_active_slot(selection, role, identifier, rollback=True, _abort_nonce=expected_nonce)


def _change_active_slot(selection, role, identifier, nonce, kind, phase, *, per_job=False, _admission_ceiling_ns=None):
    from pixel_access_bridge import LaunchdAccessBridge
    from pixel_connections import custody
    from pixel_connections.planner import ConnectionDeployment
    if (type(selection) is not custody._NativeSourceSelection or selection.seal is not custody._NATIVE_SEAL
            or type(selection.bridge) is not LaunchdAccessBridge or type(selection.deployment) is not ConnectionDeployment):
        raise custody.CustodyError('native-source-deployment-required')
    if (type(per_job) is not bool or type(nonce) is not str or not re.fullmatch('[a-f0-9]{32}', nonce)
            or phase not in ('prepared', 'running', 'quiescing', 'unknown', 'completed', 'retired')):
        raise ValueError('native-source-lifecycle-control-invalid')
    if kind == 'rearm':
        # Refuse missing/foreign/revoked scope before any archival intent or
        # runtime publication. Check again when computing the actual admission.
        LaunchdAccessBridge._native_source_deadline_ns(selection.bridge)
    with LaunchdAccessBridge._native_runtime_operation(selection.bridge):
        change = {'kind': kind, 'expectedNonce': nonce, 'phase': phase}
        if per_job:
            from pixel_connections.native_executor import verify_stopped
            job = verify_stopped(selection, role, identifier, nonce)
            change['stopProof'] = {'job': job, 'sha256': sha(custody._native_encoded(job))}
        return _publish_active_slot(selection, role, identifier,
            _change=change, _admission_ceiling_ns=_admission_ceiling_ns)


def publish_active_slot(selection, role, identifier=None, *, rollback=False):
    """Reuse protected hashes for this operation, never Root authority/results."""
    from pixel_access_bridge import LaunchdAccessBridge
    from pixel_connections import custody
    from pixel_connections.planner import ConnectionDeployment
    # Check the exact sealed type before accessing any caller object attributes.
    if type(selection) is not custody._NativeSourceSelection:
        raise custody.CustodyError('native-source-deployment-required')
    if (selection.seal is not custody._NATIVE_SEAL
            or type(selection.bridge) is not LaunchdAccessBridge
            or type(selection.deployment) is not ConnectionDeployment):
        raise custody.CustodyError('native-source-deployment-required')
    with LaunchdAccessBridge._native_runtime_operation(selection.bridge):
        return _publish_active_slot(selection, role, identifier, rollback=rollback)


def admit_active_slot(selection, role, identifier=None, *, _admission_ceiling_ns=None):
    """Executable admission requires the existing Root bounded-operation ceiling."""
    from pixel_access_bridge import LaunchdAccessBridge
    from pixel_connections import custody
    from pixel_connections.planner import ConnectionDeployment
    if (type(selection) is not custody._NativeSourceSelection or selection.seal is not custody._NATIVE_SEAL
            or type(selection.bridge) is not LaunchdAccessBridge or type(selection.deployment) is not ConnectionDeployment):
        raise custody.CustodyError('native-source-deployment-required')
    LaunchdAccessBridge._native_source_deadline_ns(selection.bridge)
    with LaunchdAccessBridge._native_runtime_operation(selection.bridge):
        return _publish_active_slot(selection, role, identifier, _admit=True, _admission_ceiling_ns=_admission_ceiling_ns)


def _publish_active_slot(selection, role, identifier=None, *, rollback=False, _change=None, _abort_nonce=None,
                        _admit=False, _admission_ceiling_ns=None):
    """Root-only fixed publisher, caller holds existing admission lock.

    No launchctl mutation/Source dispatch. Root inputs and intent precede the
    atomic Gate replace. Additive slots preserve every sibling's immutable
    invocation and definition. Only a globally quiescent latest slot rolls back.
    """
    import copy
    from pixel_macos_custody import protected_directory
    if type(rollback) is not bool:
        raise ValueError('native-source-active-control-invalid')
    custody, doc, component, ref = _material(selection)
    custody.native_child_arguments(role, identifier)
    with protected_directory(custody.NATIVE_META) as metadata, protected_directory(custody.NATIVE_DEFINITIONS) as units, \
            protected_directory(custody.NATIVE_STATE.parent) as parent:
        gate = os.open(custody.NATIVE_STATE.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        jobs = None
        try:
            _kit, kit_sha = custody.native_verify_kit(selection, require_profiles=True, _publication=True)
            identity, base_binding, expected = custody._native_environment_binding(selection, metadata, gate, kit_sha, _publication=True)
            publication, _inode = custody._native_root_file(metadata, 'source-publication.json')
            old_binding = dict(base_binding, publicationSha256=sha(publication))
            origin, origin_raw = custody._native_json_at(metadata, 'source-jobs-prepare.json')
            custody._native_job_record(origin, old_binding, expected)
            if origin['phase'] != 'committed-inactive':
                raise ValueError('native-source-active-origin-unsettled')
            jobs = custody._native_job_directory(metadata, origin)
            environment = custody._native_environment_snapshot(gate, custody.NATIVE_ENV.name, identity['id'])
            env_record, env_raw = custody._native_json_at(metadata, 'source-environment.json')
            custody._native_environment_record(env_record, base_binding)
            if environment != origin['environment'] or env_record['pending'] is not None or environment != env_record['current']:
                raise ValueError('native-source-active-environment-changed')
            binding = dict(old_binding, runtimeCustodySha256=doc['runtimeCustodySha256'],
                bundleSha256=doc['bundleSha256'], sourceRef=ref)
            label, definition_body = _active_definition(custody, json.loads(expected), role, identifier, True)
            record, raw = custody._native_json_at(metadata, 'source-jobs-active.json')
            if record is None:
                if rollback or _change is not None:
                    raise ValueError('native-source-active-recovery-missing')
                before, inode = custody._native_root_file(gate, 'custody.json', mode=0o640, gid=identity['id'], limit=16384)
                custody._native_validate_inactive_generation(selection, metadata, gate, identity, base_binding, expected, before, inode)
                witness = _no_actors(selection, custody, metadata, _generation=True)
                if witness != origin['witness']:
                    raise ValueError('native-source-active-boot-changed')
                state = {'gate': {'document': json.loads(before), 'inode': inode},
                    'manifest': {'document': custody._native_job_manifest(origin), 'inode': origin['manifest']['inode']},
                    'definitions': {name: {**{key: item[key] for key in ('role', 'identifier', 'inode', 'sha256')},
                        'active': False, 'profileSha256': sha(component['source-' + item['role'] + '.sb'])}
                        for name, item in origin['definitions'].items()}}
                record = {'schemaVersion': 1, 'kind': 'ods-native-source-active-journal', 'binding': binding,
                    'originSha256': sha(origin_raw), 'phase': 'committed', 'current': state,
                    'transaction': None, 'retained': {}, 'retired': []}
            _active_record(custody, record, origin, binding, expected)
            if (_admit and record['phase'] != 'committed' and record['transaction'] is not None
                    and 'admissionCeilingNs' not in record['transaction']):
                raise ValueError('native-source-execution-unbound-preparation')
            if record['phase'] in ('unknown', 'retired'):
                raise ValueError('native-source-active-explicit-recovery-required')
            if _abort_nonce is not None:
                abort = record.get('transaction') or {}
                if (abort.get('label') != label or abort.get('change', {}).get('kind') != 'rearm'
                        or abort.get('invocation', {}).get('nonce') != _abort_nonce
                        or abort['before']['gate']['document']['launch']['invocations'][label]['phase'] not in ('completed', 'retired')):
                    raise ValueError('native-source-lifecycle-abort-mismatch')
            elif rollback and (record.get('history') is not None or (record.get('transaction') or {}).get('change') is not None):
                # A former prepared/running Gate must never be restored after
                # lifecycle revocation or a completed invocation was archived.
                raise ValueError('native-source-lifecycle-rollback-refused')

            def body_for(state, name):
                if name == 'gate':
                    return custody._native_encoded(state['gate']['document']), 0o640, identity['id']
                if name == 'manifest':
                    return custody._native_encoded(state['manifest']['document']), 0o600, 0
                item = state['definitions'][name]
                return _active_definition(custody, json.loads(expected), item['role'], item['identifier'], item['active'])[1], 0o644, 0

            def observed(directory, name, candidates, *, mode, gid=0):
                present = custody._native_at_inode(directory, name)
                if present is None:
                    return None
                data, inode = custody._native_root_file(directory, name, mode=mode, gid=gid, limit=16384 if mode != 0o600 else 65536)
                for tag, body, known in candidates:
                    if known is not None and (data, inode) == (body, known):
                        return tag
                raise ValueError('native-source-active-image-changed')

            def images():
                txn = record['transaction']
                if txn is None:
                    return {}
                stem = _active_stem(txn)
                before, after = txn['before'], txn['after']
                return {key: (stem + key, *body_for(before if key == 'before' else after,
                        'gate' if key == 'before' else txn['label'] if key == 'definition' else key))
                    for key in txn['images']}

            def namespace():
                txn = record['transaction']
                allowed = {'manifest.json', '.gate-before', *record['retained']}
                if 'history' in record:
                    allowed.add('history')
                if txn is not None:
                    stem = _active_stem(txn)
                    allowed.update(name for name, *_rest in images().values())
                    allowed.update((stem + 'definition-old', stem + 'manifest-old'))
                if set(os.listdir(jobs)) - allowed:
                    raise ValueError('native-source-active-unmanaged-directory')
                history_fd, archive_fd = _active_history(custody, jobs, record)
                try:
                    pending = record.get('history', {}).get('pending')
                    for name, image in record['retained'].items():
                        directory = jobs
                        if custody._native_at_inode(jobs, name) is None and pending and name in pending['files']:
                            directory = archive_fd
                        if directory is None:
                            raise ValueError('native-source-history-file-changed')
                        data, inode = custody._native_root_file(directory, name, mode=image['mode'], gid=image['gid'])
                        if inode != image['inode'] or sha(data) != image['sha256']:
                            raise ValueError('native-source-active-retained-changed')
                finally:
                    for fd in (archive_fd, history_fd):
                        if fd is not None:
                            os.close(fd)

            def guard():
                current_doc = custody._native_selected(selection, _publication=True)
                current_identity, current_binding, current_expected = custody._native_environment_binding(
                    selection, metadata, gate, kit_sha, _publication=True)
                if (current_doc != doc or current_identity != identity or current_binding != base_binding or current_expected != expected
                        or custody._native_at_inode(parent, custody.NATIVE_STATE.name) != base_binding['gate']
                        or custody._native_json_at(metadata, 'source-jobs-prepare.json')[1] != origin_raw
                        or custody._native_root_file(metadata, 'source-publication.json')[0] != publication
                        or custody._native_json_at(metadata, 'source-environment.json')[1] != env_raw
                        or custody._native_environment_snapshot(gate, custody.NATIVE_ENV.name, identity['id']) != environment
                        or custody._native_json_at(metadata, 'source-jobs-active.json')[1] != raw):
                    raise ValueError('native-source-active-input-changed')
                custody._native_verify_prepared_logs(metadata, identity, base_binding)
                if custody._native_boot_uuid() != origin['witness']['boot']:
                    raise ValueError('native-source-active-boot-changed')
                # A committed transaction is a receipt. Its old stopped job
                # may already have been replaced by the executor's next nonce;
                # only this operation or unfinished publication needs a fresh
                # live stop proof. Archived receipts never grant authority.
                change = _change
                if change is None and record['phase'] not in ('committed', 'restored'):
                    change = (record.get('transaction') or {}).get('change')
                must_quiesce = change is not None and (change['kind'] == 'rearm' or change['phase'] in ('completed', 'retired'))
                if must_quiesce and 'stopProof' in change:
                    from pixel_connections.native_executor import verify_stopped
                    proof = change['stopProof']
                    actual = verify_stopped(selection, proof['job']['role'], proof['job']['identifier'], change['expectedNonce'])
                    if actual != proof['job'] or sha(custody._native_encoded(actual)) != proof['sha256']:
                        raise ValueError('native-source-stop-proof-changed')
                elif (must_quiesce or role in ('exchange', 'revoke') and change is None) and _no_actors(selection, custody, metadata, _generation=True) != origin['witness']:
                    raise ValueError('native-source-active-grant-quiesce-required')
                check_jobs = custody._native_job_directory(metadata, origin)
                os.close(check_jobs)
                namespace()
                txn = record['transaction']
                strict = txn is None or record['phase'] in ('committed', 'restored')
                states = [('current', record['current'])] if strict else [('before', txn['before']), ('after', txn['after'])]
                if record['phase'] == 'restoring' and txn['images']['before']['inode'] is not None:
                    restored = copy.deepcopy(txn['before'])
                    restored['gate']['inode'] = txn['images']['before']['inode']
                    states.append(('restored', restored))
                if observed(gate, 'custody.json', [(tag, body_for(state, 'gate')[0], state['gate']['inode']) for tag, state in states],
                        mode=0o640, gid=identity['id']) is None:
                    raise ValueError('native-source-active-gate-missing')
                known_labels = set().union(*(set(state['definitions']) for _tag, state in states))
                if set(name for name in os.listdir(units) if name.startswith(custody.NATIVE_LABEL)) - {name + '.plist' for name in known_labels}:
                    raise ValueError('native-source-active-unmanaged-definition')
                for name in set().union(*(set(state['definitions']) for _tag, state in states)):
                    found = observed(units, name + '.plist', [(tag, body_for(state, name)[0], state['definitions'][name]['inode'])
                        for tag, state in states if name in state['definitions']], mode=0o644)
                    if found is None and (strict or name != txn['label']):
                        raise ValueError('native-source-active-definition-missing')
                manifest = observed(jobs, 'manifest.json', [(tag, body_for(state, 'manifest')[0], state['manifest']['inode']) for tag, state in states], mode=0o600)
                if manifest is None and strict:
                    raise ValueError('native-source-active-manifest-missing')
                # Existing labels can remain loaded while an independent slot
                # is added. Unknown labels (including the prospective target)
                # do not acquire provenance from the new transaction.
                loaded = custody.native_launchd_loaded_labels(
                    selection.bridge.command(['/bin/launchctl', 'print', 'system'], timeout=10))
                allowed_labels = set(record['current']['gate']['document'].get('launch', {}).get('invocations', {}))
                if {label for label in loaded if label.startswith(custody.NATIVE_LABEL)} - allowed_labels:
                    raise ValueError('native-source-active-unbound-loaded-job')
                if txn is not None:
                    for key, (name, body, mode, gid) in images().items():
                        _generation_prefix(custody, jobs, name, body, txn['images'][key], mode=mode, gid=gid)
                    stem = _active_stem(txn)
                    for suffix, key in (('definition-old', txn['label']), ('manifest-old', 'manifest')):
                        if custody._native_at_inode(jobs, stem + suffix) is not None:
                            if key not in txn['before']['definitions'] and key != 'manifest':
                                raise ValueError('native-source-active-image-changed')
                            state = txn['before']
                            observed(jobs, stem + suffix, [('before', body_for(state, key)[0],
                                state['manifest']['inode'] if key == 'manifest' else state['definitions'][key]['inode'])],
                                mode=0o600 if key == 'manifest' else 0o644)

            def refresh():
                txn = record['transaction']
                if txn is not None:
                    after, image = txn['after'], txn['images']
                    if 'definition' in image:
                        after['definitions'][txn['label']]['inode'] = image['definition']['inode']
                    after['gate']['document'] = _active_projection(custody, txn['before'], binding, environment,
                        txn['label'], txn['invocation'], after['definitions'][txn['label']],
                        phase=txn.get('change', {}).get('phase', 'prepared'))
                    after['gate']['inode'] = image['gate']['inode']
                    after['manifest']['document'] = _active_manifest(custody, after, binding)
                    after['manifest']['inode'] = image['manifest']['inode']

            def save():
                nonlocal raw
                refresh()
                guard()
                _active_record(custody, record, origin, binding, expected)
                raw = custody._native_save_at(selection, metadata, 'source-jobs-active.json', record, previous=raw, _publication=True)

            guard()
            txn = record['transaction']
            if rollback:
                if txn is None or txn['label'] != label:
                    raise ValueError('native-source-active-recovery-mismatch')
                if _abort_nonce is not None and record['phase'] == 'restored':
                    return {'status': 'restored', 'label': label, 'generation': origin['generation']}
                # Removing admitted authority requires explicit global quiesce.
                # This does not relax the DATA-only no-actors guard.
                if _no_actors(selection, custody, metadata, _generation=True) != origin['witness']:
                    raise ValueError('native-source-active-quiesce-required')
                record['phase'] = 'restoring'
                save()
                stem = _active_stem(txn)
                before, after = txn['before'], txn['after']
                tag = observed(gate, 'custody.json', [('before', body_for(before, 'gate')[0], before['gate']['inode']),
                    ('after', body_for(after, 'gate')[0], after['gate']['inode']),
                    ('restored', body_for(before, 'gate')[0], txn['images']['before']['inode'])], mode=0o640, gid=identity['id'])
                if tag == 'after':
                    guard()
                    os.replace(stem + 'before', 'custody.json', src_dir_fd=jobs, dst_dir_fd=gate)
                    os.fsync(gate)
                    os.fsync(jobs)
                    before['gate']['inode'] = txn['images']['before']['inode']
                    save()
                elif tag == 'restored':
                    before['gate']['inode'] = txn['images']['before']['inode']
                    save()
                for key, directory, name in (('definition', units, label + '.plist'), ('manifest', jobs, 'manifest.json')):
                    if key not in txn['images']:
                        continue
                    prior = before['definitions'].get(label) if key == 'definition' else before['manifest']
                    old_name, new_name = stem + key + '-old', stem + key
                    if custody._native_at_inode(directory, name) == txn['images'][key]['inode']:
                        if custody._native_at_inode(jobs, new_name) is not None:
                            raise ValueError('native-source-active-restore-conflict')
                        guard()
                        os.rename(name, new_name, src_dir_fd=directory, dst_dir_fd=jobs)
                        os.fsync(directory)
                        os.fsync(jobs)
                    if prior is not None and custody._native_at_inode(directory, name) is None:
                        guard()
                        os.rename(old_name, name, src_dir_fd=jobs, dst_dir_fd=directory)
                        os.fsync(directory)
                        os.fsync(jobs)
                record['current'] = before
                record['phase'] = 'restored'
                if _abort_nonce is None and label not in record['retired']:
                    record['retired'].append(label)
                save()
                return {'status': 'restored', 'label': label, 'generation': origin['generation']}
            if record['phase'] in ('restoring', 'restored'):
                if (record['phase'] == 'restored' and _change is not None and _change['kind'] == 'rearm'
                        and (txn or {}).get('change', {}).get('kind') == 'rearm'):
                    _archive_active(custody, jobs, record, guard, save)
                    txn = record['transaction']
                else:
                    raise ValueError('native-source-active-explicit-recovery-required')
            if record['phase'] == 'committed':
                current = record['current']
                row = current['gate']['document'].get('launch', {}).get('invocations', {}).get(label)
                if row is not None and _change is None:
                    if _admit and (txn is None or txn['label'] != label or txn['invocation'] != row['invocation']
                            or 'admissionCeilingNs' not in txn):
                        raise ValueError('native-source-execution-unbound-preparation')
                    return {'status': 'observed', 'label': label, 'invocation': row['invocation'],
                        'eligible': row['phase'] in ('prepared', 'running') and custody._native_clock_ns() < int(
                            row['invocation']['admissionDeadlineNs' if row['phase'] == 'prepared' else 'workerDeadlineNs'])}
                if _change is None and record.get('history', {}).get('pending') is not None:
                    raise ValueError('native-source-history-recovery-required')
                if _change is not None:
                    if row is None:
                        raise ValueError('native-source-lifecycle-slot-missing')
                    previous = (txn or {}).get('change')
                    if previous is not None and {key: previous.get(key) for key in _change} == _change:
                        # The committed transition is the recovery receipt. Its
                        # old expected nonce may differ after a successful rearm.
                        return {'status': 'observed', 'label': label, 'phase': row['phase'], 'invocation': row['invocation']}
                    if row['invocation']['nonce'] != _change['expectedNonce']:
                        raise ValueError('native-source-lifecycle-nonce-changed')
                    if _change['kind'] == 'phase' and row['phase'] == _change['phase']:
                        return {'status': 'observed', 'label': label, 'phase': row['phase'], 'invocation': row['invocation']}
                    probe = dict(_change, id=uuid.uuid4().hex)
                    prospective = dict(row['invocation'])
                    if _change['kind'] == 'rearm':
                        prospective['nonce'] = probe['id']
                    _active_change(custody, probe, current, label, prospective)
                    # Archive only completed publication material. No active
                    # authority or business data is moved or inferred from it.
                    _archive_active(custody, jobs, record, guard, save)
                    txn = record['transaction']
                if _change is None and label in record['retired']:
                    raise ValueError('native-source-active-retired-slot')
                if txn is not None and _change is None:
                    # Retain previous exact Root evidence, never delete/adopt it.
                    for name in os.listdir(jobs):
                        if name.startswith(_active_stem(txn)):
                            data, inode = custody._native_root_file(jobs, name,
                                mode=0o640 if name.endswith(('.before', '.gate')) else 0o644 if 'definition' in name else 0o600,
                                gid=identity['id'] if name.endswith(('.before', '.gate')) else 0)
                            record['retained'][name] = {'inode': inode, 'sha256': sha(data),
                                'mode': 0o640 if name.endswith(('.before', '.gate')) else 0o644 if 'definition' in name else 0o600,
                                'gid': identity['id'] if name.endswith(('.before', '.gate')) else 0}
                boot = custody._native_boot_uuid()
                if boot != origin['witness']['boot']:
                    raise ValueError('native-source-active-boot-changed')
                now = custody._native_clock_ns()
                admission = now + (30 if role == 'sync' else custody.NATIVE_CHILD_BUDGETS[role]) * 10**9
                worker = now + custody.NATIVE_CHILD_BUDGETS[role] * 10**9
                if _admit or _change is not None and _change['kind'] == 'rearm':
                    from pixel_access_bridge import LaunchdAccessBridge
                    ceiling = LaunchdAccessBridge._native_source_deadline_ns(selection.bridge)
                    if _admission_ceiling_ns is not None:
                        if type(_admission_ceiling_ns) is not int or not 0 < _admission_ceiling_ns < 2**63:
                            raise ValueError('native-source-active-ceiling-invalid')
                        ceiling = min(ceiling, _admission_ceiling_ns)
                    admission = min(admission, ceiling)
                    if role != 'sync':
                        worker = min(worker, ceiling)
                invocation = (row['invocation'] if _change is not None and _change['kind'] == 'phase' else
                    custody._native_invocation({'role': role, 'identifier': identifier,
                        'nonce': uuid.uuid4().hex, 'boot': boot, 'clock': 'darwin-clock-monotonic-raw-ns-v1',
                        'generation': origin['generation'], 'issuedNs': str(now),
                        'admissionDeadlineNs': str(admission), 'workerDeadlineNs': str(worker)}).document)
                after = copy.deepcopy(current)
                if _change is None:
                    after['definitions'][label] = {'role': role, 'identifier': identifier, 'active': True, 'inode': None,
                        'sha256': sha(definition_body), 'profileSha256': sha(component['source-' + role + '.sb'])}
                after['gate']['inode'] = after['manifest']['inode'] = None
                record['transaction'] = {'label': label, 'invocation': invocation, 'before': copy.deepcopy(current),
                    'after': after, 'images': {key: {'inode': None, 'ready': False} for key in (
                        ('definition', 'manifest', 'gate', 'before') if _change is None else ('manifest', 'gate', 'before'))}}
                if _change is not None:
                    record['transaction']['change'] = dict(_change, id=uuid.uuid4().hex)
                if _admit or _change is not None and _change['kind'] == 'rearm':
                    record['transaction']['admissionCeilingNs'] = str(ceiling)
                record['phase'] = 'staging'
                refresh()
                if len(custody._native_encoded(after['gate']['document'])) > 16384 or len(after['definitions']) > 64:
                    raise ValueError('native-source-active-projection-too-large')
                # Closed prospective journal too, before any intent/file write.
                _active_record(custody, record, origin, binding, expected)
                if len(custody._native_encoded(record)) > 131072:
                    raise ValueError('native-source-active-record-too-large')
                save()
                txn = record['transaction']
            if txn['label'] != label:
                raise ValueError('native-source-active-pending-slot-preserved')
            actual_change = txn.get('change')
            if ((actual_change is None) != (_change is None) or actual_change is not None
                    and {key: actual_change.get(key) for key in _change} != _change):
                raise ValueError('native-source-lifecycle-pending-preserved')
            if record['phase'] == 'staging':
                for key in ('definition', 'manifest', 'gate', 'before'):
                    if key not in txn['images']:
                        continue
                    refresh()
                    name, body, mode, gid = images()[key]
                    _generation_stage(custody, jobs, name, body, txn['images'][key], save, guard, mode=mode, gid=gid)
                record['phase'] = 'publishing'
                save()
            stem = _active_stem(txn)
            if record['phase'] == 'publishing':
                for key, directory, name in (('definition', units, label + '.plist'), ('manifest', jobs, 'manifest.json')):
                    if key not in txn['images']:
                        continue
                    old = txn['before']['definitions'].get(label) if key == 'definition' else txn['before']['manifest']
                    if custody._native_at_inode(directory, name) == (old['inode'] if old else None) and old is not None:
                        if custody._native_at_inode(jobs, stem + key + '-old') is not None:
                            raise ValueError('native-source-active-before-conflict')
                        guard()
                        os.rename(name, stem + key + '-old', src_dir_fd=directory, dst_dir_fd=jobs)
                        os.fsync(directory)
                        os.fsync(jobs)
                    if custody._native_at_inode(directory, name) is None:
                        guard()
                        os.rename(stem + key, name, src_dir_fd=jobs, dst_dir_fd=directory)
                        os.fsync(directory)
                        os.fsync(jobs)
                record['phase'] = 'gate-ready'
                save()
            guard()
            if observed(gate, 'custody.json', [('before', body_for(txn['before'], 'gate')[0], txn['before']['gate']['inode']),
                    ('after', body_for(txn['after'], 'gate')[0], txn['after']['gate']['inode'])], mode=0o640, gid=identity['id']) == 'before':
                granting = actual_change is None or actual_change['phase'] in ('prepared', 'running')
                if (granting and custody._native_clock_ns() >= int(txn['invocation']['admissionDeadlineNs'])
                        or custody._native_boot_uuid() != txn['invocation']['boot']):
                    raise ValueError('native-source-active-admission-expired')
                # Root manifest + exact target definition must precede authority.
                for key, directory, name in (('definition', units, label + '.plist'), ('manifest', jobs, 'manifest.json')):
                    data, inode = custody._native_root_file(directory, name, mode=0o644 if key == 'definition' else 0o600)
                    wanted_inode = (txn['images'][key]['inode'] if key in txn['images'] else txn['after']['definitions'][label]['inode'])
                    if (data, inode) != (body_for(txn['after'], label if key == 'definition' else 'manifest')[0], wanted_inode):
                        raise ValueError('native-source-active-input-unconfirmed')
                guard()
                # Qualification above can block. Boot first, then read the
                # original clock ceiling LAST before this single-file commit.
                # This is not an atomic clock+filesystem operation.
                if custody._native_boot_uuid() != txn['invocation']['boot']:
                    raise ValueError('native-source-active-boot-changed')
                if granting and custody._native_clock_ns() >= int(txn['invocation']['admissionDeadlineNs']):
                    raise ValueError('native-source-active-admission-expired')
                os.replace(stem + 'gate', 'custody.json', src_dir_fd=jobs, dst_dir_fd=gate)
                os.fsync(gate)
                os.fsync(jobs)
            guard()
            record['current'] = txn['after']
            record['phase'] = 'committed'
            save()
            return {'status': 'prepared' if actual_change is None else 'committed', 'label': label,
                **({'phase': actual_change['phase']} if actual_change is not None else {}), 'invocation': txn['invocation']}
        finally:
            if jobs is not None:
                os.close(jobs)
            os.close(gate)


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


def rollover_installer_history(selection, *, transaction):
    """Retain settled installer metadata before another prepared upgrade.

    The fixed custody plan qualifies the producing and new Root transactions.
    No historical record is reset, copied into current authority, or deleted.
    """
    custody, doc, _component, _ref = _material(selection)
    if _installer_domain(transaction) != 'installer':
        raise ValueError('native-source-installer-history-purpose-invalid')
    from pixel_macos_custody import protected_directory
    name = 'source-installer-history.json'
    with protected_directory(custody.NATIVE_META) as metadata:
        plan = custody.native_installer_history_plan(selection, transaction=transaction)
        ledger, raw = custody._native_json_at(metadata, name)
        if encoded(ledger) != encoded(plan['ledger']):
            raise ValueError('native-source-installer-history-record-changed')
        incoming = plan['entry']
        if incoming is None:
            return {'status': 'observed', 'transaction': dict(transaction)}
        if (type(incoming) is not dict or set(incoming) != {'transaction', 'runtime', 'owner', 'boot', 'phase', 'artifacts'}
                or incoming['transaction'] != transaction or incoming['phase'] not in ('pending', 'committed')
                or type(incoming['artifacts']) is not dict or not incoming['artifacts']
                or incoming['artifacts'] != plan['records']):
            raise ValueError('native-source-installer-history-entry-invalid')
        for leaf, image in incoming['artifacts'].items():
            if (leaf not in ('source-installer-rebind.json', 'source-installer-code-only.json')
                    or type(image) is not dict or set(image) != {'transaction', 'name', 'inode', 'sha256'}
                    or _installer_domain(image['transaction']) != 'installer'
                    or image['name'] != leaf[:-5] + '-' + image['transaction']['transaction'] + '.json'
                    or not custody._native_inode(image['inode']) or type(image['sha256']) is not str
                    or not HEX.fullmatch(image['sha256'])):
                raise ValueError('native-source-installer-history-entry-invalid')
        expected = {key: value for key, value in incoming.items() if key != 'phase'}
        if ledger is None:
            ledger = {'schemaVersion': 1, 'kind': 'ods-native-source-installer-history', 'entries': []}
        else:
            ledger = json.loads(encoded(ledger), object_pairs_hook=_pairs)
        if ledger['entries'] and ledger['entries'][-1]['transaction'] == transaction:
            entry = ledger['entries'][-1]
            if {key: value for key, value in entry.items() if key != 'phase'} != expected:
                raise ValueError('native-source-installer-history-entry-changed')
        else:
            if len(ledger['entries']) >= 64 or ledger['entries'] and ledger['entries'][-1]['phase'] != 'committed':
                raise ValueError('native-source-installer-history-preserved')
            entry = json.loads(encoded(dict(incoming, phase='pending')), object_pairs_hook=_pairs)
            ledger['entries'].append(entry)
        witness = plan['witness']

        def guard():
            fresh = custody.native_installer_history_plan(selection, transaction=transaction)
            if (custody._native_json_at(metadata, name)[1] != raw
                    or custody._native_selected(selection, _publication=True) != doc
                    or fresh['entry'] is None
                    or {key: value for key, value in fresh['entry'].items() if key != 'phase'} != expected
                    or fresh['records'] != entry['artifacts'] or fresh['witness'] != witness):
                raise ValueError('native-source-installer-history-authority-changed')

        def save():
            nonlocal raw
            guard()
            raw = custody._native_save_at(selection, metadata, name, ledger, previous=raw, _publication=True)

        if entry['phase'] == 'committed':
            guard()
            return {'status': 'retained', 'transaction': dict(transaction)}
        save()  # Parent intent binds each exact original inode before a rename.
        for leaf, image in sorted(entry['artifacts'].items()):
            guard()
            current = custody._native_at_inode(metadata, leaf)
            if current == image['inode']:
                body, inode = custody._native_root_file(metadata, leaf)
                if (inode != image['inode'] or sha(body) != image['sha256']
                        or custody._native_at_inode(metadata, image['name']) is not None):
                    raise ValueError('native-source-installer-history-before-changed')
                os.rename(leaf, image['name'], src_dir_fd=metadata, dst_dir_fd=metadata)
                os.fsync(metadata)
            elif current is not None:
                raise ValueError('native-source-installer-history-before-changed')
            body, inode = custody._native_root_file(metadata, image['name'])
            if inode != image['inode'] or sha(body) != image['sha256']:
                raise ValueError('native-source-installer-history-retained-changed')
        guard()
        entry['phase'] = 'committed'
        save()
        guard()
        return {'status': 'retained', 'transaction': dict(transaction)}


def _installer_code_only_record(custody, record):
    keys = {'schemaVersion', 'kind', 'transaction', 'proofSha256', 'nonce', 'phase', 'deploymentSha256',
        'sourceRef', 'kitSha256', 'kitParent', 'oldKit', 'candidate', 'backup', 'candidateInode',
        'stagedFiles', 'publication', 'sidefiles', 'witness'}
    if (type(record) is not dict or set(record) != keys or type(record['schemaVersion']) is not int
            or record['schemaVersion'] != 1 or record['kind'] != 'ods-native-source-installer-code-only'
            or _installer_domain(record['transaction']) != 'installer'
            or type(record['nonce']) is not str or not re.fullmatch('[a-f0-9]{32}', record['nonce'])
            or record['phase'] not in ('staging', 'kit', 'publication', 'sidefiles', 'committed')
            or record['candidate'] != '.source-kit-' + record['nonce']
            or record['backup'] != '.source-kit-before-' + record['nonce']
            or any(type(record[key]) is not str or not HEX.fullmatch(record[key])
                for key in ('proofSha256', 'deploymentSha256', 'kitSha256'))
            or type(record['sourceRef']) is not str or not re.fullmatch('[a-f0-9]{40}', record['sourceRef'])
            or not custody._native_inode(record['kitParent'])
            or record['candidateInode'] is not None and not custody._native_inode(record['candidateInode'])
            or type(record['oldKit']) is not dict or set(record['oldKit']) != {'inode', 'files'}
            or not custody._native_inode(record['oldKit']['inode']) or type(record['oldKit']['files']) is not dict
            or type(record['stagedFiles']) is not dict or set(record['stagedFiles']) != {'source-kit.json', *PAYLOADS}):
        raise ValueError('native-source-installer-code-only-record-changed')
    for item in record['stagedFiles'].values():
        if (type(item) is not dict or set(item) != {'inode', 'ready'} or type(item['ready']) is not bool
                or item['inode'] is not None and not custody._native_inode(item['inode'])
                or item['ready'] and item['inode'] is None):
            raise ValueError('native-source-installer-code-only-record-changed')
    for name in ('publication', 'sidefiles'):
        item = record[name]
        if (type(item) is not dict or set(item) != {'before', 'beforeInode', 'after', 'afterInode', 'ready'}
                or type(item['before']) is not dict or not custody._native_inode(item['beforeInode'])
                or type(item['after']) is not dict and not (name == 'publication' and item['after'] is None)
                or item['afterInode'] is not None and not custody._native_inode(item['afterInode'])
                or type(item['ready']) is not bool or item['ready'] and item['afterInode'] is None):
            raise ValueError('native-source-installer-code-only-record-changed')
    if record['phase'] != 'staging' and record['publication']['after'] is None:
        raise ValueError('native-source-installer-code-only-record-changed')


def _installer_code_only_publication(record, files):
    return _installer_target_publication(dict(record, binding={'deploymentSha256': record['deploymentSha256']}), files)


def verify_installer_code_only_kit(selection, *, transaction, proof):
    """Only the fixed completed inert-code upgrade, never private generation authority."""
    custody, _doc, _component, _ref = _material(selection)
    from pixel_macos_custody import protected_directory
    with protected_directory(custody.NATIVE_META) as metadata:
        record, raw = custody._native_json_at(metadata, 'source-installer-code-only.json')
        _installer_code_only_record(custody, record)
        if (record['phase'] != 'committed' or record['transaction'] != transaction
                or record['proofSha256'] != sha(encoded(proof))):
            raise ValueError('native-source-installer-code-only-incomplete')
        plan = custody.native_installer_code_only_kit_plan(selection, transaction=transaction, proof=proof)
        if (record['publication']['after'] != _installer_code_only_publication(record, plan['targetKit'])
                or record['kitSha256'] != sha(plan['targetKit']['source-kit.json'])):
            raise ValueError('native-source-installer-code-only-publication-changed')
        if custody._native_json_at(metadata, 'source-installer-code-only.json')[1] != raw:
            raise ValueError('native-source-installer-code-only-record-changed')
        return {'status': 'prepared-no-jobs', 'kitSha256': record['kitSha256'], 'transaction': dict(transaction)}


def publish_installer_code_only_kit(selection, *, transaction, proof):
    """Replace only an independently qualified inactive code kit, retaining its inodes."""
    custody, doc, _component, ref = _material(selection)
    if (_installer_domain(transaction) != 'installer' or type(proof) is not dict
            or proof.get('kind') != 'ods-native-source-installer-absence' or proof.get('kit') is None):
        raise ValueError('native-source-installer-code-only-purpose-invalid')
    from pixel_macos_custody import protected_directory
    name = 'source-installer-code-only.json'
    with protected_directory(custody.NATIVE_META) as metadata, protected_directory(custody.NATIVE_KIT.parent) as parent:
        plan = custody.native_installer_code_only_kit_plan(selection, transaction=transaction, proof=proof)
        files = plan['targetKit']
        record, raw = custody._native_json_at(metadata, name)
        if record is None:
            nonce = uuid.uuid4().hex
            record = {'schemaVersion': 1, 'kind': 'ods-native-source-installer-code-only',
                'transaction': dict(transaction), 'proofSha256': sha(encoded(proof)), 'nonce': nonce,
                'phase': 'staging', 'deploymentSha256': selection.deployment.checksum, 'sourceRef': ref,
                'kitSha256': sha(files['source-kit.json']), 'kitParent': plan['kitParent'], 'oldKit': plan['oldKit'],
                'candidate': '.source-kit-' + nonce, 'backup': '.source-kit-before-' + nonce,
                'candidateInode': None, 'stagedFiles': {leaf: {'inode': None, 'ready': False} for leaf in files},
                'publication': {'before': json.loads(plan['publication']['before'], object_pairs_hook=_pairs),
                    'beforeInode': plan['publication']['inode'], 'after': None, 'afterInode': None, 'ready': False},
                'sidefiles': {'before': json.loads(plan['sidefiles']['before'], object_pairs_hook=_pairs),
                    'beforeInode': plan['sidefiles']['inode'],
                    'after': json.loads(plan['sidefiles']['after'], object_pairs_hook=_pairs),
                    'afterInode': None, 'ready': False}, 'witness': plan['witness']}

        def authority_guard():
            _installer_code_only_record(custody, record)
            custody.native_installer_code_only_kit_authority(selection, transaction=transaction, proof=proof)
            if (record['transaction'] != transaction or record['proofSha256'] != sha(encoded(proof))
                    or record['deploymentSha256'] != selection.deployment.checksum or record['sourceRef'] != ref
                    or custody._native_selected(selection, _publication=True) != doc
                    or custody._native_json_at(metadata, name)[1] != raw
                    or _at_inode(parent, '.') != record['kitParent']):
                raise ValueError('native-source-installer-code-only-authority-changed')
            # This domain contains no Source private state or invocation. Its
            # frozen file images survive boot; every mutation still observes
            # the actual current boot's complete label/process census.
            fresh = _no_actors(selection, custody, metadata)
            if dict(fresh, boot=record['witness']['boot']) != record['witness']:
                raise ValueError('native-source-installer-code-only-authority-changed')

        def guard():
            authority_guard()
            current = custody.native_installer_code_only_kit_plan(selection, transaction=transaction, proof=proof)
            if (current['targetKit'] != files or current['oldKit'] != record['oldKit']
                    or current['kitParent'] != record['kitParent'] or current['witness'] != record['witness']
                    or current['publication'] != {'before': encoded(record['publication']['before']), 'inode': record['publication']['beforeInode']}
                    or current['sidefiles'] != {'before': encoded(record['sidefiles']['before']),
                        'inode': record['sidefiles']['beforeInode'], 'after': encoded(record['sidefiles']['after'])}):
                raise ValueError('native-source-installer-code-only-derivation-changed')

        def save():
            nonlocal raw
            authority_guard()  # New inert FD inode intent precedes its first byte.
            raw = custody._native_save_at(selection, metadata, name, record, previous=raw, _publication=True)

        if raw is None:
            save()
        guard()
        if record['phase'] == 'staging':
            expected = _stage_kit(selection, custody, metadata, parent, record, files, save, _guard=guard)
            _kit_image(custody, parent, record['candidate'], expected=expected)
            record['publication']['after'] = _installer_code_only_publication(record, files)
            record['phase'] = 'kit'
            save()
        expected = {'inode': record['candidateInode'], 'files': {leaf: {
            'inode': record['stagedFiles'][leaf]['inode'], 'sha256': sha(body)} for leaf, body in files.items()}}
        if record['phase'] == 'kit':
            guard()
            current = _at_inode(parent, custody.NATIVE_KIT.name)
            if current == record['oldKit']['inode']:
                _kit_image(custody, parent, custody.NATIVE_KIT.name, expected=record['oldKit'])
                if _at_inode(parent, record['backup']) is not None:
                    raise ValueError('native-source-installer-code-only-backup-conflict')
                os.rename(custody.NATIVE_KIT.name, record['backup'], src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
                current = None
            if current is None:
                guard()
                _kit_image(custody, parent, record['backup'], expected=record['oldKit'])
                _kit_image(custody, parent, record['candidate'], expected=expected)
                os.rename(record['candidate'], custody.NATIVE_KIT.name, src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
            _kit_image(custody, parent, custody.NATIVE_KIT.name, expected=expected)
            record['phase'] = 'publication'
            save()
        for phase, leaf, label, next_phase in (('publication', 'source-publication.json', 'publication', 'sidefiles'),
                ('sidefiles', 'source-sidefiles.json', 'sidefiles', 'committed')):
            if record['phase'] != phase:
                continue
            image = record[label]
            body = encoded(image['after'])
            if label == 'publication' and body != encoded(_installer_code_only_publication(record, files)):
                raise ValueError('native-source-installer-code-only-publication-changed')
            temporary = '.source-code-only-' + label + '-next-' + record['nonce']
            backup = '.source-code-only-' + label + '-before-' + record['nonce'] + '.json'
            current = custody._native_at_inode(metadata, leaf)
            if current != image['afterInode'] or image['afterInode'] is None:
                stage = {'inode': image['afterInode'], 'ready': image['ready']}
                def image_save():
                    image.update(afterInode=stage['inode'], ready=stage['ready'])
                    save()
                _generation_stage(custody, metadata, temporary, body, stage, image_save, guard)
                guard()
                if current == image['beforeInode']:
                    if (custody._native_root_file(metadata, leaf) != (encoded(image['before']), image['beforeInode'])
                            or custody._native_at_inode(metadata, backup) is not None):
                        raise ValueError('native-source-installer-code-only-before-changed')
                    os.rename(leaf, backup, src_dir_fd=metadata, dst_dir_fd=metadata)
                    os.fsync(metadata)
                elif current is not None or custody._native_root_file(metadata, backup) != (encoded(image['before']), image['beforeInode']):
                    raise ValueError('native-source-installer-code-only-before-changed')
                guard()
                os.rename(temporary, leaf, src_dir_fd=metadata, dst_dir_fd=metadata)
                os.fsync(metadata)
            if custody._native_root_file(metadata, leaf) != (body, image['afterInode']):
                raise ValueError('native-source-installer-code-only-after-changed')
            record['phase'] = next_phase
            save()
        guard()
        return verify_installer_code_only_kit(selection, transaction=transaction, proof=proof)


def _installer_absent_kit_control(custody, selection, control, *, physical):
    if (type(control) is not dict or set(control) != {'transaction', 'proof'}
            or _installer_domain(control['transaction']) != 'installer'
            or type(control['proof']) is not dict or control['proof'].get('kind') != 'ods-native-source-installer-absence'):
        raise ValueError('native-source-installer-first-kit-control-invalid')
    name = 'native_installer_absent_kit_plan' if physical else 'native_installer_absent_kit_authority'
    verifier = getattr(custody, name, None)
    if not callable(verifier):
        raise ValueError('native-source-installer-first-kit-verifier-unavailable')
    return verifier(selection, transaction=control['transaction'], proof=control['proof'])


def verify_installer_absent_kit(selection, *, transaction, proof):
    """Distinct terminal absence-purpose proof, with no account or live data."""
    custody, _doc, component, ref = _material(selection)
    control = {'transaction': transaction, 'proof': proof}
    _installer_absent_kit_control(custody, selection, control, physical=True)
    from pixel_macos_custody import protected_directory
    files = _publication_files(component, ref, selection.deployment.checksum)
    with protected_directory(custody.NATIVE_META) as metadata:
        record, raw = custody._native_json_at(metadata, 'source-publication.json')
        _publication_record(record, selection.deployment.checksum, files)
        if (record['phase'] != 'prepared-no-jobs' or record['old'] is not None or record['bindings']
                or dict(_no_actors(selection, custody, metadata), boot=record['witness']['boot']) != record['witness']):
            raise ValueError('native-source-installer-first-kit-publication-incomplete')
        if custody._native_json_at(metadata, 'source-publication.json')[1] != raw:
            raise ValueError('native-source-installer-first-kit-publication-changed')
    _installer_absent_kit_control(custody, selection, control, physical=True)
    return {'status': 'prepared-no-jobs', 'kitSha256': sha(files['source-kit.json']), 'transaction': dict(transaction)}


def publish_installer_absent_kit(selection, *, transaction, proof):
    """Only the actual active candidate's first inert kit, never private state.

    Failure after publication begins preserves the active installer journal and
    exact same kit intent. A legacy restored runtime cannot mint this kit.
    """
    custody, _doc, _component, _ref = _material(selection)
    control = {'transaction': transaction, 'proof': proof}
    _installer_absent_kit_control(custody, selection, control, physical=True)
    publish_kit(selection, _installer=control)
    return verify_installer_absent_kit(selection, transaction=transaction, proof=proof)


def publish_kit(selection, *, rollback=False, _installer=None):
    """Inert same-deployment A->B publication. Never creates an account/job.

    All names are fixed or nonce-derived under the same Root journal. Previous
    metadata/kit inodes are retained, allowing exact restoration without ever
    reading or backing up a refresh token or owner proposal.
    """
    if type(rollback) is not bool:
        raise ValueError('native-source-publication-control-invalid')
    if _installer is not None and rollback:
        raise ValueError('native-source-installer-first-kit-preserve-active-recovery')
    custody, doc, component, ref = _material(selection)
    from pixel_macos_custody import protected_directory
    with protected_directory(custody.NATIVE_META) as metadata, protected_directory(custody.NATIVE_KIT.parent) as parent:
        def installer_guard():
            if _installer is not None:
                _installer_absent_kit_control(custody, selection, _installer, physical=True)
        installer_guard()
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
            observed = dict(witness, boot=record['witness']['boot']) if _installer is not None else witness
            if record['witness'] != observed:
                raise ValueError('native-source-publication-witness-changed')
        _publication_record(record, selection.deployment.checksum, files)
        def save():
            nonlocal raw
            _publication_record(record, selection.deployment.checksum, files)
            observed = _no_actors(selection, custody, metadata)
            if _installer is not None:
                observed = dict(observed, boot=record['witness']['boot'])
            if observed != record['witness']:
                raise ValueError('native-source-publication-witness-changed')
            if _installer is not None:
                # Bind a newly-created inert FD before its first bytes; the
                # full physical prefix is checked after this private CAS.
                _installer_absent_kit_control(custody, selection, _installer, physical=False)
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
            expected = _stage_kit(selection, custody, metadata, parent, record, files, save,
                _guard=installer_guard if _installer is not None else None)
            _kit_image(custody, parent, record['candidate'], expected=expected)
            record['phase'] = 'publishing-kit'
            save()
        expected = {'inode': record['candidateInode'], 'files': {name: {
            'inode': record['stagedFiles'][name]['inode'], 'sha256': sha(body)} for name, body in files.items()}}
        if record['phase'] == 'publishing-kit':
            installer_guard()
            current = _at_inode(parent, custody.NATIVE_KIT.name)
            if record['old'] is not None and current == record['old']['inode']:
                _kit_image(custody, parent, custody.NATIVE_KIT.name, expected=record['old'])
                if _at_inode(parent, record['backup']) is not None:
                    raise ValueError('native-source-old-kit-backup-conflict')
                os.rename(custody.NATIVE_KIT.name, record['backup'], src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
                current = None
            if current is None:
                installer_guard()
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
            installer_guard()
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
