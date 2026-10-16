"""Bounded, observational API checks after native macOS installer continuation.

This does not activate services or certify protected recovery, chat/tool delivery
or release identity. Credentials and upstream response bodies are never printed.
"""
import argparse
import http.client
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import sys


HERE = Path(__file__).resolve().parent
MAX_RESPONSE = 1024 * 1024


def load(name):
    spec = importlib.util.spec_from_file_location('readiness_' + name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _http_worker():
    # A separate process gives the parent a wall-clock deadline even when a
    # listener trickles headers/body bytes often enough to avoid socket timeout.
    request = json.loads(sys.stdin.buffer.read(16385))
    port, path, key = request['port'], request['path'], request.get('key', '')
    if (type(port) is not int or not 1 <= port <= 65535 or type(path) is not str
            or not path.startswith('/') or len(path) > 2048
            or any(ord(c) < 33 or ord(c) > 126 for c in path)
            or type(key) is not str or len(key) > 4096
            or any(ord(c) < 33 or ord(c) > 126 for c in key)):
        raise ValueError('invalid-probe')
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
    try:
        headers = {'Authorization': 'Bearer ' + key} if key else {}
        connection.request('GET', path, headers=headers)
        response = connection.getresponse()
        # No redirects and no proxy environment: a local credential must stay
        # on the exact loopback endpoint selected by this installation.
        if response.status != 200:
            raise ValueError('http-status')
        body = response.read(MAX_RESPONSE + 1)
        if len(body) > MAX_RESPONSE:
            raise ValueError('response-too-large')
        value = json.loads(body) if request.get('json', True) else {'httpStatus': 200}
        if type(value) not in (dict, list):
            raise ValueError('invalid-response')
        print(json.dumps(value))
    finally:
        connection.close()


def probe(port, path, *, key='', json_response=True, timeout=30):
    payload = json.dumps({'port': port, 'path': path, 'key': key, 'json': json_response})
    if len(payload.encode()) > 16384:
        raise ValueError('native-readiness-request-invalid')
    try:
        result = subprocess.run([sys.executable, '-I', str(Path(__file__).resolve()), '--http-probe'],
            input=payload, capture_output=True, text=True, cwd='/', timeout=timeout, check=False)
        if result.returncode or len(result.stdout) > 6 * MAX_RESPONSE:
            raise ValueError('native-readiness-http-failed')
        return json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        raise ValueError('native-readiness-http-failed') from None


def port(values, name, default):
    value = values.get(name, str(default))
    if not re.fullmatch(r'[1-9][0-9]{0,4}', value) or int(value) > 65535:
        raise ValueError('native-readiness-port-invalid')
    return int(value)


def service_checks(services, rows):
    """Interpret the rendered selection, including init jobs and scale-to-zero."""
    if (type(services) is not dict or not services or len(services) > 512
            or any(type(name) is not str or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}', name)
                   or type(value) is not dict for name, value in services.items())
            or type(rows) is not list or len(rows) > 1024 or any(type(row) is not dict for row in rows)):
        raise ValueError('native-readiness-services-invalid')
    completed_jobs = set()
    for definition in services.values():
        if type(definition.get('deploy', {})) is not dict:
            raise ValueError('native-readiness-services-invalid')
        if definition.get('deploy', {}).get('replicas', definition.get('scale', 1)) == 0:
            continue
        dependencies = definition.get('depends_on', {})
        if type(dependencies) is not dict:
            raise ValueError('native-readiness-services-invalid')
        for name, dependency in dependencies.items():
            if type(dependency) is not dict:
                raise ValueError('native-readiness-services-invalid')
            if dependency.get('condition') == 'service_completed_successfully':
                completed_jobs.add(name)
    checks = []
    for name, definition in services.items():
        # Compose config already filters inactive profiles. A retained profile
        # present in this rendering must be checked like every other service.
        deploy = definition.get('deploy', {})
        if type(deploy) is not dict:
            raise ValueError('native-readiness-services-invalid')
        replicas = deploy.get('replicas', definition.get('scale', 1))
        health = definition.get('healthcheck', {})
        if type(replicas) is not int or not 0 <= replicas <= 512 or type(health) is not dict:
            raise ValueError('native-readiness-services-invalid')
        matches = [row for row in rows if row.get('Service') == name]
        if replicas == 0:
            passed = all(row.get('State') in ('exited', 'dead') for row in matches)
        elif name in completed_jobs:
            passed = len(matches) == replicas and all(row.get('State') == 'exited'
                and type(row.get('ExitCode')) is int and row['ExitCode'] == 0 for row in matches)
        else:
            requires_health = bool(health) and health.get('disable') is not True and health.get('test') != ['NONE']
            passed = len(matches) == replicas and all(row.get('State') == 'running'
                and (row.get('Health') == 'healthy' if requires_health else row.get('Health') in ('', 'none', None))
                for row in matches)
        checks.append({'name': name, 'passed': passed})
    if not checks:
        raise ValueError('native-readiness-services-invalid')
    return checks


