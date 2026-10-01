#!/usr/bin/env python3
"""Durable, exact-byte custody for a held Linux Pixel source upgrade.

This module does not authorize access, run an installer, restart a service, or
restore an access receipt. The installer supplies the existing authenticated
coordinator's hold/proof operations. Every write is conditioned on that hold.
Snapshots contain source and coordinator files, live under root custody, and
are never imported by this module. Private coordinator payloads are stored as
private content-addressed blobs; journal metadata contains only their hashes.
"""
from __future__ import annotations

import contextlib
import fcntl
import functools
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import socket
import struct
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True


HEX = re.compile(r"[a-f0-9]{64}\Z")
ROOTS = ("bin", "lib", "scripts", "installers", "extensions", "vendor")
EXECUTION_CONTROLS = frozenset((
    'extensions/services/pixel-agent/host/cancellable-exec.sh',
    'extensions/services/pixel-agent/host/noninteractive-sudo.sh'))
IGNORED = frozenset((".git", "__pycache__", "node_modules", "dist"))
MAX_FILES = 30000
MAX_BYTES = 512 * 1024 * 1024
MAX_FILE = 64 * 1024 * 1024
MIRROR = Path('/usr/local/libexec/ods-pixel-access')
MIRROR_LEAVES = frozenset(('/etc/ods/pixel-access.json', '/etc/ods/pixel-access-relay.key',
                         '/etc/systemd/system/ods-pixel-access.service'))
SYSTEM_ROOT = Path('/')
SYSTEM_UID = 0

# The renderer is the exact root-custodied source blob, never an owner-selected
# command. It runs as the installation owner over disposable private copies.
_OVERLAY_CHILD = r'''
import contextlib, hashlib, io, json, os, pathlib, sys, tempfile
value = json.load(sys.stdin)
with tempfile.TemporaryDirectory(prefix='ods-source-overlay-') as temporary:
    folder = pathlib.Path(temporary)
    path = folder / 'config.json'
    path.write_text(value['config'], encoding='utf-8')
    path.chmod(0o600)
    answers = folder / 'answers.json'
    answers.write_text(json.dumps(value['answers']), encoding='utf-8')
    answers.chmod(0o600)
    sys.argv = ['pixel-runtime-budget.py', str(path), str(value['port']), str(answers),
                value['home'], value['transport'], value['socket']]
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        try:
            exec(compile(value['renderer'], 'pixel-runtime-budget.py', 'exec'), {'__name__': '__main__'})
        except SystemExit as error:
            if error.code not in (None, 0):
                raise
    name = output.getvalue().strip()
    result = path if name == 'unchanged' else pathlib.Path(name)
    if (result.parent != folder or result.is_symlink() or not result.is_file()
            or result.stat().st_size > 2 * 1024 * 1024
            or result != path and not result.name.startswith('.ods-pixel-runtime-budget.')):
        raise SystemExit(1)
    print(hashlib.sha256(result.read_bytes()).hexdigest())
'''

# Only validation functions of the exact source-plan installer are called. No
# build, installation, service mutation, or model/workspace code is executed.
_PROVISION_CHILD = r'''
import importlib.util, json, pathlib, sys, tempfile
value = json.load(sys.stdin)
with tempfile.TemporaryDirectory(prefix='ods-source-provision-') as temporary:
    root = pathlib.Path(temporary)
    for name, source in value['sources'].items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding='utf-8')
    path = root / value['installer']
    spec = importlib.util.spec_from_file_location('ods_provision_contract', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.validate_config(value['config'])
    if value['kind'] == 'inspection':
        module.validate_image(value['images'][0], value['config']['imageId'])
        module.docker_path('local')
        files, unit = module.RUNTIME_FILES, None
    else:
        files, unit = module.FILES, module.unit_bytes(value['config']).decode('utf-8')
        module.common.docker_path('local')
    print(json.dumps({'files': list(files), 'unit': unit}))
'''


class UpgradeError(RuntimeError):
    pass


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def mount_id(fd):
    """Linux mount identity also distinguishes bind mounts sharing st_dev."""
    try:
        with open(f'/proc/self/fdinfo/{fd}', encoding='ascii') as handle:
            raw = handle.read(65537)
        values = re.findall(r'^mnt_id:\s*([0-9]+)$', raw, re.MULTILINE)
        if len(raw) > 65536 or len(values) != 1:
            raise ValueError('missing mount identity')
        return int(values[0])
    except (OSError, UnicodeError, ValueError):
        raise UpgradeError('source-filesystem-proof-unavailable') from None


FEATURE_COMPOSE_SERVICES = frozenset({
    'litellm', 'searxng', 'token-spy', 'whisper', 'tts', 'n8n', 'qdrant',
    'embeddings', 'hermes', 'hermes-proxy', 'pixel-edge', 'pixel-model-relay',
    'openclaw', 'ape', 'comfyui', 'perplexica', 'privacy-shield', 'ods-proxy',
    'tailscale', 'langfuse', 'brave-search',
})


def installed_projection(before, candidate):
    after = {**before, **candidate}
    # Only Phase03's closed feature selection can retire its exact counterpart.
    # Unknown extensions and absent candidate pairs retain their installed data.
    for service in FEATURE_COMPOSE_SERVICES:
        enabled = f'extensions/services/{service}/compose.yaml'
        disabled = enabled + '.disabled'
        if enabled in candidate and disabled in candidate:
            raise UpgradeError('source-feature-selection-ambiguous')
        if enabled in candidate:
            after.pop(disabled, None)
        elif disabled in candidate:
            after.pop(enabled, None)
    return after


def release_guard(state, install, uid, transaction, outcome, *, state_uid=0):
    """Called by the protected coordinator before it releases model admission.

    A shell flag is not authority to release a source-update hold. Only the
    same root-owned, completed source plan permits the existing coordinator to
    continue its fresh config/receipt/runtime proof and release sequence.
    """
    source_state = Path(state) / "source-upgrade"
    if not os.path.lexists(source_state):
        return
    manager = SourceUpgrade(source_state, install, uid, state_uid=state_uid)
    value = manager.journal()
    if value is None:
        raise UpgradeError("source-recovery-required")
    if value["phase"] == "complete" and value["hold"] != transaction:
        return  # Retained completed evidence does not own a newer transaction.
    if (value["hold"] != transaction or value["phase"] != "complete"
            or value["outcome"] != outcome):
        raise UpgradeError("source-completion-required")
    side = "before" if outcome == "rolled-back" else "after"
    if inventory(Path(install), uid) != value[side]:
        raise UpgradeError("source-live-drift")
    # The new protected guard is retained even when the source is restored.
    # Downgrading it under a live hold would remove the completion barrier.
    manager.verify_mirror()


def _overlay_private(root, name, uid):
    item, raw = read_file(Path(root), name, uid)
    if item is None or item['mode'] & 0o077 or len(raw) > 2 * 1024 * 1024:
        raise UpgradeError('source-overlay-state-unsafe')
    return raw


def _overlay_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate')
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=pairs)
    if type(value) is not dict:
        raise UpgradeError('source-overlay-state-invalid')
    encoded(value)  # Reject non-finite JSON values as well.
    return value


