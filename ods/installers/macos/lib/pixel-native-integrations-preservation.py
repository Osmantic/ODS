"""Root-owned intent for preserving a newly added integration capability.

The complete private-root family is declared before provisioning. A restored
legacy deployment can quarantine that exact family without adopting arbitrary
residue or requiring the failed candidate to run. Owner data is never removed.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat


NAME = 'integrations-preservation.json'
PENDING = '.integrations-preservation.pending'
LIMIT = 2 * 1024 * 1024
ROOTS = ('/private/var/lib/ods-pixel-integrations',
    '/private/var/lib/ods-pixel-integrations-auth', '/private/var/log/ods-pixel-integrations')


def sha(body):
    return hashlib.sha256(body).hexdigest()


def _body(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()


def make(*, owner, install_dir, settings, services, installation, current_digest,
         candidate_digest, common_body):
    if (any(type(value) is not str or not re.fullmatch('[a-f0-9]{64}', value)
            for value in (current_digest, candidate_digest)) or current_digest == candidate_digest
            or type(common_body) is not bytes or not 0 < len(common_body) <= 1024 * 1024):
        raise ValueError('native-integrations-preservation-invalid')
    compile(common_body, 'preserved-integrations-common.py', 'exec')
    value = dict(schemaVersion=1, kind='ods-native-integrations-preservation',
        owner=owner.pw_uid, ownerName=owner.pw_name, installDir=str(install_dir),
        currentDigest=current_digest, candidateDigest=candidate_digest,
        rootsBefore={path: None for path in ROOTS},
        prior={'settings': sha(settings), 'services': sha(services), 'installation': sha(installation)},
        common={'sha256': sha(common_body), 'body': base64.b64encode(common_body).decode('ascii')})
    validate(value, owner=owner, install_dir=install_dir, settings=settings, services=services,
             installation=installation)
    return value


def validate(value, *, owner, install_dir, settings, services, installation):
    """Validate against independently read protected prior deployment authority."""
    try:
        if (type(value) is not dict or set(value) != {'schemaVersion', 'kind', 'owner', 'ownerName',
                'installDir', 'currentDigest', 'candidateDigest', 'rootsBefore', 'prior', 'common'}
                or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
                or value['kind'] != 'ods-native-integrations-preservation'
                or type(value['owner']) is not int or value['owner'] != owner.pw_uid or owner.pw_uid <= 0
                or value['ownerName'] != owner.pw_name or value['installDir'] != str(install_dir)
                or value['rootsBefore'] != {path: None for path in ROOTS}
                or any(type(value[key]) is not str or not re.fullmatch('[a-f0-9]{64}', value[key])
                       for key in ('currentDigest', 'candidateDigest'))
                or value['currentDigest'] == value['candidateDigest']
                or value['prior'] != {'settings': sha(settings), 'services': sha(services), 'installation': sha(installation)}
                or type(value['common']) is not dict or set(value['common']) != {'sha256', 'body'}
                or type(value['common']['body']) is not str or len(value['common']['body']) > 1400000):
            raise ValueError()
        root = Path(install_dir)
        if not root.is_absolute() or root == Path('/') or '..' in root.parts:
            raise ValueError()
        s, managed, activation = (json.loads(body) for body in (settings, services, installation))
        if (s.get('owner') != owner.pw_name or s.get('install_dir') != str(root)
                or managed.get('owner') != owner.pw_uid or managed.get('requiresRecovery')
                or managed.get('progress', {}).get('phase') != 'services-active'
                or set(managed.get('recovery', {}).get('definitions', {})) != {'manager', 'promoter', 'operations'}
                or activation.get('owner') != owner.pw_uid or activation.get('phase') != 'active'):
            raise ValueError()
        body = base64.b64decode(value['common']['body'], validate=True)
        if (not 0 < len(body) <= 1024 * 1024 or base64.b64encode(body).decode('ascii') != value['common']['body']
                or sha(body) != value['common']['sha256']):
            raise ValueError()
        compile(body, 'preserved-integrations-common.py', 'exec')
        return body
    except (ValueError, TypeError, KeyError, AttributeError, SyntaxError) as error:
        raise ValueError('native-integrations-preservation-authority-mismatch') from error


def load(state, custody):
    path = Path(state) / NAME
    if not os.path.lexists(path):
        return None
    body = custody.protected_inspection_bytes(path, limit=LIMIT)
    if body is None:
        return None
    info = path.lstat()
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError('native-integrations-preservation-custody')
    try:
        value = json.loads(body)
    except (ValueError, TypeError):
        raise ValueError('native-integrations-preservation-invalid') from None
    if _body(value) != body:
        raise ValueError('native-integrations-preservation-invalid')
    return value


def publish(state, value, custody):
    """Exclusive atomic publication under the already held controller lock.

    A killed writer's fixed pending file is never authority. With no private
    roots created yet, a retry preserves those bytes under their digest before
    constructing a fresh intent. Retirement may archive that pending file too.
    """
    body = _body(value)
    if len(body) > LIMIT:
        raise ValueError('native-integrations-preservation-too-large')
    with custody.protected_directory(state) as directory:
        if stat.S_IMODE(os.fstat(directory).st_mode) != 0o700:
            raise ValueError('native-integrations-preservation-custody')
        existing = load(state, custody)
        if existing is not None:
            if existing != value:
                raise ValueError('native-integrations-preservation-conflict')
            os.fsync(directory)
            return
        if any(os.path.lexists(path) for path in ROOTS):
            raise ValueError('native-integrations-preservation-unbound-roots')
        pending = Path(state) / PENDING
        if os.path.lexists(pending):
            if any(os.path.lexists(path) for path in ROOTS):
                raise ValueError('native-integrations-preservation-unbound-roots')
            old = custody.protected_bytes(pending, limit=LIMIT)
            if stat.S_IMODE(pending.lstat().st_mode) != 0o600:
                raise ValueError('native-integrations-preservation-custody')
            history = 'integrations-preservation-interrupted-' + sha(old) + '.json'
            destination = Path(state) / history
            if os.path.lexists(destination):
                if custody.protected_bytes(destination, limit=LIMIT) != old:
                    raise ValueError('native-integrations-preservation-conflict')
                os.unlink(PENDING, dir_fd=directory)
            else:
                os.rename(PENDING, history, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        fd = os.open(PENDING, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        # A root-only directory and the controller lock exclude untrusted
        # publication races. Refuse drift rather than replacing prior intent.
        if os.path.lexists(Path(state) / NAME):
            raise ValueError('native-integrations-preservation-conflict')
        os.rename(PENDING, NAME, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
