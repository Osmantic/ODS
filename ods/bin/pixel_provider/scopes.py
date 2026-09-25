"""Owner-selected provider preferences, distinct from per-run approval.

Tasks have explicit owner-created IDs and an explicit end. A native run never
creates, completes, or renames a task. Default changes affect newly begun tasks;
existing tasks retain their snapshot. Every handoff still needs its checkpoint
approval. This module has no model, process, privilege or credential authority.
"""
import copy
import hashlib
import re

from .store import ProviderStore, StoreError

CHAT = re.compile(r'[A-Za-z0-9_-]{1,128}')
TASK = re.compile(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}')
PROVIDER = re.compile(r'[a-z][a-z0-9_-]{0,63}')
PREFIX = 'agent:pixel:openai-user:'
SCOPES = ('task', 'conversation', 'default')
MAX_CONVERSATIONS = 256
MAX_TASKS = 1024


def chat_id(value):
    # Ingress hashes the exact UTF-8 user before OpenClaw normalizes its key.
    # Browser IDs differing only in case therefore remain distinct here.
    if type(value) is not str or not CHAT.fullmatch(value):
        raise StoreError('invalid-scope-request')
    return value


def native_key(value):
    return PREFIX + 'ods-' + hashlib.sha256(chat_id(value).encode('utf-8')).hexdigest()


def _revision(value):
    return type(value) is int and 0 <= value < 2**53-1


def _rule(value):
    if value is None:
        return None
    if (type(value) is not dict or set(value) != {'providerId', 'providerRevision', 'allowCloud', 'acceptUnknownCost'}
            or type(value['providerId']) is not str or not PROVIDER.fullmatch(value['providerId'])
            or not _revision(value['providerRevision'])
            or any(type(value[key]) is not bool for key in ('allowCloud', 'acceptUnknownCost'))):
        raise StoreError('invalid-scope-state')
    return value


def default_state():
    return dict(schemaVersion=1, revision=0, defaultSelection=None, conversations=[], taskIds=[])


def normalize_state(value):
    if (type(value) is not dict or set(value) != {'schemaVersion', 'revision', 'defaultSelection', 'conversations', 'taskIds'}
            or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
            or not _revision(value['revision']) or type(value['conversations']) is not list
            or len(value['conversations']) > MAX_CONVERSATIONS or type(value['taskIds']) is not list
            or len(value['taskIds']) > MAX_TASKS):
        raise StoreError('invalid-scope-state')
    _rule(value['defaultSelection'])
    tasks = value['taskIds']
    if any(type(item) is not str or not TASK.fullmatch(item) for item in tasks) or len(set(tasks)) != len(tasks):
        raise StoreError('invalid-scope-state')
    chats = set()
    for row in value['conversations']:
        if (type(row) is not dict or set(row) != {'chatId', 'taskId', 'taskSelection', 'conversationSelection', 'defaultSnapshot'}
                or chat_id(row['chatId']) != row['chatId'] or row['chatId'] in chats
                or row['taskId'] is not None and (type(row['taskId']) is not str or row['taskId'] not in tasks)):
            raise StoreError('invalid-scope-state')
        chats.add(row['chatId'])
        for key in ('taskSelection', 'conversationSelection', 'defaultSnapshot'):
            _rule(row[key])
        if row['taskId'] is None and (row['taskSelection'] is not None or row['defaultSnapshot'] is not None):
            raise StoreError('invalid-scope-state')
    return copy.deepcopy(value)