def _overlay_candidate(install, source_ref, uid):
    """Read a pinned private candidate beneath the installer's shared data dir.

    data/ may legitimately be group-writable; data/pixel/ and every private
    descendant still require owner custody. Descriptor traversal never follows
    links, and parent identities are rechecked before returning. The caller
    authenticates these bytes against the root release-intent SHA separately.
    """
    names = ('data', 'pixel', 'source-' + source_ref, 'dist', 'openclaw.json')
    fds = [os.open(install, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)]
    links = []
    try:
        for index, name in enumerate(names):
            parent = fds[-1]
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if index < len(names) - 1:
                flags |= os.O_DIRECTORY
            fd = os.open(name, flags, dir_fd=parent)
            fds.append(fd)
            info = os.fstat(fd)
            mask = 0o002 if index == 0 else 0o077 if index in (1, 4) else 0o022
            if (info.st_uid != uid or info.st_mode & mask
                    or index < 4 and not stat.S_ISDIR(info.st_mode)
                    or index == 4 and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                                      or info.st_size > 2 * 1024 * 1024)):
                raise UpgradeError('source-overlay-candidate-unsafe')
            links.append((parent, name, info.st_dev, info.st_ino))
        before = os.fstat(fds[-1])
        with os.fdopen(fds[-1], 'rb', closefd=False) as handle:
            raw = handle.read(2 * 1024 * 1024 + 1)
        after = os.fstat(fds[-1])
        if len(raw) > 2 * 1024 * 1024 or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise UpgradeError('source-overlay-candidate-changed')
        for parent, name, device, inode in links:
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (device, inode):
                raise UpgradeError('source-overlay-candidate-changed')
        root = os.stat(install, follow_symlinks=False)
        opened = os.fstat(fds[0])
        if (root.st_dev, root.st_ino) != (opened.st_dev, opened.st_ino):
            raise UpgradeError('source-overlay-candidate-changed')
        return raw
    finally:
        for fd in reversed(fds):
            os.close(fd)


def _provision_contract(manager, plan, kind, config, images):
    installer = 'installers/lib/pixel-' + ('preview-inspection' if kind == 'inspection' else 'project-runtime') + '.py'
    names = {installer, 'installers/lib/pixel-preview-inspection.py',
             'extensions/services/pixel-agent/host/preview_inspection_protocol.py'}
    sources = {name: manager._blob(plan['after'][name]['sha256']).decode('utf-8') for name in names}
    result = subprocess.run([sys.executable, '-I', '-c', _PROVISION_CHILD],
        input=json.dumps(dict(installer=installer, sources=sources, kind=kind,
                             config=config, images=images)).encode(),
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15,
        env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}, cwd='/')
    if result.returncode or len(result.stdout) > 16384:
        raise UpgradeError('source-overlay-provision-contract-invalid')
    value = _overlay_json(result.stdout)
    if (set(value) != {'files', 'unit'} or type(value['files']) is not list
            or not 1 <= len(value['files']) <= 32
            or any(type(name) is not str or not re.fullmatch(r'[a-z_]+\.py', name) for name in value['files'])):
        raise UpgradeError('source-overlay-provision-contract-invalid')
    return value


def _provision_peer(path):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(2)
        connection.connect(str(path))
        return struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[:2]


def _prove_provision(bridge, manager, plan, kind):
    """Bound installed channel proof, not an attestation of image build inputs.

    This is only used for an absent capability in the original release. The
    trusted installer source, protected deployment config and running unit/peer
    must agree. No HTTP request, browser operation, project job or grant occurs.
    """
    inspection = kind == 'inspection'
    stem = 'ods-pixel-inspection' if inspection else 'ods-pixel-project'
    unit_name = 'pixel-preview-inspection.service' if inspection else stem + '.service'
    program = 'usr/local/libexec/' + stem
    config_name = 'etc/' + stem + '.json'
    evidence = {}
    def protected(name):
        item, raw = read_file(SYSTEM_ROOT, name, SYSTEM_UID)
        if item is None or len(raw) > 2 * 1024 * 1024:
            raise UpgradeError('source-overlay-provision-file-invalid')
        evidence[name] = item
        return raw
    config = _overlay_json(protected(config_name))
    if config.get('ownerUid') != bridge.owner.pw_uid:
        raise UpgradeError('source-overlay-provision-owner-mismatch')
    if inspection and config.get('transport') != 'local':
        raise UpgradeError('source-overlay-provision-contract-invalid')
    images = []
    for key in ('imageId', 'pythonImageId'):
        image = config.get(key)
        if key == 'pythonImageId' and image is None:
            continue
        if type(image) is not str or not re.fullmatch(r'sha256:[a-f0-9]{64}', image):
            raise UpgradeError('source-overlay-provision-image-invalid')
        rows = json.loads(bridge.command(['/usr/bin/docker', 'image', 'inspect', image], timeout=5))
        if type(rows) is not list or len(rows) != 1 or rows[0].get('Id') != image:
            raise UpgradeError('source-overlay-provision-image-invalid')
        images.append(rows)
    contract = _provision_contract(manager, plan, kind, config, images)
    for name in contract['files']:
        raw = protected(program + '/' + name)
        source = plan['after']['extensions/services/pixel-agent/host/' + name]
        if sha(raw) != source['sha256']:
            raise UpgradeError('source-overlay-provision-source-changed')
    unit_path = 'etc/systemd/system/' + unit_name
    unit = protected(unit_path)
    expected = (manager._blob(plan['after']['extensions/services/pixel-agent/host/' + unit_name]['sha256'])
                if inspection else contract['unit'].encode('utf-8'))
    if unit != expected:
        raise UpgradeError('source-overlay-provision-unit-changed')
    properties = 'LoadState,ActiveState,SubState,MainPID,User,FragmentPath,DropInPaths,NeedDaemonReload'
    def active():
        raw = bridge.command(['/usr/bin/systemctl', 'show', unit_name, '--property=' + properties], timeout=5)
        rows = dict(line.split('=', 1) for line in raw.splitlines())
        if (set(rows) != set(properties.split(',')) or rows['LoadState'] != 'loaded'
                or rows['ActiveState'] != 'active' or rows['SubState'] != 'running'
                or rows['User'] != ('root' if inspection else bridge.owner.pw_name)
                or rows['FragmentPath'] != '/' + unit_path or rows['DropInPaths']
                or rows['NeedDaemonReload'] != 'no' or not rows['MainPID'].isdigit()
                or int(rows['MainPID']) <= 1):
            raise UpgradeError('source-overlay-provision-unit-unavailable')
        return rows
    status = active()
    socket_path = SYSTEM_ROOT / ('run/ods-pixel-inspection/control.sock' if inspection
                                  else 'var/lib/ods-pixel-project/control.sock')
    socket_uid = SYSTEM_UID if inspection else bridge.owner.pw_uid
    directory(socket_path.parent, socket_uid)
    info = socket_path.lstat()
    if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != socket_uid or info.st_mode & 0o007
            or _provision_peer(socket_path) != (int(status['MainPID']), socket_uid)):
        raise UpgradeError('source-overlay-provision-peer-mismatch')
    # The owner must also be able to reach the provisioned channel. Inspector
    # uses its existing supplementary ods-pixel group; project is owner-private.
    if os.geteuid() == 0:
        groups = os.getgrouplist(bridge.owner.pw_name, bridge.owner.pw_gid)
        check = subprocess.run([sys.executable, '-I', '-c',
            'import socket,sys; s=socket.socket(socket.AF_UNIX); s.settimeout(2); s.connect(sys.argv[1]); s.close()',
            str(socket_path)], user=bridge.owner.pw_uid, group=bridge.owner.pw_gid,
            extra_groups=groups, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4)
        if check.returncode:
            raise UpgradeError('source-overlay-provision-owner-unavailable')
    if active() != status or any(read_file(SYSTEM_ROOT, name, SYSTEM_UID)[0] != item for name, item in evidence.items()):
        raise UpgradeError('source-overlay-provision-changed')
    return dict(files=evidence, unit=status, socket=[info.st_dev, info.st_ino], images=images)


