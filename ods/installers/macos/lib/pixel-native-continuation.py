"""Continue reviewed owner-level setup after protected Pixel recovery."""
import importlib.util
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
from urllib.parse import unquote, urlsplit


HERE = Path(__file__).resolve().parent
MODEL_KEYS = frozenset((
    'ODS_MODE', 'GPU_BACKEND', 'LLM_BACKEND', 'EXTERNAL_LLM_URL', 'LEMONADE_EXTERNAL',
    'ODS_ACTIVE_MODEL_STORE', 'MODEL_SELECTION_SOURCE',
    'GGUF_FILE', 'LLM_MODEL', 'MAX_CONTEXT', 'CTX_SIZE',
    'MODEL_RECOMMENDED_MODEL', 'MODEL_RECOMMENDED_GGUF', 'MODEL_RECOMMENDED_CONTEXT',
))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _saved_environment(install_dir, keys, duplicate_error):
    environment = load('continuation_env', HERE / 'pixel-native-env.py')
    snapshot = environment.snapshot(Path(install_dir) / '.env')
    selected = {}
    for line in snapshot[0].decode('utf-8').splitlines():
        match = environment.ASSIGNMENT.fullmatch(line)
        if match and match[1] in keys:
            key, value = match.groups()
            if key in selected:
                raise ValueError(duplicate_error)
            selected[key] = environment.values.parse_env_value(value)
    return selected, snapshot


def saved_model_environment(install_dir):
    return _saved_environment(install_dir, MODEL_KEYS, 'duplicate-retained-model-setting')


def optional_setup_selection(install_dir, selected_services, *, opencode_choice=None):
    """Resolve saved or explicitly confirmed choices without changing them."""
    if (type(selected_services) is not dict or not selected_services
            or any(type(name) is not str or type(value) is not dict
                   for name, value in selected_services.items())):
        raise ValueError('native-compose-services-invalid')
    saved, snapshot = _saved_environment(install_dir, {'ENABLE_OPENCODE'},
        'duplicate-retained-opencode-setting')
    choice = saved.get('ENABLE_OPENCODE')
    if choice is not None and choice not in ('true', 'false'):
        raise ValueError('invalid-retained-opencode-selection')
    if opencode_choice not in (None, 'enabled', 'disabled'):
        raise ValueError('invalid-confirmed-opencode-selection')
    source = 'saved' if choice is not None else 'missing'
    if opencode_choice is not None:
        confirmed = 'true' if opencode_choice == 'enabled' else 'false'
        if choice is not None and choice != confirmed:
            raise ValueError('confirmed-opencode-selection-conflict')
        if choice is None:
            choice, source = confirmed, 'confirmed'
    return {'opencode': {'selected': None if choice is None else choice == 'true', 'source': source},
        'whisperModel': 'whisper' in selected_services, 'perplexica': 'perplexica' in selected_services}, snapshot


def inspect_remaining_setup(install_dir, selected_services, *, opencode_choice=None):
    """Describe retained choices only, without executing setup or proving health."""
    optional, snapshot = optional_setup_selection(install_dir, selected_services, opencode_choice=opencode_choice)
    choice = optional['opencode']['selected']
    plan, model_snapshot = inspect_model_upgrade(install_dir, **bootstrap_settings(install_dir))
    if snapshot != model_snapshot or saved_model_environment(install_dir)[1] != snapshot:
        raise ValueError('retained-model-environment-changed')
    # Do not print the environment, rendered Compose definitions or download
    # arguments: these are a configuration summary, not shareable diagnostics.
    checks = ['protected-activation', 'pixel-services', 'selected-service-health',
              'host-agent', 'active-model', 'portal']
    if plan['status'] == 'upgrade-required':
        checks.append('full-model-download')
    if choice is True:
        checks.append('opencode')
    if optional['whisperModel']:
        checks.append('whisper-model-cache')
    if optional['perplexica']:
        checks.append('perplexica-inference-route')
    return {'status': 'continuation-inspection', 'installerComplete': False,
        'protectedActivationVerified': False,
        'model': {key: plan[key] for key in ('status', 'modelId') if key in plan},
        'optionalSetup': optional, 'requiresChoice': ['opencode'] if choice is None else [],
        'checksRequired': checks}