class ScopeStore(ProviderStore):
    config_name = 'provider-scopes.json'

    def __init__(self, directory):
        super().__init__(directory, validator=normalize_state, default_factory=default_state)

    @staticmethod
    def _conversation(state, identity):
        return next((row for row in state['conversations'] if row['chatId'] == identity), None)

    @staticmethod
    def _effective(row):
        if row is None:
            return None, None
        for scope, key in (('task', 'taskSelection'), ('conversation', 'conversationSelection'), ('default', 'defaultSnapshot')):
            if row[key] is not None:
                return scope, row[key]
        return None, None

    def _project(self, state, identity):
        row = self._conversation(state, identity)
        scope, selected = self._effective(row)
        return dict(schemaVersion=1, revision=state['revision'], chatId=identity,
            taskId=row['taskId'] if row else None,
            taskSelection=row['taskSelection'] if row else None,
            conversationSelection=row['conversationSelection'] if row else None,
            defaultSnapshot=row['defaultSnapshot'] if row else None,
            defaultSelection=state['defaultSelection'], effectiveScope=scope, effectiveSelection=selected,
            runtimeStatus='preference-only', checkpointApproval='required-each-handoff-run')

    def status(self, identity):
        identity = chat_id(identity)
        try:
            self.directory.lstat()
        except FileNotFoundError:
            return self._project(default_state(), identity)
        return self._project(self.load(), identity)

    def change(self, action, body):
        common = {'chatId', 'expectedRevision'}
        fields = {'begin': common | {'taskId'}, 'end': common | {'taskId'},
            'select': common | {'scope', 'taskId', 'providerId', 'providerRevision', 'allowCloud', 'acceptUnknownCost'},
            'return': common | {'scope', 'taskId'}}
        if (action not in fields or type(body) is not dict or set(body) != fields[action]
                or not _revision(body['expectedRevision'])):
            raise StoreError('invalid-scope-request')
        identity = chat_id(body['chatId'])
        task = body['taskId']
        if task is not None and (type(task) is not str or not TASK.fullmatch(task)):
            raise StoreError('invalid-scope-request')
        if action in ('begin', 'end') and task is None:
            raise StoreError('scope-task-required')
        if action in ('select', 'return') and body['scope'] not in SCOPES:
            raise StoreError('invalid-scope-request')
        # Same stable lock as provider Settings: no nested acquisition and no
        # provider-revision race while validating a recipient and saving intent.
        with self._locked(True) as fd:
            state = self._load(fd)
            if state['revision'] != body['expectedRevision']:
                raise StoreError('stale-revision')
            row = self._conversation(state, identity)
            if row is None:
                if len(state['conversations']) >= MAX_CONVERSATIONS:
                    raise StoreError('scope-capacity-reached')
                row = dict(chatId=identity, taskId=None, taskSelection=None,
                    conversationSelection=None, defaultSnapshot=None)
                state['conversations'].append(row)
            if action == 'begin':
                if task in state['taskIds']:
                    raise StoreError('scope-task-replayed')
                if row['taskId'] is not None:
                    raise StoreError('scope-task-already-active')
                if len(state['taskIds']) >= MAX_TASKS:
                    raise StoreError('scope-capacity-reached')
                state['taskIds'].append(task)
                row.update(taskId=task, taskSelection=None, defaultSnapshot=copy.deepcopy(state['defaultSelection']))
            elif action == 'end':
                if row['taskId'] != task:
                    raise StoreError('scope-task-mismatch')
                row.update(taskId=None, taskSelection=None, defaultSnapshot=None)
            else:
                scope = body['scope']
                if row['taskId'] != task or scope == 'task' and task is None:
                    raise StoreError('scope-task-mismatch')
                rule = None
                if action == 'select':
                    rule = _rule({key: body[key] for key in ('providerId', 'providerRevision', 'allowCloud', 'acceptUnknownCost')})
                    config = ProviderStore(self.directory)._load(fd)
                    self._check_target(config, rule)
                if scope == 'default':
                    state['defaultSelection'] = rule
                else:
                    row[scope+'Selection'] = rule
            saved = self._commit(fd, state, body['expectedRevision'])
            return self._project(saved, identity)

    @staticmethod
    def _check_target(config, rule):
        if config['revision'] != rule['providerRevision']:
            raise StoreError('stale-provider-revision')
        if not config['enabled'] or rule['providerId'] != config['roles']['handoff']:
            raise StoreError('handoff-recipient-not-configured')
        providers = {item['id']: item for item in config['providers']}
        target, leader = providers[rule['providerId']], providers[config['roles']['leader']]
        if (not target['enabled'] or target['id'] == leader['id']
                or target['contextTokens'] < leader['contextTokens']
                or target['maxOutputTokens'] < leader['maxOutputTokens']
                or any(leader[key] and not target[key] for key in ('supportsTools', 'supportsVision', 'reasoning'))):
            raise StoreError('handoff-recipient-incompatible')
        if target['kind'] == 'cloud' and not (config['policy']['allowCloud'] and rule['allowCloud'] and rule['acceptUnknownCost']):
            raise StoreError('cloud-transfer-confirmation-required')

    def resolve(self, session_key, expected_revision):
        """Only a trusted native lease worker calls this, never an HTTP client.

        No prefix guessing for cron, other agents, explicit noncanonical session
        keys or session IDs. Missing identity is unavailable, not default routing.
        Unregistered conversations use the configured leader; defaults are copied
        only by explicit begin, never by a model call.
        """
        if type(session_key) is not str or not re.fullmatch(re.escape(PREFIX) + r'ods-[a-f0-9]{64}', session_key):
            raise StoreError('scope-session-unavailable')
        if not _revision(expected_revision):
            raise StoreError('scope-session-unavailable')
        with self._locked(False) as fd:
            state = self._load(fd)
            row = next((row for row in state['conversations'] if native_key(row['chatId']) == session_key), None)
            scope, rule = self._effective(row)
            if rule is None:
                return None
            config = ProviderStore(self.directory)._load(fd)
            if expected_revision != config['revision']:
                raise StoreError('stale-provider-revision')
            self._check_target(config, rule)
            return dict(scope=scope, scopeRevision=state['revision'], **rule)


def manager(data_dir):
    from .host_api import _directory
    return ScopeStore(_directory(data_dir))


def handle(data_dir, action, body):
    store = manager(data_dir)
    if action == 'status':
        if type(body) is not dict or set(body) != {'chatId'}:
            raise StoreError('invalid-scope-request')
        return store.status(body['chatId'])
    try:
        store.directory.mkdir(mode=0o700)
    except FileExistsError:
        pass
    return store.change(action, body)
