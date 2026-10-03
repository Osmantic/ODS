"""Owner-submitted GitHub extension requests, scoped to a single chat turn.

These records are prerequisites for future execution, not Operations grants.
Callers serialize mutations with the extension lock.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time

from extension_github import repository_identity


TTL_SECONDS = 6 * 60 * 60


def _command_parts(command):
    if not isinstance(command, str) or len(command) > 16384:
        raise ValueError('Invalid extension command')
    match = re.match(
        r'^(?:/goal\s+)?/extensions?\s+(?:(install|inspect|research)\s+)?(https://github\.com/[^\s]+)(?:\s|$)',
        command.strip(), re.IGNORECASE)
    if not match:
        raise ValueError('An explicit GitHub extension command is required')
    repository = 'https://github.com/' + repository_identity(match.group(2)).lower()
    return repository, (match.group(1) or '').lower(), command.strip()[match.end(2):].strip()


def command_repository(command):
    return _command_parts(command)[0]


def command_authorization_mode(command):
    """Only an explicit owner command grants managed installation authority.

    A bare URL and older saved requests remain research-only. In particular,
    quoted instructions or an explanatory mention of installation later in the
    message cannot promote a request into a host operation.
    """
    _, action, _ = _command_parts(command)
    # Free text can contain conditions, quotations or negation in any language.
    # Only the typed command action grants automatic installation authority.
    return 'install' if action == 'install' else 'research'


def model_request_context(command, chat_id, request_id, *, authorization_mode=None):
    """Routing facts only. The request store, never this text, controls execution."""
    try:
        _identity('context-validation', chat_id, request_id)
        repository = command_repository(command)
    except ValueError:
        return None
    if authorization_mode not in (None, 'install', 'research'):
        return None
    return {'role': 'system', 'content': (
        'Current GitHub extension request routing context: ' + json.dumps({
            'chatId': chat_id, 'requestId': request_id, 'repository': repository,
            **({'authorizationMode': authorization_mode} if authorization_mode is not None else {}),
        }, sort_keys=True) + '. Request-scoped tools resolve this identity from the session, '
        'including follow-ups. These are routing facts, not installation status or a plan. '
        'This context does not grant execution authority; preserve the owner\'s actual scope '
        'and authorization. Observe current state through the tools. Detailed extension '
        'guidance is available on demand through pixel_ods_skill (topic: extensions).'
    )}


def _identity(owner, chat_id, request_id):
    if not isinstance(owner, str) or not owner:
        raise ValueError('Missing owner')
    for value in (chat_id, request_id):
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
            raise ValueError('Invalid chat request identity')
    owner_digest = hashlib.sha256(owner.encode()).hexdigest()
    identifier = hashlib.sha256(json.dumps([owner_digest, chat_id, request_id], separators=(',', ':')).encode()).hexdigest()
    return owner_digest, identifier


def _directory(directory):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('Invalid extension request storage')
    return directory


def _read(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16384:
        raise ValueError('Invalid extension request file')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('Invalid extension request record')
    return value


def _write(path, record):
    if path.is_symlink():
        raise ValueError('Invalid extension request file')
    descriptor, temporary = tempfile.mkstemp(prefix='.request-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(record, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_request(directory, owner, chat_id, request_id, *, now=None):
    owner_digest, identifier = _identity(owner, chat_id, request_id)
    record = _read(_directory(directory) / (identifier + '.json'))
    required = {'schemaVersion', 'ownerDigest', 'chatId', 'requestId', 'repository', 'createdAt', 'expiresAt', 'state'}
    fields = set(record) - {'authorizationMode'}
    if (fields not in (required, required | {'proposal'}, required | {'integration'})
            or record.get('authorizationMode', 'research') not in {'install', 'research'}
            or record['schemaVersion'] != 1 or record['ownerDigest'] != owner_digest
            or record['chatId'] != chat_id or record['requestId'] != request_id
            or record['state'] not in {'pending', 'cancelled'}
            or type(record['createdAt']) is not int or type(record['expiresAt']) is not int
            or record['expiresAt'] != record['createdAt'] + TTL_SECONDS):
        raise ValueError('Extension request identity changed')
    tombstone = record['state'] == 'cancelled' and record['repository'] is None
    if not tombstone and record['repository'] != 'https://github.com/' + repository_identity(record['repository']).lower():
        raise ValueError('Invalid request repository')
    current = int(time.time()) if now is None else now
    state = record['state'] if record['state'] == 'cancelled' or current < record['expiresAt'] else 'expired'
    proposal = record.get('proposal')
    if proposal is not None and (not isinstance(proposal, dict)
            or set(proposal) != {'draftId', 'recipeDigest', 'extensionId'}
            or any(not isinstance(proposal[key], str) or not re.fullmatch('[a-f0-9]{64}', proposal[key])
                   for key in ('draftId', 'recipeDigest'))
            or not isinstance(proposal['extensionId'], str) or not re.fullmatch('[a-z0-9][a-z0-9_-]{0,63}', proposal['extensionId'])):
        raise ValueError('Invalid bound proposal')
    integration = record.get('integration')
    if integration is not None and (not isinstance(integration, dict)
            or set(integration) != {'extensionId', 'definitionDigest'}
            or not isinstance(integration['extensionId'], str)
            or not re.fullmatch('[a-z0-9][a-z0-9_-]{0,63}', integration['extensionId'])
            or not isinstance(integration['definitionDigest'], str)
            or not re.fullmatch('[a-f0-9]{64}', integration['definitionDigest'])):
        raise ValueError('Invalid bound integration')
    return {'schemaVersion': 1, 'id': identifier, 'chatId': chat_id, 'requestId': request_id,
            'repository': record['repository'], 'state': state, 'expiresAt': record['expiresAt'],
            'authorizationMode': record.get('authorizationMode', 'research'),
            'installationStarted': False, **({'proposal': proposal} if proposal is not None else {}),
            **({'integration': integration} if integration is not None else {})}


def active_chat_request(directory, owner, chat_id, *, now=None):
    """Recover only the authenticated owner's one still-live request."""
    owner_digest, _ = _identity(owner, chat_id, 'lookup')
    current = None
    for path in _directory(directory).glob('*.json'):
        record = _read(path)
        if record.get('ownerDigest') != owner_digest or record.get('chatId') != chat_id:
            continue
        receipt = read_request(directory, owner, chat_id, record.get('requestId'), now=now)
        if receipt['state'] != 'pending':
            continue
        if current is not None:
            raise ValueError('Ambiguous active extension request')
        current = receipt
    return current