def model_upgrade_plan(saved, catalog, *, bootstrap_file, bootstrap_model, bootstrap_context):
    """Recover the saved recommendation, never run hardware-based selection.

    The returned arguments are for the existing bootstrap-upgrade.sh protocol.
    Planning is not proof of the live model, download completion or install health.
    """
    mode = saved.get('ODS_MODE', '')
    if mode == 'cloud':
        return {'status': 'cloud-model', 'arguments': []}
    if (mode != 'local' or saved.get('GPU_BACKEND') != 'apple'
            or saved.get('LLM_BACKEND') != 'llama-server'
            or saved.get('EXTERNAL_LLM_URL') or saved.get('LEMONADE_EXTERNAL', 'false') != 'false'
            or saved.get('ODS_ACTIVE_MODEL_STORE', 'default') != 'default'):
        raise ValueError('retained-local-model-route-required')
    filename = saved.get('MODEL_RECOMMENDED_GGUF', '')
    model_name = saved.get('MODEL_RECOMMENDED_MODEL', '')
    context = saved.get('MODEL_RECOMMENDED_CONTEXT', '')
    if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*\.gguf', filename)
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]*', model_name)
            or not re.fullmatch(r'[1-9][0-9]{0,8}', context)):
        raise ValueError('saved-model-recommendation-required')
    records = catalog.get('models') if type(catalog) is dict else None
    if type(records) is not list or any(type(record) is not dict for record in records):
        raise ValueError('installed-model-catalog-required')
    # A filename must have exactly one meaning in this installed catalog.
    matches = [record for record in records if record.get('gguf_file') == filename]
    if len(matches) != 1 or matches[0].get('llm_model_name') != model_name:
        raise ValueError('saved-model-recommendation-mismatch')
    model = matches[0]
    limit = model.get('max_context_length', model.get('context_length'))
    if type(limit) is not int or not 1024 <= int(context) <= limit:
        raise ValueError('saved-model-context-invalid')
    active = (saved.get('GGUF_FILE'), saved.get('LLM_MODEL'),
              saved.get('MAX_CONTEXT'), saved.get('CTX_SIZE'))
    target = (filename, model_name, context, context)
    if active == target:
        return {'status': 'selected-model', 'modelId': model.get('id'), 'arguments': []}
    if (active != (bootstrap_file, bootstrap_model, str(bootstrap_context), str(bootstrap_context))
            or saved.get('MODEL_SELECTION_SOURCE', 'installer') != 'installer'):
        raise ValueError('retained-bootstrap-model-required')
    # Reuse artifact validation, but require a checksum and a pinned revision for
    # a new automatic download. The existing upgrader accepts one GGUF only.
    contracts = load('continuation_model_contracts', HERE.parents[2] / 'scripts/preserve-active-model.py')
    artifacts = contracts.manifest_for(model)
    if not artifacts or len(artifacts) != 1 or artifacts[0]['file'] != filename:
        raise ValueError('single-file-bootstrap-model-required')
    artifact = artifacts[0]
    url = urlsplit(artifact['url'])
    if (url.scheme != 'https' or url.netloc != 'huggingface.co'
            or url.query or url.fragment
            or not re.fullmatch(r'/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+/resolve/[a-f0-9]{40}/[^/]+', url.path)
            or unquote(url.path.rsplit('/', 1)[1]) != filename
            or not re.fullmatch(r'[a-f0-9]{64}', artifact['sha256'])):
        raise ValueError('pinned-bootstrap-model-required')
    return {'status': 'upgrade-required', 'modelId': model.get('id'),
            'arguments': [filename, artifact['url'], artifact['sha256'],
                          model_name, context, bootstrap_file]}


def inspect_model_upgrade(install_dir, **bootstrap):
    install_dir = Path(install_dir).resolve(strict=True)
    saved, snapshot = saved_model_environment(install_dir)
    catalog = json.loads((install_dir / 'config/model-library.json').read_text(encoding='utf-8'))
    return model_upgrade_plan(saved, catalog, **bootstrap), snapshot


