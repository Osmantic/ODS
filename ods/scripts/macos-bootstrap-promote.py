#!/usr/bin/env python3
"""Darwin-only bootstrap promotion client for ODS model activation.

Reads private environment, verifies catalog pair, performs read-only
preflight, POSTs a single activation request, then verifies persisted
state. Never writes files, never retries POST, never falls back.
"""
import argparse
import importlib.util
import json
import os
import re
import stat
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def _load_env_values():
    here = Path(__file__).resolve()
    candidate = here.parents[1] / 'extensions' / 'services' / 'dashboard-api' / 'env_values.py'
    if not candidate.is_file():
        raise SystemExit('env_values.py not found')
    spec = importlib.util.spec_from_file_location('ods_env_values', candidate)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


values = _load_env_values()

ASSIGNMENT = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$')
MAX_ENV_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
ACTIVATION_TIMEOUT = 600.0
PREFLIGHT_TIMEOUT = 30.0
PREFLIGHT_WAIT_SECONDS = 180.0
MAX_GGUF_BASENAME = 255


class AmbiguousActivation(Exception):
    pass


class PreflightFailure(Exception):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, 'redirect-refused', headers, fp)


def _read_private_env(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise ValueError('private-owner-environment-required') from exc
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.getuid() or info.st_mode & 0o077
                or info.st_size > MAX_ENV_BYTES):
            raise ValueError('private-owner-environment-required')
        body = stream.read(MAX_ENV_BYTES + 1)
        if len(body) > MAX_ENV_BYTES:
            raise ValueError('environment-too-large')
    return _parse_env(body.decode('utf-8'))


def _parse_env(text):
    env = {}
    for line in text.splitlines():
        match = ASSIGNMENT.match(line)
        if match:
            env[match.group(1)] = values.parse_env_value(match.group(2))
    return env


def _validate_install_dir(raw):
    if not raw or '\x00' in raw:
        raise ValueError('invalid-install-dir')
    path = Path(raw)
    if not path.is_absolute():
        raise ValueError('install-dir-must-be-absolute')
    if not path.is_dir():
        raise ValueError('install-dir-missing')
    return path


def _validate_token(name, raw):
    if not raw or '\x00' in raw or '\n' in raw or '\r' in raw:
        raise ValueError(f'invalid-{name}')
    if len(raw) > 512:
        raise ValueError(f'invalid-{name}')
    return raw


def _validate_gguf_basename(raw):
    if not raw or '\x00' in raw or '\n' in raw or '\r' in raw:
        raise ValueError('invalid-gguf')
    if len(raw) > MAX_GGUF_BASENAME:
        raise ValueError('invalid-gguf')
    if '/' in raw or '\\' in raw or '..' in raw:
        raise ValueError('invalid-gguf')
    if raw in ('.', '..'):
        raise ValueError('invalid-gguf')
    return raw


def _load_catalog(install_dir, full_gguf, full_llm):
    catalog_path = install_dir / 'config' / 'model-library.json'
    with open(catalog_path, 'r', encoding='utf-8') as stream:
        catalog = json.load(stream)
    if not isinstance(catalog, dict) or not isinstance(catalog.get('models'), list):
        raise ValueError('catalog-malformed')
    matches = [row for row in catalog['models']
               if isinstance(row, dict)
               and row.get('gguf_file') == full_gguf
               and row.get('llm_model_name') == full_llm]
    if len(matches) != 1:
        raise ValueError('catalog-pair-not-unique')
    row = matches[0]
    model_id = row.get('id')
    if not isinstance(model_id, str) or not model_id:
        raise ValueError('catalog-id-missing')
    context = row.get('max_context_length')
    if context is None:
        context = row.get('context_length')
    if isinstance(context, bool) or not isinstance(context, int) or context <= 0:
        raise ValueError('catalog-context-invalid')
    return model_id, context


def _resolve_port(env):
    raw = env.get('ODS_AGENT_PORT', '7710')
    try:
        port = int(raw)
    except (TypeError, ValueError):
        raise ValueError('invalid-agent-port')
    if port < 1 or port > 65535:
        raise ValueError('invalid-agent-port')
    return port


def _resolve_auth(env):
    for key in ('ODS_AGENT_KEY', 'DASHBOARD_API_KEY'):
        value = env.get(key)
        if value:
            return value
    raise ValueError('missing-agent-key')