def _render_overlay(renderer, anchor, home, uid, gid, *, provision=None):
    config = _overlay_json(anchor)
    args = config['plugins']['entries']['pixel-ods']['config']
    port = args['perplexicaPort']
    provision = provision or {}
    transport = 'unix' if 'inspection' in provision else args.get('workspacePreviewInspectionTransport', '')
    socket = args.get('projectBuildSocket', '')
    if 'project' in provision:
        socket = '/var/lib/ods-pixel-project/control.sock'
    providers = config['models']['providers']
    provider = next(iter(providers)) if len(providers) == 1 else None
    if provider not in ('ods-gateway', 'ods-local'):
        raise UpgradeError('source-overlay-contract-unavailable')
    model = providers[provider]['models'][0]
    image_policy = args.get('modelImageInput', 'unknown')
    # Derive inputs from the completed release, not the current config/answers.
    # New channels require the separately verified protected provisioning proof.
    if (type(port) is not int or not 1 <= port <= 65535 or transport != 'unix'
            or socket not in ('', '/var/lib/ods-pixel-project/control.sock')
            or image_policy not in ('unknown', 'supported', 'unsupported')
            or provider == 'ods-gateway' and (type(args.get('modelRouteFingerprint')) is not str
                or not HEX.fullmatch(args['modelRouteFingerprint']))
            or provider == 'ods-local' and 'modelRouteFingerprint' in args):
        raise UpgradeError('source-overlay-contract-unavailable')
    answers = dict(modelProvider=provider, modelId=model['id'], modelName=model['name'],
                   modelImageInput=image_policy)
    if provider == 'ods-gateway':
        answers['modelRouteFingerprint'] = args['modelRouteFingerprint']
    payload = dict(renderer=renderer.decode('utf-8'), config=anchor.decode('utf-8'),
                   port=port, transport=transport, socket=socket, home=str(home / '.openclaw'),
                   answers=answers)
    identity = dict(user=uid, group=gid, extra_groups=[]) if os.geteuid() == 0 else {}
    if not identity and (os.geteuid() != uid or os.getegid() != gid):
        raise UpgradeError('source-overlay-owner-mismatch')
    result = subprocess.run([sys.executable, '-I', '-c', _OVERLAY_CHILD],
        input=json.dumps(payload).encode('utf-8'), stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, cwd='/', timeout=20,
        env={'PATH': '/usr/bin:/bin', 'HOME': str(home), 'LANG': 'C.UTF-8'}, **identity)
    if result.returncode or len(result.stdout) != 65:
        raise UpgradeError('source-overlay-render-failed')
    value = result.stdout.decode('ascii').strip()
    if not HEX.fullmatch(value):
        raise UpgradeError('source-overlay-render-failed')
    return value


def prove_runtime_overlay(bridge, transaction, config_sha, outcome):
    """Prove the exact ODS overlay; never alter receipts or release admission.

    Called under the coordinator lock with its native/Edge leases held. The
    caller must repeat this proof after a NEW runtime attestation, and compare
    both results before writing a separate completion and releasing its gates.
    Legacy owner snapshots are authenticated by the root preparation digests;
    the original candidate also reconstructs the receipt's revocation baseline.
    """
    from access_mode_config import rebase_enabled
    if outcome != 'applied':
        raise UpgradeError('source-overlay-outcome-invalid')
    uid, gid = bridge.owner.pw_uid, bridge.owner.pw_gid
    home = Path(bridge.owner.pw_dir)
    if uid <= 0 or home != Path(bridge.home) or home.resolve() != home:
        raise UpgradeError('source-overlay-owner-mismatch')
    directory(home, uid)
    manager = SourceUpgrade(bridge.state / 'source-upgrade', bridge.install, uid)
    plan = manager.journal()
    if (plan is None or plan['phase'] != 'complete' or plan['outcome'] != 'applied'
            or plan['hold'] != transaction):
        raise UpgradeError('source-overlay-plan-required')
    release_guard(bridge.state, bridge.install, uid, transaction, outcome)
    pending = bridge.model_journal(transaction)
    if pending['configured_mode'] != 'full-access' or pending['phase'] not in ('held', 'finishing', 'error'):
        raise UpgradeError('source-overlay-hold-required')
    native, edge = bridge.native(), bridge.edge()
    if (not bridge.owns_native_hold(native, pending['token']) or edge.get('phase') != 'held'
            or edge.get('streams') or edge.get('revision') != pending['edge_revision']):
        raise UpgradeError('source-overlay-hold-required')
    # No live proof is inferred from these records. They authenticate only the
    # immutable input and the original, already completed access migration.
    root_records = {}
    for name in ('release-intent', 'release-baseline', 'release-prepared', 'release-completed'):
        raw = _overlay_private(bridge.state, name + '.json', 0)
        root_records[name] = (_overlay_json(raw), sha(raw))
    intent, baseline, prepared, completed = (root_records[name][0] for name in
        ('release-intent', 'release-baseline', 'release-prepared', 'release-completed'))
    if (set(intent) != {'transactionId', 'candidateSha256'}
            or set(baseline) != {'transactionId', 'configSha256', 'receiptSha256'}
            or set(prepared) != {'transactionId', 'candidateSha256', 'beforeSha', 'afterSha'}
            or completed != dict(transactionId=transaction, configSha256=prepared.get('afterSha'), outcome='apply')
            or any(value.get('transactionId') != transaction for value in (intent, baseline, prepared))
            or prepared['beforeSha'] != baseline['configSha256']
            or prepared['candidateSha256'] != intent['candidateSha256']):
        raise UpgradeError('source-overlay-release-unbound')
    owner_root = home / '.openclaw/.ods-access-mode'
    directory(owner_root, uid, private=True)
    if os.path.lexists(owner_root / 'access-release-journal.json'):
        raise UpgradeError('source-overlay-release-pending')
    records = [_overlay_private(owner_root, name, uid) for name in (
        'access-release-config-before.json', 'access-release-config-after.json',
        'access-release-receipt-before.json', 'access-release-receipt-after.json',
        'access-release-completed.json', 'pixel-access-mode.json')]
    before, anchor, receipt_before, receipt_after, owner_completed, receipt_live = records
    if (sha(before) != baseline['configSha256'] or sha(anchor) != prepared['afterSha']
            or sha(receipt_before) != baseline['receiptSha256']
            or receipt_after != encoded(_overlay_json(receipt_live))):
        raise UpgradeError('source-overlay-snapshot-changed')
    candidate = _overlay_candidate(bridge.install, plan['identity']['afterRef'], uid)
    if sha(candidate) != intent['candidateSha256']:
        raise UpgradeError('source-overlay-candidate-changed')
    original_receipt = _overlay_json(receipt_before)
    migrated, restore_baseline = rebase_enabled(_overlay_json(before), _overlay_json(candidate),
                                               original_receipt['baseline'])
    if (encoded(migrated) != anchor or encoded(dict(original_receipt, baseline=restore_baseline,
            config_sha256=sha(anchor))) != receipt_after):
        raise UpgradeError('source-overlay-receipt-unbound')
    path = home / '.openclaw/openclaw.json'
    if (original_receipt.get('status') != 'full-access' or original_receipt.get('config_path') != str(path)
            or _overlay_json(owner_completed) != dict(transactionId=transaction, configPath=str(path),
                configSha256=sha(anchor), receiptSha256=sha(receipt_after), outcome='apply')):
        raise UpgradeError('source-overlay-owner-completion-invalid')
    current = _overlay_private(home, '.openclaw/openclaw.json', uid)
    if sha(current) != config_sha:
        raise UpgradeError('source-overlay-config-changed')
    renderer_item = plan['after']['installers/lib/pixel-runtime-budget.py']
    renderer = manager._blob(renderer_item['sha256'])
    anchor_args = _overlay_json(anchor)['plugins']['entries']['pixel-ods']['config']
    promoted = []
    if anchor_args.get('workspacePreviewInspectionTransport') in (None, ''):
        promoted.append('inspection')
    if (not anchor_args.get('projectBuildSocket')
            and 'installers/lib/pixel-project-runtime.py' in plan['after']):
        promoted.append('project')
    provision = {kind: _prove_provision(bridge, manager, plan, kind) for kind in promoted}
    render_args = dict(provision=provision) if provision else {}
    if len(renderer) > 256 * 1024 or _render_overlay(renderer, anchor, home, uid, gid, **render_args) != config_sha:
        raise UpgradeError('source-overlay-not-derived')
    if {kind: _prove_provision(bridge, manager, plan, kind) for kind in promoted} != provision:
        raise UpgradeError('source-overlay-provision-changed')
    # Rendering is a subprocess. A same-owner edit, revocation, source change,
    # or process restart during that interval invalidates the entire evidence.
    release_guard(bridge.state, bridge.install, uid, transaction, outcome)
    if (manager.journal() != plan or manager._blob(renderer_item['sha256']) != renderer
            or bridge.model_journal(transaction) != pending
            or os.path.lexists(owner_root / 'access-release-journal.json')
            or _overlay_private(home, '.openclaw/openclaw.json', uid) != current
            or _overlay_candidate(bridge.install, plan['identity']['afterRef'], uid) != candidate
            or any(sha(_overlay_private(bridge.state, name + '.json', 0)) != item[1]
                for name, item in root_records.items())
            or [_overlay_private(owner_root, name, uid) for name in (
                'access-release-config-before.json', 'access-release-config-after.json',
                'access-release-receipt-before.json', 'access-release-receipt-after.json',
                'access-release-completed.json', 'pixel-access-mode.json')] != records):
        raise UpgradeError('source-overlay-state-changed')
    current_native, current_edge = bridge.native(), bridge.edge()
    if (not bridge.owns_native_hold(current_native, pending['token'])
            or current_native.get('pid') != native.get('pid')
            or current_edge.get('phase') != 'held' or current_edge.get('streams')
            or current_edge.get('revision') != edge.get('revision')):
        raise UpgradeError('source-overlay-runtime-changed')
    result = dict(version=1, transactionId=transaction, sourcePlanSha256=sha(encoded(plan)),
        rendererSha256=sha(renderer), beforeSha256=sha(anchor), configSha256=config_sha,
        candidateSha256=sha(candidate), ownerSnapshots=[sha(raw) for raw in records],
        rootRecords={key: value[1] for key, value in root_records.items()})
    if provision:
        result['provisionSha256'] = sha(encoded(provision))
    return result


