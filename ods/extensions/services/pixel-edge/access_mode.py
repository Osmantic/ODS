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


_PENDING_INSTANT = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z')
_PENDING_ERROR = re.compile(r'[a-z][a-z0-9-]{0,95}')


def _connection_pending(value):
    if value is None:
        return True
    if (type(value) is not dict or set(value) != {'action', 'phase', 'expiresAt', 'error', 'since'}
            or not _maybe(value['since'], _PENDING_INSTANT) or value['since'] is None
            or value['phase'] not in ('consent', 'activating', 'deactivating', 'failed')
            or value['action'] != ('disconnect' if value['phase'] == 'deactivating' else 'connect')
            or not _maybe(value['error'], _PENDING_ERROR)
            or value['phase'] == 'failed' and value['error'] is None):
        return False
    if value['phase'] == 'consent':
        return type(value['expiresAt']) is str and _PENDING_INSTANT.fullmatch(value['expiresAt']) is not None
    return value['expiresAt'] is None


def _connection_google(value):
    if (type(value) is not dict or set(value) != {'state', 'reason', 'account', 'capabilities', 'cloudModels',
                                                  'connectedAt', 'grantedScopes', 'checkedAt', 'sources', 'pending',
                                                  'waitingChanges', 'waitingDrafts'}
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
            and all(_connection_source(item) for item in sources.values())
            and _connection_pending(value['pending'])
            and all((value[key] is None) != connected
                    and (value[key] is None or type(value[key]) is int and 0 <= value[key] <= 50)
                    for key in ('waitingChanges', 'waitingDrafts')))


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


# Connected-apps actions: the custodian's exact request and reply shapes. The root
# coordinator validates again; tests/test_pixel_connection_wire.py keeps the host
# relay and the Edge copies identical.
_CONNECTION_OPERATIONS = ('connection-begin', 'connection-complete', 'connection-activate',
                          'connection-cancel', 'connection-disconnect', 'connection-sync',
                          'connection-proposals', 'connection-approve', 'connection-decline',
                          'connection-reconcile', 'connection-activity', 'connection-undo',
                          'connection-drafts')
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
_PROPOSAL = re.compile(r'calendar-[0-9]{13}-[a-f0-9]{8}')
_DRAFT = re.compile(r'gmail-draft-[0-9]{13}-[a-f0-9]{8}')
_HEX = re.compile(r'[a-f0-9]{64}')
_ATTENDEE = re.compile(r'[^\s@<>\x00-\x1f\x7f]+@[^\s@<>\x00-\x1f\x7f]+')
_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
_CHANGE_OUTCOMES = {'connection-approve': ('applied', 'rejected', 'not-applied', 'unconfirmed'),
                    'connection-reconcile': ('applied', 'rejected', 'not-applied', 'unconfirmed', 'waiting'),
                    'connection-decline': ('declined',),
                    'connection-undo': ('applied', 'rejected', 'not-applied', 'unconfirmed')}
# Approving, checking and declining also take a Gmail draft; Undo takes only a calendar change.
_DRAFT_OPERATIONS = ('connection-approve', 'connection-reconcile', 'connection-decline')


def _text(value, pattern):
    return type(value) is str and pattern.fullmatch(value) is not None


def _capability_flags(value):
    return (type(value) is dict and set(value) == set(_CAPABILITY_IDS)
            and all(type(flag) is bool for flag in value.values()))


def _words(value, limit):
    # Text the owner reads before approving: bounded, and only tabs and line breaks as control characters.
    return value is None or type(value) is str and len(value) <= limit and _CONTROL.search(value) is None


def _calendar_change(value):
    attendees = value.get('attendees') if type(value) is dict else None
    return (type(value) is dict and set(value) == {'summary', 'start', 'end', 'allDay', 'timeZone', 'location',
                                                   'description', 'attendees', 'sendUpdates'}
            and _words(value['summary'], 1000) and _words(value['start'], 64) and _words(value['end'], 64)
            and (value['allDay'] is None or type(value['allDay']) is bool) and _words(value['timeZone'], 100)
            and _words(value['location'], 1000) and _words(value['description'], 20000)
            and (attendees is None or type(attendees) is list and len(attendees) <= 100
                 and all(_words(item, 320) and _text(item, _ATTENDEE) for item in attendees))
            and value['sendUpdates'] in ('none', 'all'))


def _calendar_event(value):
    return value is None or (type(value) is dict and set(value) == {'title', 'start', 'end', 'allDay'}
                             and _words(value['title'], 1000) and _words(value['start'], 64)
                             and _words(value['end'], 64) and type(value['allDay']) is bool)


def _calendar_entry(value):
    if (type(value) is not dict or set(value) != {'proposalId', 'sha256', 'state', 'action', 'createdAt', 'change',
                                                  'event', 'tooLarge'}
            or not _text(value['proposalId'], _PROPOSAL) or type(value['tooLarge']) is not bool
            or value['state'] not in ('waiting', 'unconfirmed', 'applied', 'rejected', 'not-applied', 'invalid')
            or not _words(value['createdAt'], 64)):
        return False
    if value['state'] == 'invalid':
        return (value['tooLarge'] is False and value['createdAt'] is None
                and all(value[key] is None for key in ('sha256', 'action', 'change', 'event')))
    shown = not value['tooLarge']
    return (value['action'] in ('create', 'update', 'delete')
            and (_text(value['sha256'], _HEX) if shown else value['sha256'] is None)
            and (_calendar_change(value['change']) if shown else value['change'] is None)
            and (_calendar_event(value['event']) if shown else value['event'] is None))


