"""Stage and publish a reproducible native Pixel runtime without executing it.

The manifest binds every entry, including internal relative symlinks. Optional
release selection proves only recorded ODS source bindings, not a whole release
or a running process. Other inputs remain explicitly selected local artifacts.
Publication requires a selected digest and root custody; loader compatibility,
configuration activation and runtime behavior need separate gates.
"""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile


MANIFEST = 'bundle.json'
RELEASE_SELECTION = 'ods-release-selection.json'
MAX_SELECTION = 2 * 1024 * 1024
SELECTION_KIND = 'ods-pixel-expected-release-selection'
SELECTION_SCOPE = 'expected-artifacts-not-running'
UNKNOWN_SOURCE_REASONS = {'source-unavailable', 'source-dirty', 'source-ref-mismatch',
    'source-inputs-unmapped', 'source-bytes-mismatch', 'legacy-bundle',
    'service-source-unavailable', 'source-phase-mismatch'}
SOURCE_SCOPE = 'recorded-bindings-only'
ODS_SERVICE_SOURCES = {
    'manager/extension_manager.py': 'extensions/services/pixel-agent/host/extension_manager.py',
    'manager/unix_peer.py': 'extensions/services/pixel-agent/host/unix_peer.py',
    'promoter/artifact_promoter.py': 'extensions/services/pixel-agent/host/artifact_promoter.py',
    'promoter/unix_peer.py': 'extensions/services/pixel-agent/host/unix_peer.py',
    'promoter/pixel_macos_custody.py': 'bin/pixel_macos_custody.py',
    'helpers/extension_search.py': 'extensions/services/pixel-agent/host/extension_search.py',
    'helpers/system_observe.py': 'extensions/services/pixel-agent/host/system_observe.py',
    'helpers/preview_inspection.py': 'extensions/services/pixel-agent/host/preview_inspection.py',
    'helpers/preview_inspection_protocol.py': 'extensions/services/pixel-agent/host/preview_inspection_protocol.py',
    'helpers/workspace_preview.py': 'extensions/services/pixel-agent/host/workspace_preview.py',
    'helpers/unix_peer.py': 'extensions/services/pixel-agent/host/unix_peer.py',
}
GENERATED_SERVICE_ARTIFACTS = {'operations/policy.json', 'helpers/extension-catalog.json', 'helpers/preview-inspection.json'}
INSPECTION_SERVICE_ARTIFACTS = {'helpers/preview_inspection.py', 'helpers/preview_inspection_protocol.py',
    'helpers/workspace_preview.py', 'helpers/unix_peer.py', 'helpers/preview-inspection.json'}
MAX_ENTRIES = 200000
MAX_MANIFEST = 32 * 1024 * 1024
INSTALL_ROOT = Path('/usr/local/libexec/ods-pixel-runtimes')
STREAM_PROGRESS_FILE = 'dist/selection-BEwSQKM-.js'
STREAM_PROGRESS_SOURCE_SHA256 = 'ae83457af1947f3eaf3e08f8fbde869a1c023a80acfbf0f27d3db887af1d36d3'
REPAIR_ROOT = Path(__file__).resolve().parents[3] / 'extensions/services/pixel-agent/host'
SHARED_REPAIRS = (
    ('openclaw-tool-recovery.json', 'tool-loop-detection-C0oQKkXZ.js'),
    ('openclaw-completion-recovery.json', 'agent-command-DeS125kF.js'),
    ('openclaw-image-envelope.json', 'tool-search-BInRpkE3.js'),
    ('openclaw-compaction-export.json', 'embedded-agent-subscribe.handlers.compaction.runtime.js'),
    ('openclaw-compaction-idle.json', 'sessions-KE_Xmzwf.js'),
    ('openclaw-compaction-resume.json', 'sessions-CZbwb3_c.js'),
    ('openclaw-read-range.json', 'openclaw-tools-iHHy99PD.js'),
    ('openclaw-compaction-budget.json', 'selection-BEwSQKM-.js'),
)


class BundleError(ValueError):
    pass