def relative(value):
    if (type(value) is not str or not value or len(value) > 4096
            or any(ord(c) < 32 for c in value) or "\\" in value
            or PurePosixPath(value).is_absolute()
            or any(c in ("", ".", "..") for c in value.split("/"))):
        raise UpgradeError("source-path-invalid")
    return value


def directory(path, uid, *, private=False, candidate=False):
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_uid != uid or (not candidate and info.st_mode & (0o077 if private else 0o022))):
        raise UpgradeError("source-directory-unsafe")


@contextlib.contextmanager
def parent_fd(root, name, uid, *, candidate=False):
    """All descendant traversal is descriptor-relative and never follows links."""
    parts = relative(name).split("/")
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
            info = os.fstat(fd)
            if info.st_uid != uid or (not candidate and info.st_mode & 0o022):
                raise UpgradeError("source-directory-unsafe")
        yield fd, parts[-1]
    finally:
        os.close(fd)


def read_file(root, name, uid, *, candidate=False):
    with parent_fd(root, name, uid, candidate=candidate) as (parent, leaf):
        try:
            fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        except FileNotFoundError:
            return None, None
        with os.fdopen(fd, "rb") as handle:
            before = os.fstat(handle.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or before.st_uid != uid or before.st_size > MAX_FILE
                    or (not candidate and before.st_mode & 0o022)):
                raise UpgradeError("source-file-unsafe")
            raw = handle.read(MAX_FILE + 1)
            after = os.fstat(handle.fileno())
            found = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
            def identity(s):
                return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            if len(raw) > MAX_FILE or identity(before) != identity(after) or identity(after) != identity(found):
                raise UpgradeError("source-file-changed")
    mode = (0o755 if before.st_mode & 0o111 else 0o644) if candidate else stat.S_IMODE(before.st_mode)
    if candidate and name in EXECUTION_CONTROLS:
        mode = 0o755  # Exact existing Phase06 execution-control normalization.
    return dict(sha256=sha(raw), mode=mode), raw


def inventory(root, uid, *, candidate=False):
    directory(root, uid, candidate=candidate)
    files = {}
    total = 0
    for top in ROOTS:
        base = root / top
        if not base.exists() and not base.is_symlink():
            raise UpgradeError("source-tree-missing")
        for current, folders, names in os.walk(base, followlinks=False):
            current = Path(current)
            directory(current, uid, candidate=candidate)
            folders[:] = sorted(name for name in folders if name not in IGNORED)
            for name in folders:
                directory(current / name, uid, candidate=candidate)
            for name in sorted(names):
                if name.endswith((".pyc", ".log")):
                    continue
                rel = (current / name).relative_to(root).as_posix()
                item, raw = read_file(root, rel, uid, candidate=candidate)
                if item is None:
                    raise UpgradeError("source-file-changed")
                files[rel] = item
                total += len(raw)
                if len(files) > MAX_FILES or total > MAX_BYTES:
                    raise UpgradeError("source-inventory-limit")
    return files


def locked(function):
    @functools.wraps(function)
    def call(self, *args, **kwargs):
        directory(self.state, self.state_uid, private=True)
        fd = os.open(self.state / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != self.state_uid or info.st_mode & 0o077):
                raise UpgradeError("source-lock-unsafe")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise UpgradeError("source-upgrade-busy") from None
            return function(self, *args, **kwargs)
        finally:
            os.close(fd)
    return call


