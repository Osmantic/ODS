"""Small stateful Docker boundary used by native config transaction tests."""
import copy
import json
from types import SimpleNamespace

KEY = 'pixel-native-config'
NAME = 'ods_' + KEY


def mapping(document):
    document.update(name='ods')
    document.setdefault('volumes', {})[KEY] = {'name': NAME}
    for service, target, readonly in (('pixel-native-config-init', '/config', False),
            ('pixel-native-config-write', '/config', False), ('pixel-native-ingress', '/run/pixel-config', True)):
        document['services'].setdefault(service, {})['volumes'] = [
            {'type': 'volume', 'source': KEY, 'target': target, 'read_only': readonly}]
    return document


class StorageEngine:
    def __init__(self, root, existing=False):
        self.root = root
        (root / 'data/pixel-native/preparation').mkdir(parents=True, mode=0o700, exist_ok=True)
        self.volume = self.row() if existing else None
        self.consumers = []
        self.calls = []
        self.create_failure = False
        self.raced_create = False

    @staticmethod
    def row():
        return {'Name': NAME, 'Driver': 'local', 'Scope': 'local', 'Options': {},
            'CreatedAt': '2026-10-14T09:38:39Z', 'Labels': {
                'com.docker.compose.project': 'ods', 'com.docker.compose.volume': KEY}}

    def __call__(self, *args, **kwargs):
        self.calls.append(args)
        assert kwargs == {'timeout': 30}
        if args == ('volume', 'ls', '--quiet'):
            value = NAME if self.volume else ''
        elif args[:2] == ('volume', 'create'):
            # Real create returns the existing name without changing its labels.
            if not self.volume:
                self.volume = self.row()
                if not self.raced_create:
                    self.volume['Labels'].update(dict(args[index + 1].split('=', 1)
                        for index, value in enumerate(args) if value == '--label'))
            if self.create_failure:
                raise TimeoutError('create outcome unknown')
            value = NAME
        elif args == ('volume', 'inspect', NAME):
            value = json.dumps([copy.deepcopy(self.volume)])
        elif args == ('ps', '-aq', '--no-trunc', '--filter', 'volume=' + NAME):
            value = '\n'.join(row['Id'] for row in self.consumers)
        elif args[0] == 'inspect':
            value = json.dumps(self.consumers)
        else:
            raise AssertionError('Unexpected storage operation: ' + str(args))
        return SimpleNamespace(returncode=0, stdout=value)
