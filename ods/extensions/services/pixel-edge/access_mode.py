"""Bounded public projection; no controller paths, keys or receipts escape."""
import re


def valid_change(value):
    return (type(value) is dict and set(value) == {'mode', 'revision', 'confirmed'}
            and value['mode'] in ('sandboxed', 'full-access')
            and type(value['confirmed']) is bool
            and (value['mode'] != 'full-access' or value['confirmed'])
            and type(value['revision']) is str and re.fullmatch('[a-f0-9]{64}', value['revision']))


def public_status(value):
    if type(value) is not dict:
        raise ValueError()
    keys = ('available', 'surface', 'configured_mode', 'effective_mode', 'runtime_verified',
            'revision', 'busy', 'pending', 'reason', 'scope')
    result = {key: value.get(key) for key in keys}
    if (any(type(result[key]) is not bool for key in ('available', 'runtime_verified', 'busy', 'pending'))
            or result['surface'] not in ('linux-systemd', 'wsl-systemd', 'linux', 'darwin', 'windows')
            or result['scope'] != 'owner-host'
            or any(result[key] not in ('sandboxed', 'full-access', 'unknown')
                   for key in ('configured_mode', 'effective_mode'))
            or result['revision'] is not None and (type(result['revision']) is not str or not re.fullmatch('[a-f0-9]{64}', result['revision']))
            or result['reason'] is not None and (type(result['reason']) is not str or not re.fullmatch('[a-z][a-z0-9-]{0,95}', result['reason']))
            or result['runtime_verified'] != (result['effective_mode'] != 'unknown')
            or result['runtime_verified'] and (not result['available'] or result['pending']
                or result['configured_mode'] != result['effective_mode'])):
        raise ValueError()
    return result


# Same public connected-apps contract as bin/pixel_connections/status.py
# validate_public; tests/test_pixel_connection_wire.py keeps the two in step.
_CONNECTION_STATES = frozenset({'not-connected', 'first-sync', 'connected', 'needs-reconnect', 'blocked-by-admin',
                                'setup-broken', 'missing-permission', 'unknown-error', 'behind'})
_CAPABILITIES = frozenset({'calendar.read', 'calendar.write', 'calendar.direct', 'gmail.read', 'gmail.drafts'})
_TOKEN = re.compile(r'[a-z][a-z0-9-]{0,63}')
_INSTANT = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z')
_EMAIL = re.compile(r'[^@\s<>\x00-\x1f\x7f]{1,64}@[^@\s<>\x00-\x1f\x7f]{1,253}')
_ZONE = re.compile(r'[A-Za-z][A-Za-z0-9_+\-/]{0,63}')
_SCOPE = re.compile(r'(?:https://www\.googleapis\.com/auth/[a-z0-9._-]{1,100}|https://mail\.google\.com/|openid|email|profile)')


def _maybe(value, pattern):
    return value is None or type(value) is str and pattern.fullmatch(value) is not None


def _connection_source(value):
    return value is None or (
        type(value) is dict
        and set(value) == {'state', 'errorClass', 'httpStatus', 'lastSuccessAt', 'failingSince', 'recordCount', 'complete'}
        and value['state'] in ('ok', 'error', 'disabled') and _maybe(value['errorClass'], _TOKEN)
        and (value['httpStatus'] is None or type(value['httpStatus']) is int and 100 <= value['httpStatus'] <= 599)
        and _maybe(value['lastSuccessAt'], _INSTANT) and _maybe(value['failingSince'], _INSTANT)
        and type(value['recordCount']) is int and value['recordCount'] >= 0
        and (value['complete'] is None or type(value['complete']) is bool))


def _connection_google(value):
    if (type(value) is not dict or set(value) != {'state', 'reason', 'account', 'capabilities', 'cloudModels',
                                                  'connectedAt', 'grantedScopes', 'checkedAt', 'sources'}
            or value['state'] not in _CONNECTION_STATES):
        return False
    connected = value['state'] != 'not-connected'
    capabilities, scopes, sources = value['capabilities'], value['grantedScopes'], value['sources']
    return (_maybe(value['reason'], _TOKEN) and (value['account'] is None) != connected
            and _maybe(value['account'], _EMAIL) and (capabilities is None) != connected
            and (capabilities is None or type(capabilities) is dict and set(capabilities) == _CAPABILITIES
                 and all(type(flag) is bool for flag in capabilities.values()))
            and (value['cloudModels'] is None or type(value['cloudModels']) is bool)
            and _maybe(value['connectedAt'], _INSTANT) and _maybe(value['checkedAt'], _INSTANT)
            and (scopes is None or type(scopes) is list and all(type(item) is str and _SCOPE.fullmatch(item) for item in scopes))
            and type(sources) is dict and set(sources) == {'email', 'calendar'}
            and all(_connection_source(item) for item in sources.values()))


def public_connections(value):
    if (type(value) is not dict
            or set(value) != {'schemaVersion', 'available', 'surface', 'reason', 'ownerTimeZone', 'connectors'}
            or value['schemaVersion'] != 1 or type(value['available']) is not bool
            or value['surface'] not in ('linux-systemd', 'wsl-systemd', 'linux', 'darwin', 'windows')
            or not _maybe(value['reason'], _TOKEN) or not _maybe(value['ownerTimeZone'], _ZONE)
            or type(value['connectors']) is not dict or set(value['connectors']) != {'google'}
            or not _connection_google(value['connectors']['google'])):
        raise ValueError('invalid-connection-status')
    return value


# Same bounded model-control wire contract as the host relay.
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

