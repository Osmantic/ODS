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
    if path not in ('/v1/access-mode', '/v1/model-control', '/v1/connections'):
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
    return (type(value) is dict and required <= set(value) <= required | {'routeFingerprint', 'imageInput', 'thinkingControl'}
            and type(value['model']) is str
            and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+:/ @(),=-]{0,255}', value['model']) is not None
            and type(value['contextLength']) is int and 4096 <= value['contextLength'] <= 10000000
            and type(value['maxTokens']) is int and 1 <= value['maxTokens'] <= value['contextLength']
            and type(value['reasoning']) is bool
            and ('routeFingerprint' not in value or _model_hex(value['routeFingerprint']))
            and ('imageInput' not in value or value['imageInput'] in ('supported', 'unsupported', 'unknown'))
            and ('thinkingControl' not in value or value['thinkingControl'] in ('enable_thinking', 'always', 'none')))


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


def request_runtime_connections(*, config):
    """Connected-apps status from the agent's root controller, read-only."""
    system = platform.system()
    if system == 'Darwin' or not config.get('PIXEL_OPENWEBUI_KEY') and system != 'Linux':
        # The Source Broker runs only under Linux or WSL systemd.
        from pixel_connections.status import unavailable
        return 200, unavailable('darwin' if system == 'Darwin' else system.lower())
    if not config.get('PIXEL_OPENWEBUI_KEY'):
        from pixel_access_client import request_access
        return request_access('connection-status')
    return _request_runtime_controller(None, config=config, path='/v1/connections', timeout=22)


# Each action's controller budget; the relay waits a little longer than the controller.
CONNECTION_TIMEOUTS = {'connection-begin': 70, 'connection-complete': 160, 'connection-activate': 310,
                       'connection-cancel': 30, 'connection-disconnect': 160, 'connection-sync': 30}


def request_runtime_connection_action(operation, request, *, config):
    """One custodian action on the agent's root controller: sign-in, activation or disconnect."""
    payload = {'operation': operation, 'request': request}
    if not valid_connection_action(payload):
        return 400, {'error': 'invalid-request'}
    system = platform.system()
    if system == 'Darwin' or not config.get('PIXEL_OPENWEBUI_KEY') and system != 'Linux':
        return 409, {'error': 'not-available-on-this-system'}
    if not config.get('PIXEL_OPENWEBUI_KEY'):
        from pixel_access_client import request_access
        status, body = request_access(operation, request)
    else:
        status, body = _request_runtime_controller(payload, config=config, path='/v1/connections',
                                                   timeout=CONNECTION_TIMEOUTS[operation])
    if status == 200:
        try:
            body = public_connection_action(operation, body)
        except ValueError:
            raise AccessRelayError('connection-change-unconfirmed') from None
    elif not (set(body) == {'error'} and isinstance(body['error'], str) and _REASON.fullmatch(body['error'])):
        body = {'error': 'connection-change-unconfirmed'}
    return status, body


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


# Connected-apps actions: the custodian's exact request and reply shapes. The root
# coordinator validates again; tests/test_pixel_connection_wire.py keeps the host
# relay and the Edge copies identical.
_CONNECTION_OPERATIONS = ('connection-begin', 'connection-complete', 'connection-activate',
                          'connection-cancel', 'connection-disconnect', 'connection-sync')
_CAPABILITY_IDS = ('calendar.read', 'calendar.write', 'calendar.direct', 'gmail.read', 'gmail.drafts')
_FLOW = re.compile(r'[a-f0-9]{32}')
_STATE = re.compile(r'[A-Za-z0-9_-]{32,128}')
_CODE = re.compile(r'[\x21-\x7e]{1,2048}')
_CLIENT_ID = re.compile(r'[0-9]{4,32}-[a-z0-9]{8,64}\.apps\.googleusercontent\.com')
_CLIENT_SECRET = re.compile(r'[\x21-\x7e]{8,256}')
_PROJECT = re.compile(r'[a-z][a-z0-9-]{4,28}[a-z0-9]')
_ACCOUNT = re.compile(r'[^@\s<>\x00-\x1f\x7f]{1,64}@[^@\s<>\x00-\x1f\x7f]{1,253}')
_TIME_ZONE = re.compile(r'[A-Za-z][A-Za-z0-9_+\-/]{0,63}')
_SECOND = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z')
_REASON = re.compile(r'[a-z][a-z0-9-]{0,95}')
_AUTH_URL = re.compile(r'https://accounts\.google\.com/o/oauth2/auth\?[A-Za-z0-9._~%&=+-]{1,4000}')