def observe_services(install_dir):
    """Read the installed gateway's Docker transport; never start a service."""
    continuation = load('pixel-native-continuation')
    values, snapshot = continuation._saved_environment(install_dir, {'PIXEL_NATIVE_GATEWAY_PORT'},
        'duplicate-retained-readiness-setting')
    gateway_port = port(values, 'PIXEL_NATIVE_GATEWAY_PORT', 18789)
    installer, compose = load('pixel-macos-access-install'), load('pixel-native-compose')
    stack, finalize = load('pixel-native-stack'), load('pixel-native-finalize')
    owner = pwd.getpwuid(os.getuid())
    document, environment, *_ = installer._source_gateway(installer._launchd.GATEWAY_PLIST, owner.pw_name, gateway_port)
    transport = {key: environment[name] for key, name in (
        ('docker', 'PIXEL_HISTORY_DOCKER'), ('project', 'PIXEL_HISTORY_PROJECT'),
        ('image', 'PIXEL_HISTORY_IMAGE'), ('user', 'PIXEL_HISTORY_USER'))}
    installer._native_transport_environment(transport, owner)
    endpoint = environment.get('DOCKER_HOST', '')
    if not endpoint.startswith('unix:///') or not Path(endpoint[7:]).is_socket():
        raise ValueError('installed-local-docker-socket-required')
    process_env = {'HOME': owner.pw_dir, 'PATH': environment['PATH'],
        'DOCKER_HOST': endpoint, 'DOCKER_CONFIG': environment['DOCKER_CONFIG']}
    tokens = finalize.compose_flags(install_dir, process_env)
    original = [install_dir / value for value in tokens[1::2]]
    compose.validate_stack(install_dir, original)
    paths = [install_dir / value for value in stack.resolve_files(install_dir, tokens[1::2])]
    compose.validate_stack(install_dir, paths)
    command = [transport['docker'], 'compose', '--project-directory', str(install_dir),
        '--project-name', transport['project'], '--env-file', str(install_dir / '.env')]
    for path in paths:
        if not path.is_file() or path.resolve(strict=True) != path or install_dir not in path.parents:
            raise ValueError('installed-compose-file-required')
        command.extend(['-f', str(path)])

    def unchanged():
        if (continuation.saved_model_environment(install_dir)[1] != snapshot
                or finalize.compose_flags(install_dir, process_env) != tokens
                or [install_dir / value for value in stack.resolve_files(install_dir, tokens[1::2])] != paths
                or installer._source_gateway(installer._launchd.GATEWAY_PLIST, owner.pw_name, gateway_port)[0] != document):
            raise ValueError('native-readiness-selection-changed')
        compose.validate_stack(install_dir, original)
        compose.validate_stack(install_dir, paths)

    def read(*args):
        unchanged()
        result = subprocess.run([*command, *args], cwd=install_dir, env=process_env,
            capture_output=True, text=True, timeout=30, check=False)
        if result.returncode or len(result.stdout) > 4 * MAX_RESPONSE:
            raise ValueError('native-readiness-compose-unavailable')
        return result.stdout

    rendered = json.loads(read('config', '--format', 'json'))
    if type(rendered) is not dict or rendered.get('name') != transport['project']:
        raise ValueError('native-compose-project-mismatch')
    body = read('ps', '--all', '--format', 'json').strip()
    rows = json.loads(body) if body.startswith('[') else [json.loads(line) for line in body.splitlines()]
    services = rendered.get('services')
    checks = service_checks(services, rows)
    if any(row.get('Project') != transport['project'] for row in rows):
        raise ValueError('native-compose-project-mismatch')
    if json.loads(read('config', '--format', 'json')) != rendered:
        raise ValueError('native-readiness-selection-changed')
    unchanged()
    selected = {name: value for name, value in services.items()
                if value.get('deploy', {}).get('replicas', value.get('scale', 1)) != 0}
    return selected, checks