def _calendar_moment(value):
    return value is None or (type(value) is dict and set(value) == {'start', 'end', 'allDay'}
                             and _words(value['start'], 64) and _words(value['end'], 64)
                             and type(value['allDay']) is bool)


def _calendar_activity(value):
    return (type(value) is dict and set(value) == {'proposalId', 'action', 'title', 'start', 'end', 'allDay',
                                                   'previous', 'appliedAt', 'undo'}
            and _text(value['proposalId'], _PROPOSAL) and value['action'] in ('create', 'update')
            and _words(value['title'], 1000) and _words(value['start'], 64) and _words(value['end'], 64)
            and type(value['allDay']) is bool and _calendar_moment(value['previous'])
            and _words(value['appliedAt'], 64)
            and value['undo'] in ('available', 'syncing', 'done', 'refused', 'unconfirmed', 'changed', 'gone'))


def _suggestion(operation, value):
    return _text(value, _PROPOSAL) or operation in _DRAFT_OPERATIONS and _text(value, _DRAFT)


def _gmail_draft(value):
    to = value.get('to') if type(value) is dict else None
    cc = value.get('cc') if type(value) is dict else None
    reply = value.get('replyTo') if type(value) is dict else None
    return (type(value) is dict and set(value) == {'to', 'cc', 'subject', 'body', 'replyTo'}
            and type(to) is list and type(cc) is list and 1 <= len(to) and len(to) + len(cc) <= 50
            and all(_words(item, 320) and _text(item, _ATTENDEE) for item in to + cc)
            and type(value['subject']) is str and _words(value['subject'], 998)
            and '\n' not in value['subject'] and '\r' not in value['subject']
            and type(value['body']) is str and value['body'].strip() != '' and _words(value['body'], 100000)
            and (reply is None or type(reply) is dict and set(reply) == {'from', 'subject'}
                 and _words(reply['from'], 320) and _words(reply['subject'], 998)))


def _draft_entry(value):
    if (type(value) is not dict or set(value) != {'proposalId', 'sha256', 'state', 'createdAt', 'draft', 'tooLarge'}
            or not _text(value['proposalId'], _DRAFT) or type(value['tooLarge']) is not bool
            or value['state'] not in ('waiting', 'unconfirmed', 'applied', 'rejected', 'not-applied', 'invalid')
            or not _words(value['createdAt'], 64)):
        return False
    if value['state'] == 'invalid':
        return (value['tooLarge'] is False and value['createdAt'] is None
                and value['sha256'] is None and value['draft'] is None)
    shown = not value['tooLarge']
    return ((_text(value['sha256'], _HEX) if shown else value['sha256'] is None)
            and (_gmail_draft(value['draft']) if shown else value['draft'] is None))


def valid_connection_action(value):
    if (type(value) is not dict or set(value) != {'operation', 'request'}
            or value['operation'] not in _CONNECTION_OPERATIONS or type(value['request']) is not dict):
        return False
    operation, request = value['operation'], value['request']
    if operation in ('connection-activate', 'connection-sync', 'connection-proposals', 'connection-activity',
                     'connection-drafts'):
        return request == {}
    if operation == 'connection-approve':
        return (set(request) == {'proposalId', 'sha256'} and _suggestion(operation, request['proposalId'])
                and _text(request['sha256'], _HEX))
    if operation in ('connection-decline', 'connection-reconcile', 'connection-undo'):
        return set(request) == {'proposalId'} and _suggestion(operation, request['proposalId'])
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
    elif operation == 'connection-proposals':
        valid = (set(value) == {'proposals', 'more'} and type(value['more']) is int and value['more'] >= 0
                 and type(value['proposals']) is list and len(value['proposals']) <= 20
                 and all(_calendar_entry(item) for item in value['proposals']))
    elif operation == 'connection-activity':
        valid = (set(value) == {'changes', 'more'} and type(value['more']) is int and value['more'] >= 0
                 and type(value['changes']) is list and len(value['changes']) <= 20
                 and all(_calendar_activity(item) for item in value['changes']))
    elif operation == 'connection-drafts':
        valid = (set(value) == {'drafts', 'more'} and type(value['more']) is int and value['more'] >= 0
                 and type(value['drafts']) is list and len(value['drafts']) <= 20
                 and all(_draft_entry(item) for item in value['drafts']))
    elif operation in _CHANGE_OUTCOMES:
        valid = (set(value) == {'proposalId', 'outcome'} and _suggestion(operation, value['proposalId'])
                 and value['outcome'] in _CHANGE_OUTCOMES[operation])
    else:
        outcomes = {'connection-activate': ('applied', 'unchanged'), 'connection-cancel': ('cancelled',),
                    'connection-sync': ('started',)}.get(operation, ())
        valid = set(value) == {'outcome'} and value['outcome'] in outcomes
    if not valid:
        raise ValueError('invalid-connection-reply')
    return value
