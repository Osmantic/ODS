"""Fixed native Source retirement inventories and preserved archive cursor.

Imported lazily by Darwin custody entry points. Nothing here grants Source
execution, adopts a filename prefix, reads an OAuth token, or deletes history.
An archive cursor moves only the exact Root-witness-bound Source family, by
rename, after fresh Core and Source process/definition absence proofs.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
from types import SimpleNamespace
import uuid

from . import custody as c

HISTORY_KIND = 'ods-native-source-retirement-history'
CODE_ONLY_KIND = 'ods-native-source-retirement-code-only'
ARCHIVE_KIND = 'ods-native-source-retirement-archive'
ARCHIVE_ROOT = Path('/private/var/lib/ods-pixel-retired')
SETTINGS = Path('/private/etc/ods/pixel-access.json')
DAEMONS = Path('/Library/LaunchDaemons')
LAUNCHER = Path('/usr/local/libexec/ods-pixel-access/openclaw-gateway-launcher')
CURSOR = 'source-retirement-cursor.json'
CORE_PLAN = 'source-core-plan.json'
CODE_ONLY_LEDGER = 'source-installer-code-only.json'
CORE_ROLES = ('native-gateway', 'access', 'access-relay', 'native-manager', 'native-promoter', 'native-operations')
MAX_HISTORY_ENTRIES = 1024
MAX_HISTORY_BYTES = 32 * 1024 * 1024
_SHA = re.compile('[a-f0-9]{64}')
_NONCE = re.compile('[a-f0-9]{32}')
_SOURCE_PREFIXES = ('source-', '.source-', 'native-source-', '.native-source-')
_CURRENT = frozenset(('source-identity.json', 'source-custody-prepare.json', 'source-environment.json',
    'source-logs-prepare.json', 'source-publication.json', 'source-jobs-prepare.json', 'source-jobs-active.json',
    'source-execution.json', 'source-environment-generation.json', 'source-installer-rebind.json',
    'source-boot-quiesce.json', 'source-runtime-installation.json', 'source-runtime-publication.json',
    'source-sidefiles.json', 'source-initial-generation.json', 'source-initial-boot.json',
    'source-installer-code-only.json', 'source-installer-history.json',
    'native-source-jobs', 'native-app-enrollment.json'))


def _fail(code):
    raise c.CustodyError('native-source-retirement-' + code)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _runtime(value):
    return (type(value) is dict and set(value) == {'deploymentSha256', 'custodySha256', 'bundleSha256', 'sourceRef'}
        and all(type(value[key]) is str and _SHA.fullmatch(value[key])
            for key in ('deploymentSha256', 'custodySha256', 'bundleSha256'))
        and type(value['sourceRef']) is str and re.fullmatch('[a-f0-9]{40}', value['sourceRef']) is not None)


def _image(value):
    return (type(value) is dict and set(value) == {'inode', 'sha256'} and c._native_inode(value['inode'])
        and type(value['sha256']) is str and _SHA.fullmatch(value['sha256']) is not None)


def _name(value):
    return (type(value) is str and 0 < len(value) <= 255 and value not in ('.', '..')
        and '/' not in value and not any(ord(char) < 32 or ord(char) == 127 for char in value))


def _node(value, *, remaining, owners=None, path=()):
    """Pure finite manifest validation; capture is restricted by actual ledgers."""
    remaining[0] -= 1
    if remaining[0] < 0 or type(value) is not dict:
        _fail('history-invalid')
    common = {'kind', 'inode', 'mode', 'gid'}
    owner = (owners or {}).get(path)
    if owner is not None:
        common.add('uid')
        if (type(value.get('uid')) is not int or value['uid'] != owner[0]
                or value.get('gid') != owner[1] or value.get('mode') != 0o600 or value.get('kind') != 'file'):
            _fail('history-invalid')
    if (not common <= set(value) or not c._native_inode(value['inode'])
            or type(value['mode']) is not int or value['mode'] not in (0o600, 0o640, 0o644, 0o700, 0o755)
            or type(value['gid']) is not int or value['gid'] < 0):
        _fail('history-invalid')
    if value['kind'] == 'file':
        if (set(value) != common | {'sha256', 'size'} or type(value['sha256']) is not str
                or not _SHA.fullmatch(value['sha256']) or type(value['size']) is not int
                or not 0 <= value['size'] <= MAX_HISTORY_BYTES):
            _fail('history-invalid')
    elif value['kind'] == 'directory':
        if set(value) != common | {'entries'} or type(value['entries']) is not dict:
            _fail('history-invalid')
        for name, child in value['entries'].items():
            if not _name(name): _fail('history-invalid')
            _node(child, remaining=remaining, owners=owners, path=(*path, name))
    else:
        _fail('history-invalid')
    return value


def validate_history(value, *, owner_uid, owner_gid, runtime):
    """Shape only. No path or physical archive authority is conferred."""
    if (type(value) is not dict or set(value) != {'schemaVersion', 'kind', 'metadata', 'kits'}
            or type(value['schemaVersion']) is not int or value['schemaVersion'] not in (1, 2) or value['kind'] != HISTORY_KIND
            or type(owner_uid) is not int or owner_uid <= 0 or type(owner_gid) is not int or owner_gid < 0
            or not _runtime(runtime) or type(value['metadata']) is not dict or type(value['kits']) is not dict
            or len(value['metadata']) + len(value['kits']) > MAX_HISTORY_ENTRIES):
        _fail('history-invalid')
    remaining = [MAX_HISTORY_ENTRIES]
    for family in ('metadata', 'kits'):
        for name, image in value[family].items():
            if not _name(name): _fail('history-invalid')
            owners = {}
            # Schema2 records the original owner on exactly these two frozen
            # leaves. This is shape validation only: the initial capture still
            # requires the complete Root ledger/capsule; later reads use that
            # exact Root-witness-bound image, never a discovered prefix.
            if (value['schemaVersion'] == 2 and family == 'metadata'
                    and re.fullmatch(re.escape(c._SIDEFILE_ARCHIVE_PREFIX) + '[a-f0-9]{64}', name)
                    and type(image) is dict and image.get('kind') == 'directory'
                    and type(image.get('entries')) is dict and set(image['entries']) == {
                        'manifest.json', 'context.json', 'journal.json', 'source-sidefiles.json',
                        'source-runtime-installation.json', 'gateway.env', 'onboarding.json'}):
                if (image.get('mode'), image.get('gid')) != (0o700, 0): _fail('history-invalid')
                owners = {(leaf,): (owner_uid, owner_gid) for leaf in ('gateway.env', 'onboarding.json')}
            _node(image, remaining=remaining, owners=owners)
    return value


def validate_code_only_inventory(value, *, owner_uid, owner_gid, runtime):
    fields = {'schemaVersion', 'kind', 'owner', 'runtime', 'boot', 'identity', 'metadata', 'kit', 'portalRecord', 'history'}
    if (type(value) is not dict or set(value) != fields or type(value['schemaVersion']) is not int
            or value['schemaVersion'] != 1 or value['kind'] != CODE_ONLY_KIND
            or value['owner'] != {'uid': owner_uid, 'gid': owner_gid} or not _runtime(runtime)
            or value['runtime'] != runtime or type(value['metadata']) is not dict
            or any(not _name(name) or not _image(image) for name, image in value['metadata'].items())
            or type(value['boot']) is not str or not re.fullmatch('[A-F0-9]{8}(?:-[A-F0-9]{4}){3}-[A-F0-9]{12}', value['boot'])
            or value['portalRecord'] is not None and not _image(value['portalRecord'])):
        _fail('code-only-inventory-invalid')
    identity = value['identity']
    if identity != {'kind': 'absent'}:
        if type(identity) is not dict or set(identity) != {'kind', 'record'} or identity['kind'] != 'retained':
            _fail('code-only-inventory-invalid')
        c._native_identity_intent(identity['record'])
    kit = value['kit']
    if kit is not None and (type(kit) is not dict or set(kit) != {'inode', 'files'}
            or not c._native_inode(kit['inode']) or type(kit['files']) is not dict
            or set(kit['files']) not in ({'source-kit.json', 'broker.py', 'source-broker.env'},
                {'source-kit.json', 'broker.py', 'source-broker.env', *c.NATIVE_PROFILES})
            or any(not _image(image) for image in kit['files'].values())):
        _fail('code-only-inventory-invalid')
    validate_history(value['history'], owner_uid=owner_uid, owner_gid=owner_gid, runtime=runtime)
    return value


def _capture_node(directory, name, *, remaining, depth=0, expected=None):
    """Read only a ledger-validated history subtree, never the live data gate."""
    remaining[0] -= 1
    if remaining[0] < 0 or depth > 16 or not _name(name): _fail('history-limit')
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        before = os.fstat(fd)
        c._native_no_acl(fd)
        inode = [before.st_dev, before.st_ino]
        uid = expected.get('uid', 0) if expected is not None else 0
        if type(uid) is not int or uid < 0 or before.st_uid != uid or c._native_at_inode(directory, name) != inode:
            _fail('history-custody-changed')
        value = dict(inode=inode, mode=stat.S_IMODE(before.st_mode), gid=before.st_gid)
        if uid:
            value['uid'] = uid
        if stat.S_ISDIR(before.st_mode):
            names = sorted(os.listdir(fd))
            value.update(kind='directory', entries={child: _capture_node(fd, child, remaining=remaining, depth=depth + 1,
                expected=expected.get('entries', {}).get(child) if expected is not None else None)
                for child in names})
            if sorted(os.listdir(fd)) != names: _fail('history-changed')
        elif stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= MAX_HISTORY_BYTES:
            remaining[1] -= before.st_size
            if remaining[1] < 0: _fail('history-limit')
            digest = hashlib.sha256()
            size = 0
            while True:
                block = os.read(fd, min(65536, before.st_size + 1 - size))
                if not block: break
                size += len(block)
                digest.update(block)
                if size > before.st_size: _fail('history-changed')
            if size != before.st_size: _fail('history-changed')
            value.update(kind='file', sha256=digest.hexdigest(), size=size)
        else:
            _fail('history-custody-changed')
        after = os.fstat(fd)
        if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or c._native_at_inode(directory, name) != inode):
            _fail('history-changed')
        return value
    finally:
        os.close(fd)


def code_only_inventory(selection):
    from pixel_macos_custody import protected_directory
    from .native_executor import _publisher
    doc = c._native_selected(selection, _publication=True)
    inspected = c.native_inspect_installer_absence(selection.bridge, current_digest=doc['bundleSha256'])
    kit, metadata, identity, witness = inspected['state']
    publisher = _publisher(selection)
    _module, _doc, _component, ref = publisher._material(selection)
    runtime = dict(deploymentSha256=selection.deployment.checksum, custodySha256=doc['runtimeCustodySha256'],
        bundleSha256=doc['bundleSha256'], sourceRef=ref)
    with protected_directory(c.NATIVE_CONNECTION_RECORD.parent) as records:
        try: raw, inode = c._native_root_file(records, c.NATIVE_CONNECTION_RECORD.name, mode=0o644)
        except FileNotFoundError: portal = None
        else:
            from . import state
            state.validate(json.loads(raw, object_pairs_hook=c._native_pairs))
            portal = dict(inode=inode, sha256=_sha(raw))
    value = dict(schemaVersion=1, kind=CODE_ONLY_KIND, owner=dict(uid=doc['ownerUid'], gid=doc['ownerGid']),
        runtime=runtime, boot=witness['boot'], identity=identity, metadata=metadata, kit=kit,
        portalRecord=portal, history=_capture_histories(selection, metadata, private=None))
    validate_code_only_inventory(value, owner_uid=doc['ownerUid'], owner_gid=doc['ownerGid'], runtime=runtime)
    # Repeat the entire read-only absence boundary; no kit/data is adopted.
    if c.native_inspect_installer_absence(selection.bridge, current_digest=doc['bundleSha256']) != inspected:
        _fail('code-only-inventory-changed')
    return value


def add_history(selection, private_inventory):
    value = dict(private_inventory)
    value['history'] = _capture_histories(selection, private_inventory['metadata'], private=private_inventory)
    return value


def verify_history(selection, inventory):
    """Recheck immutable witness-qualified history during withdrawal prefixes.

    The fixed initial request independently derives the full ledger graph and
    compares it to the Root witness. A later publisher step may legitimately
    have moved its *current* generation record; it cannot change any retained
    history image frozen by that request. This check grants no extra paths or
    adoption of newly appearing history. The publisher's fixed prefix guard
    remains responsible for its exact newly created withdrawal family.
    """
    from pixel_macos_custody import protected_directory
    from .native_executor import _publisher
    doc = c._native_selected(selection, _publication=True)
    publisher = _publisher(selection)
    _module, _doc, _component, ref = publisher._material(selection)
    runtime = dict(deploymentSha256=selection.deployment.checksum, custodySha256=doc['runtimeCustodySha256'],
        bundleSha256=doc['bundleSha256'], sourceRef=ref)
    history = validate_history(inventory['history'], owner_uid=doc['ownerUid'], owner_gid=doc['ownerGid'], runtime=runtime)
    remaining = [MAX_HISTORY_ENTRIES, MAX_HISTORY_BYTES]
    with protected_directory(c.NATIVE_META) as metadata, protected_directory(c.NATIVE_KIT.parent) as kits:
        for directory, family in ((metadata, 'metadata'), (kits, 'kits')):
            for name, expected in history[family].items():
                if _capture_node(directory, name, remaining=remaining, expected=expected) != expected:
                    _fail('history-changed')
    c._native_selected(selection, _publication=True)
    return history


def _capture_histories(selection, current_metadata, *, private):
    """Discover only exact names derived and qualified by protected ledgers.

    Directory enumeration is a refusal check, never prefix-based permission.
    Each historical item must be linked by a complete publication, generation,
    initial-generation, rebind or boot ledger before its image is captured.
    """
    from pixel_macos_custody import protected_directory
    from .native_executor import _publisher
    publisher = _publisher(selection)
    _module, doc, component, ref = publisher._material(selection)
    value = dict(schemaVersion=1, kind=HISTORY_KIND, metadata={}, kits={})
    remaining = [MAX_HISTORY_ENTRIES, MAX_HISTORY_BYTES]
    with protected_directory(c.NATIVE_META) as metadata, protected_directory(c.NATIVE_KIT.parent) as kits:
        original_names = set(os.listdir(metadata))
        original_kits = set(os.listdir(kits))
        expected_meta = set(current_metadata)
        expected_meta.update(name for name in _CURRENT if c._native_at_inode(metadata, name) is not None)
        known_kits = {c.NATIVE_KIT.name}
        visited_publications = set()
        kit_contents = {}

        def root_json(name):
            raw, inode = c._native_root_file(metadata, name, limit=524288)
            try: record = json.loads(raw, object_pairs_hook=c._native_pairs)
            except (TypeError, ValueError, UnicodeError, RecursionError): _fail('history-ledger-invalid')
            return record, raw, inode

        def file_image(name, *, expected=None, inode=None, mode=0o600, gid=0):
            if not _name(name): _fail('history-name-invalid')
            raw, actual = c._native_root_file(metadata, name, mode=mode, gid=gid, limit=MAX_HISTORY_BYTES)
            if expected is not None and raw != expected or inode is not None and actual != inode:
                _fail('history-image-changed')
            expected_meta.add(name)
            value['metadata'][name] = _capture_node(metadata, name, remaining=remaining)
            return raw

        def kit_image(name, expected):
            if not _name(name): _fail('history-name-invalid')
            _image, contents = publisher._kit_image(c, kits, name, expected=expected)
            kit_contents[_sha(contents['source-kit.json'])] = contents
            known_kits.add(name)
            value['kits'][name] = _capture_node(kits, name, remaining=remaining)
            return contents

        def publication(record, contents):
            key = _sha(c._native_encoded(record))
            if key in visited_publications: return
            visited_publications.add(key)
            wanted = record.get('new', {}).get('source-kit.json') if type(record) is dict else None
            if contents is None or _sha(contents['source-kit.json']) != wanted:
                contents = kit_contents.get(wanted)
            if contents is None: _fail('history-publication-kit-missing')
            publisher._publication_record(record, record.get('deploymentSha256'), contents)
            if record['phase'] != 'prepared-no-jobs': _fail('history-publication-unsettled')
            # The candidate is now the live kit, never a second old authority.
            if c._native_at_inode(kits, record['candidate']) is not None:
                _fail('history-publication-unsettled')
            if record['old'] is not None:
                kit_image(record['backup'], record['old'])
            elif c._native_at_inode(kits, record['backup']) is not None and record['backup'] not in value['kits']:
                _fail('history-unbound-kit')
            for index, (_name_, image) in enumerate(sorted(record['bindings'].items())):
                if not image['ready']: _fail('history-publication-unsettled')
                file_image('.source-binding-before-' + record['nonce'] + '-' + str(index),
                    expected=c._native_encoded(image['before']), inode=image['beforeInode'],
                    mode=image['mode'], gid=image['gid'])
                if c._native_at_inode(metadata, '.source-binding-' + record['nonce'] + '-' + str(index)) is not None:
                    _fail('history-publication-unsettled')

        # Interrupted native sidefiles are retained data only, from a terminal
        # ledger and complete private capsule. No prefix grants a name.
        sidefiles = c.native_sidefile_archives(owner_uid=doc['ownerUid'], owner_gid=doc['ownerGid'])
        if sidefiles['ledger'] is not None:
            file_image(c._SIDEFILE_ARCHIVES, inode=sidefiles['ledger']['inode'])
            if value['metadata'][c._SIDEFILE_ARCHIVES]['sha256'] != sidefiles['ledger']['sha256']:
                _fail('history-sidefile-archive-changed')
            for name, item in sidefiles['archives'].items():
                manifest = item['manifest']
                if manifest['schemaVersion'] == 2:
                    value['schemaVersion'] = 2
                owners = {'entries': {leaf: {'uid': image['uid']} for leaf, image in manifest['files'].items()
                    if image['uid'] != 0}}
                node = _capture_node(metadata, name, remaining=remaining, expected=owners)
                if (node['inode'] != item['inode'] or (node['kind'], node['mode'], node['gid']) != ('directory', 0o700, 0)
                        or set(node['entries']) != {'manifest.json', *(key + '.json' for key in manifest['authority']), *manifest['files']}
                        or node['entries']['manifest.json']['inode'] != item['manifestImage']['inode']
                        or node['entries']['manifest.json'] != dict(kind='file', mode=0o600, gid=0,
                            size=item['manifestSize'], **item['manifestImage'])):
                    _fail('history-sidefile-archive-changed')
                expected_files = {
                    **{key + '.json': dict(image, mode=0o600, gid=0)
                       for key, image in item['manifest']['authority'].items()},
                    **item['manifest']['files']}
                for leaf, image in expected_files.items():
                    expected = dict(kind='file', **{key: image[key] for key in ('inode', 'mode', 'gid', 'sha256', 'size')})
                    if image.get('uid', 0):
                        expected['uid'] = image['uid']
                    if node['entries'][leaf] != expected:
                        _fail('history-sidefile-archive-changed')
                expected_meta.add(name)
                value['metadata'][name] = node

        current_files = None
        if c._native_at_inode(kits, c.NATIVE_KIT.name) is not None:
            _image, current_files = publisher._kit_image(c, kits, c.NATIVE_KIT.name)
            kit_contents[_sha(current_files['source-kit.json'])] = current_files
        # Current and explicitly rolled installer ledgers anchor their old
        # immutable kits before any synthetic/initial publication is visited.
        # The retained reader independently reopens old Root contexts/journals;
        # filenames are never discovered as permission by their prefix.
        installer_records = list(c.native_installer_history_records(selection))
        if c._native_at_inode(metadata, 'source-installer-history.json') is not None:
            history_ledger, _raw, _inode = root_json('source-installer-history.json')
            c._native_installer_history_record(history_ledger)
            if any(entry['phase'] != 'committed' for entry in history_ledger['entries']):
                _fail('history-installer-unsettled')
        for fixed in ('source-installer-rebind.json', 'source-installer-code-only.json'):
            if c._native_at_inode(metadata, fixed) is not None:
                document, _raw, _inode = root_json(fixed)
                installer_records.append(dict(name=fixed, fixed=fixed, document=document, metadata=set(), kits=set()))
        seen_installers = set()
        for item in installer_records:
            record = item['document']
            if item['name'] in seen_installers: _fail('history-installer-duplicate')
            seen_installers.add(item['name'])
            if item['fixed'] == 'source-installer-rebind.json':
                publisher._installer_rebind_record(c, record)
                if private is None: _fail('history-rebind-unsettled')
            elif item['fixed'] == 'source-installer-code-only.json':
                publisher._installer_code_only_record(c, record)
            else: _fail('history-installer-invalid')
            if record['phase'] != 'committed': _fail('history-installer-unsettled')
            kit_image(record['backup'], record['oldKit'])
            if c._native_at_inode(kits, record['candidate']) is not None: _fail('history-installer-unsettled')
            if item['name'] != item['fixed']:
                file_image(item['name'], expected=c._native_encoded(record))
            # Load any still older kit referenced by this retained publication
            # before initial publication qualification needs that exact image.
            before = record['publication']['before']
            if before.get('old') is not None: kit_image(before['backup'], before['old'])
        if c._native_at_inode(metadata, 'source-publication.json') is not None:
            record, _raw, _inode = root_json('source-publication.json')
            if current_files is None: _fail('history-kit-missing')
            publication(record, current_files)

        roots = {private['generation']} if private is not None else set()
        edges = {}
        if c._native_at_inode(metadata, 'source-initial-generation.json') is not None:
            initial, _raw, _inode = root_json('source-initial-generation.json')
            publisher._initial_generation_record(c, initial)
            if private is None or initial['phase'] != 'committed': _fail('history-initial-unsettled')
            roots.add(initial['generation'])
            before = initial['before']
            retained = '.source-initial-publication-' + initial['nonce'] + '.json'
            file_image(retained, expected=c._native_encoded(before['document']), inode=before['inode'])
            publication(before['document'], current_files)
            if c._native_at_inode(metadata, '.source-initial-publication-next-' + initial['nonce']) is not None:
                _fail('history-initial-unsettled')
        if c._native_at_inode(metadata, 'source-initial-boot.json') is not None:
            initial_boot, _raw, _inode = root_json('source-initial-boot.json')
            publisher._initial_boot_record(c, initial_boot)
            initial, _raw, _inode = root_json('source-initial-generation.json')
            publisher._initial_generation_record(c, initial)
            if (private is None or initial['phase'] != 'committed'
                    or publisher._initial_static_sha(initial) != initial_boot['bindingSha256']):
                _fail('history-initial-boot-unbound')
            for item in initial_boot['transactions']:
                if item['phase'] != 'committed': _fail('history-initial-boot-unsettled')
                names = publisher._initial_boot_names(item)
                parent = item['parent']['before']
                raw = file_image(names['parent_before'], inode=parent['inode'], mode=parent['mode'], gid=parent['gid'])
                before = _json(raw)
                publisher._initial_generation_record(c, before)
                if _sha(raw) != parent['sha256'] or publisher._initial_static_sha(before) != initial_boot['bindingSha256']:
                    _fail('history-initial-boot-unbound')
                if item['child'] is not None:
                    child = item['child']['before']
                    raw = file_image(names['child_before'], inode=child['inode'], mode=child['mode'], gid=child['gid'])
                    before = _json(raw)
                    if (_sha(raw) != child['sha256'] or type(before.get('witness')) is not dict
                            or _sha(c._native_encoded(dict(before, witness=dict(before['witness'], boot=item['boot']))))
                                != item['child']['afterSha256']):
                        _fail('history-initial-boot-unbound')
                if any(c._native_at_inode(metadata, names[key]) is not None for key in ('parent_next', 'child_next')):
                    _fail('history-initial-boot-unsettled')
        for item in installer_records:
            if item['fixed'] != 'source-installer-rebind.json': continue
            rebound = item['document']
            old_files = kit_image(rebound['backup'], rebound['oldKit'])
            retained = '.source-publication-before-' + rebound['nonce'] + '.json'
            file_image(retained, expected=c._native_encoded(rebound['publication']['before']),
                inode=rebound['publication']['beforeInode'])
            publication(rebound['publication']['before'], old_files)
            for index, (_name_, image) in enumerate(sorted(rebound['bindings'].items())):
                file_image('.source-binding-before-' + rebound['nonce'] + '-' + str(index),
                    expected=c._native_encoded(image['before']), inode=image['beforeInode'], mode=image['mode'], gid=image['gid'])
            # The retained withdrawal parent is identified by its actual inode
            # and hash, not a wildcard filename or a new generation assertion.
            matched = []
            for name in original_names:
                if re.fullmatch(r'source-environment-generation-[a-f0-9]{32}\.json', name):
                    raw, inode = c._native_root_file(metadata, name, limit=65536)
                    if inode == rebound['withdrawalParent']['inode'] and _sha(raw) == rebound['withdrawalParent']['sha256']:
                        matched.append(name)
            if len(matched) != 1: _fail('history-rebind-parent-missing')
            edges[rebound['generation']] = matched[0][len('source-environment-generation-'):-5]
        for item in installer_records:
            if item['fixed'] != 'source-installer-code-only.json': continue
            record = item['document']
            files = kit_image(record['backup'], record['oldKit'])
            for label in ('publication', 'sidefiles'):
                image = record[label]
                file_image('.source-code-only-' + label + '-before-' + record['nonce'] + '.json',
                    expected=c._native_encoded(image['before']), inode=image['beforeInode'])
                if c._native_at_inode(metadata, '.source-code-only-' + label + '-next-' + record['nonce']) is not None:
                    _fail('history-installer-unsettled')
            publication(record['publication']['before'], files)

        generations = {}
        for name in sorted(original_names):
            current = name == 'source-environment-generation.json'
            if not current and not re.fullmatch(r'source-environment-generation-[a-f0-9]{32}\.json', name): continue
            record, _raw, _inode = root_json(name)
            if private is None: _fail('history-private-without-gate')
            publisher._environment_generation_record(c, record, record.get('binding'))
            if (record['phase'] not in ('committed', 'withdrawn')
                    or record['binding']['ownerUid'] != doc['ownerUid']
                    or record['binding']['brokerUid'] != private['identity']['id']
                    or record['binding']['brokerGuid'] != private['identity']['userGuid']
                    or not current and name != 'source-environment-generation-' + record['generation'] + '.json'):
                _fail('history-generation-unsettled')
            generations[record['generation']] = (name, record)
            edges[record['generation']] = record['previousGeneration']
            if current: roots.add(record['generation'])
        reachable, pending = set(), list(roots)
        while pending:
            key = pending.pop()
            if key in reachable: continue
            if len(reachable) > MAX_HISTORY_ENTRIES: _fail('history-limit')
            reachable.add(key)
            if key in edges: pending.append(edges[key])
        for generation, (name, record) in generations.items():
            if generation not in reachable: _fail('history-unbound-generation')
            publisher._environment_retained(c, metadata, record)
            archive = publisher._environment_archive_name(record)
            expected_meta.add(archive)
            value['metadata'][archive] = _capture_node(metadata, archive, remaining=remaining)
            if name != 'source-environment-generation.json': file_image(name)

        # Old boot observations are immutable data only. Their own closed
        # ledger identifier derives the filename, and their origin generation
        # must be in the qualified history graph; no old PID becomes authority.
        for name in sorted(original_names):
            if not re.fullmatch(r'\.source-boot-quiesce-[a-f0-9]{32}\.json', name): continue
            boot, _raw, _inode = root_json(name)
            from .native_executor import boot_quiesce_record
            boot_quiesce_record(boot)
            if (name != '.source-boot-quiesce-' + boot['id'] + '.json'
                    or boot['snapshot']['generation'] not in reachable):
                _fail('history-boot-unbound')
            file_image(name)
        actual_source = {name for name in original_names if name.startswith(_SOURCE_PREFIXES) or name == 'native-app-enrollment.json'}
        if actual_source != expected_meta or set(os.listdir(metadata)) != original_names:
            _fail('history-namespace-unbound')
        actual_kits = {name for name in original_kits if name == c.NATIVE_KIT.name or name.startswith('.source-kit-')}
        if actual_kits != known_kits & original_kits or set(os.listdir(kits)) != original_kits:
            _fail('history-kit-namespace-unbound')
    runtime = dict(deploymentSha256=selection.deployment.checksum, custodySha256=doc['runtimeCustodySha256'],
        bundleSha256=doc['bundleSha256'], sourceRef=ref)
    return validate_history(value, owner_uid=doc['ownerUid'], owner_gid=doc['ownerGid'], runtime=runtime)


def archive_begin(*, transaction, archive_id):
    """Freeze a fixed Source prefix in the already-created Core archive.

    The Core caller holds its existing Root lock. This entry point independently
    reopens all authority; no old live bridge, callback or caller path is used.
    """
    from pixel_macos_custody import protected_directory
    witness, _raw, witness_inode = _witness(transaction)
    if (type(archive_id) is not str or not _NONCE.fullmatch(archive_id)
            or archive_id != witness['source']['nonce']): _fail('archive-id-invalid')
    _core_stopped(witness)
    runtime, publisher = _old_runtime(witness)
    _identity(witness)
    with protected_directory(c.NATIVE_META) as metadata, protected_directory(ARCHIVE_ROOT) as archives:
        try: previous, _inode = c._native_root_file(metadata, CURSOR, limit=32 * 1024 * 1024)
        except FileNotFoundError: previous = None
        if previous is not None:
            value = _cursor_value(_json(previous), transaction)
            if value['archiveId'] != archive_id or value['witnessInode'] != witness_inode:
                _fail('cursor-changed')
            _bind_plan(witness, value)
            archive = _root_stat(archives, archive_id, value['archiveInode'])
            try:
                _verify_prefix(metadata, archive, value, witness, publisher)
                _bind_archive_receipt(archive, witness, transaction, value)
            finally:
                os.close(archive)
            return _public_cursor(value)
        archive_inode = c._native_at_inode(archives, archive_id)
        if archive_inode is None: _fail('archive-missing')
        archive = _root_stat(archives, archive_id, archive_inode)
        try:
            if set(os.listdir(archive)) != {'receipt.json'}: _fail('archive-not-empty')
            receipt_raw = c._native_root_file(archive, 'receipt.json')[0]
            _archive_receipt(_json(receipt_raw), witness, transaction)
            private = witness['source']['inventory']['kind'] != CODE_ONLY_KIND
            receipt, record = _private_complete(witness, transaction, runtime, publisher) if private else (None, None)
            value = dict(schemaVersion=1, kind='ods-native-source-retirement-cursor', transaction=transaction,
                archiveId=archive_id, archiveInode=archive_inode, witnessInode=witness_inode,
                inventorySha256=_sha(c._native_encoded(witness['source']['inventory'])), receipt=receipt,
                plan=_plan(witness, transaction, publisher, receipt, record), next=0, phase='source')
            value['planSha256'] = _sha(c._native_encoded(value['plan']))
            _cursor_value(value, transaction)
            _bind_plan(witness, value)
            _verify_prefix(metadata, archive, value, witness, publisher)
            _core_stopped(witness)
            if _witness(transaction)[2] != witness_inode: _fail('witness-changed')
            _save_at(metadata, CURSOR, value, expected=None)
            _bind_archive_receipt(archive, witness, transaction, value)
            return _public_cursor(value)
        finally:
            os.close(archive)


def archive_step(*, transaction):
    """Move at most one exact inventory-bound item, preserving its inode."""
    from pixel_macos_custody import protected_directory
    witness, _raw, witness_inode = _witness(transaction)
    _core_stopped(witness)
    _runtime_value, publisher = _old_runtime(witness)
    _identity(witness)
    with protected_directory(c.NATIVE_META) as metadata, protected_directory(ARCHIVE_ROOT) as archives:
        previous = c._native_root_file(metadata, CURSOR, limit=32 * 1024 * 1024)[0]
        value = _cursor_value(_json(previous), transaction)
        if value['witnessInode'] != witness_inode: _fail('witness-changed')
        _bind_plan(witness, value)
        archive = _root_stat(archives, value['archiveId'], value['archiveInode'])
        try:
            _verify_prefix(metadata, archive, value, witness, publisher)
            _bind_archive_receipt(archive, witness, transaction, value)
            if value['phase'] == 'core': return _public_cursor(value)
            if value['next'] < len(value['plan']):
                row = value['plan'][value['next']]
                origin = _row_origin(row)
                target = _destination(value['next'])
                with protected_directory(origin.parent) as parent:
                    source_inode = c._native_at_inode(parent, origin.name)
                    target_inode = c._native_at_inode(archive, target)
                    expected_inode = _entry_inode(row)
                    if source_inode == expected_inode and target_inode is None:
                        _verify_entry(parent, origin.name, row, witness, publisher)
                        _core_stopped(witness)
                        if (c._native_root_file(metadata, CURSOR, limit=32 * 1024 * 1024)[0] != previous
                                or _witness(transaction)[2] != witness_inode): _fail('cursor-changed')
                        os.rename(origin.name, target, src_dir_fd=parent, dst_dir_fd=archive)
                        os.fsync(parent)
                        os.fsync(archive)
                    elif source_inode is not None or target_inode != expected_inode:
                        _fail('archive-image-changed')
                    # Includes an exact rename whose acknowledgement was lost.
                    if c._native_at_inode(parent, origin.name) is not None: _fail('archive-origin-reappeared')
                    _verify_entry(archive, target, row, witness, publisher)
                value['next'] += 1
            if value['next'] == len(value['plan']): value['phase'] = 'core'
            _core_stopped(witness)
            _verify_prefix(metadata, archive, value, witness, publisher)
            _save_at(metadata, CURSOR, value, expected=previous)
            _bind_archive_receipt(archive, witness, transaction, value)
            return _public_cursor(value)
        finally:
            os.close(archive)


def _transaction(value):
    if (type(value) is not dict or set(value) != {'kind', 'transaction', 'currentDigest'}
            or value['kind'] != 'native-installer-retirement'
            or any(type(value[key]) is not str or not _SHA.fullmatch(value[key])
                for key in ('transaction', 'currentDigest'))):
        _fail('transaction-invalid')
    return value


def _json(raw):
    try: return json.loads(raw, object_pairs_hook=c._native_pairs)
    except (TypeError, ValueError, UnicodeError, RecursionError): _fail('record-invalid')


def _witness(transaction):
    """Reopen immutable actual Core authority; never import a mutable installer."""
    from pixel_macos_custody import protected_directory
    c._native_root()
    _transaction(transaction)
    with protected_directory(c.NATIVE_META) as metadata:
        raw, inode = c._native_root_file(metadata, 'retirement.json', limit=32 * 1024 * 1024)
    value = _json(raw)
    if (_sha(raw) != transaction['transaction'] or type(value) is not dict
            or set(value) != {'schema', 'kind', 'owner', 'boot', 'hashes', 'trees', 'sandboxes', 'source'}
            or type(value['schema']) is not int or value['schema'] != 3 or value['kind'] != 'ods-native-source-installer-retirement'
            or type(value['owner']) is not int or value['owner'] <= 0
            or type(value['hashes']) is not dict or not 1 <= len(value['hashes']) <= 512
            or type(value['trees']) is not dict or type(value['sandboxes']) is not list):
        _fail('witness-invalid')
    source = value['source']
    if (type(source) is not dict or set(source) != {'schemaVersion', 'kind', 'nonce', 'owner', 'runtime', 'inventory'}
            or type(source['schemaVersion']) is not int or source['schemaVersion'] != 1
            or source['kind'] != 'ods-native-source-retirement-intent' or type(source['nonce']) is not str
            or not _NONCE.fullmatch(source['nonce']) or not _runtime(source['runtime'])
            or source['runtime']['bundleSha256'] != transaction['currentDigest']
            or type(source['owner']) is not dict or set(source['owner']) != {'uid', 'gid', 'name', 'installDir'}
            or source['owner']['uid'] != value['owner']):
        _fail('witness-invalid')
    try: owner = pwd.getpwuid(value['owner'])
    except KeyError: _fail('owner-changed')
    if (source['owner']['name'], source['owner']['gid']) != (owner.pw_name, owner.pw_gid): _fail('owner-changed')
    root = source['owner']['installDir']
    if (type(root) is not str or not root.startswith('/') or root in ('/', owner.pw_dir)
            or any(part in ('', '.', '..') for part in root.split('/')[1:])):
        _fail('owner-changed')
    inventory = source['inventory']
    if inventory.get('kind') == CODE_ONLY_KIND:
        c.native_retirement_inventory_receipt(inventory, owner_uid=owner.pw_uid, owner_gid=owner.pw_gid, runtime=source['runtime'])
    else:
        if 'history' not in inventory: _fail('history-missing')
        c.native_retirement_inventory_receipt({key: item for key, item in inventory.items() if key != 'history'},
            owner_uid=owner.pw_uid, owner_gid=owner.pw_gid, runtime=source['runtime'])
        validate_history(inventory['history'], owner_uid=owner.pw_uid, owner_gid=owner.pw_gid, runtime=source['runtime'])
    try:
        if str(uuid.UUID(value['boot'])) != value['boot'] or inventory['boot'].lower() != value['boot']:
            raise ValueError()
    except (TypeError, ValueError, AttributeError): _fail('boot-invalid')
    expected = {'system/com.ods.pixel-' + role for role in CORE_ROLES}
    if set(value['trees']) not in (expected, expected | {'system/com.ods.pixel-native-integrations'}):
        _fail('core-service-family-invalid')
    for tree in value['trees'].values():
        if (type(tree) is not list or not 1 <= len(tree) <= 4096
                or any(type(row) is not list or len(row) != 3 or any(type(n) is not int for n in row)
                    or row[0] <= 0 or row[1] <= 0 or not 0 <= row[2] < 1000000 for row in tree)):
            _fail('core-stop-witness-invalid')
    return value, raw, inode


def _core_name(name):
    return (name in {'installation.json', 'installation-edge.json', 'lock', 'model-before.json', 'model-completed.json',
        'model-route-completed.json', 'model-promotion-completed.json', 'service-installation.json', 'service-baseline.json',
        'verified.json', 'integrations-preservation.json', '.integrations-preservation.pending', 'ops-identity.json', 'ops-identity.lock'}
        or re.fullmatch(r'integrations-preservation-interrupted-[a-f0-9]{64}\.json', name)
        or re.fullmatch(r'runtime-upgrade-[a-f0-9]{64}\.completed\.json', name)
        or re.fullmatch(r'runtime-upgrade-(?:context|edge)-[a-f0-9]{64}\.json', name)
        or re.fullmatch(r'runtime-upgrade-stop-[a-f0-9]{64}-(?:candidate|previous)-(?:access|gateway|integrations|manager|operations|promoter|relay)\.json', name)
        or re.fullmatch(r'runtime-controller-repair-[a-f0-9]{64}\.json', name))


def _command(argv):
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10, check=False,
        cwd='/', env={'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'HOME': '/', 'LANG': 'C', 'LC_ALL': 'C'})
    if result.returncode or len(result.stdout) > 1024 * 1024: _fail('census-unavailable')
    return result.stdout


def _core_stopped(witness):
    """Fresh boot/labels and retained birth identities; never kill a PID."""
    from pixel_macos_custody import protected_bytes
    if c._native_boot_uuid().lower() != witness['boot']: _fail('boot-changed')
    for path, checksum in witness['hashes'].items():
        if type(path) is not str or type(checksum) is not str or not _SHA.fullmatch(checksum): _fail('core-binding-invalid')
        selected = Path(path)
        if not (selected == SETTINGS or selected.parent == c.NATIVE_META and _core_name(selected.name)
                or selected.parent == DAEMONS and 'system/' + selected.stem in witness['trees']):
            _fail('core-binding-invalid')
        if _sha(protected_bytes(selected, limit=32 * 1024 * 1024)) != checksum: _fail('core-binding-changed')
    _core_no_actors(witness)


def _core_no_actors(witness):
    """Fresh OS absence only; caller separately proves live/archived authority."""
    from pixel_macos_process import process_birth, ProcessIdentityError
    import errno
    if c._native_boot_uuid().lower() != witness['boot']: _fail('boot-changed')
    # Only loaded jobs count: launchd keeps a booted-out label in its
    # persistent override table, which the same output also prints.
    loaded = c.native_launchd_loaded_labels(_command(['/bin/launchctl', 'print', 'system']))
    core = {target.removeprefix('system/') for target in witness['trees']}
    if any(label in core or label.startswith(c.NATIVE_LABEL) for label in loaded):
        _fail('actors-present')
    for tree in witness['trees'].values():
        for pid, sec, usec in tree:
            try:
                if process_birth(pid) == (sec, usec): _fail('core-process-survives')
            except ProcessIdentityError as error:
                if getattr(error, 'errno', None) != errno.ESRCH: _fail('census-unavailable')
    identity = witness['source']['inventory']['identity']
    if identity == {'kind': 'absent'}: broker = None
    elif identity.get('kind') == 'retained': broker = identity['record']['id']
    else: broker = identity['id']
    processes = _command(['/bin/ps', '-axo', 'pid=,uid='])
    seen = set()
    for line in processes.splitlines():
        match = re.fullmatch(r'\s*([1-9][0-9]*)\s+(-?[0-9]+)\s*', line)
        if match is None or int(match[1]) in seen: _fail('census-unavailable')
        seen.add(int(match[1]))
        if broker is not None and int(match[2]) == broker: _fail('actors-present')
    if not seen: _fail('census-unavailable')


def _old_runtime(witness):
    """Complete immutable file custody after Core stopped; no live selection."""
    from pixel_macos_custody import protected_bytes
    from pixel_provider.runtime_custody import RuntimeCustody
    binary = str(LAUNCHER)
    settings = _json(protected_bytes(SETTINGS, limit=65536))
    owner = witness['source']['owner']
    if (type(settings) is not dict or settings.get('owner') != owner['name']
            or settings.get('install_dir') != owner['installDir'] or settings.get('state_dir') != str(c.NATIVE_META)
            or settings.get('openclaw_bin') != binary):
        _fail('core-binding-changed')
    value, checksum = RuntimeCustody(SimpleNamespace(binary=binary)).inspect()
    runtime = witness['source']['runtime']
    if (checksum != runtime['custodySha256']
            or value['runtimeRoot'] != str(c.NATIVE_RUNTIMES / runtime['bundleSha256'] / 'runtime')
            or value['sourceRoot'] != str(c.NATIVE_RUNTIMES / runtime['bundleSha256'] / 'ods-source')):
        _fail('runtime-changed')
    path = Path(value['sourceRoot']) / 'installers/macos/lib/pixel-native-source-broker.py'
    protected_bytes(path, limit=2 * 1024 * 1024)
    spec = importlib.util.spec_from_file_location('protected_native_retirement_publisher', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return value, module


def _identity(witness):
    inventory = witness['source']['inventory']
    value = inventory['identity']
    if value == {'kind': 'absent'}:
        if c._native_ds_record('Users') is not None or c._native_ds_record('Groups') is not None:
            _fail('identity-changed')
        for lookup in (c.pwd.getpwnam, c.grp.getgrnam):
            try: lookup(c.NATIVE_BROKER)
            except KeyError: continue
            _fail('identity-changed')
        return None
    identity = c._native_identity_intent(value['record'] if value.get('kind') == 'retained' else value)
    for kind in ('Groups', 'Users'):
        c._native_identity_record(c._native_ds_record(kind), c._native_identity_attributes(identity, kind), complete=True)
    c._native_identity_collisions(identity)
    if c._native_uuid(c._native_acl_library(), identity['id']) != uuid.UUID(identity['userGuid']).bytes:
        _fail('identity-changed')
    if 'ownerGuid' in inventory and c._native_uuid(c._native_acl_library(), witness['owner']) != uuid.UUID(inventory['ownerGuid']).bytes:
        _fail('owner-changed')
    return identity


def _private_complete(witness, transaction, runtime, publisher):
    """Prove the complete revoked v1 boundary without a running gateway."""
    from pixel_macos_custody import protected_directory
    inventory = witness['source']['inventory']
    identity = _identity(witness)
    if identity is None: _fail('withdrawal-identity-missing')
    with protected_directory(c.NATIVE_META) as metadata, protected_directory(c.NATIVE_KIT.parent) as kits:
        publisher._kit_image(c, kits, c.NATIVE_KIT.name, expected=inventory['kit'])
        record, _raw = c._native_json_at(metadata, 'source-environment-generation.json')
        publisher._environment_generation_record(c, record, inventory['binding'])
        installer = record.get('installer')
        if (record['phase'] != 'withdrawn' or type(installer) is not dict or installer.get('transaction') != transaction
                or not installer.get('receipt', {}).get('ready') or record['previousGeneration'] != inventory['generation']
                or record['old']['gate'] != inventory['gate'] or record['old']['environment'] != inventory['environment']
                or record['old']['directory'] != inventory['jobs']['directory']
                or _sha(c._native_encoded(record['old']['jobs'])) != inventory['jobs']['sha256']):
            _fail('withdrawal-unconfirmed')
        intent = installer['intent']
        c.native_retirement_withdrawal_intent(intent, transaction=transaction,
            owner_uid=witness['owner'], owner_gid=witness['source']['owner']['gid'])
        if (intent['generation'] != inventory['generation'] or intent['binding'] != inventory['binding']
                or intent['boot'] != inventory['generationBoot'] or intent['token'] != inventory['token']):
            _fail('withdrawal-unconfirmed')
        publisher._environment_retained(c, metadata, record)
        expected = c._native_encoded(dict(schemaVersion=1, kind='ods-native-source-custody', phase='prepared-no-jobs',
            deploymentSha256=witness['source']['runtime']['deploymentSha256'], kitSha256=inventory['binding']['kitSha256'],
            ownerUid=witness['owner'], ownerGid=witness['source']['owner']['gid'], ownerGuid=inventory['ownerGuid'],
            broker=identity, state=str(c.NATIVE_STATE), env=str(c.NATIVE_ENV), kit=str(c.NATIVE_KIT), hostPython=runtime['hostPython']))
        descriptor = dict(inode=record['v1']['inode'], sha256=_sha(expected), mode=0o640, gid=identity['id'])
        gate = os.open(c.NATIVE_STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            if (list(c.native_verify_gate(gate, witness['owner'], identity['id'])) != inventory['binding']['gate']
                    or c._native_root_file(gate, 'custody.json', mode=0o640, gid=identity['id']) != (expected, record['v1']['inode'])
                    or c._native_environment_snapshot(gate, c.NATIVE_ENV.name, identity['id']) != inventory['environment']
                    or publisher._installer_token_metadata(c, gate, identity['id']) != inventory['token']):
                _fail('withdrawal-data-changed')
        finally:
            os.close(gate)
        with protected_directory(c.NATIVE_DEFINITIONS) as units:
            if any(name.startswith(c.NATIVE_LABEL) for name in os.listdir(units)):
                _fail('withdrawal-definitions-present')
        if any(c._native_at_inode(metadata, name) is not None for name in (
                'native-source-jobs', 'source-jobs-prepare.json', 'source-jobs-active.json', 'source-execution.json')):
            _fail('withdrawal-jobs-present')
        c._native_verify_prepared_logs(metadata, identity,
            {key: item for key, item in inventory['binding'].items() if key not in ('runtimeCustodySha256', 'bundleSha256', 'sourceRef')})
        archive_name = publisher._environment_archive_name(record)
        archive = os.open(archive_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=metadata)
        try:
            raw, inode = c._native_root_file(archive, 'withdrawal-receipt.json')
            expected_audit = dict(schemaVersion=1, kind='ods-native-source-retirement-withdrawal-archive', transaction=transaction,
                intentSha256=_sha(c._native_encoded(intent)), binding=inventory['binding'], old=record['old'],
                descriptor=descriptor, withdrawal=record['generation'], boot=installer['boot'])
            if raw != c._native_encoded(expected_audit) or inode != installer['receipt']['inode']:
                _fail('withdrawal-receipt-changed')
        finally:
            os.close(archive)
        receipt = dict(schemaVersion=1, kind='ods-native-source-retirement-withdrawal', transaction=transaction,
            intentSha256=_sha(c._native_encoded(intent)), binding=inventory['binding'], runtime=intent['runtime'], owner=intent['owner'],
            generation=inventory['generation'], boot=installer['boot'], environment=inventory['environment'], token=inventory['token'],
            withdrawal=record['generation'], archive=dict(name=archive_name, inode=record['archive'], receiptSha256=_sha(raw)),
            descriptor=descriptor, metadata=intent['metadata'])
        c.native_retirement_withdrawal_receipt(receipt, intent=intent, transaction=transaction,
            owner_uid=witness['owner'], owner_gid=witness['source']['owner']['gid'])
        return receipt, record


def _fixed_root(kind):
    result = {'state': c.NATIVE_STATE, 'logs': c.NATIVE_LOGS, 'kit': c.NATIVE_KIT, 'portal': c.NATIVE_CONNECTION_RECORD}.get(kind)
    if result is None: _fail('cursor-path-invalid')
    return result


def _code_only_history_nonces(inventory):
    """Code-only installer transactions whose before-images and old kit the witness froze."""
    history = inventory['history']
    nonces = set()
    for name in history['metadata']:
        match = re.fullmatch(r'\.source-code-only-publication-before-([a-f0-9]{32})\.json', name)
        if (match is not None and '.source-code-only-sidefiles-before-' + match[1] + '.json' in history['metadata']
                and '.source-kit-before-' + match[1] in history['kits']):
            nonces.add(match[1])
    return nonces


def _code_only_ledger(witness, metadata, publisher):
    """Bind the committed code-only installer ledger to the witness history it produced.

    `_capture_histories` freezes that transaction's before-images and old kit
    but never images the fixed ledger, which stays a current name. It is
    retained installer history, not authority: the plan archives it by rename
    and nothing reads it again or follows it to another path.
    """
    if c._native_at_inode(metadata, CODE_ONLY_LEDGER) is None:
        return None
    raw, inode = c._native_root_file(metadata, CODE_ONLY_LEDGER, limit=524288)
    record = _json(raw)
    publisher._installer_code_only_record(c, record)
    if record['phase'] != 'committed': _fail('code-only-ledger-unsettled')
    inventory, runtime = witness['source']['inventory'], witness['source']['runtime']
    history = inventory['history']
    kit = history['kits'].get(record['backup'])
    before = {label: (history['metadata'].get('.source-code-only-' + label + '-before-' + record['nonce'] + '.json'),
        dict(inode=record[label]['beforeInode'], sha256=_sha(c._native_encoded(record[label]['before']))))
        for label in ('publication', 'sidefiles')}
    if (_code_only_history_nonces(inventory) != {record['nonce']}
            or record['transaction']['candidateDigest'] != runtime['bundleSha256']
            or record['deploymentSha256'] != runtime['deploymentSha256'] or record['sourceRef'] != runtime['sourceRef']
            or type(kit) is not dict or kit.get('inode') != record['oldKit']['inode']
            or any(type(image) is not dict or {key: image.get(key) for key in wanted} != wanted
                for image, wanted in before.values())):
        _fail('code-only-ledger-unbound')
    node = _capture_node(metadata, CODE_ONLY_LEDGER, remaining=[MAX_HISTORY_ENTRIES, MAX_HISTORY_BYTES])
    if (node['kind'], node['inode'], node['sha256'], node['mode'], node['gid']) != ('file', inode, _sha(raw), 0o600, 0):
        _fail('code-only-ledger-changed')
    return node


def _plan(witness, transaction, publisher, receipt=None, record=None):
    """Derive literal roots and exact ledger names; never accept caller paths."""
    from pixel_macos_custody import protected_directory
    inventory = witness['source']['inventory']
    private = inventory['kind'] != CODE_ONLY_KIND
    rows, metadata_images = [], {}
    if private:
        if receipt is None or record is None: _fail('withdrawal-unconfirmed')
        rows.extend((dict(kind='state', name=None, expected=dict(inode=inventory['binding']['gate'],
            descriptor=receipt['descriptor'], environment=inventory['environment'], token=inventory['token'],
            children=inventory['children'])), dict(kind='logs', name=None, expected=inventory['logs'])))
    history = inventory['history']
    for name, image in sorted(history['kits'].items()):
        rows.append(dict(kind='kit-history', name=name, expected=image))
    if inventory['kit'] is not None: rows.append(dict(kind='kit', name=None, expected=inventory['kit']))
    if inventory['portalRecord'] is not None: rows.append(dict(kind='portal', name=None, expected=inventory['portalRecord']))
    with protected_directory(c.NATIVE_META) as metadata:
        for name, image in inventory['metadata'].items():
            if name == 'source-identity.json': continue
            actual = c._native_at_inode(metadata, name)
            if actual == image['inode']:
                body, inode = c._native_root_file(metadata, name, limit=524288)
                if _sha(body) != image['sha256'] or inode != image['inode']: _fail('metadata-changed')
                metadata_images[name] = _capture_node(metadata, name, remaining=[MAX_HISTORY_ENTRIES, MAX_HISTORY_BYTES])
            elif private and name in ('source-jobs-prepare.json', 'source-jobs-active.json', 'source-execution.json') and actual is None:
                # Exact retained old jobs were proved by the complete withdrawal.
                if record['old']['files'].get(name, {}).get('inode') != image['inode']:
                    _fail('metadata-changed')
            elif private and name == 'source-environment-generation.json':
                retained = 'source-environment-generation-' + inventory['generation'] + '.json'
                body, inode = c._native_root_file(metadata, retained, limit=524288)
                if inode != image['inode'] or _sha(body) != image['sha256']: _fail('metadata-changed')
                metadata_images[retained] = _capture_node(metadata, retained, remaining=[MAX_HISTORY_ENTRIES, MAX_HISTORY_BYTES])
            else:
                _fail('metadata-changed')
        for name, image in history['metadata'].items():
            if _capture_node(metadata, name, remaining=[MAX_HISTORY_ENTRIES, MAX_HISTORY_BYTES], expected=image) != image:
                _fail('history-changed')
            metadata_images[name] = image
        if private:
            for name in ('source-environment-generation.json', receipt['archive']['name']):
                metadata_images[name] = _capture_node(metadata, name, remaining=[MAX_HISTORY_ENTRIES, MAX_HISTORY_BYTES])
        elif CODE_ONLY_LEDGER not in metadata_images:
            ledger = _code_only_ledger(witness, metadata, publisher)
            if ledger is not None:
                metadata_images[CODE_ONLY_LEDGER] = ledger
        expected = set(metadata_images) | ({'source-identity.json'} if c._native_at_inode(metadata, 'source-identity.json') is not None else set())
        actual_source = {name for name in os.listdir(metadata) if name.startswith(_SOURCE_PREFIXES)
            or name == 'native-app-enrollment.json'}
        if actual_source != expected: _fail('archive-namespace-unbound')
    # Ledger/history roots first, source environment parent last, keeping its
    # complete receipt live as long as possible during the preserved prefix.
    for name in sorted(metadata_images, key=lambda name: (name == 'source-environment-generation.json', name)):
        rows.append(dict(kind='metadata', name=name, expected=metadata_images[name]))
    if len(rows) > MAX_HISTORY_ENTRIES: _fail('archive-limit')
    return rows


def _row_origin(row):
    if row['kind'] == 'metadata': return c.NATIVE_META / row['name']
    if row['kind'] == 'kit-history': return c.NATIVE_KIT.parent / row['name']
    return _fixed_root(row['kind'])


def _cursor_value(value, transaction):
    fields = {'schemaVersion', 'kind', 'transaction', 'archiveId', 'archiveInode', 'witnessInode', 'inventorySha256',
        'receipt', 'plan', 'planSha256', 'next', 'phase'}
    if (type(value) is not dict or set(value) != fields or type(value['schemaVersion']) is not int
            or value['schemaVersion'] != 1 or value['kind'] != 'ods-native-source-retirement-cursor'
            or value['transaction'] != transaction or type(value['archiveId']) is not str or not _NONCE.fullmatch(value['archiveId'])
            or not c._native_inode(value['archiveInode']) or not c._native_inode(value['witnessInode'])
            or type(value['inventorySha256']) is not str or not _SHA.fullmatch(value['inventorySha256'])
            or type(value['plan']) is not list or not 0 <= len(value['plan']) <= MAX_HISTORY_ENTRIES
            or value['planSha256'] != _sha(c._native_encoded(value['plan']))
            or type(value['next']) is not int or not 0 <= value['next'] <= len(value['plan'])
            or value['phase'] not in ('source', 'core') or value['phase'] == 'core' and value['next'] != len(value['plan'])):
        _fail('cursor-invalid')
    seen = set()
    for row in value['plan']:
        if type(row) is not dict or set(row) != {'kind', 'name', 'expected'}: _fail('cursor-invalid')
        if row['kind'] in ('metadata', 'kit-history'):
            if not _name(row['name']): _fail('cursor-invalid')
        elif row['kind'] in ('state', 'logs', 'kit', 'portal'):
            if row['name'] is not None: _fail('cursor-invalid')
        else: _fail('cursor-invalid')
        key = row['kind'], row['name']
        if key in seen: _fail('cursor-invalid')
        seen.add(key)
    return value


def _bind_plan(witness, cursor):
    """Bind every logical item back to immutable Source inventory/receipt."""
    inv = witness['source']['inventory']
    if cursor['archiveId'] != witness['source']['nonce']: _fail('cursor-archive-unbound')
    private = inv['kind'] != CODE_ONLY_KIND
    allowed = {('metadata', name): image for name, image in inv['history']['metadata'].items()}
    allowed.update({('kit-history', name): image for name, image in inv['history']['kits'].items()})
    for name, image in inv['metadata'].items():
        if name == 'source-identity.json': continue
        if private and name in ('source-jobs-prepare.json', 'source-jobs-active.json', 'source-execution.json'): continue
        if private and name == 'source-environment-generation.json':
            name = 'source-environment-generation-' + inv['generation'] + '.json'
        allowed[('metadata', name)] = image
    if inv['kit'] is not None: allowed[('kit', None)] = inv['kit']
    if inv['portalRecord'] is not None: allowed[('portal', None)] = inv['portalRecord']
    if private:
        receipt = cursor['receipt']
        if (type(receipt) is not dict or receipt.get('transaction') != cursor['transaction']
                or receipt.get('binding') != inv['binding'] or receipt.get('environment') != inv['environment']
                or receipt.get('token') != inv['token'] or receipt.get('generation') != inv['generation']):
            _fail('cursor-receipt-invalid')
        allowed[('state', None)] = dict(inode=inv['binding']['gate'], descriptor=receipt['descriptor'],
            environment=inv['environment'], token=inv['token'], children=inv['children'])
        allowed[('logs', None)] = inv['logs']
        allowed[('metadata', 'source-environment-generation.json')] = None
        allowed[('metadata', receipt['archive']['name'])] = None
    elif cursor['receipt'] is not None:
        _fail('cursor-receipt-invalid')
    planned = {(row['kind'], row['name']) for row in cursor['plan']}
    ledger = ('metadata', CODE_ONLY_LEDGER)
    if not private and ledger in planned and ledger not in allowed:
        # `_code_only_ledger` proved this exact file against the one code-only
        # transaction the witness history froze; its node is pinned below.
        if len(_code_only_history_nonces(inv)) != 1: _fail('cursor-plan-unbound')
        allowed[ledger] = None
    if planned != set(allowed): _fail('cursor-plan-unbound')
    for row in cursor['plan']:
        key = row['kind'], row['name']
        image = allowed[key]
        actual = row['expected']
        if image is None:
            if not private:
                if key != ledger or (actual.get('kind'), actual.get('mode'), actual.get('gid')) != ('file', 0o600, 0):
                    _fail('cursor-plan-unbound')
            elif key[1] == cursor['receipt']['archive']['name']:
                if (actual.get('kind') != 'directory' or actual.get('inode') != cursor['receipt']['archive']['inode']
                        or actual.get('entries', {}).get('withdrawal-receipt.json', {}).get('sha256')
                            != cursor['receipt']['archive']['receiptSha256']):
                    _fail('cursor-plan-unbound')
            elif actual.get('kind') != 'file': _fail('cursor-plan-unbound')
        elif set(image) == {'inode', 'sha256'}:
            if (actual.get('kind') != 'file' or any(actual.get(name) != image[name] for name in image)):
                _fail('cursor-plan-unbound')
        elif image != actual:
            _fail('cursor-plan-unbound')


def _entry_inode(row):
    return row['expected']['directory' if row['kind'] == 'logs' else 'inode']


def _verify_entry(directory, name, row, witness, publisher):
    expected = row['expected']
    if row['kind'] in ('metadata', 'kit-history'):
        if _capture_node(directory, name, remaining=[MAX_HISTORY_ENTRIES, MAX_HISTORY_BYTES], expected=expected) != expected:
            _fail('archive-image-changed')
    elif row['kind'] == 'kit':
        publisher._kit_image(c, directory, name, expected=expected)
    elif row['kind'] == 'portal':
        raw, inode = c._native_root_file(directory, name, mode=0o644)
        if {'inode': inode, 'sha256': _sha(raw)} != expected: _fail('archive-image-changed')
    elif row['kind'] in ('state', 'logs'):
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
        try:
            identity = witness['source']['inventory']['identity']
            if row['kind'] == 'logs':
                if list(c.native_verify_log_directory(fd, identity['id'])) != expected['directory']: _fail('archive-image-changed')
                c._native_log_files(fd, {'files': {leaf: dict(inode=image, ready=True)
                    for leaf, image in expected['files'].items()}}, identity)
            else:
                if list(c.native_verify_gate(fd, witness['owner'], identity['id'])) != expected['inode']: _fail('archive-image-changed')
                descriptor, inode = c._native_root_file(fd, 'custody.json', mode=0o640, gid=identity['id'])
                if (inode != expected['descriptor']['inode'] or _sha(descriptor) != expected['descriptor']['sha256']
                        or c._native_environment_snapshot(fd, c.NATIVE_ENV.name, identity['id']) != expected['environment']
                        or publisher._installer_token_metadata(c, fd, identity['id']) != expected['token']):
                    _fail('archive-data-changed')
                layout = c._native_layout({'ownerUid': witness['owner'], 'ownerGid': witness['source']['owner']['gid']}, identity)
                for relative, uid, gid, mode in layout:
                    child = c._native_directory_fd(fd, relative)
                    try:
                        info = os.fstat(child)
                        if ([info.st_dev, info.st_ino] != expected['children'][relative]
                                or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (uid, gid, mode)):
                            _fail('archive-data-changed')
                        c._native_no_acl(child)
                    finally:
                        os.close(child)
        finally:
            os.close(fd)
    else:
        _fail('cursor-path-invalid')


def _archive_receipt(value, witness, transaction, cursor=None):
    if (type(value) is not dict or set(value) != {'schema', 'kind', 'owner', 'installDir', 'status', 'moves', 'source'}
            or type(value['schema']) is not int or value['schema'] != 2 or value['kind'] != ARCHIVE_KIND
            or value['owner'] != witness['owner'] or value['installDir'] != witness['source']['owner']['installDir']
            or value['status'] != 'retiring' or value['moves'] != [] or type(value['source']) is not dict
            or set(value['source']) != {'transaction', 'inventorySha256', 'planSha256', 'phase'}
            or value['source']['transaction'] != transaction
            or value['source']['inventorySha256'] != _sha(c._native_encoded(witness['source']['inventory']))
            or value['source']['phase'] not in ('prepared', 'source', 'core')):
        _fail('archive-receipt-invalid')
    if cursor is None:
        if value['source']['phase'] != 'prepared' or value['source']['planSha256'] is not None:
            _fail('archive-receipt-invalid')
    elif (value['source']['planSha256'] != cursor['planSha256'] or value['source']['phase'] not in ('source', 'core')):
        _fail('archive-receipt-changed')
    return value


def _save_at(directory, name, value, *, expected):
    """Fixed control-file CAS, never a generic caller-selected mutation path."""
    if name not in (CURSOR, CORE_PLAN, 'receipt.json'): _fail('control-name-invalid')
    try: current = c._native_root_file(directory, name, limit=32 * 1024 * 1024)[0]
    except FileNotFoundError: current = None
    if current != expected: _fail('control-changed')
    raw = c._native_encoded(value)
    temporary = '.retirement-control-' + os.urandom(16).hex()
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    try:
        os.fchown(fd, 0, 0)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(fd)
        try: fresh = c._native_root_file(directory, name, limit=32 * 1024 * 1024)[0]
        except FileNotFoundError: fresh = None
        if fresh != expected: _fail('control-changed')
        os.rename(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        os.close(fd)
        try: os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError: pass
    if c._native_root_file(directory, name, limit=32 * 1024 * 1024)[0] != raw: _fail('control-unconfirmed')
    return raw


def _root_stat(directory, name, expected_inode):
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
    try:
        info = os.fstat(fd)
        c._native_no_acl(fd)
        if (expected_inode != [info.st_dev, info.st_ino] or c._native_at_inode(directory, name) != expected_inode
                or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, 0o700)):
            _fail('archive-custody-changed')
        return fd
    except BaseException:
        os.close(fd)
        raise


def _destination(index):
    if type(index) is not int or not 0 <= index < MAX_HISTORY_ENTRIES: _fail('cursor-invalid')
    return 'source-item-' + str(index)


def _public_cursor(value):
    return dict(status='source-preserved' if value['phase'] == 'core' else 'preserving-source',
        phase=value['phase'], archive=str(ARCHIVE_ROOT / value['archiveId']),
        completed=value['next'], total=len(value['plan']))


def _bind_archive_receipt(directory, witness, transaction, value):
    raw = c._native_root_file(directory, 'receipt.json')[0]
    receipt = _json(raw)
    # Cursor creation precedes receipt binding; this exact prefix is resumable.
    if receipt.get('source', {}).get('phase') == 'prepared':
        _archive_receipt(receipt, witness, transaction)
    else:
        _archive_receipt(receipt, witness, transaction, value)
    wanted = dict(receipt, source=dict(receipt['source'], planSha256=value['planSha256'], phase=value['phase']))
    if wanted != receipt: _save_at(directory, 'receipt.json', wanted, expected=raw)


def _verify_prefix(metadata, archive, value, witness, publisher):
    """Reprove every live or preserved inode and reject unbound new names."""
    from pixel_macos_custody import protected_directory
    inv = witness['source']['inventory']
    if value['inventorySha256'] != _sha(c._native_encoded(inv)): _fail('cursor-unbound')
    expected_meta = set()
    if 'source-identity.json' in inv['metadata']:
        raw, inode = c._native_root_file(metadata, 'source-identity.json')
        if dict(inode=inode, sha256=_sha(raw)) != inv['metadata']['source-identity.json']:
            _fail('identity-changed')
        expected_meta.add('source-identity.json')
    expected_meta.add(CURSOR)
    # The first prewrite check is the only point where the cursor is absent.
    if c._native_at_inode(metadata, CURSOR) is None:
        if value['next'] != 0: _fail('cursor-missing')
        expected_meta.remove(CURSOR)
    expected_kits, expected_archive = set(), {'receipt.json'}
    private = inv['kind'] != CODE_ONLY_KIND
    for index, row in enumerate(value['plan']):
        origin = _row_origin(row)
        target = _destination(index)
        with protected_directory(origin.parent) as parent:
            source_inode = c._native_at_inode(parent, origin.name)
            target_inode = c._native_at_inode(archive, target)
            expected_inode = _entry_inode(row)
            if source_inode == expected_inode and target_inode is None and index >= value['next']:
                _verify_entry(parent, origin.name, row, witness, publisher)
                if row['kind'] == 'metadata': expected_meta.add(row['name'])
                if row['kind'] == 'kit-history': expected_kits.add(row['name'])
                if row['kind'] == 'kit': expected_kits.add(c.NATIVE_KIT.name)
            elif source_inode is None and target_inode == expected_inode and index <= value['next']:
                _verify_entry(archive, target, row, witness, publisher)
                expected_archive.add(target)
            else:
                _fail('archive-image-changed')
    actual_meta = {name for name in os.listdir(metadata) if name.startswith(_SOURCE_PREFIXES)
        or name == 'native-app-enrollment.json'}
    if actual_meta != expected_meta: _fail('archive-namespace-unbound')
    with protected_directory(c.NATIVE_KIT.parent) as kits:
        actual_kits = {name for name in os.listdir(kits) if name == c.NATIVE_KIT.name or name.startswith('.source-kit-')}
        if actual_kits != expected_kits: _fail('archive-kit-namespace-unbound')
    with protected_directory(c.NATIVE_DEFINITIONS) as units:
        if any(name.startswith(c.NATIVE_LABEL) for name in os.listdir(units)): _fail('actors-present')
    if set(os.listdir(archive)) != expected_archive: _fail('archive-contents-changed')
    if not private and any(os.path.lexists(path) for path in (c.NATIVE_STATE, c.NATIVE_ENV, c.NATIVE_LEGACY_ENV, c.NATIVE_LOGS)):
        _fail('code-only-state-created')