def _request(url, *, method, headers, body, timeout):
    request = urllib.request.Request(url, data=body, method=method)
    for name, value in headers.items():
        request.add_header(name, value)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirect(),
        urllib.request.HTTPHandler(),
    )
    opener.addheaders = []
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.status != 200:
                raise PreflightFailure('unexpected-status')
            payload = response.read(MAX_RESPONSE_BYTES + 1)
            if len(payload) > MAX_RESPONSE_BYTES:
                raise PreflightFailure('response-too-large')
            return json.loads(payload.decode('utf-8'))
    except urllib.error.HTTPError as exc:
        raise PreflightFailure(f'http-{exc.code}')
    except (urllib.error.URLError, OSError):
        raise PreflightFailure('transport-error')
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise PreflightFailure('bad-json')


def _preflight(base, auth, transport):
    headers = {'Authorization': f'Bearer {auth}', 'Accept': 'application/json'}
    recovery = transport(f'{base}/v1/model/recovery', method='GET',
                        headers=headers, body=None, timeout=PREFLIGHT_TIMEOUT)
    if isinstance(recovery, dict) and recovery.get('pending') is True:
        raise PreflightFailure('model-switch-recovery-required')
    if not isinstance(recovery, dict) or recovery.get('pending') is not False \
            or recovery.get('error'):
        raise PreflightFailure('recovery-not-clear')
    status = transport(f'{base}/v1/model/status', method='GET',
                      headers=headers, body=None, timeout=PREFLIGHT_TIMEOUT)
    if not isinstance(status, dict) or status.get('modelTransactionPending') is not False \
            or status.get('status') not in ('idle', 'complete', 'already_downloaded') \
            or status.get('error'):
        raise PreflightFailure('status-not-idle')


def _wait_for_preflight(base, auth, transport):
    # A fresh installer starts the downloader before its host agent. Retry only
    # read-only connection readiness, never a pending recovery or a POST.
    deadline = time.monotonic() + PREFLIGHT_WAIT_SECONDS
    while True:
        try:
            _preflight(base, auth, transport)
            return
        except PreflightFailure as exc:
            if str(exc) not in ('transport-error', 'http-503'):
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PreflightFailure('host-agent-readiness-timeout') from exc
            time.sleep(min(2.0, remaining))


def _post_activation(base, auth, model_id, context, transport):
    body = json.dumps({'model_id': model_id, 'context_length': context}).encode('utf-8')
    headers = {'Authorization': f'Bearer {auth}', 'Content-Type': 'application/json',
               'Accept': 'application/json'}
    try:
        return transport(f'{base}/v1/model/activate', method='POST',
                        headers=headers, body=body, timeout=ACTIVATION_TIMEOUT)
    except (PreflightFailure, OSError, ValueError) as exc:
        raise AmbiguousActivation('activation-result-unconfirmed') from exc


def _verify_activation(response, model_id, full_gguf, full_llm, context):
    if not isinstance(response, dict):
        raise AmbiguousActivation('bad-response')
    if response.get('status') != 'activated':
        raise AmbiguousActivation('status-not-activated')
    if response.get('model_id') != model_id:
        raise AmbiguousActivation('model-id-mismatch')
    if response.get('gguf_file') != full_gguf:
        raise AmbiguousActivation('gguf-mismatch')
    if response.get('llm_model') != full_llm:
        raise AmbiguousActivation('llm-mismatch')
    ctx = response.get('context_length')
    if isinstance(ctx, bool) or not isinstance(ctx, int) or ctx != context:
        raise AmbiguousActivation('context-mismatch')
    consumers = response.get('consumers')
    if not isinstance(consumers, dict) or consumers.get('pixel') != 'reconciled':
        raise AmbiguousActivation('consumer-not-reconciled')
    if response.get('error') or response.get('pending'):
        raise AmbiguousActivation('error-or-pending')


def _verify_persisted(env_path, full_gguf, full_llm, context):
    env = _read_private_env(env_path)
    if env.get('ODS_MODE', 'local').strip().lower() not in {'local', 'hybrid'} or env.get('GPU_BACKEND') != 'apple':
        raise AmbiguousActivation('persisted-runtime-mode-mismatch')
    if env.get('GGUF_FILE') != full_gguf:
        raise AmbiguousActivation('persisted-gguf-mismatch')
    if env.get('LLM_MODEL') != full_llm:
        raise AmbiguousActivation('persisted-llm-mismatch')
    for key in ('MAX_CONTEXT', 'CTX_SIZE'):
        raw = env.get(key)
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            raise AmbiguousActivation(f'persisted-{key}-invalid')
        if parsed != context:
            raise AmbiguousActivation(f'persisted-{key}-mismatch')