def _relative(value):
    if (type(value) is not str or not value or value.startswith('/')
            or any(part in ('', '.', '..') for part in value.split('/'))
            or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
        raise BundleError('invalid-bundle-path')
    return value


def _encode(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n').encode()


def _git(root, *args, limit=MAX_SELECTION):
    result = subprocess.run(['git', '--no-replace-objects', '--no-optional-locks', '--no-pager',
        '-c', 'core.fsmonitor=false', '-c', 'core.untrackedCache=false', *args],
        cwd=root, stdin=subprocess.DEVNULL, capture_output=True, timeout=20)
    if result.returncode or len(result.stdout) > limit:
        raise ValueError('source-unavailable')
    return result.stdout


def _git_blob(body):
    return hashlib.sha1(b'blob ' + str(len(body)).encode() + b'\0' + body).hexdigest()


def _service_source_prefix(bindings):
    """All service mappings must describe one repository-relative ODS root."""
    prefixes = set()
    for name, record in bindings.items():
        source, relative = _relative(record['source']), ODS_SERVICE_SOURCES[name]
        if source != relative and not source.endswith('/' + relative):
            raise BundleError('service-source-binding-changed')
        prefixes.add(source[:-len(relative)])
    if len(prefixes) != 1:
        raise BundleError('service-source-binding-changed')
    return prefixes.pop()


def service_source_provenance(selected, files):
    value = {'scope': SOURCE_SCOPE,
        'odsSource': {'state': 'unknown', 'commit': None, 'reason': selected['reason']},
        'sourceBindings': {},
        'generatedArtifacts': {name: {'sha256': hashlib.sha256(files[name]).hexdigest(), 'sourceState': 'unknown'}
                               for name in sorted(GENERATED_SERVICE_ARTIFACTS)}}
    if selected['reason'] is not None:
        return value
    bindings = {}
    for name, relative in ODS_SERVICE_SOURCES.items():
        source = selected['prefix'] + relative
        mode, oid = selected['objects'].get(source, (None, None))
        if mode not in ('100644', '100755') or oid != _git_blob(files[name]):
            value['odsSource']['reason'] = 'source-bytes-mismatch'
            return value
        bindings[name] = {'source': source, 'sha256': hashlib.sha256(files[name]).hexdigest()}
    value['odsSource'] = {'state': 'verified-source-bindings', 'commit': selected['commit'], 'reason': None}
    value['sourceBindings'] = bindings
    return value


def validate_service_manifest_provenance(manifest):
    """Validate an expected service snapshot, not the installed service bytes."""
    keys = {'schemaVersion', 'status', 'requiresServiceQualification', 'pixelSourceRef',
            'candidateConfigSha256', 'files'}
    names = set(ODS_SERVICE_SOURCES) | GENERATED_SERVICE_ARTIFACTS | {'operations/broker.py'}
    # Existing approved bundles remain readable for migration, rollback and
    # uninstall. New staging always emits the complete inspection capability;
    # partial inspection bundles are never an accepted legacy contract.
    if isinstance(manifest, dict) and isinstance(manifest.get('files'), dict) \
            and set(manifest['files']) == names - INSPECTION_SERVICE_ARTIFACTS:
        names -= INSPECTION_SERVICE_ARTIFACTS
    generated = GENERATED_SERVICE_ARTIFACTS & names
    mapped = set(ODS_SERVICE_SOURCES) & names
    if (type(manifest) is not dict or set(manifest) not in (keys, keys | {'sourceProvenance'})
            or type(manifest['schemaVersion']) is not int or manifest['schemaVersion'] != 1
            or manifest['status'] != 'staged' or manifest['requiresServiceQualification'] is not True
            or type(manifest['pixelSourceRef']) is not str or not re.fullmatch('[a-f0-9]{40}', manifest['pixelSourceRef'])
            or type(manifest['candidateConfigSha256']) is not str or not re.fullmatch('[a-f0-9]{64}', manifest['candidateConfigSha256'])
            or type(manifest['files']) is not dict or set(manifest['files']) != names):
        raise BundleError('invalid-service-source-provenance')
    for record in manifest['files'].values():
        if (type(record) is not dict or set(record) != {'sha256', 'bytes'}
                or type(record['bytes']) is not int or not 0 < record['bytes'] <= MAX_SELECTION
                or type(record['sha256']) is not str or not re.fullmatch('[a-f0-9]{64}', record['sha256'])):
            raise BundleError('invalid-service-source-provenance')
    provenance = manifest.get('sourceProvenance')
    if 'sourceProvenance' not in manifest:
        return {'scope': SOURCE_SCOPE, 'odsSource': {'state': 'unknown', 'commit': None, 'reason': 'legacy-bundle'}}
    if (type(provenance) is not dict or set(provenance) != {'scope', 'odsSource', 'sourceBindings', 'generatedArtifacts'}
            or provenance['scope'] != SOURCE_SCOPE or type(provenance['odsSource']) is not dict
            or set(provenance['odsSource']) != {'state', 'commit', 'reason'}
            or type(provenance['sourceBindings']) is not dict or type(provenance['generatedArtifacts']) is not dict
            or set(provenance['generatedArtifacts']) != generated):
        raise BundleError('invalid-service-source-provenance')
    for name, record in provenance['generatedArtifacts'].items():
        if record != {'sha256': manifest['files'][name]['sha256'], 'sourceState': 'unknown'}:
            raise BundleError('invalid-generated-source-provenance')
    source = provenance['odsSource']
    if source['state'] == 'unknown':
        if source['commit'] is not None or type(source['reason']) is not str or source['reason'] not in UNKNOWN_SOURCE_REASONS or provenance['sourceBindings']:
            raise BundleError('invalid-service-source-provenance')
    elif (source['state'] != 'verified-source-bindings' or source['reason'] is not None
            or type(source['commit']) is not str or not re.fullmatch('[a-f0-9]{40}', source['commit'])
            or set(provenance['sourceBindings']) != mapped):
        raise BundleError('invalid-service-source-provenance')
    for name, record in provenance['sourceBindings'].items():
        if (type(record) is not dict or set(record) != {'source', 'sha256'}
                or _relative(record['source']) != record['source']
                or record['sha256'] != manifest['files'][name]['sha256']):
            raise BundleError('service-source-binding-changed')
    if provenance['sourceBindings']:
        _service_source_prefix(provenance['sourceBindings'])
    return provenance


def _selected_release_source(root, plugin_indices, *, wrapper, repairs, expected_ref=None, source_paths=()):
    """Pin Git identity before copying; later comparisons use this commit only.

    A checkout, installed archive, or external plugin may legitimately lack
    provable provenance. That limits identity reporting, not installation.
    The standard bootstrap supplies its clean source checkout separately from
    the installed copy. It supplies no trusted commit label: Git object IDs and
    subsequent comparisons with the actual copied bytes establish the binding.
    """
    selected = {'reason': 'source-unavailable', 'commit': None, 'objects': {},
                'plugins': list(plugin_indices), 'repairInputs': {}}
    try:
        bootstrap_source = os.environ.get('ODS_BOOTSTRAP_SOURCE_DIR')
        if bootstrap_source is not None:
            if not bootstrap_source or not Path(bootstrap_source).is_absolute():
                return selected
            root = bootstrap_source
        root = Path(root).resolve(strict=True)
        repository = Path(_git(root, 'rev-parse', '--show-toplevel', limit=4096).decode().strip()).resolve(strict=True)
        prefix = root.relative_to(repository).as_posix()
        prefix = '' if prefix == '.' else prefix + '/'
        # Reuse the service-stage selection when present. Never replace it with
        # a later checkout HEAD after service source has already been copied.
        commit = expected_ref if expected_ref is not None else _git(root, 'rev-parse', '--verify', 'HEAD^{commit}', limit=128).decode().strip()
        if type(commit) is not str or not re.fullmatch('[a-f0-9]{40}', commit):
            return selected
        if _git(repository, 'status', '--porcelain=v1', '--untracked-files=all'):
            return {**selected, 'reason': 'source-dirty'}
        if not plugin_indices and not source_paths:
            return {**selected, 'reason': 'source-inputs-unmapped'}
        inputs = (['extensions/services/pixel-agent/plugin'] if plugin_indices else []) + sorted(source_paths)
        if wrapper:
            inputs.append('extensions/services/pixel-agent/host/cancellable-exec.sh')
        if repairs:
            inputs.extend('extensions/services/pixel-agent/host/' + name for name, _ in SHARED_REPAIRS)
        objects = {}
        for item in _git(repository, 'ls-tree', '-r', '-z', commit, '--',
                         *(prefix + name for name in inputs)).split(b'\0'):
            if not item:
                continue
            metadata, name = item.split(b'\t', 1)
            mode, kind, oid = metadata.decode('ascii').split(' ')
            name = name.decode('utf-8')
            if kind != 'blob' or mode not in ('100644', '100755', '120000'):
                return {**selected, 'reason': 'source-inputs-unmapped'}
            objects[name] = (mode, oid)
        repair_inputs = {}
        if repairs:
            for name, _ in SHARED_REPAIRS:
                relative = prefix + 'extensions/services/pixel-agent/host/' + name
                with os.fdopen(_open_file(REPAIR_ROOT, name), 'rb') as handle:
                    body = handle.read(MAX_SELECTION + 1)
                if len(body) > MAX_SELECTION or objects.get(relative, (None, None))[1] != _git_blob(body):
                    return {**selected, 'reason': 'source-bytes-mismatch'}
                repair_inputs[name] = {'source': relative, 'sha256': hashlib.sha256(body).hexdigest()}
        return {**selected, 'reason': None, 'commit': commit, 'prefix': prefix,
                'objects': objects, 'repairInputs': repair_inputs}
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
        return selected


def _artifact_source_record(root, path, record):
    if record[0] == 'link':
        body = os.readlink(Path(root) / path).encode('utf-8')
        return '120000', _git_blob(body), hashlib.sha256(body).hexdigest()
    if record[0] != 'file':
        raise ValueError('source-inputs-unmapped')
    digest, blob = hashlib.sha256(), hashlib.sha1(b'blob ' + str(record[2]).encode() + b'\0')
    with os.fdopen(_open_file(root, path), 'rb') as handle:
        before = os.fstat(handle.fileno())
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
            blob.update(block)
        if _signature(before) != _signature(os.fstat(handle.fileno())) or digest.hexdigest() != record[3]:
            raise ValueError('source-bytes-mismatch')
    return '100755' if record[1] == 0o755 else '100644', blob.hexdigest(), digest.hexdigest()


def _release_selection(root, entries, selected, *, pixel_ref, services_digest, repairs, service_manifest=None):
    value = {'schemaVersion': 1, 'kind': SELECTION_KIND, 'scope': SELECTION_SCOPE,
        'sourceScope': SOURCE_SCOPE,
        'odsSource': {'state': 'unknown', 'commit': None, 'reason': selected['reason']},
        'pixelSourceRevision': pixel_ref, 'serviceBundleDigest': services_digest,
        'odsPluginDirectories': ['plugins/' + str(index) for index in selected['plugins']],
        'sourceBindings': {}, 'repairManifestBindings': {},
        'serviceManifest': service_manifest,
        'artifactInventorySha256': hashlib.sha256(_encode(entries)).hexdigest(),
        'runtimeMatchesRelease': None}
    if selected['reason'] is not None:
        return value
    service_source = (service_manifest or {}).get('sourceProvenance', {}).get('odsSource', {})
    if service_source.get('state') != 'verified-source-bindings':
        value['odsSource']['reason'] = 'service-source-unavailable'
        return value
    if service_source.get('commit') != selected['commit']:
        value['odsSource']['reason'] = 'source-phase-mismatch'
        return value
    bindings = {}
    try:
        prefix = selected['prefix']
        expected_plugin = prefix + 'extensions/services/pixel-agent/plugin/'
        expected_files = {name for name in selected['objects'] if name.startswith(expected_plugin)}
        if not expected_files:
            raise ValueError('source-inputs-unmapped')
        for directory in value['odsPluginDirectories']:
            matched = set()
            for path, record in entries.items():
                if not path.startswith(directory + '/') or record[0] == 'directory':
                    continue
                source = expected_plugin + path[len(directory) + 1:]
                mode, oid, checksum = _artifact_source_record(root, path, record)
                if selected['objects'].get(source) != (mode, oid):
                    raise ValueError('source-bytes-mismatch')
                matched.add(source)
                bindings[path] = {'source': source, 'sha256': checksum}
            if matched != expected_files:
                raise ValueError('source-bytes-mismatch')
        if 'cancellable-exec.sh' in entries:
            source = prefix + 'extensions/services/pixel-agent/host/cancellable-exec.sh'
            _, oid, checksum = _artifact_source_record(root, 'cancellable-exec.sh', entries['cancellable-exec.sh'])
            source_mode, source_oid = selected['objects'].get(source, (None, None))
            if source_mode not in ('100644', '100755') or source_oid != oid:
                raise ValueError('source-bytes-mismatch')
            bindings['cancellable-exec.sh'] = {'source': source, 'sha256': checksum}
        if {item['manifest']: item['manifestSha256'] for item in repairs} != {
                name: record['sha256'] for name, record in selected['repairInputs'].items()}:
            raise ValueError('source-bytes-mismatch')
    except (OSError, ValueError):
        value['odsSource']['reason'] = 'source-bytes-mismatch'
        return value
    value['odsSource'] = {'state': 'verified-source-bindings', 'commit': selected['commit'], 'reason': None}
    value['sourceBindings'] = bindings
    value['repairManifestBindings'] = selected['repairInputs']
    return value


def _validate_release_selection(value, entries, repair_receipts=()):
    keys = {'schemaVersion', 'kind', 'scope', 'odsSource', 'pixelSourceRevision',
        'serviceBundleDigest', 'odsPluginDirectories', 'sourceBindings',
        'repairManifestBindings', 'artifactInventorySha256', 'runtimeMatchesRelease', 'sourceScope', 'serviceManifest'}
    if (type(value) is not dict or set(value) != keys or type(value['schemaVersion']) is not int
            or value['schemaVersion'] != 1 or value['kind'] != SELECTION_KIND
            or value['scope'] != SELECTION_SCOPE or value['sourceScope'] != SOURCE_SCOPE or value['runtimeMatchesRelease'] is not None
            or type(value['odsSource']) is not dict or set(value['odsSource']) != {'state', 'commit', 'reason'}
            or type(value['odsPluginDirectories']) is not list
            or any(type(path) is not str or not re.fullmatch(r'plugins/[0-9]+', path)
                   or entries.get(path) != ['directory', 0o755] for path in value['odsPluginDirectories'])
            or len(value['odsPluginDirectories']) != len(set(value['odsPluginDirectories']))
            or type(value['sourceBindings']) is not dict or type(value['repairManifestBindings']) is not dict):
        raise BundleError('invalid-release-selection')
    for field, length in (('pixelSourceRevision', 40), ('serviceBundleDigest', 64)):
        if value[field] is not None and (type(value[field]) is not str or not re.fullmatch('[a-f0-9]{%d}' % length, value[field])):
            raise BundleError('invalid-release-selection')
    if value['artifactInventorySha256'] != hashlib.sha256(_encode(entries)).hexdigest():
        raise BundleError('release-selection-artifacts-changed')
    service_manifest = value['serviceManifest']
    if service_manifest is not None:
        validate_service_manifest_provenance(service_manifest)
        if (hashlib.sha256(_encode(service_manifest)).hexdigest() != value['serviceBundleDigest']
                or service_manifest['pixelSourceRef'] != value['pixelSourceRevision']):
            raise BundleError('release-selection-service-mismatch')
    source = value['odsSource']
    if source['state'] == 'unknown':
        if source['commit'] is not None or type(source['reason']) is not str or source['reason'] not in UNKNOWN_SOURCE_REASONS or value['sourceBindings'] or value['repairManifestBindings']:
            raise BundleError('invalid-release-selection')
        return value
    if (source['state'] != 'verified-source-bindings' or source['reason'] is not None
            or type(source['commit']) is not str or not re.fullmatch('[a-f0-9]{40}', source['commit'])
            or not value['odsPluginDirectories']):
        raise BundleError('invalid-release-selection')
    if (service_manifest is None or service_manifest.get('sourceProvenance', {}).get('odsSource') != source):
        raise BundleError('release-selection-source-phase-mismatch')
    prefix = _service_source_prefix(service_manifest['sourceProvenance']['sourceBindings'])
    expected = {name for name, record in entries.items() if record[0] != 'directory' and
        (name == 'cancellable-exec.sh' or any(name.startswith(path + '/') for path in value['odsPluginDirectories']))}
    if set(value['sourceBindings']) != expected:
        raise BundleError('release-selection-source-coverage')
    for path, record in value['sourceBindings'].items():
        if (type(record) is not dict or set(record) != {'source', 'sha256'}
                or _relative(record['source']) != record['source']):
            raise BundleError('invalid-release-selection')
        entry = entries[path]
        checksum = entry[3] if entry[0] == 'file' else hashlib.sha256(entry[1].encode()).hexdigest()
        relative = ('host/cancellable-exec.sh' if path == 'cancellable-exec.sh'
                    else 'plugin/' + path.split('/', 2)[2])
        if (record['sha256'] != checksum
                or record['source'] != prefix + 'extensions/services/pixel-agent/' + relative):
            raise BundleError('release-selection-source-changed')
    if (type(repair_receipts) not in (list, tuple) or any(type(item) is not dict
            or type(item.get('manifest')) is not str or type(item.get('manifestSha256')) is not str
            for item in repair_receipts)):
        raise BundleError('invalid-release-selection-repairs')
    expected_repairs = {item['manifest']: item['manifestSha256'] for item in repair_receipts}
    if (len(expected_repairs) != len(repair_receipts)
            or not set(expected_repairs) <= {name for name, _ in SHARED_REPAIRS}
            or set(value['repairManifestBindings']) != set(expected_repairs)):
        raise BundleError('release-selection-repair-coverage')
    for name, record in value['repairManifestBindings'].items():
        if (type(record) is not dict or set(record) != {'source', 'sha256'}
                or record['source'] != prefix + 'extensions/services/pixel-agent/host/' + name
                or record['sha256'] != expected_repairs[name]):
            raise BundleError('release-selection-repair-changed')
    return value


def _read_release_selection(root, entries):
    artifacts = {name: record for name, record in entries.items() if name != RELEASE_SELECTION}
    if RELEASE_SELECTION not in entries:
        return _release_selection(root, artifacts,
            {'reason': 'legacy-bundle', 'plugins': []}, pixel_ref=None, services_digest=None, repairs=[])
    try:
        with os.fdopen(_open_file(root, RELEASE_SELECTION), 'rb') as handle:
            body = handle.read(MAX_SELECTION + 1)
        value = json.loads(body)
        if len(body) > MAX_SELECTION or _encode(value) != body:
            raise BundleError('invalid-release-selection')
        repairs = []
        if 'ods-runtime-repairs.json' in entries:
            with os.fdopen(_open_file(root, 'ods-runtime-repairs.json'), 'rb') as handle:
                repair_body = handle.read(MAX_SELECTION + 1)
            if len(repair_body) > MAX_SELECTION:
                raise BundleError('invalid-release-selection-repairs')
            repairs = json.loads(repair_body)
        _validate_release_selection(value, artifacts, repairs)
        if value['serviceBundleDigest'] is not None:
            verify_service_binding(root, value['serviceBundleDigest'])
            if 'ods-service-binding.json' not in entries:
                raise BundleError('release-selection-service-binding-missing')
        return value
    except (ValueError, TypeError, KeyError, RecursionError, UnicodeError) as error:
        if isinstance(error, BundleError):
            raise
        raise BundleError('invalid-release-selection') from None


def expected_release_selection(root, *, expected_digest=None):
    """Installed expected artifacts only; never a running-process assertion."""
    manifest, _ = verify(root, expected_digest=expected_digest)
    return _read_release_selection(root, manifest['entries'])


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _open_file(root, relative):
    """Never follow an intermediate symlink while reading a selected tree."""
    parts = _relative(relative).split('/')
    parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
        return os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    finally:
        os.close(parent)


def _file(root, relative, output=None):
    fd = _open_file(root, relative)
    with os.fdopen(fd, 'rb') as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_mode & 0o7000:
            raise BundleError('unsupported-bundle-file')
        checksum = hashlib.sha256()
        for block in iter(lambda: source.read(1024 * 1024), b''):
            checksum.update(block)
            if output is not None:
                output.write(block)
        if _signature(before) != _signature(os.fstat(source.fileno())):
            raise BundleError('bundle-source-changed')
        # Modes are deliberately normalized; ACLs and xattrs are not imported.
        return ['file', 0o755 if before.st_mode & 0o111 else 0o644,
                before.st_size, checksum.hexdigest()]


def inventory(root, *, exclude_manifest=False, normalize_modes=True):
    root = Path(root)
    if not stat.S_ISDIR(root.lstat().st_mode):
        raise BundleError('bundle-directory-required')
    entries = {}
    def walk_error(error):
        raise error
    for directory, folders, files in os.walk(root, followlinks=False, onerror=walk_error):
        for name in sorted(folders + files):
            path = Path(directory) / name
            relative = _relative(path.relative_to(root).as_posix())
            if exclude_manifest and relative == MANIFEST:
                continue
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                target = os.readlink(path)
                try:
                    resolved = path.resolve(strict=True)
                except (OSError, RuntimeError):
                    raise BundleError('bundle-link-unavailable') from None
                if (target.startswith('/') or resolved != root and root not in resolved.parents
                        or any(ord(c) < 32 for c in target)):
                    raise BundleError('bundle-link-escapes')
                entries[relative] = ['link', target]
            elif stat.S_ISDIR(info.st_mode):
                if info.st_mode & 0o7000:
                    raise BundleError('unsupported-bundle-directory')
                entries[relative] = ['directory', 0o755 if normalize_modes else stat.S_IMODE(info.st_mode)]
            else:
                entries[relative] = _file(root, relative)
                if not normalize_modes:
                    entries[relative][1] = stat.S_IMODE(info.st_mode)
            if len(entries) > MAX_ENTRIES:
                raise BundleError('bundle-too-many-entries')
    return entries


def _copy_tree(source, target, entries):
    target.mkdir(mode=0o755)
    target.chmod(0o755)
    for relative, record in sorted(entries.items(), key=lambda item: (item[0].count('/'), item[0])):
        path = target / relative
        if record[0] == 'directory':
            path.mkdir(mode=0o755)
            path.chmod(0o755)
        elif record[0] == 'link':
            if os.readlink(source / relative) != record[1]:
                raise BundleError('bundle-source-changed')
            path.symlink_to(record[1])
        else:
            with path.open('xb') as output:
                actual = _file(source, relative, output)
                output.flush()
                os.fsync(output.fileno())
            if actual != record:
                raise BundleError('bundle-source-changed')
            path.chmod(record[1])
    if inventory(target, normalize_modes=False) != entries:
        raise BundleError('bundle-copy-mismatch')


def verify(root, *, expected_digest=None):
    root = Path(root).resolve(strict=True)
    path = root / MANIFEST
    if (not stat.S_ISREG(path.lstat().st_mode) or path.stat().st_size > MAX_MANIFEST):
        raise BundleError('invalid-bundle-manifest')
    with os.fdopen(_open_file(root, MANIFEST), 'rb') as handle:
        body = handle.read(MAX_MANIFEST + 1)
    value = json.loads(body)
    if (len(body) > MAX_MANIFEST or _encode(value) != body or type(value) is not dict
            or set(value) != {'schemaVersion', 'openclawVersion', 'plugins', 'entries'}
            or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
            or type(value['openclawVersion']) is not str or not value['openclawVersion']
            or type(value['entries']) is not dict or len(value['entries']) > MAX_ENTRIES
            or type(value['plugins']) is not list
            or value['plugins'] != ['plugins/' + str(i) for i in range(len(value['plugins']))]):
        raise BundleError('invalid-bundle-manifest')
    checksum = hashlib.sha256(body).hexdigest()
    if expected_digest is not None and checksum != expected_digest:
        raise BundleError('bundle-manifest-changed')
    if inventory(root, exclude_manifest=True, normalize_modes=False) != value['entries']:
        raise BundleError('bundle-content-changed')
    if (value['entries'].get('node', [None])[0] != 'file'
            or value['entries']['node'][1] != 0o755
            or value['entries'].get('runtime/openclaw.mjs', [None])[0] != 'file'
            or value['entries'].get('runtime/package.json', [None])[0] != 'file'
            or any(value['entries'].get(path) != ['directory', 0o755] for path in value['plugins'])):
        raise BundleError('invalid-bundle-layout')
    package = json.loads((root / 'runtime/package.json').read_bytes())
    if package.get('name') != 'openclaw' or package.get('version') != value['openclawVersion']:
        raise BundleError('bundle-version-mismatch')
    _read_release_selection(root, value['entries'])
    return value, checksum


def _shared_repair_contract(manifest_name, module_name):
    with os.fdopen(_open_file(REPAIR_ROOT, manifest_name), 'rb') as handle:
        body = handle.read()
    manifest = json.loads(body)
    if (type(manifest) is not dict or manifest.get('version', '2026.6.33') != '2026.6.33'
            or any(not isinstance(manifest.get(key), str)
                   or not re.fullmatch('[a-f0-9]{64}', manifest[key])
                   for key in ('sourceSha256', 'patchedSha256'))):
        raise BundleError('shared-repair-unqualified-manifest')
    return {'schemaVersion': 1, 'version': '2026.6.33', 'module': module_name,
            'manifest': manifest_name, 'manifestSha256': hashlib.sha256(body).hexdigest(),
            'sourceSha256': manifest['sourceSha256'], 'patchedSha256': manifest['patchedSha256'],
            'reviewedDependencies': manifest.get('reviewedDependencies', {})}


def _apply_shared_repairs(runtime, state_parent):
    spec = importlib.util.spec_from_file_location('ods_shared_runtime_repair',
                                                 REPAIR_ROOT / 'openclaw_tool_recovery.py')
    recovery = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recovery)
    contracts = [_shared_repair_contract(*item) for item in SHARED_REPAIRS]
    # Private provenance backups must not enter the normalized, public bundle tree.
    with tempfile.TemporaryDirectory(prefix='.ods-shared-repairs-', dir=state_parent) as directory:
        for contract in contracts:
            result = recovery.repair(runtime, Path(directory) / contract['manifest'][:-5],
                manifest_path=REPAIR_ROOT / contract['manifest'], module_name=contract['module'])
            if (result.get('schemaVersion') != 1
                    or result.get('status') not in ('changed', 'unchanged') or result.get('restored') is not False
                    or any(result.get(key) != contract[key]
                           for key in ('version', 'module', 'sourceSha256', 'patchedSha256'))
                    or result.get('reviewedDependencies', {}) != contract['reviewedDependencies']
                    or result.get('desiredSha256') != contract['patchedSha256']
                    or _file(runtime, 'dist/' + contract['module'])[3] != contract['patchedSha256']):
                raise BundleError('shared-repair-receipt-mismatch')
        if contracts != [_shared_repair_contract(*item) for item in SHARED_REPAIRS]:
            raise BundleError('shared-repair-manifest-changed')
    return contracts


def _patch_stream_progress(runtime, *, budget_receipt=None):
    """Relocate observers only for exact pristine or qualified budget-repaired bytes."""
    with os.fdopen(_open_file(runtime, STREAM_PROGRESS_FILE), 'rb') as handle:
        original = handle.read()
    source_sha256 = STREAM_PROGRESS_SOURCE_SHA256
    if budget_receipt is not None:
        contract = _shared_repair_contract(*SHARED_REPAIRS[-1])
        if (budget_receipt != contract or contract['sourceSha256'] != STREAM_PROGRESS_SOURCE_SHA256
                or 'dist/' + contract['module'] != STREAM_PROGRESS_FILE):
            raise BundleError('stream-progress-unqualified-budget-receipt')
        source_sha256 = contract['patchedSha256']
    if hashlib.sha256(original).hexdigest() != source_sha256:
        raise BundleError('stream-progress-unqualified-source')
    source = original.decode('utf-8')
    start = '\t\t\tconst configuredRunTimeoutMs = resolveAgentTimeoutMs({ cfg: params.config });'
    end = '\n\t\t\ttry {\n\t\t\t\tif (isRawModelRun) {'
    target = '\t\t\tconst innerStreamFn = activeSession.agent.streamFn;'
    if any(source.count(marker) != 1 for marker in (start, end, target)):
        raise BundleError('stream-progress-patch-layout-changed')
    begin, finish, insertion = source.index(start), source.index(end), source.index(target)
    if not insertion < begin < finish:
        raise BundleError('stream-progress-patch-order-changed')
    observers = source[begin:finish]
    patched = (source[:insertion] + observers + '\n' + source[insertion:begin] + source[finish:]).encode('utf-8')
    (runtime / STREAM_PROGRESS_FILE).write_bytes(patched)
    return {'id': 'openclaw-buffered-stream-progress-v1', 'file': STREAM_PROGRESS_FILE,
            'sourceSha256': source_sha256,
            'patchedSha256': hashlib.sha256(patched).hexdigest()}


def qualify_stream_progress_upgrade(current, candidate, *, current_digest, candidate_digest):
    """Read-only gate: this upgrade may change neither Node nor plugin code."""
    old, _ = verify(current, expected_digest=current_digest)
    new, _ = verify(candidate, expected_digest=candidate_digest)
    if (old['openclawVersion'] != '2026.6.33'
            or new['openclawVersion'] != old['openclawVersion']
            or new['plugins'] != old['plugins']):
        raise BundleError('runtime-upgrade-version-mismatch')
    changed = {name for name in old['entries'].keys() | new['entries'].keys()
               if old['entries'].get(name) != new['entries'].get(name)}
    expected = {'runtime/' + STREAM_PROGRESS_FILE, 'ods-runtime-patches.json'}
    if changed != expected or 'ods-runtime-patches.json' in old['entries']:
        raise BundleError('runtime-upgrade-unexpected-changes')
    # Reproduce the patch from the current trusted bytes, rather than trusting
    # a candidate's self-declared receipt or package version.
    with tempfile.TemporaryDirectory(prefix='ods-runtime-upgrade-check-') as directory:
        staged = Path(directory)
        target = staged / STREAM_PROGRESS_FILE
        target.parent.mkdir(parents=True)
        target.write_bytes((Path(current) / 'runtime' / STREAM_PROGRESS_FILE).read_bytes())
        receipt = _patch_stream_progress(staged)
        if ((Path(candidate) / 'runtime' / STREAM_PROGRESS_FILE).read_bytes() != target.read_bytes()
                or (Path(candidate) / 'ods-runtime-patches.json').read_bytes() != _encode([receipt])):
            raise BundleError('runtime-upgrade-patch-mismatch')
    return {'currentDigest': current_digest, 'candidateDigest': candidate_digest,
            'changedEntries': sorted(changed), 'scope': 'runtime content only; no activation'}


def qualify_workspace_root_upgrade(current, candidate, *, current_digest, candidate_digest):
    """Accept only the inherited-workspace fix, with all other bytes unchanged."""
    old, _ = verify(current, expected_digest=current_digest)
    new, _ = verify(candidate, expected_digest=candidate_digest)
    entry = 'plugins/0/index.js'
    if (old['openclawVersion'] != '2026.6.33'
            or new['openclawVersion'] != old['openclawVersion']
            or old['plugins'] != ['plugins/0'] or new['plugins'] != old['plugins']):
        raise BundleError('workspace-upgrade-version-mismatch')
    changed = {name for name in old['entries'].keys() | new['entries'].keys()
               if old['entries'].get(name) != new['entries'].get(name)}
    if changed != {entry}:
        raise BundleError('workspace-upgrade-unexpected-changes')
    before = b'      const workspaceRoot = api.config?.agents?.list?.find(agent => agent.id === AGENT_ID)?.workspace;'
    after = before[:-1] + b'\n        ?? api.config?.agents?.defaults?.workspace;'
    source = (Path(current) / entry).read_bytes()
    if source.count(before) != 1 or (Path(candidate) / entry).read_bytes() != source.replace(before, after):
        raise BundleError('workspace-upgrade-patch-mismatch')
    if (Path(current) / entry).stat().st_mode != (Path(candidate) / entry).stat().st_mode:
        raise BundleError('workspace-upgrade-mode-mismatch')
    return {'currentDigest': current_digest, 'candidateDigest': candidate_digest,
            'changedEntries': [entry], 'scope': 'plugin content only; no activation'}


def verify_service_binding(root, services_digest):
    path = Path(root) / 'ods-service-binding.json'
    if not os.path.lexists(path):
        return  # Legacy bundles predate deployment binding.
    with os.fdopen(_open_file(Path(root), path.name), 'rb') as stream:
        body = stream.read(1025)
    expected = {'schemaVersion': 1, 'serviceBundleDigest': services_digest}
    if len(body) > 1024 or body != _encode(expected):
        raise BundleError('bundle-service-binding-mismatch')


def build(*, node, runtime, destination, plugins=(), expected_version='2026.6.33',
          stream_progress_fix=False, services_digest=None, exec_wrapper=None,
          shared_runtime_repairs=False, ods_source=None, pixel_source_ref=None,
          ods_plugin_indices=(), service_manifest=None):
    if shared_runtime_repairs and expected_version != '2026.6.33':
        raise BundleError('shared-repairs-unqualified-version')
    if services_digest is not None and (type(services_digest) is not str
            or not re.fullmatch('[a-f0-9]{64}', services_digest)):
        raise BundleError('invalid-service-bundle-digest')
    node, runtime = Path(node).resolve(strict=True), Path(runtime).resolve(strict=True)
    plugins = [Path(path).resolve(strict=True) for path in plugins]
    if (pixel_source_ref is not None and (type(pixel_source_ref) is not str
            or not re.fullmatch('[a-f0-9]{40}', pixel_source_ref))):
        raise BundleError('invalid-pixel-source-revision')
    if (type(ods_plugin_indices) not in (list, tuple) or
            any(type(index) is not int or not 0 <= index < len(plugins) for index in ods_plugin_indices)
            or len(set(ods_plugin_indices)) != len(ods_plugin_indices)):
        raise BundleError('invalid-ods-plugin-mapping')
    service_source = None
    if service_manifest is not None:
        service_manifest = json.loads(_encode(service_manifest))
        service_source = validate_service_manifest_provenance(service_manifest)
        if (hashlib.sha256(_encode(service_manifest)).hexdigest() != services_digest
                or service_manifest['pixelSourceRef'] != pixel_source_ref):
            raise BundleError('selected-service-manifest-mismatch')
    selected = None
    if ods_source is not None:
        selected = _selected_release_source(ods_source, ods_plugin_indices,
            wrapper=exec_wrapper is not None, repairs=shared_runtime_repairs,
            expected_ref=service_source['odsSource']['commit'] if service_source else None)
    destination = Path(destination)
    if not destination.is_absolute() or os.path.lexists(destination):
        raise BundleError('new-absolute-bundle-destination-required')
    parent = destination.parent.resolve(strict=True)
    destination = parent / destination.name
    roots = [runtime, *plugins]
    if any(root == destination or root in destination.parents for root in roots):
        raise BundleError('bundle-destination-inside-source')
    with os.fdopen(_open_file(runtime, 'package.json'), 'rb') as handle:
        package = json.load(handle)
    if package.get('name') != 'openclaw' or package.get('version') != expected_version:
        raise BundleError('unqualified-openclaw-version')
    snapshots = [inventory(root) for root in roots]
    node_record = _file(node.parent, node.name)
    if node_record[1] != 0o755:
        raise BundleError('bundle-node-not-executable')
    wrapper = Path(exec_wrapper).absolute() if exec_wrapper is not None else None
    wrapper_record = _file(wrapper.parent, wrapper.name) if wrapper else None
    staged = Path(tempfile.mkdtemp(prefix='.ods-pixel-bundle-', dir=parent))
    try:
        with (staged / 'node').open('xb') as output:
            if _file(node.parent, node.name, output) != node_record:
                raise BundleError('bundle-source-changed')
            output.flush()
            os.fsync(output.fileno())
        (staged / 'node').chmod(0o755)
        if wrapper is not None:
            with (staged / 'cancellable-exec.sh').open('xb') as output:
                if _file(wrapper.parent, wrapper.name, output) != wrapper_record:
                    raise BundleError('bundle-exec-wrapper-changed')
                output.flush()
                os.fsync(output.fileno())
            (staged / 'cancellable-exec.sh').chmod(0o755)
        _copy_tree(runtime, staged / 'runtime', snapshots[0])
        repairs = []
        if shared_runtime_repairs:
            repairs = _apply_shared_repairs(staged / 'runtime', parent)
            (staged / 'ods-runtime-repairs.json').write_bytes(_encode(repairs))
            (staged / 'ods-runtime-repairs.json').chmod(0o644)
        if stream_progress_fix:
            receipt = _patch_stream_progress(staged / 'runtime',
                                             budget_receipt=repairs[-1] if repairs else None)
            (staged / 'ods-runtime-patches.json').write_bytes(_encode([receipt]))
            (staged / 'ods-runtime-patches.json').chmod(0o644)
        (staged / 'plugins').mkdir(mode=0o755)
        (staged / 'plugins').chmod(0o755)
        for index, (source, entries) in enumerate(zip(plugins, snapshots[1:])):
            _copy_tree(source, staged / 'plugins' / str(index), entries)
        if services_digest is not None:
            binding = staged / 'ods-service-binding.json'
            binding.write_bytes(_encode({'schemaVersion': 1, 'serviceBundleDigest': services_digest}))
            binding.chmod(0o644)
        if selected is not None:
            selection = _release_selection(staged, inventory(staged), selected,
                pixel_ref=pixel_source_ref, services_digest=services_digest,
                repairs=repairs, service_manifest=service_manifest)
            selection_body = _encode(selection)
            if len(selection_body) > MAX_SELECTION:
                raise BundleError('release-selection-too-large')
            (staged / RELEASE_SELECTION).write_bytes(selection_body)
            (staged / RELEASE_SELECTION).chmod(0o644)
        value = {'schemaVersion': 1, 'openclawVersion': expected_version,
                 'plugins': ['plugins/' + str(i) for i in range(len(plugins))],
                 'entries': inventory(staged)}
        body = _encode(value)
        if len(body) > MAX_MANIFEST:
            raise BundleError('bundle-manifest-too-large')
        with (staged / MANIFEST).open('xb') as output:
            output.write(body)
            output.flush()
            os.fsync(output.fileno())
        (staged / MANIFEST).chmod(0o644)
        verify(staged)
        if os.path.lexists(destination):
            raise BundleError('bundle-destination-appeared')
        os.rename(staged, destination)
        return hashlib.sha256(body).hexdigest()
    finally:
        if staged.exists():
            shutil.rmtree(staged)


def publish(source, *, expected_digest, install_root=INSTALL_ROOT):
    """Publish a content-addressed copy as root, without changing any service.

    The independently selected digest is mandatory. Existing versions must
    pass both full custody and content checks; drift is never repaired in place.
    """
    if os.geteuid() != 0:
        raise BundleError('root-bundle-publisher-required')
    if (type(expected_digest) is not str or len(expected_digest) != 64
            or any(c not in '0123456789abcdef' for c in expected_digest)):
        raise BundleError('approved-bundle-digest-required')
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'bin'))
    from pixel_macos_custody import protected_directory, protected_tree_metadata
    source = Path(source).resolve(strict=True)
    value, checksum = verify(source, expected_digest=expected_digest)
    install_root = Path(install_root)
    destination = install_root / checksum
    if source == install_root or source in install_root.parents:
        raise BundleError('bundle-destination-inside-source')
    with protected_directory(install_root, create=True) as parent:
        # Serialize cooperative installers on the verified root directory;
        # no user-writable lock file or user-selected executable is involved.
        fcntl.flock(parent, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if os.path.lexists(destination):
            protected_tree_metadata(destination)
            verify(destination, expected_digest=checksum)
            return destination
        temporary = Path(tempfile.mkdtemp(prefix='.publishing-', dir=install_root))
        staged = temporary / 'bundle'
        try:
            _copy_tree(source, staged, value['entries'])
            with (staged / MANIFEST).open('xb') as output:
                output.write(_encode(value))
                output.flush()
                os.fsync(output.fileno())
            (staged / MANIFEST).chmod(0o644)
            protected_tree_metadata(staged)
            verify(staged, expected_digest=checksum)
            # Flush directories bottom-up before publishing the whole tree.
            for directory, _, _ in os.walk(staged, topdown=False, followlinks=False):
                with protected_directory(directory) as fd:
                    os.fsync(fd)
            if os.path.lexists(destination):
                raise BundleError('bundle-destination-appeared')
            os.rename(staged, destination)
            os.fsync(parent)
            protected_tree_metadata(destination)
            verify(destination, expected_digest=checksum)
            return destination
        finally:
            shutil.rmtree(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify', type=Path)
    parser.add_argument('--publish', type=Path, help='Publish a staged bundle as root; never starts services')
    parser.add_argument('--expected-digest', help='Previously selected manifest SHA256 required for publication')
    parser.add_argument('--node', type=Path)
    parser.add_argument('--runtime', type=Path)
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--plugin', type=Path, action='append', default=[])
    parser.add_argument('--expected-version', default='2026.6.33')
    parser.add_argument('--stream-progress-fix', action='store_true',
                        help='Build-only opt-in patch for reviewed OpenClaw buffered stream observers')
    args = parser.parse_args()
    if args.publish:
        if args.verify or any((args.node, args.runtime, args.destination, args.plugin, args.stream_progress_fix)):
            parser.error('--publish cannot be combined with build or verify')
        published = publish(args.publish, expected_digest=args.expected_digest)
        manifest, checksum = verify(published, expected_digest=args.expected_digest)
    elif args.verify:
        if args.stream_progress_fix:
            parser.error('--stream-progress-fix is build-only')
        manifest, checksum = verify(args.verify)
    else:
        if not all((args.node, args.runtime, args.destination)):
            parser.error('--node, --runtime and --destination are required to build')
        checksum = build(node=args.node, runtime=args.runtime, destination=args.destination,
                         plugins=args.plugin, expected_version=args.expected_version,
                         stream_progress_fix=args.stream_progress_fix)
        manifest, _ = verify(args.destination, expected_digest=checksum)
    print(json.dumps({'manifestSha256': checksum, 'openclawVersion': manifest['openclawVersion'],
                      'entries': len(manifest['entries']), 'plugins': len(manifest['plugins']),
                      'scope': 'content integrity only; not root custody or runtime qualification'}))


if __name__ == '__main__':
    main()