class SourceUpgrade:
    """A single update, bound to one installer and one exact model hold.

    state_uid is explicit for isolated unprivileged tests. Production callers
    use root-owned state and a non-root installation owner. The callbacks must
    check the actual coordinator, never a stale verified.json or a caller bool.
    """

    def __init__(self, state, install, owner_uid, *, state_uid=0):
        self.state = Path(state)
        self.install = Path(install)
        self.uid = owner_uid
        self.state_uid = state_uid
        directory(self.state, state_uid, private=True)
        directory(self.install, owner_uid)
        if self.state.resolve() != self.state or self.install.resolve() != self.install:
            raise UpgradeError("source-root-unsafe")

    def _write(self, name, raw):
        relative(name)
        fd, tmp = tempfile.mkstemp(prefix=".source-", dir=self.state)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.state / name)
            self._sync(self.state)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    @staticmethod
    def _sync(path):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _blob(self, digest):
        if type(digest) is not str or not HEX.fullmatch(digest):
            raise UpgradeError("source-snapshot-invalid")
        item, raw = read_file(self.state, digest, self.state_uid)
        if item is None or item["sha256"] != digest:
            raise UpgradeError("source-snapshot-changed")
        return raw

    def journal(self):
        item, raw = read_file(self.state, "source-upgrade.json", self.state_uid)
        if item is None:
            return None
        value = json.loads(raw)
        if (type(value) is not dict or set(value) != {"version", "install", "uid", "identity", "before", "after", "candidate", "hold", "phase", "outcome"}
                or value["version"] != 1 or value["install"] != str(self.install) or value["uid"] != self.uid
                or value["phase"] not in ("staged", "held", "applying", "applied", "restoring", "restored", "complete")
                or value["outcome"] not in (None, "applied", "rolled-back")
                or type(value["identity"]) is not dict
                or set(value["identity"]) != {"beforeRef", "afterRef", "markerSha256", "configSha256", "receiptSha256"}
                or any(type(value["identity"][k]) is not str or not re.fullmatch(r"[a-f0-9]{40}", value["identity"][k]) for k in ("beforeRef", "afterRef"))
                or type(value["identity"].get("markerSha256")) is not str
                or not HEX.fullmatch(value["identity"]["markerSha256"])
                or type(value['identity']['configSha256']) is not str
                or not HEX.fullmatch(value['identity']['configSha256'])
                or value['identity']['receiptSha256'] is not None and (type(value['identity']['receiptSha256']) is not str
                    or not HEX.fullmatch(value['identity']['receiptSha256']))
                or value["hold"] is not None and (type(value["hold"]) is not str or not HEX.fullmatch(value["hold"]))
                or any(type(value[k]) is not dict or len(value[k]) > MAX_FILES for k in ("before", "after", "candidate"))):
            raise UpgradeError("source-journal-invalid")
        for side in ("before", "after", "candidate"):
            for path, item in value[side].items():
                relative(path)
                if path.split("/")[0] not in ROOTS:
                    raise UpgradeError("source-journal-invalid")
                if (type(item) is not dict or set(item) != {"sha256", "mode"}
                        or type(item["sha256"]) is not str or not HEX.fullmatch(item["sha256"])
                        or type(item["mode"]) is not int or item["mode"] & ~0o755):
                    raise UpgradeError("source-journal-invalid")
        if value["after"] != installed_projection(value['before'], value['candidate']):
            raise UpgradeError("source-journal-invalid")
        return value

    def _save(self, value):
        self._write("source-upgrade.json", encoded(value))

    @contextlib.contextmanager
    def _scratch(self, *, prepare=False):
        """Exact root-private rename buffer, outside the inventoried source.

        The owner can rename the installation's children, but cannot populate
        this private directory. Its durable receipt precedes mkdir; the sole
        recoverable unbound state is an empty root-owned 0700 directory.
        """
        root = os.open(self.install, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        scratch = None
        try:
            root_info = os.fstat(root)
            item, raw = read_file(self.state, 'source-scratch.json', self.state_uid)
            if item is None:
                if not prepare:
                    raise UpgradeError('source-scratch-missing')
                record = dict(version=1, name='.ods-source-staging-' + os.urandom(16).hex(),
                              parent=[root_info.st_dev, root_info.st_ino], identity=None)
                self._write('source-scratch.json', encoded(record))
            else:
                record = json.loads(raw)
            if (type(record) is not dict or set(record) != {'version', 'name', 'parent', 'identity'}
                    or record['version'] != 1 or type(record['name']) is not str
                    or not re.fullmatch(r'\.ods-source-staging-[a-f0-9]{32}', record['name'])
                    or record['parent'] != [root_info.st_dev, root_info.st_ino]
                    or record['identity'] is not None and (type(record['identity']) is not list
                        or len(record['identity']) != 2
                        or any(type(n) is not int or n < 0 for n in record['identity']))):
                raise UpgradeError('source-scratch-invalid')
            if record['identity'] is None:
                if not prepare:
                    raise UpgradeError('source-scratch-unprepared')
                try:
                    os.mkdir(record['name'], mode=0o700, dir_fd=root)
                    os.fsync(root)
                except FileExistsError:
                    pass
            scratch = os.open(record['name'], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
            info = os.fstat(scratch)
            if (info.st_uid != self.state_uid or stat.S_IMODE(info.st_mode) != 0o700
                    or info.st_dev != root_info.st_dev):
                raise UpgradeError('source-scratch-unsafe')
            if record['identity'] is None:
                if os.listdir(scratch):
                    raise UpgradeError('source-scratch-unbound-content')
                record['identity'] = [info.st_dev, info.st_ino]
                self._write('source-scratch.json', encoded(record))
            def validate():
                parent = self.install.lstat()
                linked = os.stat(record['name'], dir_fd=root, follow_symlinks=False)
                current = os.fstat(scratch)
                if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != self.uid or parent.st_mode & 0o022
                        or [parent.st_dev, parent.st_ino] != record['parent']
                        or not stat.S_ISDIR(linked.st_mode)
                        or [linked.st_dev, linked.st_ino] != record['identity']
                        or [current.st_dev, current.st_ino] != record['identity']
                        or linked.st_uid != self.state_uid or stat.S_IMODE(linked.st_mode) != 0o700):
                    raise UpgradeError('source-scratch-changed')
            validate()
            yield scratch, validate
        finally:
            if scratch is not None:
                os.close(scratch)
            os.close(root)

    def _clear_scratch(self, scratch, validate):
        validate()
        names = os.listdir(scratch)
        if set(names) - {'payload'}:
            raise UpgradeError('source-scratch-unexpected')
        if names:
            fd = os.open('payload', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=scratch)
            try:
                info = os.fstat(fd)
                linked = os.stat('payload', dir_fd=scratch, follow_symlinks=False)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_FILE
                        or info.st_uid not in (self.state_uid, self.uid)
                        or stat.S_IMODE(info.st_mode) not in (0o600, 0o644, 0o755)
                        or (info.st_dev, info.st_ino) != (linked.st_dev, linked.st_ino)):
                    raise UpgradeError('source-scratch-payload-unsafe')
                validate()
                os.unlink('payload', dir_fd=scratch)
                os.fsync(scratch)
            finally:
                os.close(fd)

    def _prepare_scratch(self):
        with self._scratch(prepare=True) as (scratch, validate):
            self._clear_scratch(scratch, validate)
            device = os.fstat(scratch).st_dev
            mount = mount_id(scratch)
            # All existing destination directories must support the same
            # filesystem rename BEFORE acquiring admission. New directories
            # inherit their nearest existing parent's device.
            for top in ROOTS:
                for current, folders, _ in os.walk(self.install / top, followlinks=False):
                    directory(Path(current), self.uid)
                    if Path(current).lstat().st_dev != device:
                        raise UpgradeError('source-cross-filesystem-unsupported')
                    with parent_fd(self.install, (Path(current).relative_to(self.install) / '.probe').as_posix(), self.uid) as (fd, _):
                        if mount_id(fd) != mount:
                            raise UpgradeError('source-cross-filesystem-unsupported')
                    folders[:] = [name for name in folders if name not in IGNORED]
            validate()

    def _mirror_name(self):
        value = self.journal()
        if value is None:
            raise UpgradeError('source-stage-required')
        return 'mirror-' + sha(encoded([value['identity'], value['candidate']])) + '.json'

    def downstream_name(self):
        return self._mirror_name().replace('mirror-', 'downstream-', 1)

    @locked
    def record_mirror_write(self, path, raw, mode, uid, gid):
        """Record exact bootstrap/coordinator bytes before the existing writer.

        This is invoked only inside the installer's root transition lock, not
        exposed through the owner socket. Non-coordinator downstream changes
        use resume-only recovery and never borrow this rollback authorization.
        """
        path = Path(path)
        if (str(path) not in MIRROR_LEAVES and MIRROR not in path.parents
                or path.resolve() != path or type(raw) is not bytes or len(raw) > MAX_FILE
                or mode not in (0o600, 0o644) or uid not in (0, self.uid)
                or type(gid) is not int or gid < 0):
            raise UpgradeError('source-mirror-target-invalid')
        value = self.journal()
        if value is None or value['phase'] == 'complete':
            raise UpgradeError('source-mirror-stage-required')
        if MIRROR in path.parents:
            rel = path.relative_to(MIRROR).as_posix()
            expected = [value['candidate'].get(prefix + rel, {}).get('sha256')
                        for prefix in ('bin/', 'extensions/services/pixel-agent/host/')]
            if sha(raw) not in expected:
                raise UpgradeError('source-mirror-candidate-mismatch')
        elif str(path) == '/etc/systemd/system/ods-pixel-access.service':
            if sha(raw) != value['candidate'].get('extensions/services/pixel-agent/host/ods-pixel-access.service', {}).get('sha256'):
                raise UpgradeError('source-mirror-candidate-mismatch')
        item, record_raw = read_file(self.state, self._mirror_name(), self.state_uid)
        record = self._mirror_record(require_complete=False) if item else {'before': {}, 'after': {}}
        current, current_raw = absolute_file(path)
        wanted = dict(sha256=sha(raw), mode=mode, uid=uid, gid=gid)
        key = str(path)
        if key in record['after']:
            if record['after'][key] != wanted or current not in (record['before'][key], wanted):
                raise UpgradeError('source-mirror-changed')
        else:
            record['before'][key] = current
            record['after'][key] = wanted
            if current is not None:
                self._write(current['sha256'], current_raw)
        self._write(wanted['sha256'], raw)
        self._write(self._mirror_name(), encoded(record))

    def _mirror_record(self, *, require_complete=True):
        item, raw = read_file(self.state, self._mirror_name(), self.state_uid)
        if item is None:
            raise UpgradeError('source-mirror-journal-missing')
        record = json.loads(raw)
        if (type(record) is not dict or set(record) != {'before', 'after'}
                or type(record['before']) is not dict or type(record['after']) is not dict
                or record['before'].keys() != record['after'].keys()
                or len(record['after']) > MAX_FILES
                or (require_complete and not {str(MIRROR / name) for name in ('pixel_access_bridge.py', 'pixel_source_upgrade.py', 'access_mode_server.py')} <= record['after'].keys())):
            raise UpgradeError('source-mirror-journal-invalid')
        for side in ('before', 'after'):
            for key, value in record[side].items():
                path = Path(key)
                if (not path.is_absolute() or str(path) != key or path.resolve() != path
                        or str(path) not in MIRROR_LEAVES and MIRROR not in path.parents):
                    raise UpgradeError('source-mirror-journal-invalid')
                if value is None and side == 'before':
                    continue
                if (type(value) is not dict or set(value) != {'sha256', 'mode', 'uid', 'gid'}
                        or type(value['sha256']) is not str or not HEX.fullmatch(value['sha256'])
                        or type(value['mode']) is not int or value['mode'] & ~0o755
                        or type(value['uid']) is not int or value['uid'] not in (SYSTEM_UID, self.uid)
                        or type(value['gid']) is not int or value['gid'] < 0):
                    raise UpgradeError('source-mirror-journal-invalid')
        return record

    def verify_mirror(self, *, rollback=False):
        record = self._mirror_record()
        expected = record['before' if rollback else 'after']
        for key, value in expected.items():
            if absolute_file(Path(key))[0] != value:
                raise UpgradeError('source-mirror-changed')

    def uninstall_inventory(self):
        """Validate retained update evidence before the real uninstall removes it."""
        value = self.journal()
        if value is None or value['phase'] != 'complete' or os.path.lexists(self.state.parent / 'transition.json'):
            raise UpgradeError('source-completion-required')
        # Completed updates do not freeze owner-managed extensions forever.
        # The uninstall validates its protected mirror and install binding;
        # it must not require a mutable source tree to remain a time capsule.
        self.verify_mirror()
        with self._scratch() as (scratch, validate):
            validate()
            if os.listdir(scratch):
                raise UpgradeError('source-scratch-unexpected')
        total = 0
        for index, path in enumerate(self.state.iterdir()):
            if index >= MAX_FILES * 3:
                raise UpgradeError('source-inventory-limit')
            name = path.name
            if not (HEX.fullmatch(name) or re.fullmatch(r'(?:mirror|downstream)-[a-f0-9]{64}\.json', name)
                    or re.fullmatch(r'\.source-[a-z0-9_]{8}', name)
                    or name in ('lock', 'source-upgrade.json', 'previous-completion.json', 'source-scratch.json')):
                raise UpgradeError('source-state-unexpected')
            item, raw = read_file(self.state, name, self.state_uid)
            if item is None or item['mode'] != 0o600:
                raise UpgradeError('source-state-unsafe')
            total += len(raw)
            if total > 4 * MAX_BYTES or (HEX.fullmatch(name) and sha(raw) != name):
                raise UpgradeError('source-snapshot-changed')
            if name.endswith('.json') and type(json.loads(raw)) is not dict:
                raise UpgradeError('source-journal-invalid')
        return self._mirror_record()['after']

    @locked
    def stage(self, source, source_uid, identity, *, retire_complete=None, rebase_unheld=False):
        source = Path(source)
        if source.resolve() == self.install:
            raise UpgradeError('source-separate-candidate-required')
        if (type(identity) is not dict
                or set(identity) != {'beforeRef', 'afterRef', 'markerSha256', 'configSha256', 'receiptSha256'}
                or any(type(identity[key]) is not str or not re.fullmatch(r'[a-f0-9]{40}', identity[key])
                       for key in ('beforeRef', 'afterRef'))
                or any(type(identity[key]) is not str or not HEX.fullmatch(identity[key])
                       for key in ('markerSha256', 'configSha256'))
                or identity['receiptSha256'] is not None and (type(identity['receiptSha256']) is not str
                    or not HEX.fullmatch(identity['receiptSha256']))):
            raise UpgradeError('source-identity-invalid')
        candidate = inventory(source, source_uid, candidate=True)
        existing = self.journal()
        preserved_mirror = None
        if existing is not None:
            same = existing['identity'] == identity and existing['candidate'] == candidate
            if existing['phase'] == 'complete' and callable(retire_complete):
                retire_complete(existing)
                self.verify_mirror()
                # A released upgrade no longer owns the mutable installation:
                # supported extension changes become this new plan's baseline.
                # Only active/replayed plans require their original exact tree.
                # One compact prior receipt survives journal replacement. Blobs
                # remain content-addressed, never adopted as active authority.
                self._write('previous-completion.json', encoded(existing))
            elif not same and rebase_unheld:
                baseline_keys = {'configSha256', 'receiptSha256'}
                if (existing['phase'] != 'staged' or existing['hold'] is not None
                        or candidate != existing['candidate']
                        or {k: v for k, v in identity.items() if k not in baseline_keys}
                        != {k: v for k, v in existing['identity'].items() if k not in baseline_keys}
                        or inventory(self.install, self.uid) != existing['before']):
                    raise UpgradeError('source-unheld-rebase-refused')
                item, raw = read_file(self.state, self._mirror_name(), self.state_uid)
                if item is not None:
                    record = self._mirror_record(require_complete=False)
                    for key in record['after']:
                        if absolute_file(Path(key))[0] not in (record['before'][key], record['after'][key]):
                            raise UpgradeError('source-mirror-changed')
                    preserved_mirror = raw
            elif not same:
                raise UpgradeError("source-candidate-changed")
            else:
                if existing['phase'] == 'staged' and existing['hold'] is None:
                    self._prepare_scratch()
                return existing
        before = inventory(self.install, self.uid)
        # Match source-copy's existing non-deleting semantics: an upgrade is
        # not authorization to remove owner-added extensions or old helpers.
        after = installed_projection(before, candidate)
        value = dict(version=1, install=str(self.install), uid=self.uid,
                     identity=identity, before=before, after=after, candidate=candidate,
                     hold=None, phase="staged", outcome=None)
        # Capture both complete inventories before publishing intent. A crash
        # leaves unused hash-addressed blobs, never an authorized partial plan.
        for root, uid, listing, is_candidate in ((self.install, self.uid, before, False), (source, source_uid, candidate, True)):
            for path, expected in listing.items():
                actual, raw = read_file(root, path, uid, candidate=is_candidate)
                if actual != expected:
                    raise UpgradeError("source-file-changed")
                self._write(expected["sha256"], raw)
        if before != inventory(self.install, self.uid) or candidate != inventory(source, source_uid, candidate=True):
            raise UpgradeError("source-file-changed")
        if preserved_mirror is not None:
            name = 'mirror-' + sha(encoded([identity, candidate])) + '.json'
            self._write(name, preserved_mirror)
        self._save(value)
        self.journal()  # validate the complete serialized contract before use
        self._prepare_scratch()
        return value

    @locked
    def bind(self, transaction, verify_hold):
        if type(transaction) is not str or not HEX.fullmatch(transaction):
            raise UpgradeError("source-hold-invalid")
        value = self.journal()
        if value is None or value["hold"] not in (None, transaction):
            raise UpgradeError("source-hold-mismatch")
        if value["phase"] == "staged":
            self._prepare_scratch()
            if inventory(self.install, self.uid) != value["before"]:
                raise UpgradeError("source-before-changed")
            verify_hold(transaction)
            value.update(hold=transaction, phase="held")
            self._save(value)
        else:
            if value['hold'] != transaction:
                raise UpgradeError('source-hold-mismatch')
            verify_hold(transaction)
        return value

    def _replace(self, path, target, permitted, verify_hold, transaction):
        # Creation is confined to the bound installation, no-follow at each
        # component. Empty newly created directories are harmless on rollback.
        verify_hold(transaction)
        fd = os.open(self.install, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for component in relative(path).split("/")[:-1]:
                created = False
                try:
                    os.mkdir(component, mode=0o755, dir_fd=fd)
                    created = True
                except FileExistsError:
                    pass
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
                if created:
                    os.fchown(fd, self.uid, -1)
                    os.fsync(fd)
                info = os.fstat(fd)
                if info.st_uid != self.uid or info.st_mode & 0o022:
                    raise UpgradeError("source-directory-unsafe")
        finally:
            os.close(fd)
        actual, _ = read_file(self.install, path, self.uid)
        if actual not in permitted:
            raise UpgradeError("source-live-drift")
        if actual == target:
            return
        verify_hold(transaction)
        with parent_fd(self.install, path, self.uid) as (fd, leaf):
            if target is None:
                if read_file(self.install, path, self.uid)[0] not in permitted:
                    raise UpgradeError("source-live-drift")
                os.unlink(leaf, dir_fd=fd)
            else:
                raw = self._blob(target["sha256"])
                with self._scratch() as (scratch, validate):
                    self._clear_scratch(scratch, validate)
                    if (os.fstat(fd).st_dev != os.fstat(scratch).st_dev
                            or mount_id(fd) != mount_id(scratch)):
                        raise UpgradeError('source-cross-filesystem-unsupported')
                    output = os.open('payload', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                     0o600, dir_fd=scratch)
                    with os.fdopen(output, "wb") as handle:
                        handle.write(raw)
                        handle.flush()
                        os.fchown(handle.fileno(), self.uid, -1)
                        os.fchmod(handle.fileno(), target["mode"])
                        os.fsync(handle.fileno())
                    # Recheck immediately before publication; concurrent owner
                    # edits never become permission to overwrite unknown bytes.
                    if read_file(self.install, path, self.uid)[0] not in permitted:
                        raise UpgradeError("source-live-drift")
                    validate()
                    with parent_fd(self.install, path, self.uid) as (current_fd, _):
                        if os.fstat(current_fd) != os.fstat(fd):
                            raise UpgradeError('source-directory-changed')
                    os.replace('payload', leaf, src_dir_fd=scratch, dst_dir_fd=fd)
                    os.fsync(scratch)
            os.fsync(fd)

    @locked
    def publish(self, verify_hold, *, rollback=False, checkpoint=lambda _: None):
        value = self.journal()
        if value is None or not value["hold"] or value["phase"] in ("staged", "complete"):
            raise UpgradeError("source-not-held")
        verify_hold(value["hold"])
        with self._scratch() as (scratch, validate):
            self._clear_scratch(scratch, validate)
        target_side = "before" if rollback else "after"
        if not rollback and value["phase"] in ("restoring", "restored"):
            raise UpgradeError("source-rollback-in-progress")
        actual = inventory(self.install, self.uid)
        if any(path not in value["before"] and path not in value["after"]
               or item not in (value["before"].get(path), value["after"].get(path))
               for path, item in actual.items()):
            raise UpgradeError("source-live-drift")
        # Missing previously present paths are only valid if the plan removes
        # them; interruption is a mix of the two exact inventories, not absence.
        if any(path not in actual for path in value["before"].keys() & value["after"].keys()):
            raise UpgradeError("source-live-drift")
        value["phase"] = "restoring" if rollback else "applying"
        self._save(value)
        checkpoint("intent")
        for path in sorted(value["before"].keys() | value["after"].keys()):
            before, after = value["before"].get(path), value["after"].get(path)
            self._replace(path, value[target_side].get(path), (before, after), verify_hold, value["hold"])
            checkpoint(path)
        if inventory(self.install, self.uid) != value[target_side]:
            raise UpgradeError("source-live-drift")
        verify_hold(value["hold"])
        value["phase"] = "restored" if rollback else "applied"
        self._save(value)
        checkpoint(value["phase"])

    @locked
    def finish(self, verify_runtime):
        value = self.journal()
        if value is None or value["phase"] not in ("applied", "restored", "complete"):
            raise UpgradeError("source-outcome-unconfirmed")
        side = "before" if value["phase"] == "restored" or value["outcome"] == "rolled-back" else "after"
        if inventory(self.install, self.uid) != value[side]:
            raise UpgradeError("source-live-drift")
        # This callback must verify the actual new/restored process, config,
        # receipt and release under the existing hold. No cached attestations.
        verify_runtime(value["hold"], "rolled-back" if side == "before" else "applied")
        if inventory(self.install, self.uid) != value[side]:
            raise UpgradeError("source-live-drift")
        value["phase"] = "complete"
        value["outcome"] = "rolled-back" if side == "before" else "applied"
        self._save(value)


def absolute_file(path):
    if path != SYSTEM_ROOT and SYSTEM_ROOT not in path.parents:
        raise UpgradeError('source-mirror-parent-unsafe')
    for parent in path.parents:
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != SYSTEM_UID or info.st_mode & 0o022:
            raise UpgradeError('source-mirror-parent-unsafe')
        if parent == SYSTEM_ROOT:
            break
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None, None
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_mode & 0o022 or info.st_size > MAX_FILE):
            raise UpgradeError('source-mirror-file-unsafe')
        raw = handle.read(MAX_FILE + 1)
        observed = path.lstat()
        if (len(raw) > MAX_FILE or (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size)
                != (observed.st_dev, observed.st_ino, observed.st_mtime_ns, observed.st_size)):
            raise UpgradeError('source-mirror-file-changed')
    return dict(sha256=sha(raw), mode=stat.S_IMODE(info.st_mode), uid=info.st_uid, gid=info.st_gid), raw