def _verify_runtime(base, auth, full_gguf, context, install_dir, transport):
    headers = {'Authorization': f'Bearer {auth}', 'Accept': 'application/json'}
    recovery = transport(f'{base}/v1/model/recovery', method='GET',
                        headers=headers, body=None, timeout=PREFLIGHT_TIMEOUT)
    if not isinstance(recovery, dict) or recovery.get('pending') is not False \
            or recovery.get('error'):
        raise AmbiguousActivation('post-recovery-not-clear')
    status = transport(f'{base}/v1/model/status', method='GET',
                      headers=headers, body=None, timeout=PREFLIGHT_TIMEOUT)
    if not isinstance(status, dict):
        raise AmbiguousActivation('post-status-malformed')
    runtime = status.get('activeRuntime')
    if not isinstance(runtime, dict):
        raise AmbiguousActivation('active-runtime-missing')
    if runtime.get('source') != 'local-switchboard':
        raise AmbiguousActivation('active-runtime-source-mismatch')
    canonical = str(install_dir / 'data' / 'models' / full_gguf)
    if runtime.get('model') not in (full_gguf, canonical):
        raise AmbiguousActivation('active-runtime-model-mismatch')
    ctx = runtime.get('contextLength')
    if isinstance(ctx, bool) or not isinstance(ctx, int) or ctx != context:
        raise AmbiguousActivation('active-runtime-context-mismatch')
    if status.get('activeAgentViable') is not True:
        raise AmbiguousActivation('active-agent-not-viable')
    if status.get('modelTransactionPending') is not False:
        raise AmbiguousActivation('post-transaction-pending')


def _managed_bindings(env):
    key = env.get('PIXEL_OPENWEBUI_KEY')
    config = env.get('PIXEL_NATIVE_CONFIG_PATH')
    if not key and not config:
        return False
    if not key or not config:
        raise ValueError('incomplete-native-bindings')
    return True


def _canonical_env_path(install_dir):
    install = Path(install_dir)
    env_path = install / '.env'
    try:
        resolved = env_path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError('env-path-unresolvable') from exc
    if resolved != env_path:
        raise ValueError('env-path-not-canonical')
    return env_path


def run(install_dir, full_gguf, full_llm, full_context, *, env_path=None,
        platform=None, request=None):
    if platform is None:
        platform = sys.platform
    if platform != 'darwin':
        raise SystemExit('darwin-only')
    install = _validate_install_dir(install_dir)
    full_gguf = _validate_gguf_basename(full_gguf)
    full_llm = _validate_token('llm', full_llm)
    full_context = _validate_token('context', full_context)
    try:
        context = int(full_context)
    except ValueError:
        raise ValueError('invalid-context')
    if context <= 0:
        raise ValueError('invalid-context')
    canonical_env = _canonical_env_path(install)
    if env_path is not None:
        try:
            supplied = Path(env_path).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError('env-path-unresolvable') from exc
        if supplied != canonical_env:
            raise ValueError('env-path-not-canonical')
    env = _read_private_env(canonical_env)
    managed = _managed_bindings(env)
    if not managed:
        if os.path.lexists(install / 'data' / 'pixel-native'):
            raise ValueError('incomplete-native-bindings')
        return 10
    if env.get('GPU_BACKEND') != 'apple':
        raise ValueError('gpu-backend-not-apple')
    model_id, catalog_context = _load_catalog(install, full_gguf, full_llm)
    if context < 4096 or context > catalog_context:
        raise ValueError('context-out-of-range')
    port = _resolve_port(env)
    auth = _resolve_auth(env)
    base = f'http://127.0.0.1:{port}'
    transport = request if request is not None else _request
    _wait_for_preflight(base, auth, transport)
    try:
        response = _post_activation(base, auth, model_id, context, transport)
        _verify_activation(response, model_id, full_gguf, full_llm, context)
        _verify_persisted(canonical_env, full_gguf, full_llm, context)
        _verify_runtime(base, auth, full_gguf, context, install, transport)
    except (OSError, ValueError, PreflightFailure) as exc:
        raise AmbiguousActivation('activation-readback-unconfirmed') from exc
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Darwin bootstrap promotion client')
    parser.add_argument('install_dir')
    parser.add_argument('full_gguf')
    parser.add_argument('full_llm')
    parser.add_argument('full_context')
    args = parser.parse_args(argv)
    try:
        return run(args.install_dir, args.full_gguf, args.full_llm,
                   args.full_context)
    except AmbiguousActivation as exc:
        print(f'ambiguous_activation: {exc}', file=sys.stderr)
        return 1
    except PreflightFailure as exc:
        print(f'preflight_failure: {exc}', file=sys.stderr)
        if str(exc) == 'model-switch-recovery-required':
            print('An existing model switch needs recovery. This upgrade did not '
                  'start another activation. Preserve data/pixel-model-transaction.json; '
                  'see docs/MACOS-MODEL-PROMOTION.md before retrying.', file=sys.stderr)
        return 1
    except (ValueError, OSError):
        print('validation_failure: invalid-installation-state', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