def owned_snapshot(path):
    """Read bounded owner metadata without following links."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or info.st_mode & 0o022 or info.st_size > 65536):
            raise ValueError('owned-continuation-metadata-required')
        data = stream.read(65537)
        if len(data) > 65536:
            raise ValueError('owned-continuation-metadata-required')
        return data, (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns)


def publish_metadata(path, body, expected):
    path = Path(path)
    if path.parent.resolve(strict=True) != path.parent:
        raise ValueError('canonical-continuation-directory-required')
    with tempfile.TemporaryDirectory(prefix='.ods-continue-', dir=path.parent) as temporary:
        staged = Path(temporary) / 'metadata'
        with staged.open('xb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        if owned_snapshot(path) != expected:
            raise ValueError('continuation-metadata-changed')
        os.replace(staged, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def bootstrap_settings(install_dir):
    environment = load('continuation_bootstrap_values', HERE / 'pixel-native-env.py')
    snapshot = owned_snapshot(Path(install_dir) / 'installers/macos/lib/tier-map.sh')
    if snapshot is None:
        raise ValueError('installed-bootstrap-settings-required')
    names = {'BOOTSTRAP_GGUF_FILE': 'bootstrap_file', 'BOOTSTRAP_LLM_MODEL': 'bootstrap_model',
             'BOOTSTRAP_MAX_CONTEXT': 'bootstrap_context'}
    values = {}
    # Read only the three literal top-level constants, never source the script
    # or execute hardware selection while reconstructing a retained choice.
    for line in snapshot[0].decode('utf-8').splitlines():
        match = environment.ASSIGNMENT.fullmatch(line)
        if match and match[1] in names:
            name = names[match[1]]
            if name in values:
                raise ValueError('ambiguous-bootstrap-settings')
            values[name] = environment.values.parse_env_value(match[2])
    if (set(values) != set(names.values())
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*\.gguf', values['bootstrap_file'])
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]*', values['bootstrap_model'])
            or not re.fullmatch(r'[1-9][0-9]{0,8}', values['bootstrap_context'])):
        raise ValueError('installed-bootstrap-settings-required')
    return values


def resume_model_upgrade(install_dir, compose_files, process_env, *, verify_selection):
    """Persist retry inputs and hand off to the installed, locked upgrader.

    Called only after protected readback and ready-selection publication, while
    the recovery caller still owns the selection lock. Starting is not completion.
    """
    install_dir = Path(install_dir).resolve(strict=True)
    plan, snapshot = inspect_model_upgrade(install_dir, **bootstrap_settings(install_dir))
    cache = install_dir / '.compose-flags'
    cache_before = owned_snapshot(cache)
    relative = []
    for source in compose_files:
        path = Path(source).resolve(strict=True)
        if not path.is_file() or install_dir not in path.parents:
            raise ValueError('installed-compose-file-required')
        value = str(path.relative_to(install_dir))
        if re.search(r'\s', value):
            raise ValueError('cli-compatible-compose-path-required')
        relative.extend(['-f', value])
    if not relative:
        raise ValueError('resolved-compose-flags-required')
    cache_body = (' '.join(relative) + '\n').encode()
    if not plan['arguments']:
        verify_selection()
        if saved_model_environment(install_dir)[1] != snapshot:
            raise ValueError('retained-model-environment-changed')
        if cache_before is None or cache_before[0] != cache_body:
            publish_metadata(cache, cache_body, cache_before)
        return plan
    script = install_dir / 'scripts/bootstrap-upgrade.sh'
    if script.resolve(strict=True) != script or not script.is_file():
        raise ValueError('installed-bootstrap-upgrader-required')
    args_path = install_dir / 'data/bootstrap-upgrade.args'
    args_before = owned_snapshot(args_path)
    args_body = ('\n'.join(plan['arguments']) + '\n').encode()
    if args_before is not None and args_before[0] != args_body:
        raise ValueError('retained-upgrade-arguments-conflict')
    # A missing/stale PID file alone cannot establish that a prior worker died.
    # Check the actual same-owner process before creating another handoff.
    existing = subprocess.run(['/usr/bin/pgrep', '-u', str(os.getuid()), '-f',
        re.escape(str(script)) + '.*' + re.escape(str(install_dir))],
        capture_output=True, text=True, timeout=10, check=False)
    if existing.returncode == 0:
        raise ValueError('native-model-upgrade-already-running')
    if existing.returncode != 1:
        raise ValueError('native-model-upgrade-process-check-failed')
    environment = {key: process_env[key] for key in ('HOME', 'PATH', 'DOCKER_HOST', 'DOCKER_CONFIG')}
    if 'ODS_PYTHON_CMD' in process_env:
        environment['ODS_PYTHON_CMD'] = process_env['ODS_PYTHON_CMD']
    environment['TMPDIR'] = tempfile.gettempdir()
    pid_path = install_dir / 'data/bootstrap-upgrade.pid'
    pid_before = owned_snapshot(pid_path)
    status_path = install_dir / 'data/bootstrap-status.json'
    status_before = owned_snapshot(status_path)
    log_dir = install_dir / 'logs'
    log_dir.mkdir(exist_ok=True)
    if log_dir.resolve(strict=True) != log_dir:
        raise ValueError('canonical-continuation-directory-required')
    fd = os.open(log_dir / 'model-upgrade.log',
        os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, 'ab', buffering=0) as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or info.st_mode & 0o022):
            raise ValueError('owned-continuation-log-required')
        verify_selection()
        if saved_model_environment(install_dir)[1] != snapshot:
            raise ValueError('retained-model-environment-changed')
        if cache_before is None or cache_before[0] != cache_body:
            publish_metadata(cache, cache_body, cache_before)
        published_cache = owned_snapshot(cache)
        if args_before is None:
            publish_metadata(args_path, args_body, args_before)
        published_args = owned_snapshot(args_path)
        verify_selection()
        if saved_model_environment(install_dir)[1] != snapshot:
            raise ValueError('retained-model-environment-changed')
        if owned_snapshot(cache) != published_cache or owned_snapshot(args_path) != published_args:
            raise ValueError('continuation-metadata-changed')
        try:
            process = subprocess.Popen(['/bin/bash', str(script), str(install_dir), *plan['arguments']],
                cwd=install_dir, env=environment, stdin=subprocess.DEVNULL, stdout=stream,
                stderr=subprocess.STDOUT, close_fds=True, start_new_session=True)
        except OSError:
            # A failed spawn has no worker to publish status. Seed the normal
            # CLI retry contract, but never overwrite another operation's status.
            if status_before is None:
                failure = {'status': 'failed', 'model': plan['arguments'][0], 'percent': None,
                    'bytesDownloaded': 0, 'bytesTotal': 0, 'speedBytesPerSec': 0,
                    'eta': 'Could not start the retained model downloader.',
                    'updatedAt': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
                publish_metadata(status_path, (json.dumps(failure) + '\n').encode(), None)
            raise ValueError('native-model-upgrade-launch-failed') from None
        # Failure here must not kill an already admitted worker. An explicit
        # retry detects that live worker rather than treating a missing PID as dead.
        try:
            publish_metadata(pid_path, (str(process.pid) + '\n').encode(), pid_before)
        except (ValueError, OSError):
            raise ValueError('native-model-worker-tracking-failed') from None
    return {'status': 'download-started', 'modelId': plan['modelId'], 'pid': process.pid}


def _run_owner_setup(install_dir, process_env, *, stage, script, extra_env=None, timeout=600):
    """Run shared owner-level setup only after the caller's protected readback.

    Keep subprocess diagnostics in the private preparation directory, not in
    the shareable CLI output. The caller rechecks protected state afterwards.
    """
    install_dir = Path(install_dir).resolve(strict=True)
    preparation = install_dir / 'data/pixel-native/preparation'
    info = preparation.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077 or preparation.resolve(strict=True) != preparation):
        raise ValueError('private-native-preparation-required')
    log = preparation / ('continuation-' + stage + '.log')
    fd = os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, 'ab', buffering=0) as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise ValueError('private-native-continuation-log-required')
        environment = {key: process_env[key] for key in ('HOME', 'PATH', 'DOCKER_HOST', 'DOCKER_CONFIG')}
        if 'ODS_PYTHON_CMD' in process_env:
            environment['ODS_PYTHON_CMD'] = process_env['ODS_PYTHON_CMD']
        environment['ODS_CONTINUATION_LOG_FD'] = str(stream.fileno())
        environment.update(extra_env or {})
        result = subprocess.run(['/bin/bash', '-c', """