def _protected_json(path, uid=0):
    path = Path(path)
    for parent in path.parents:
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid not in (0, uid) or info.st_mode & 0o022:
            raise UpgradeError("source-parent-unsafe")
    item, raw = read_file(path.parent, path.name, uid)
    if item is None or len(raw) > 65536 or item["mode"] & 0o077:
        raise UpgradeError("source-state-unsafe")
    value = json.loads(raw)
    if type(value) is not dict:
        raise UpgradeError('source-state-invalid')
    return value, sha(raw)


def _client():
    program = Path("/usr/local/libexec/ods-pixel-access")
    for path in (program, *program.parents, program / "pixel_model_transition.py", program / "pixel_access_client.py"):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise UpgradeError("source-controller-unsafe")
    sys.path.insert(0, str(program))
    from pixel_model_transition import execute
    return execute


def owner_baseline(home, uid):
    """Exact identity only: no receipt is restored or treated as runtime proof."""
    home = Path(home)
    _, config_hash = _protected_json(home / '.openclaw/openclaw.json', uid)
    receipt = home / '.local/state/ods-pixel-access-mode/pixel-access-mode.json'
    receipt_hash = None
    if os.path.lexists(receipt):
        _, receipt_hash = _protected_json(receipt, uid)
    return dict(configSha256=config_hash, receiptSha256=receipt_hash)


