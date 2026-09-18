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


# Same bounded model-control wire contract as the host relay.
def valid_model_contract(value):
    required = {'model', 'contextLength', 'maxTokens', 'reasoning'}
    return (type(value) is dict and set(value) in (required, required | {'routeFingerprint'})
            and type(value['model']) is str and value['model'] == value['model'].strip()
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

