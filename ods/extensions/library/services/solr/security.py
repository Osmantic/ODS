"""Initialize native BasicAuth once; never replace an existing security policy."""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re


def credential(password, salt):
    # Solr Sha256AuthenticationProvider: SHA256(SHA256(salt || password)).
    digest = hashlib.sha256(hashlib.sha256(salt + password.encode()).digest()).digest()
    return base64.b64encode(digest).decode() + ' ' + base64.b64encode(salt).decode()


def configure():
    os.umask(0o077)
    password = os.environ.get('SOLR_ADMIN_PASSWORD', '')
    if not re.fullmatch('[a-f0-9]{64}', password):
        raise SystemExit('SOLR_ADMIN_PASSWORD must contain 64 lowercase hexadecimal characters.')
    target = Path('/var/solr/data/security.json')
    if target.exists():
        policy = json.loads(target.read_text())
        auth = policy.get('authentication', {})
        stored = auth.get('credentials', {}).get('ods', '')
        try:
            salt = base64.b64decode(stored.split(' ')[1], validate=True)
        except (ValueError, IndexError):
            raise SystemExit('Existing Solr credentials need explicit owner reconciliation.')
        if (auth.get('class') != 'solr.BasicAuthPlugin' or auth.get('blockUnknown') is not True
                or not hmac.compare_digest(stored, credential(password, salt))):
            raise SystemExit('Existing Solr security differs from configured credentials; policy was preserved.')
    else:
        policy = {
            'authentication': {'class': 'solr.BasicAuthPlugin', 'blockUnknown': True,
                               'credentials': {'ods': credential(password, os.urandom(32))}},
            'authorization': {'class': 'solr.RuleBasedAuthorizationPlugin',
                              'user-role': {'ods': 'admin'},
                              'permissions': [{'name': 'all', 'role': 'admin'}]},
        }
        # No half-written security.json may be accepted after interrupted setup.
        temporary = target.with_suffix('.ods-tmp')
        temporary.write_text(json.dumps(policy) + '\n')
        temporary.replace(target)
    Path('/tmp/ods-health-curl.conf').write_text(f'user = "ods:{password}"\n')


if __name__ == '__main__':
    configure()
