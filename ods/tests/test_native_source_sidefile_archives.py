"""Closed archive reader on real FDs; explicit Darwin owner/ACL observations."""
# ruff: noqa: E402,F401,F811
import copy
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

if sys.platform != 'linux' or not Path('/proc/self/fd').is_dir():
    pytest.skip('Linux/procfs custody fixture, not physical Darwin', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_source_custody import native
from test_connection_deployment_paths import deployment
from test_native_source_installer_recovery import upgrade, access, CURRENT, CANDIDATE
from test_native_source_installer_custody import absent
from test_native_source_initial_backend import (initial, material, built, source, package,
    publication, helper, qualification_operation)
from pixel_connections import custody as c, native_retirement as retirement
from pixel_connections import native_executor as ex
import pixel_macos_custody as mac


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write(path, body):
    path.write_bytes(body)
    path.chmod(0o600)
    info = path.stat()
    return {'inode': [info.st_dev, info.st_ino], 'sha256': sha(body), 'size': len(body)}


def make_archive(n, monkeypatch, *, real_code=False, guard=False):
    uid, gid = n.f.bridge.owner.pw_uid, n.f.bridge.owner.pw_gid
    assert uid > 0 and gid > 0
    config = n.world / 'old-config'
    monkeypatch.setattr(c, 'NATIVE_SIDEFILE_CONFIG_ROOT', config)
    parent = config / str(uid)
    parent.mkdir(parents=True, mode=0o700)
    family = c.native_installer_source_family()
    monkeypatch.setattr(upgrade, 'SOURCE_ADDITIONS', family)
    monkeypatch.setattr(upgrade, 'SOURCE_PRIVATE_RECEIPT', str(c.NATIVE_META / 'source-runtime-installation.json'))
    # guard: a main-shaped origin, which also lacks the controller's admission guard.
    legacy = c._native_installer_schedule_additions() | (c._native_installer_guard_additions() if guard else set())
    records = [dict(path=path, before=None, after=('code:' + path).encode(),
        mode=0o600 if path == str(c.NATIVE_META / 'source-runtime-installation.json') else 0o644, gid=0)
        for path in sorted(family | legacy)]
    if real_code:
        # Representative public code bytes, never private installed config or
        # token data. Frozen before-images remain invented legacy fixtures.
        root = Path(__file__).resolve().parents[1]
        def code_image(path):
            for program in (c.NATIVE_PROGRAM, access.ACCESS_PROGRAM_ROOT):
                try:
                    relative = Path(path).relative_to(program)
                    break
                except ValueError:
                    continue
            else:
                return None
            for base in (root / 'bin', root / 'extensions/services/pixel-agent/host'):
                candidate = base / relative
                if candidate.is_file():
                    return candidate.read_bytes()
            return None
        for row in records:
            body = code_image(row['path'])
            if body is not None:
                row['after'] = body
        required, _optional = access._recovery_file_contract()
        existing = {row['path'] for row in records}
        for path, (mode, group) in sorted(required.items()):
            if path in existing:
                continue
            body = code_image(path)
            records.append(dict(path=path, before=b'invented legacy Core image',
                after=body if body is not None else b'invented candidate Core image', mode=mode, gid=group))
        assert len(records) <= 128
    family_sha = upgrade.source_family_hash(records)
    context = dict(schemaVersion=2, owner={'name': n.f.bridge.owner.pw_name, 'uid': uid, 'gid': gid, 'home': str(n.f.home)},
        upgrade_qualification={'currentDigest': CURRENT, 'candidateDigest': CANDIDATE},
        runtime_bundle={'digest': CANDIDATE, 'config_path': str(parent / 'openclaw.json'),
            'connection_sidefiles': {'gateway.env': "PIXEL_LIMB_EMAIL_ENABLED='0'\n", 'onboarding.json': '{}\n'}},
        source_lifecycle={'schemaVersion': 1, 'kind': 'ods-native-source-installer-intent', 'mode': 'absent',
            'nonce': 'e' * 32, 'currentDigest': CURRENT, 'candidateDigest': CANDIDATE,
            'ownerUid': uid, 'ownerGid': gid, 'familySha256': family_sha})
    raw = c._native_encoded(context)
    proof = dict(schemaVersion=1, kind='ods-native-source-installer-absence', transaction=sha(raw),
        currentDigest=CURRENT, candidateDigest=CANDIDATE, ownerUid=uid, ownerGid=gid,
        oldRuntimeCustodySha256='d' * 64, runtimeRoot=str(c.NATIVE_RUNTIMES / CURRENT / 'runtime'),
        sourceRoot=str(c.NATIVE_RUNTIMES / CURRENT), kit=None, metadata={}, identity={'kind': 'absent'},
        witness={'boot': '01234567-89AB-4DEF-8123-0123456789AB', 'loadedSourceJobs': 0, 'brokerProcesses': 0, 'brokerUid': None})
    addition = dict(schemaVersion=1, kind='ods-native-source-installer-addition', contextSha256=sha(raw),
        familySha256=family_sha, proof=proof, absentPaths=sorted(family))
    restored = upgrade.encode_recovery(records, current_digest=CURRENT, candidate_digest=CANDIDATE,
        allowed_paths={row['path'] for row in records}, source_addition=addition)
    restored['phase'] = 'restored'
    archive = n.metadata / (c._SIDEFILE_ARCHIVE_PREFIX + CANDIDATE)
    archive.mkdir(mode=0o700)
    parent_info, info = parent.stat(), archive.stat()
    authority = {}
    for label, body, original in [('context', raw, 'runtime-upgrade-context-' + CANDIDATE + '.json'),
            ('restored', c._native_encoded(restored), 'runtime-upgrade-' + CANDIDATE + '.completed.json')]:
        prior = write(n.metadata / original, body)
        authority[label] = dict(write(archive / (label + '.json'), body), sourceName=original, sourceInode=prior['inode'])
    receipt = dict(schemaVersion=1, kind='ods-native-connection-sidefiles', ownerUid=uid, ownerGid=gid,
        configPath=context['runtime_bundle']['config_path'], bundleSha256=CANDIDATE,
        parent=[parent_info.st_dev, parent_info.st_ino],
        files={key: sha(text.encode()) for key, text in context['runtime_bundle']['connection_sidefiles'].items()},
        children={key: {'inode': None, 'ready': False} for key in ('gateway.env', 'onboarding.json')})
    archived_files = {}
    for name, body, group in [('source-sidefiles.json', c._native_encoded(receipt), 0), ('gateway.env', b'', gid)]:
        image = write(archive / name, body)
        n.owners[tuple(image['inode'])] = (0, group)
        archived_files[name] = dict(image, uid=0, gid=group, mode=0o600)
    manifest = dict(schemaVersion=1, kind='ods-native-source-sidefiles-archive', currentDigest=CURRENT,
        candidateDigest=CANDIDATE, owner={'uid': uid, 'gid': gid}, directory={'inode': [info.st_dev, info.st_ino]},
        parent={'path': str(parent), 'inode': [parent_info.st_dev, parent_info.st_ino]}, authority=authority,
        files=archived_files, absent=['onboarding.json'], origin='unbound-empty-inherited-group')
    image = write(archive / 'manifest.json', c._native_encoded(manifest))
    ledger = dict(schemaVersion=1, kind='ods-native-source-sidefiles-archives', archives={CANDIDATE: {
        'directory': manifest['directory'], 'manifest': {key: image[key] for key in ('inode', 'sha256')}}})
    write(n.metadata / c._SIDEFILE_ARCHIVES, c._native_encoded(ledger))
    return SimpleNamespace(n=n, path=archive, manifest=manifest, ledger=ledger, context=context, restored=restored,
        uid=uid, gid=gid)


def read(a):
    return c.native_sidefile_archives(owner_uid=a.uid, owner_gid=a.gid)


def make_bound_archive(n, monkeypatch, *, guard=False):
    """A second, ready-but-unpublished attempt retains the first exact capsule."""
    previous = make_archive(n, monkeypatch, guard=guard)
    n.owners[tuple(previous.manifest['parent']['inode'])] = (previous.uid, previous.gid)
    candidate = 'c' * 64
    context = copy.deepcopy(previous.context)
    for item in (context['upgrade_qualification'], context['source_lifecycle']):
        item['candidateDigest'] = candidate
    context['runtime_bundle']['digest'] = candidate
    records = [{**{key: row[key] for key in ('path', 'mode', 'gid')},
        'before': None if row['before'] is None else base64.b64decode(row['before']),
        'after': base64.b64decode(row['after'])} for row in previous.restored['files']]
    runtime_record = next(row for row in records if row['path'] == str(c.NATIVE_META / 'source-runtime-installation.json'))
    runtime_record['after'] = c._native_encoded(dict(schemaVersion=1, kind='ods-native-source-runtime-publication',
        bundleSha256=candidate, sourceRef='f' * 40, odsSourceCommit='e' * 40,
        componentSha256='1' * 64, manifestSha256='2' * 64, descriptorSha256='3' * 64, launcherSha256='4' * 64))
    context['source_lifecycle']['familySha256'] = upgrade.source_family_hash(records)
    raw = c._native_encoded(context)
    addition = copy.deepcopy(previous.restored['sourceAddition'])
    addition['familySha256'] = context['source_lifecycle']['familySha256']
    addition['contextSha256'] = addition['proof']['transaction'] = sha(raw)
    addition['proof']['candidateDigest'] = candidate
    journal = upgrade.encode_recovery(records, current_digest=CURRENT, candidate_digest=candidate,
        allowed_paths={row['path'] for row in records}, source_addition=addition)
    assert journal['schemaVersion'] == (6 if guard else 5)
    journal['phase'] = 'active'
    archive = n.metadata / (c._SIDEFILE_ARCHIVE_PREFIX + candidate)
    archive.mkdir(mode=0o700)
    info = archive.stat()
    authority = {}
    for label, body, original in [('context', raw, 'runtime-upgrade-context-' + candidate + '.json'),
            ('journal', c._native_encoded(journal), 'runtime-upgrade.json')]:
        prior = write(n.metadata / original, body)
        authority[label] = dict(write(archive / (label + '.json'), body), sourceName=original, sourceInode=prior['inode'])
    files = {}
    for name, text in context['runtime_bundle']['connection_sidefiles'].items():
        image = write(archive / name, text.encode())
        n.owners[tuple(image['inode'])] = (previous.uid, previous.gid)
        files[name] = dict(image, uid=previous.uid, gid=previous.gid, mode=0o600)
    receipt = json.loads((previous.path / 'source-sidefiles.json').read_bytes())
    receipt['bundleSha256'] = candidate
    receipt['children'] = {key: {'inode': files[key]['inode'], 'ready': True} for key in files}
    runtime = next(row['after'] for row in records if row['path'] == str(c.NATIVE_META / 'source-runtime-installation.json'))
    for name, body in [('source-sidefiles.json', c._native_encoded(receipt)), ('source-runtime-installation.json', runtime)]:
        files[name] = dict(write(archive / name, body), uid=0, gid=0, mode=0o600)
    ledger_raw = (n.metadata / c._SIDEFILE_ARCHIVES).read_bytes()
    ledger_info = (n.metadata / c._SIDEFILE_ARCHIVES).stat()
    history = dict(ledger={'inode': [ledger_info.st_dev, ledger_info.st_ino], 'sha256': sha(ledger_raw)},
        archives=copy.deepcopy(previous.ledger['archives']))
    manifest = dict(schemaVersion=2, kind='ods-native-source-sidefiles-archive', currentDigest=CURRENT,
        candidateDigest=candidate, owner={'uid': previous.uid, 'gid': previous.gid},
        directory={'inode': [info.st_dev, info.st_ino]}, parent=copy.deepcopy(previous.manifest['parent']),
        authority=authority, files=files, absent=[], origin='bound-ready-before-source-publication', history=history)
    image = write(archive / 'manifest.json', c._native_encoded(manifest))
    ledger = copy.deepcopy(previous.ledger)
    ledger['archives'][candidate] = {'directory': manifest['directory'],
        'manifest': {key: image[key] for key in ('inode', 'sha256')}}
    write(n.metadata / c._SIDEFILE_ARCHIVES, c._native_encoded(ledger))
    return SimpleNamespace(n=n, path=archive, manifest=manifest, ledger=ledger, context=context, journal=journal,
        records=records, uid=previous.uid, gid=previous.gid, previous=previous, candidate=candidate)


def snapshot(a):
    return {str(path.relative_to(a.n.metadata)): (path.read_bytes(), path.stat().st_ino, path.stat().st_mode)
        for path in a.n.metadata.rglob('*') if path.is_file() and not path.is_symlink()}


def save_manifest(a):
    image = write(a.path / 'manifest.json', c._native_encoded(a.manifest))
    a.ledger['archives'][a.manifest['candidateDigest']]['manifest'] = {key: image[key] for key in ('inode', 'sha256')}
    write(a.n.metadata / c._SIDEFILE_ARCHIVES, c._native_encoded(a.ledger))


@pytest.fixture
def archived(native, monkeypatch, request):
    return make_archive(native, monkeypatch, real_code=getattr(request, 'param', False))


@pytest.mark.parametrize('archived', [False, True], indirect=True)
def test_archive_is_readonly_and_survives_retired_original_authority(archived):
    a = archived
    before = snapshot(a)
    view = read(a)
    assert set(view['archives']) == {a.path.name} and view['ledger']['inode']
    assert snapshot(a) == before
    # A normal retire-runtime can move/remove these originals; private copies
    # remain independently bound. No resolution of a broad history prefix.
    for item in a.manifest['authority'].values():
        (a.n.metadata / item['sourceName']).rename(a.n.world / item['sourceName'])
    assert read(a) == view
    assert set(c._native_sidefile_archive_names(view)) == {a.path.name, c._SIDEFILE_ARCHIVES}


@pytest.mark.parametrize('fault', ['body', 'inode', 'mode', 'group', 'acl', 'hardlink', 'symlink', 'directory',
    'missing', 'extra', 'manifest', 'ledger', 'owner', 'context', 'restored-phase', 'restored-body', 'receipt-ready', 'receipt-bool-inode', 'source-inode'])
def test_archive_refuses_drift_and_forged_capsules_without_repair(archived, fault):
    a = archived
    path = a.path / 'gateway.env'
    if fault == 'body': path.write_bytes(b'private existing body')
    elif fault == 'inode':
        replacement = a.n.world / 'replacement'
        replacement.write_bytes(b'')
        replacement.chmod(0o600)
        info = replacement.stat()
        a.n.owners[(info.st_dev, info.st_ino)] = (0, a.gid)
        os.replace(replacement, path)
    elif fault == 'mode': path.chmod(0o640)
    elif fault == 'group': a.n.owners[tuple(a.manifest['files']['gateway.env']['inode'])] = (0, 0)
    elif fault == 'acl':
        key = tuple(a.manifest['files']['gateway.env']['inode'])
        a.n.acl.acls[key] = {'entries': [{'tag': 1, 'mask': 2, 'flags': 0, 'uuid': b'R' * 16}], 'flags': 0}
    elif fault == 'hardlink': os.link(path, a.n.world / 'alias')
    elif fault == 'symlink':
        path.rename(a.n.world / 'retained')
        path.symlink_to(a.n.world / 'retained')
    elif fault == 'directory': a.path.chmod(0o755)
    elif fault == 'missing': path.rename(a.n.world / 'retained')
    elif fault == 'extra': write(a.path / 'unknown', b'preserve foreign data')
    elif fault == 'manifest': (a.path / 'manifest.json').write_bytes(b'{}')
    elif fault == 'ledger': (a.n.metadata / c._SIDEFILE_ARCHIVES).write_bytes(b'{}')
    elif fault == 'owner': a.uid += 1
    else:
        if fault in ('context', 'restored-phase', 'restored-body'):
            label = 'context' if fault == 'context' else 'restored'
            value = copy.deepcopy(a.context if label == 'context' else a.restored)
            if fault == 'context': value['source_lifecycle']['mode'] = 'withdrawal'
            elif fault == 'restored-phase': value['phase'] = 'active'
            else: value['files'][0]['afterSha256'] = '0' * 64
            image = write(a.path / (label + '.json'), c._native_encoded(value))
            a.manifest['authority'][label].update(image)
        elif fault in ('receipt-ready', 'receipt-bool-inode'):
            value = json.loads((a.path / 'source-sidefiles.json').read_bytes())
            if fault == 'receipt-ready':
                value['children']['gateway.env'] = {'inode': [1, 2], 'ready': True}
            else:
                a.manifest['parent']['inode'][0] = 1
                value['parent'][0] = True
            a.manifest['files']['source-sidefiles.json'].update(write(a.path / 'source-sidefiles.json', c._native_encoded(value)))
        else: a.manifest['authority']['context']['sourceInode'] = a.manifest['authority']['context']['inode']
        save_manifest(a)
    before = snapshot(a)
    with pytest.raises((c.CustodyError, OSError, ValueError)):
        read(a)
    assert snapshot(a) == before


@pytest.mark.parametrize('retained', [False, True])
@pytest.mark.parametrize('bound', [False, True])
def test_legacy_absence_validates_history_without_turning_it_into_live_source(absent, monkeypatch, retained, bound):
    n = absent
    if not retained:
        (n.metadata / 'source-identity.json').unlink()
        n.records.update(Users=None, Groups=None)
    before = c._native_installer_absence_state(n.f.bridge)
    if not retained:
        assert before[:3] == (None, {}, {'kind': 'absent'})
    a = (make_bound_archive if bound else make_archive)(n, monkeypatch)
    assert c._native_installer_absence_state(n.f.bridge) == before
    (n.metadata / c._SIDEFILE_ARCHIVES).rename(n.world / 'saved-ledger')
    with pytest.raises(c.CustodyError, match='metadata-preserved'):
        c._native_installer_absence_state(n.f.bridge)
    assert a.path.is_dir()


@pytest.mark.parametrize('grant', [False, True])
@pytest.mark.parametrize('bound', [False, True])
def test_actual_initial_prepare_keeps_archive_and_grant_refusals(initial, monkeypatch, grant, bound):
    n = initial
    a = (make_bound_archive if bound else make_archive)(n, monkeypatch)
    archive_before = {path.name: (path.read_bytes(), path.stat().st_ino) for path in a.path.iterdir()}
    publish, reached = helper.publish_initial_generation, []
    if grant:
        def publish_with_invalid_grant(selection):
            text = c.env_text(c.parse_env(c.NATIVE_ENV.read_text()), {'PIXEL_LIMB_EMAIL_ENABLED': '1'})
            c.native_publish_environment(selection, text)
            reached.append(True)
            return publish(selection)
        monkeypatch.setattr(helper, 'publish_initial_generation', publish_with_invalid_grant)
        with pytest.raises((c.CustodyError, ValueError)):
            n.backend.prepare(reader=n.f.bridge.owner.pw_name)
        assert reached and not (n.metadata / 'source-initial-generation.json').exists()
    else:
        n.backend.prepare(reader=n.f.bridge.owner.pw_name)
        record = json.loads((n.metadata / 'source-initial-generation.json').read_bytes())
        assert record['phase'] == 'committed'
        assert helper.verify_initial_generation_child(n.selected)['phase'] == 'committed-inactive'
        assert not (c.NATIVE_STATE / 'private/google-token.json').exists()
    assert read(a)['archives'][a.path.name]['manifest'] == a.manifest
    assert {path.name: (path.read_bytes(), path.stat().st_ino) for path in a.path.iterdir()} == archive_before


@pytest.mark.parametrize('drift', [False, True])
def test_retirement_captures_complete_capsule_only_after_bound_reader(material, monkeypatch, drift):
    n = material
    a = make_archive(n, monkeypatch)
    def publisher(selection):
        helper._material(selection)  # Only protected import location is modeled.
        return helper
    monkeypatch.setattr(ex, '_publisher', publisher)
    metadata = {name: {'inode': [path.stat().st_dev, path.stat().st_ino], 'sha256': sha(path.read_bytes())}
        for name in retirement._CURRENT if (path := n.metadata / name).is_file()}
    actual = retirement._capture_node
    mutated = []
    def capture(directory, name, **kwargs):
        if drift and name == a.path.name and not mutated:
            (a.path / 'context.json').write_bytes(b'preserve changed capsule')
            mutated.append(True)
        return actual(directory, name, **kwargs)
    monkeypatch.setattr(retirement, '_capture_node', capture)
    if drift:
        with pytest.raises(c.CustodyError, match='history-sidefile-archive-changed'):
            retirement._capture_histories(n.selected, metadata, private=None)
        assert mutated and (a.path / 'context.json').read_bytes() == b'preserve changed capsule'
    else:
        before = snapshot(a)
        history = retirement._capture_histories(n.selected, metadata, private=None)
        node = history['metadata'][a.path.name]
        assert set(node['entries']) == {'manifest.json', 'context.json', 'restored.json', 'source-sidefiles.json', 'gateway.env'}
        assert node['inode'] == a.manifest['directory']['inode']
        assert node['entries']['gateway.env']['gid'] == a.gid
        assert node['entries']['gateway.env']['inode'] == a.manifest['files']['gateway.env']['inode']
        assert c._SIDEFILE_ARCHIVES in history['metadata']
        assert snapshot(a) == before


def test_bound_ready_archive_preserves_both_attempts_without_live_source(absent, monkeypatch):
    a = make_bound_archive(absent, monkeypatch)
    before = snapshot(a)
    view = read(a)
    assert set(view['archives']) == {a.path.name, a.previous.path.name}
    assert view['archives'][a.path.name]['manifest'] == a.manifest
    assert not c.NATIVE_KIT.exists()  # Frozen failed prepublication; not complete Source.
    assert snapshot(a) == before
    for image in a.manifest['authority'].values():
        (a.n.metadata / image['sourceName']).rename(a.n.world / image['sourceName'])
    assert read(a) == view


@pytest.mark.parametrize('fault', [None, 'schema-five'])
def test_bound_ready_archive_reads_a_main_shaped_guard_journal_only_as_schema_six(absent, monkeypatch, fault):
    a = make_bound_archive(absent, monkeypatch, guard=True)
    assert c._native_installer_guard_additions() <= {row['path'] for row in a.records if row['before'] is None}
    if fault:
        journal = dict(copy.deepcopy(a.journal), schemaVersion=5)
        a.manifest['authority']['journal'].update(write(a.path / 'journal.json', c._native_encoded(journal)))
        save_manifest(a)
        before = snapshot(a)
        with pytest.raises((c.CustodyError, OSError, ValueError)):
            read(a)
        assert snapshot(a) == before
        return
    before = snapshot(a)
    view = read(a)
    assert set(view['archives']) == {a.path.name, a.previous.path.name}
    assert view['archives'][a.path.name]['manifest'] == a.manifest
    assert snapshot(a) == before


@pytest.mark.parametrize('fault', ['uid', 'gid', 'mode', 'inode', 'acl', 'hardlink', 'symlink', 'extra',
    'ready-false', 'ready-integer', 'child-inode', 'journal-phase', 'journal-body', 'runtime-image',
    'history-missing', 'history-image', 'history-self', 'authority-name'])
def test_bound_ready_archive_refuses_changes_without_modifying_data(native, monkeypatch, fault):
    a = make_bound_archive(native, monkeypatch)
    path = a.path / 'gateway.env'
    image = a.manifest['files']['gateway.env']
    if fault == 'uid': a.n.owners[tuple(image['inode'])] = (0, a.gid)
    elif fault == 'gid': a.n.owners[tuple(image['inode'])] = (a.uid, 0)
    elif fault == 'mode': path.chmod(0o644)
    elif fault == 'inode':
        replacement = a.n.world / 'replacement'
        info = write(replacement, path.read_bytes())
        a.n.owners[tuple(info['inode'])] = (a.uid, a.gid)
        os.replace(replacement, path)
    elif fault == 'acl':
        a.n.acl.acls[tuple(image['inode'])] = {'entries': [{'tag': 1, 'mask': 2, 'flags': 0, 'uuid': b'R' * 16}], 'flags': 0}
    elif fault == 'hardlink': os.link(path, a.n.world / 'alias')
    elif fault == 'symlink':
        path.rename(a.n.world / 'retained')
        path.symlink_to(a.n.world / 'retained')
    elif fault == 'extra': write(a.path / 'foreign', b'preserved')
    else:
        if fault.startswith('ready-') or fault == 'child-inode':
            receipt = json.loads((a.path / 'source-sidefiles.json').read_bytes())
            if fault == 'child-inode': receipt['children']['gateway.env']['inode'][1] += 1
            else: receipt['children']['gateway.env']['ready'] = False if fault == 'ready-false' else 1
            a.manifest['files']['source-sidefiles.json'].update(write(a.path / 'source-sidefiles.json', c._native_encoded(receipt)))
        elif fault.startswith('journal-'):
            journal = copy.deepcopy(a.journal)
            if fault == 'journal-phase': journal['phase'] = 'restored'
            else: journal['files'][0]['afterSha256'] = '0' * 64
            a.manifest['authority']['journal'].update(write(a.path / 'journal.json', c._native_encoded(journal)))
        elif fault == 'runtime-image':
            a.manifest['files']['source-runtime-installation.json'].update(
                write(a.path / 'source-runtime-installation.json', b'other preserved installation'))
        elif fault == 'history-missing': a.ledger['archives'].pop(CANDIDATE)
        elif fault == 'history-image': a.manifest['history']['archives'][CANDIDATE]['manifest']['sha256'] = '0' * 64
        elif fault == 'history-self': a.manifest['history']['archives'][a.candidate] = a.ledger['archives'][a.candidate]
        else: a.manifest['authority']['journal']['sourceName'] = 'runtime-upgrade-other.json'
        save_manifest(a)
    before = snapshot(a)
    with pytest.raises((c.CustodyError, OSError, ValueError)):
        read(a)
    assert snapshot(a) == before


@pytest.mark.parametrize('fault', [None, 'uid', 'body', 'foreign-leaf'])
def test_bound_ready_retirement_keeps_exact_owner_uid_through_recapture(material, monkeypatch, fault):
    n = material
    a = make_bound_archive(n, monkeypatch)
    def publisher(selection):
        helper._material(selection)
        return helper
    monkeypatch.setattr(ex, '_publisher', publisher)
    metadata = {name: {'inode': [path.stat().st_dev, path.stat().st_ino], 'sha256': sha(path.read_bytes())}
        for name in retirement._CURRENT if (path := n.metadata / name).is_file()}
    history = retirement._capture_histories(n.selected, metadata, private=None)
    assert history['schemaVersion'] == 2
    node = history['metadata'][a.path.name]
    assert set(node['entries']) == set(a.manifest['files']) | {'manifest.json', 'context.json', 'journal.json'}
    for name in ('gateway.env', 'onboarding.json'):
        assert node['entries'][name]['uid'] == a.uid
        assert node['entries'][name]['inode'] == a.manifest['files'][name]['inode']
    # No expected ownership map means the original Root-only capture remains closed.
    with mac.protected_directory(n.metadata) as directory:
        with pytest.raises(c.CustodyError, match='history-custody-changed'):
            retirement._capture_node(directory, a.path.name, remaining=[1024, 32 * 1024 * 1024])
    if fault == 'uid': n.owners[tuple(a.manifest['files']['gateway.env']['inode'])] = (a.uid + 1, a.gid)
    elif fault == 'body': (a.path / 'onboarding.json').write_bytes(b'changed')
    elif fault == 'foreign-leaf':
        node['entries']['context.json']['uid'] = a.uid
    if fault:
        with pytest.raises(c.CustodyError):
            retirement.verify_history(n.selected, {'history': history})
    else:
        before = snapshot(a)
        retirement.verify_history(n.selected, {'history': history})
        row = {'kind': 'metadata', 'name': a.path.name, 'expected': node}
        with mac.protected_directory(n.metadata) as directory:
            retirement._verify_entry(directory, a.path.name, row, {}, helper)
        assert snapshot(a) == before
        # The retirement cursor verifies the identical node after its rename;
        # the private capsule keeps both original owner UIDs at that location.
        destination = n.world / 'retirement-destination'
        destination.mkdir(mode=0o700)
        a.path.rename(destination / 'retained')
        fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            retirement._verify_entry(fd, 'retained', row, {}, helper)
            moved = retirement._capture_node(fd, 'retained', remaining=[1024, 32 * 1024 * 1024], expected=node)
            assert moved == node
        finally:
            os.close(fd)