def begin_plan(state, install, owner, *, installer=False):
    """Called while the protected coordinator holds its admission lock."""
    path = Path(state) / 'source-upgrade'
    if not os.path.lexists(path):
        if installer:
            raise UpgradeError('source-stage-required')
        return None
    manager = SourceUpgrade(path, install, owner.pw_uid)
    value = manager.journal()
    if value is None:
        raise UpgradeError('source-stage-required')
    if value['phase'] == 'complete':
        if installer:
            raise UpgradeError('source-already-complete')
        return None
    if not installer:
        raise UpgradeError('source-installer-hold-required')
    current = owner_baseline(owner.pw_dir, owner.pw_uid)
    if current != {key: value['identity'][key] for key in current}:
        raise UpgradeError('source-owner-state-changed')
    _, marker_hash = _protected_json(Path(owner.pw_dir) / '.config/ods/pixel-managed.json', owner.pw_uid)
    if marker_hash != value['identity']['markerSha256']:
        raise UpgradeError('source-owner-state-changed')
    return manager


def _pending(state, transaction):
    pending, _ = _protected_json(state / "transition.json")
    if (pending.get("kind") != "model" or pending.get("transaction_id") != transaction
            or pending.get("phase") != "held" or pending.get("configured_mode") not in ("full-access", "sandboxed")
            or type(pending.get("token")) is not str or not HEX.fullmatch(pending["token"])):
        raise UpgradeError("source-hold-unconfirmed")
    return pending