def observe_apis(install_dir, *, opencode_choice=None, request=probe, include_services=False):
    continuation = load('pixel-native-continuation')
    values, snapshot = continuation._saved_environment(install_dir, continuation.MODEL_KEYS | {
        'DASHBOARD_API_KEY', 'DASHBOARD_API_PORT', 'DASHBOARD_PORT', 'ODS_NATIVE_LLAMA_PORT',
        'LITELLM_PORT', 'LITELLM_KEY', 'ODS_MODEL_SWITCHBOARD', 'ENABLE_OPENCODE'},
        'duplicate-retained-readiness-setting')
    key = values.get('DASHBOARD_API_KEY', '')
    if not re.fullmatch('[a-f0-9]{64}', key):
        raise ValueError('native-readiness-dashboard-key-required')
    api_port = port(values, 'DASHBOARD_API_PORT', 3002)
    dashboard_port = port(values, 'DASHBOARD_PORT', 3001)
    selected_services, docker_checks = ({'dashboard-api': {}}, None)
    if include_services:
        selected_services, docker_checks = observe_services(install_dir)
    optional, optional_snapshot = continuation.optional_setup_selection(
        install_dir, selected_services, opencode_choice=opencode_choice)
    plan, model_snapshot = continuation.inspect_model_upgrade(
        install_dir, **continuation.bootstrap_settings(install_dir))
    if snapshot != optional_snapshot or snapshot != model_snapshot:
        raise ValueError('native-readiness-environment-changed')
    checks = []
    if docker_checks is not None:
        checks.append({'name': 'selected-service-health', 'passed': all(item['passed'] for item in docker_checks)})

    def check(name, operation):
        try:
            passed = operation() is True
        except (ValueError, TypeError, KeyError, IndexError, AttributeError, OSError):
            passed = False
        checks.append({'name': name, 'passed': passed})

    def api(path):
        return request(api_port, path, key=key)

    check('dashboard', lambda: request(dashboard_port, '/', json_response=False).get('httpStatus') == 200)
    check('dashboard-api', lambda: api('/health').get('status') == 'ok')
    application = None

    def host_agent():
        nonlocal application
        # This Dashboard endpoint uses the authenticated host-agent client;
        # failures are 503/502, not a cached or synthetic success response.
        application = api('/api/apps/opencode')
        return (application.get('id') == 'opencode' and application.get('platform') == 'darwin'
                and application.get('state') in ('running', 'starting', 'installing', 'stopped', 'not_installed'))
    check('authenticated-host-agent', host_agent)
    selected = optional['opencode']['selected']
    if selected is None:
        checks.append({'name': 'opencode-choice', 'passed': False})
    elif selected:
        check('selected-opencode', lambda: application.get('state') == 'running'
              and application.get('installed') is True and application.get('running') is True)

    if values.get('ODS_MODE') == 'local':
        runtime_port = port(values, 'ODS_NATIVE_LLAMA_PORT', 8080)
        filename = values['MODEL_RECOMMENDED_GGUF']
        aliases = {filename, values['MODEL_RECOMMENDED_MODEL'], plan.get('modelId')}
        context = int(values['MODEL_RECOMMENDED_CONTEXT'])

        def native_model():
            if plan['status'] != 'selected-model':
                return False
            if request(runtime_port, '/health').get('status') != 'ok':
                return False
            models = request(runtime_port, '/v1/models').get('data')
            if not isinstance(models, list) or not any(
                    isinstance(item, dict) and item.get('id') in aliases for item in models):
                return False
            props = request(runtime_port, '/props')
            actual = props.get('default_generation_settings', {}).get('n_ctx')
            file = props.get('model_path')
            return (type(file) is str and Path(file).name == filename
                    and type(actual) is int and context <= actual <= ((context + 255) // 256) * 256)
        check('selected-native-model', native_model)
        check('dashboard-model-selection', lambda: api('/api/models').get('currentModel') == plan['modelId'])
    else:
        cloud_key = values.get('LITELLM_KEY', '')
        alias = 'ods/current' if values.get('ODS_MODEL_SWITCHBOARD', 'enabled') == 'enabled' else 'default'
        check('cloud-model-route', lambda: bool(cloud_key) and any(
            isinstance(item, dict) and item.get('id') == alias for item in request(
                port(values, 'LITELLM_PORT', 4000), '/v1/models', key=cloud_key).get('data', [])))

    def extensions():
        result = api('/api/extensions/catalog')
        return (result.get('agent_available') is True and result.get('library_available') is True
                and isinstance(result.get('extensions'), list) and bool(result['extensions']))
    check('extensions-catalog', extensions)
    release_state = 'unverified'

    def portal():
        nonlocal release_state
        result = api('/api/pixel/status')
        readiness = result.get('readiness', {})
        release_state = 'mismatch' if readiness.get('releaseState') == 'mismatch' else 'unverified'
        return (result.get('available') is True and readiness.get('routeAvailable') is True
                and readiness.get('accessState') == 'verified'
                and readiness.get('effectiveMode') in ('sandboxed', 'full-access')
                and release_state != 'mismatch')
    check('portal-access', portal)
    if continuation.saved_model_environment(install_dir)[1] != snapshot:
        raise ValueError('native-readiness-environment-changed')
    pending = ['protected-recovery', 'selected-optional-state', 'model-completion', 'portal-chat-and-preview']
    if docker_checks is None or not all(item['passed'] for item in docker_checks):
        pending.insert(1, 'selected-service-health')
    if (docker_checks is not None and optional['opencode']['selected'] is False
            and not optional['whisperModel'] and not optional['perplexica']):
        pending.remove('selected-optional-state')
    return {'status': 'api-checks-passed' if all(c['passed'] for c in checks) else 'needs-attention',
        'checks': checks, 'installerComplete': False, 'releaseState': release_state,
        **({'serviceChecks': docker_checks} if docker_checks is not None else {}),
        'pendingVerification': pending}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--http-probe', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--install-dir')
    parser.add_argument('--opencode-choice', choices=('enabled', 'disabled'))
    parser.add_argument('--include-services', action='store_true',
        help='Also inspect the retained Compose selection using the installed native Docker transport; no service changes')
    args = parser.parse_args()
    if args.http_probe:
        try:
            _http_worker()
            return 0
        except Exception:
            print('http-probe-failed', file=sys.stderr)
            return 1
    if not args.install_dir:
        parser.error('--install-dir is required')
    if sys.platform != 'darwin' or os.geteuid() == 0:
        parser.error('run as the signed-in macOS owner')
    try:
        result = observe_apis(Path(args.install_dir).expanduser().resolve(strict=True),
                              opencode_choice=args.opencode_choice, include_services=args.include_services)
    except (ValueError, OSError, KeyError, subprocess.SubprocessError):
        print(json.dumps({'status': 'inspection-unavailable', 'installerComplete': False}))
        return 1
    print(json.dumps(result))
    return 0 if result['status'] == 'api-checks-passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
