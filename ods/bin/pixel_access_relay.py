"""Fixed owner-authorized transport to the configured agent's access controller.

The Dashboard host can be Windows/macOS while the managed agent is in Linux.
Use its existing Edge container and private ingress, never a guessed WSL user,
distribution, public listener, or caller-selected executable/endpoint.
"""
import json
import platform
import re
import subprocess


class AccessRelayError(RuntimeError):
    pass


# Runs in the inspected Edge image. Credentials travel only over stdin, and
# response allocation is bounded before any bytes reach the host process.
_CLIENT = r'''
import http.client, json, sys
try:
    value = json.loads(sys.stdin.buffer.read(8193))
    request = value['request']
    connection = http.client.HTTPConnection('127.0.0.1', 9595, timeout=value['timeout'])
    path = value.get('path', '/v1/access-mode')
    if path not in ('/v1/access-mode', '/v1/model-control'):
        raise ValueError()
    connection.request('GET' if request is None else 'POST', path,
        None if request is None else json.dumps(request).encode(),
        {'Authorization': 'Bearer ' + value['key'], 'Content-Type': 'application/json'})
    response = connection.getresponse()
    raw = response.read(65537)
    if len(raw) > 65536 or response.status not in (200, 400, 403, 409, 503):
        raise ValueError()
    body = json.loads(raw)
    if type(body) is not dict:
        raise ValueError()
    sys.stdout.write(json.dumps({'status': response.status, 'body': body}))
except Exception:
    sys.exit(1)
'''


def valid_change(value):
    return (type(value) is dict and set(value) == {'mode', 'revision', 'confirmed'}
            and value['mode'] in ('sandboxed', 'full-access')
            and type(value['revision']) is str and re.fullmatch('[a-f0-9]{64}', value['revision'])
            and type(value['confirmed']) is bool
            and (value['mode'] != 'full-access' or value['confirmed']))


def valid_model_contract(value):
    required = {'model', 'contextLength', 'maxTokens', 'reasoning'}
    return (type(value) is dict and set(value) in (required, required | {'routeFingerprint'})
            and type(value['model']) is str
            and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+:/ @(),=-]{0,255}', value['model']) is not None
            and type(value['contextLength']) is int and 4096 <= value['contextLength'] <= 10000000
            and type(value['maxTokens']) is int and 1 <= value['maxTokens'] <= value['contextLength']
            and type(value['reasoning']) is bool
            and ('routeFingerprint' not in value or _model_hex(value['routeFingerprint'])))


def _model_hex(value):
    return type(value) is str and re.fullmatch('[a-f0-9]{64}', value) is not None


def valid_model_control(value):
    if type(value) is not dict:
        return False
    operation = value.get('operation')
    if operation == 'model-status':
        return set(value) == {'operation'}
    if set(value) != {'operation', 'request'} or type(value.get('request')) is not dict:
        return False
    request = value['request']
    if not _model_hex(request.get('transactionId')):
        return False
    if operation == 'model-begin':
        return set(request) == {'transactionId', 'revision'} and _model_hex(request['revision'])
    if operation == 'model-apply':
        return set(request) == {'transactionId', 'target'} and valid_model_contract(request['target'])
    if operation == 'model-finish':
        return set(request) == {'transactionId', 'outcome'} and request['outcome'] in ('commit', 'rollback')
    return False


def public_model_control(value):
    keys = {'schemaVersion', 'status', 'revision', 'contract', 'pending', 'transactionId', 'outcome'}
    if (type(value) is not dict or not keys.issubset(value)
            or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
            or value['status'] not in ('ready', 'held', 'applied', 'completed')
            or not _model_hex(value['revision']) or not valid_model_contract(value['contract'])
            or type(value['pending']) is not bool
            or value['pending'] != (value['status'] in ('held', 'applied'))
            or (value['transactionId'] is not None and not _model_hex(value['transactionId']))
            or (value['status'] == 'ready') != (value['transactionId'] is None)
            or (value['status'] == 'completed' and value['outcome'] not in ('commit', 'rollback'))
            or (value['status'] != 'completed' and value['outcome'] is not None)):
        raise ValueError('invalid-model-control-response')
    return {key: value[key] for key in keys}


def request_runtime_model_control(operation, request=None, *, config):
    payload = {'operation': operation}
    if request is not None:
        payload['request'] = request
    if not valid_model_control(payload):
        return 400, {'error': 'invalid-request'}
    if not config.get('PIXEL_OPENWEBUI_KEY'):
        raise AccessRelayError('managed-model-controller-unavailable')
    status, body = _request_runtime_controller(payload, config=config, path='/v1/model-control',
                                              timeout=22 if operation == 'model-status' else 310)
    if status == 200:
        try:
            body = public_model_control(body)
        except ValueError:
            raise AccessRelayError('model-change-unconfirmed') from None
    return status, body


def request_runtime_access(operation, request=None, *, config):
    if operation not in ('status', 'change') or operation == 'change' and not valid_change(request):
        return 400, {'error': 'invalid-request'}
    if not config.get('PIXEL_OPENWEBUI_KEY'):
        if platform.system() == 'Linux':
            from pixel_access_client import request_access
            return request_access(operation, request)
        return 200, dict(available=False, surface=platform.system().lower(), configured_mode='unknown',
                        effective_mode='unknown', runtime_verified=False, revision=None, busy=False,
                        pending=False, scope='owner-host', reason='managed-runtime-unavailable')
    return _request_runtime_controller(request if operation == 'change' else None, config=config,
                                       path='/v1/access-mode', timeout=310 if operation == 'change' else 22)


def _request_runtime_controller(request, *, config, path, timeout):
    key = config.get('DASHBOARD_API_KEY', '')
    if (not isinstance(key, str) or not re.fullmatch(r'[!-~]{32,4096}', key)
            or key == config.get('PIXEL_OPENWEBUI_KEY')):
        raise AccessRelayError('access-owner-auth-unavailable')
    options = {'stdout': subprocess.PIPE, 'stderr': subprocess.DEVNULL, 'check': True}
    if platform.system() == 'Windows':
        options['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    try:
        inspected = subprocess.run(['docker', 'inspect', 'ods-pixel-edge', '--format',
            '{{.Id}} {{.State.Running}} {{index .Config.Labels "com.docker.compose.service"}}'],
            timeout=5, **options).stdout.decode().strip().split()
        if (len(inspected) != 3 or not re.fullmatch('[a-f0-9]{64}', inspected[0])
                or inspected[1:] != ['true', 'pixel-edge']):
            raise AccessRelayError('agent-access-runtime-unavailable')
        payload = json.dumps({'key': key, 'request': request, 'path': path, 'timeout': timeout}).encode()
        result = subprocess.run(['docker', 'exec', '-i', inspected[0], 'python3', '-I', '-c', _CLIENT],
                                input=payload, timeout=timeout + 3, **options)
        if len(result.stdout) > 65536:
            raise ValueError()
        value = json.loads(result.stdout)
        if (type(value) is not dict or set(value) != {'status', 'body'}
                or type(value['status']) is not int or value['status'] not in (200, 400, 403, 409, 503)
                or type(value['body']) is not dict):
            raise ValueError()
        return value['status'], value['body']
    except (OSError, subprocess.SubprocessError, ValueError):
        # A timeout never means the mutation was cancelled. Refresh status;
        # only the durable controller may recover or release admission holds.
        raise AccessRelayError('agent-access-runtime-unavailable') from None