def _manager(install, owner, *, create=False):
    import pwd
    if sys.platform != "linux" or os.geteuid() != 0:
        raise UpgradeError("source-upgrade-root-linux-only")
    account = pwd.getpwnam(owner)
    install = Path(install)
    if account.pw_uid == 0 or not install.is_absolute() or install.resolve() != install:
        raise UpgradeError("source-owner-invalid")
    state = Path("/var/lib/ods-pixel-access")
    directory(state, 0, private=True)
    settings, _ = _protected_json("/etc/ods/pixel-access.json")
    if settings.get("install_dir") != str(install) or settings.get("owner") != owner:
        raise UpgradeError("source-controller-install-mismatch")
    child = state / "source-upgrade"
    if not os.path.lexists(child):
        if not create:
            return None, account, state
        child.mkdir(mode=0o700)
    return SourceUpgrade(child, install, account.pw_uid), account, state


def _stage(manager, account, source, requested_ref):
    # Caller holds the same root admission lock used by all coordinator writes.
    existing = manager.journal()
    # Feature selection runs before Phase06 and always enables these for Pixel.
    # Refuse an inconsistent source instead of relying on a later mutable
    # LiteLLM rename that would invalidate the exact source inventory.
    for name in ('pixel-edge', 'litellm'):
        item, _ = read_file(Path(source), f'extensions/services/{name}/compose.yaml', account.pw_uid, candidate=True)
        if item is None:
            raise UpgradeError('source-pixel-services-required')
    if os.path.lexists(manager.state.parent / 'transition.json'):
        pending, _ = _protected_json(manager.state.parent / 'transition.json')
        if (existing is None or existing['hold'] is None
                or pending.get('kind') != 'model' or pending.get('transaction_id') != existing['hold']):
            raise UpgradeError('source-previous-hold-pending')
    marker, marker_sha = _protected_json(Path(account.pw_dir) / ".config/ods/pixel-managed.json", account.pw_uid)
    if (marker.get("schema_version") != 2 or marker.get("manager") != "ods"
            or marker.get("install_dir") != str(manager.install) or marker.get("initial_active_state") != "absent"
            or not re.fullmatch(r"[a-f0-9]{40}", requested_ref)):
        raise UpgradeError("source-managed-identity-invalid")
    fresh = existing is None or (existing['phase'] == 'complete'
                               and not os.path.lexists(manager.state.parent / 'transition.json'))
    rebase_unheld = False
    if fresh:
        if marker.get("state") != "ready" or not re.fullmatch(r"[a-f0-9]{40}", marker.get("pixel_source_ref", "")):
            raise UpgradeError("source-ready-baseline-required")
        identity = dict(beforeRef=marker["pixel_source_ref"], afterRef=requested_ref, markerSha256=marker_sha,
                        **owner_baseline(account.pw_dir, account.pw_uid))
        if identity["beforeRef"] != requested_ref:
            _, version_bytes = read_file(Path(source), 'vendor/pixel/VERSION', account.pw_uid, candidate=True)
            if version_bytes is None or len(version_bytes) > 128:
                raise UpgradeError('source-new-version-required')
            version_raw = version_bytes.decode('ascii').strip()
            old_version = marker.get("active_release_version", "")
            if (not re.fullmatch(r"\d+\.\d+\.\d+", version_raw)
                    or not re.fullmatch(r"\d+\.\d+\.\d+", old_version)
                    or tuple(map(int, version_raw.split("."))) <= tuple(map(int, old_version.split(".")))):
                raise UpgradeError("source-new-version-required")
    else:
        identity = existing["identity"]
        if (identity["afterRef"] != requested_ref or marker.get("state") not in ("ready", "installing")
                or marker.get("pixel_source_ref") not in (identity["beforeRef"], identity["afterRef"])
                or marker.get("requested_source_ref") not in (None, identity["afterRef"])):
            raise UpgradeError("source-resume-identity-changed")
        if existing['phase'] == 'staged' and existing['hold'] is None:
            current = owner_baseline(account.pw_dir, account.pw_uid)
            if marker_sha != identity['markerSha256']:
                raise UpgradeError('source-owner-state-changed')
            if current != {key: identity[key] for key in current}:
                # The owner may revoke Full Access before acquisition. No hold
                # or source copy exists yet; preserve that CURRENT preference,
                # never restore the old receipt. Later held plans stay strict.
                identity = {**identity, **current}
                rebase_unheld = True
    def retire(_completed):
        if os.path.lexists(manager.state.parent / 'transition.json'):
            raise UpgradeError('source-previous-hold-pending')
    manager.stage(Path(source), account.pw_uid, identity, retire_complete=retire, rebase_unheld=rebase_unheld)


@contextlib.contextmanager
def admission_lock(state):
    directory(state, 0, private=True)
    fd = os.open(state / 'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                or info.st_mode & 0o077):
            raise UpgradeError('source-admission-lock-unsafe')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise UpgradeError('source-admission-busy') from None
        yield
    finally:
        os.close(fd)


def main(argv):
    """Installer-only local entry point; deliberately no HTTP or tool surface."""
    if len(argv) < 3 or argv[0] not in ("stage", "hold", "copy", "status", "finish", "rollback", "downstream"):
        raise UpgradeError("source-command-invalid")
    action, install, owner, *rest = argv
    if len(rest) != (2 if action == "stage" else 0):
        raise UpgradeError("source-command-invalid")
    manager, account, state = _manager(install, owner, create=action == 'stage')
    if manager is None:
        if action == 'status':
            print(json.dumps({'pending': False}))
            return
        raise UpgradeError('source-stage-required')
    if action == "stage":
        with admission_lock(state):
            _stage(manager, account, *rest)
        return
    value = manager.journal()
    if value is None:
        if action == "status":
            print(json.dumps({"pending": False}))
            return
        raise UpgradeError("source-stage-required")
    client = _client()
    if action == "status":
        pending = client("status")
        if value["phase"] == "complete" and pending == {"pending": False}:
            print(json.dumps({"pending": False}))
        else:
            print(json.dumps(dict(pending=True, transaction=value["hold"], phase=value["phase"],
                                  mode=pending.get("configured_mode"))))
        return
    if action == "hold":
        transaction = value["hold"]
        # source-begin reserves this exact token under the root admission lock
        # before any gate call. A lost reply can never orphan an unrelated hold.
        status = client('status')
        if (transaction is None or status == {'pending': False}
                or status.get('phase') in ('acquiring', 'draining')
                or status.get('phase') == 'error' and value['phase'] == 'held'):
            transaction = client("source-begin")
        manager.bind(transaction, lambda token: client("verify", token))
        print(transaction)
        return
    transaction = value["hold"]
    if not transaction:
        raise UpgradeError("source-hold-required")
    if action == "downstream":
        _pending(state, transaction)
        manager._write(manager.downstream_name(), encoded({"transaction": transaction}))
        return
    if action in ("copy", "rollback"):
        if action == "rollback" and os.path.lexists(manager.state / manager.downstream_name()):
            raise UpgradeError("source-downstream-resume-required")
        manager.publish(lambda token: _pending(state, token), rollback=action == "rollback")
        return
    if action == "finish":
        manager.verify_mirror()
        # The protected finish operation has its own restart/release replay.
        # Do not force a releasing transaction back through a held-only probe.
        if value['phase'] != 'complete':
            manager.finish(lambda token, _outcome: client("verify", token))
        value = manager.journal()
        client("finish", transaction, value["outcome"])


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except (UpgradeError, OSError, ValueError, RuntimeError):
        # Paths, config values and snapshot payloads are never diagnostic text.
        print("Pixel source upgrade is incomplete; keep admission held and resume the same reviewed installer.", file=sys.stderr)
        raise SystemExit(1) from None