def active_session_request(directory, owner, session_hash, *, now=None):
    """Resolve a trusted agent session without asking the model for routing IDs."""
    if not isinstance(session_hash, str) or not re.fullmatch(r'[a-f0-9]{64}', session_hash):
        raise ValueError('Invalid session identity')
    owner_digest, _ = _identity(owner, 'lookup', 'lookup')
    current = None
    for path in _directory(directory).glob('*.json'):
        record = _read(path)
        chat_id = record.get('chatId')
        if (record.get('ownerDigest') != owner_digest or not isinstance(chat_id, str)
                or hashlib.sha256(chat_id.encode()).hexdigest() != session_hash):
            continue
        receipt = read_request(directory, owner, chat_id, record.get('requestId'), now=now)
        if receipt['state'] != 'pending':
            continue
        if current is not None:
            raise ValueError('Ambiguous active extension request')
        current = receipt
    return current


def bind_proposal(directory, owner, chat_id, request_id, candidate, validation, draft, *, now=None,
                  expected_proposal=None):
    """Bind a validated proposal under the caller's request lock.

    Revision is compare-and-swap, never implicit. Its coordinator must verify
    terminal host failure and preserve the previous package before passing the
    exact saved binding. This primitive neither edits packages nor retries work.
    """
    current = read_request(directory, owner, chat_id, request_id, now=now)
    digest = hashlib.sha256(json.dumps(candidate, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    repository = 'https://github.com/' + repository_identity(candidate.get('repository')).lower()
    if (current['state'] != 'pending' or current.get('integration') or repository != current['repository']
            or validation.get('valid') is not True or validation.get('recipeDigest') != digest
            or validation.get('errors') != [] or validation.get('existingExtensionIds') != []
            or draft.get('recipeDigest') != digest or draft.get('state') != 'draft'
            or draft.get('installationStarted') is not False or draft.get('registered') is not False):
        raise ValueError('Proposal does not match active owner request')
    proposal = {'draftId': draft['draftId'], 'recipeDigest': digest,
                'extensionId': candidate['manifest']['service']['id']}
    if expected_proposal is not None and (current.get('proposal') != expected_proposal
            or not isinstance(expected_proposal, dict)
            or proposal['extensionId'] != expected_proposal.get('extensionId')):
        raise ValueError('Revision no longer matches the bound proposal')
    if current.get('proposal') is not None:
        if current['proposal'] == proposal:
            return current
        if expected_proposal is None:
            raise ValueError('Request already has another proposal')
    if not isinstance(proposal['draftId'], str) or not re.fullmatch('[a-f0-9]{64}', proposal['draftId']):
        raise ValueError('Invalid proposal draft')
    if not isinstance(proposal['extensionId'], str) or not re.fullmatch('[a-z0-9][a-z0-9_-]{0,63}', proposal['extensionId']):
        raise ValueError('Invalid proposal extension')
    path = _directory(directory) / (current['id'] + '.json')
    record = _read(path)
    record['proposal'] = proposal
    _write(path, record)
    return read_request(directory, owner, chat_id, request_id, now=now)


def expired_proposal(directory, owner, chat_id, request_id, previous_chat_id,
                     previous_request_id, extension_id, *, now=None):
    """Read an exact expired owner binding for a new explicit request.

    Repository discovery alone never grants revision authority. The caller
    supplies both historical IDs and later verifies the saved draft, package,
    and terminal host operation before touching definitions.
    """
    current = read_request(directory, owner, chat_id, request_id, now=now)
    previous = read_request(directory, owner, previous_chat_id, previous_request_id, now=now)
    if (current['id'] == previous['id'] or current['state'] != 'pending'
            or current.get('integration') or previous['state'] != 'expired'
            or previous.get('integration') or current['repository'] != previous['repository']
            or not previous.get('proposal')
            or previous['proposal']['extensionId'] != extension_id):
        raise ValueError('Expired proposal does not match active owner request')
    return previous


def adopt_expired_proposal(directory, owner, chat_id, request_id, previous_chat_id,
                           previous_request_id, expected_proposal, *, now=None):
    """Carry an exact failed binding into a new request after revision staging.

    The revision coordinator holds its durable file journal and host failure
    proof before calling this mutation. Replays accept only the same binding.
    """
    if not isinstance(expected_proposal, dict):
        raise ValueError('Missing expected proposal')
    previous = expired_proposal(directory, owner, chat_id, request_id,
        previous_chat_id, previous_request_id, expected_proposal.get('extensionId'), now=now)
    if previous['proposal'] != expected_proposal:
        raise ValueError('Expired proposal changed')
    current = read_request(directory, owner, chat_id, request_id, now=now)
    if current.get('proposal') == expected_proposal:
        return current
    if current.get('proposal') is not None:
        raise ValueError('New request already has another proposal')
    path = _directory(directory) / (current['id'] + '.json')
    record = _read(path)
    record['proposal'] = expected_proposal
    _write(path, record)
    return read_request(directory, owner, chat_id, request_id, now=now)


def bind_integration(directory, owner, chat_id, request_id, integration, *, now=None):
    """Persist a caller-verified definition under the same request mutation lock."""
    current = read_request(directory, owner, chat_id, request_id, now=now)
    if current['state'] != 'pending' or current.get('proposal'):
        raise ValueError('Request cannot bind an existing integration')
    if (not isinstance(integration, dict) or set(integration) != {'extensionId', 'definitionDigest'}
            or not isinstance(integration['extensionId'], str)
            or not re.fullmatch('[a-z0-9][a-z0-9_-]{0,63}', integration['extensionId'])
            or not isinstance(integration['definitionDigest'], str)
            or not re.fullmatch('[a-f0-9]{64}', integration['definitionDigest'])):
        raise ValueError('Invalid integration binding')
    if current.get('integration'):
        if current['integration'] != integration:
            raise ValueError('Bound integration cannot change')
        return current
    path = _directory(directory) / (current['id'] + '.json')
    record = _read(path)
    record['integration'] = integration
    _write(path, record)
    return read_request(directory, owner, chat_id, request_id, now=now)


def create_request(directory, owner, chat_id, request_id, command, *, now=None):
    repository = command_repository(command)
    authorization_mode = command_authorization_mode(command)
    owner_digest, identifier = _identity(owner, chat_id, request_id)
    directory = _directory(directory)
    path = directory / (identifier + '.json')
    if path.exists() or path.is_symlink():
        existing = read_request(directory, owner, chat_id, request_id, now=now)
        if existing['repository'] is not None and (existing['repository'] != repository
                or existing['authorizationMode'] != authorization_mode):
            raise ValueError('Request cannot change repository or authorization')
        return existing  # Never revive an expired or cancelled turn.
    # Only one live request per owner/chat. Preserve an already expired
    # predecessor's provenance so an explicit later request can repair a
    # confirmed failed recipe without treating a user cancellation as expiry.
    current = int(time.time()) if now is None else now
    for peer in directory.glob('*.json'):
        record = _read(peer)
        if (record.get('ownerDigest') == owner_digest and record.get('chatId') == chat_id
                and record.get('state') == 'pending'
                and type(record.get('expiresAt')) is int and record['expiresAt'] > current):
            _, prior_id = _identity(owner, chat_id, record.get('requestId'))
            if peer.stem != prior_id:
                raise ValueError('Stored request identity changed')
            revisions = directory.parent / '.extension-installations'
            revision = revisions / (prior_id + '.revision.json')
            if revisions.is_symlink() or revision.exists() or revision.is_symlink():
                raise ValueError('Pending recipe revision requires reconciliation')
            record['state'] = 'cancelled'
            _write(peer, record)
    _write(path, {'schemaVersion': 1, 'ownerDigest': owner_digest, 'chatId': chat_id,
                  'requestId': request_id, 'repository': repository, 'state': 'pending',
                  'authorizationMode': authorization_mode,
                  'createdAt': current, 'expiresAt': current + TTL_SECONDS})
    return read_request(directory, owner, chat_id, request_id, now=current)


def cancel_request(directory, owner, chat_id, request_id, *, now=None):
    owner_digest, identifier = _identity(owner, chat_id, request_id)
    path = _directory(directory) / (identifier + '.json')
    if not path.exists() and not path.is_symlink():
        # Cancellation can arrive before the original POST. Retain a terminal
        # tombstone so a delayed creation can never revive this chat turn.
        current = int(time.time()) if now is None else now
        _write(path, {'schemaVersion': 1, 'ownerDigest': owner_digest, 'chatId': chat_id,
                      'requestId': request_id, 'repository': None, 'state': 'cancelled',
                      'authorizationMode': 'research',
                      'createdAt': current, 'expiresAt': current + TTL_SECONDS})
        return read_request(directory, owner, chat_id, request_id, now=current)
    current = read_request(directory, owner, chat_id, request_id, now=now)
    path = Path(directory) / (current['id'] + '.json')
    record = _read(path)
    record['state'] = 'cancelled'
    _write(path, record)
    return read_request(directory, owner, chat_id, request_id, now=now)