def _text(value, pattern):
    return type(value) is str and pattern.fullmatch(value) is not None


def _capability_flags(value):
    return (type(value) is dict and set(value) == set(_CAPABILITY_IDS)
            and all(type(flag) is bool for flag in value.values()))


def valid_connection_action(value):
    if (type(value) is not dict or set(value) != {'operation', 'request'}
            or value['operation'] not in _CONNECTION_OPERATIONS or type(value['request']) is not dict):
        return False
    operation, request = value['operation'], value['request']
    if operation in ('connection-activate', 'connection-sync'):
        return request == {}
    if operation == 'connection-disconnect':
        return request == {'connector': 'google'}
    if operation == 'connection-cancel':
        return set(request) == {'flowId'} and _text(request['flowId'], _FLOW)
    if operation == 'connection-complete':
        return (set(request) in ({'flowId', 'state', 'code'}, {'flowId', 'state', 'error'})
                and _text(request['flowId'], _FLOW) and _text(request['state'], _STATE)
                and ('code' not in request or _text(request['code'], _CODE))
                and ('error' not in request or request['error'] in ('access-denied', 'consent-failed')))
    client = request.get('client')
    port = request.get('redirectPort')
    return (set(request) == {'connector', 'client', 'capabilities', 'cloudModels', 'ownerTimeZone', 'loginHint',
                             'redirectPort'}
            and request['connector'] == 'google'
            and type(client) is dict and set(client) == {'clientId', 'clientSecret', 'projectId'}
            and _text(client['clientId'], _CLIENT_ID) and _text(client['clientSecret'], _CLIENT_SECRET)
            and _text(client['projectId'], _PROJECT)
            and _capability_flags(request['capabilities']) and type(request['cloudModels']) is bool
            and (request['ownerTimeZone'] is None or _text(request['ownerTimeZone'], _TIME_ZONE))
            and (request['loginHint'] is None or _text(request['loginHint'], _ACCOUNT))
            and type(port) is int and 1024 <= port <= 65535)


def public_connection_action(operation, value):
    """The coordinator's reply, refused unless it is exactly the shape of its operation."""
    if type(value) is not dict:
        raise ValueError('invalid-connection-reply')
    if operation == 'connection-begin':
        valid = (set(value) == {'flowId', 'authUrl', 'redirectUri', 'expiresAt'} and _text(value['flowId'], _FLOW)
                 and _text(value['authUrl'], _AUTH_URL) and _text(value['expiresAt'], _SECOND)
                 and type(value['redirectUri']) is str
                 and re.fullmatch(r'http://127\.0\.0\.1:[0-9]{4,5}', value['redirectUri']) is not None)
    elif operation == 'connection-complete':
        connected = value.get('outcome') == 'connected'
        valid = (set(value) == {'outcome', 'errorClass', 'account', 'capabilities', 'missing'}
                 and value['outcome'] in ('connected', 'failed')
                 and (value['errorClass'] is None) == connected and (connected or _text(value['errorClass'], _REASON))
                 and (_text(value['account'], _ACCOUNT) if connected else value['account'] is None)
                 and (_capability_flags(value['capabilities']) if connected else value['capabilities'] is None)
                 and type(value['missing']) is list and all(item in _CAPABILITY_IDS for item in value['missing']))
    elif operation == 'connection-disconnect':
        valid = set(value) == {'outcome', 'revoked'} and value['outcome'] == 'disconnected' and type(value['revoked']) is bool
    else:
        outcomes = {'connection-activate': ('applied', 'unchanged'), 'connection-cancel': ('cancelled',),
                    'connection-sync': ('started',)}.get(operation, ())
        valid = set(value) == {'outcome'} and value['outcome'] in outcomes
    if not valid:
        raise ValueError('invalid-connection-reply')
    return value