set -euo pipefail
LIB_DIR="$1"
INSTALL_DIR="$2"
export ODS_HOME="$INSTALL_DIR"
ai() { printf '%s\\n' "$*"; }
ai_ok() { ai "[OK] $*"; }
ai_warn() { ai "[WARN] $*"; }
ai_err() { ai "[ERROR] $*"; }
chapter() { ai "$*"; }
ODS_LOG_FILE="/dev/fd/$ODS_CONTINUATION_LOG_FD"
""" + script, 'ods-recovery-' + stage, str(HERE), str(install_dir)],
            cwd=install_dir, env=environment, stdin=subprocess.DEVNULL,
            stdout=stream, stderr=subprocess.STDOUT, close_fds=True,
            pass_fds=(stream.fileno(),), timeout=timeout, check=False)
        if result.returncode:
            raise ValueError('native-recovery-' + stage + '-failed')


def restore_host_agent(install_dir, process_env):
    _run_owner_setup(install_dir, process_env, stage='host-agent', script="""
for library in constants env-generator bridge-manager host-agent-listener host-agent-install; do
    source "$LIB_DIR/$library.sh"
done
ODS_LOG_FILE="/dev/fd/$ODS_CONTINUATION_LOG_FD"
ods_macos_install_host_agent
""")


def restore_optional_tools(install_dir, selected_services, process_env, *,
                           opencode_choice=None, verify_selection, expected_snapshot=None):
    optional, snapshot = optional_setup_selection(install_dir, selected_services, opencode_choice=opencode_choice)
    if expected_snapshot is not None and snapshot != expected_snapshot:
        raise ValueError('retained-model-environment-changed')
    if optional['opencode']['selected'] is None:
        raise ValueError('retained-opencode-choice-required')
    verify_selection()
    values, current = _saved_environment(install_dir, {
        'ODS_MODE', 'LLM_MODEL', 'GGUF_FILE', 'MAX_CONTEXT', 'LLM_API_URL', 'WHISPER_PORT',
        'ODS_NATIVE_LLAMA_PORT'}, 'duplicate-retained-optional-setting')
    if current != snapshot:
        raise ValueError('retained-model-environment-changed')
    if not any((optional['opencode']['selected'], optional['whisperModel'], optional['perplexica'])):
        return {'status': 'not-selected', 'selection': optional}
    if values.get('ODS_MODE') not in ('local', 'cloud'):
        raise ValueError('retained-optional-route-required')
    context = values.get('MAX_CONTEXT', '32768')
    if not re.fullmatch(r'[1-9][0-9]{0,8}', context) or int(context) < 1024:
        raise ValueError('retained-optional-context-invalid')
    for name, default in (('WHISPER_PORT', '9000'), ('ODS_NATIVE_LLAMA_PORT', '8080')):
        value = values.get(name, default)
        if not re.fullmatch(r'[1-9][0-9]{0,4}', value) or int(value) > 65535:
            raise ValueError('retained-optional-port-invalid')
        values[name] = value
    extra = {'ENABLE_OPENCODE': str(optional['opencode']['selected']).lower(),
        'ENABLE_VOICE': str(optional['whisperModel']).lower(),
        'ENABLE_PERPLEXICA': str(optional['perplexica']).lower(),
        'CLOUD_MODE': str(values['ODS_MODE'] == 'cloud').lower(),
        'LLM_MODEL': values.get('LLM_MODEL', ''), 'GGUF_FILE': values.get('GGUF_FILE', ''),
        'MAX_CONTEXT': context, 'WHISPER_PORT': values['WHISPER_PORT'],
        'CONTAINER_LLM_URL': values.get('LLM_API_URL') or
            'http://host.docker.internal:' + values['ODS_NATIVE_LLAMA_PORT']}
    _run_owner_setup(install_dir, process_env, stage='optional-tools', extra_env=extra, timeout=1800, script="""
SCRIPT_DIR="$(cd "$LIB_DIR/.." && pwd)"
for library in constants env-generator opencode-selection host-agent-install post-pixel-install; do
    source "$LIB_DIR/$library.sh"
done
ODS_LOG_FILE="/dev/fd/$ODS_CONTINUATION_LOG_FD"
# A retained disabled choice is not permission to stop a current user session.
OPENCODE_DISABLE_EXPLICIT=false
OPENCODE_DISABLE_SELECTED=false
ods_macos_install_opencode true
ods_macos_prepare_voice true
ods_macos_configure_perplexica
""")
    verify_selection()
    if saved_model_environment(install_dir)[1] != snapshot:
        raise ValueError('retained-model-environment-changed')
    return {'status': 'ready', 'selection': optional}
