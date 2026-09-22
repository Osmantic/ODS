"""Map the declared ODS credential to changedetection.io 0.60.7's login contract."""
import base64
import hashlib
import os
import sys


def upstream_password(password):
    if not password:
        raise ValueError('CHANGEDETECTION_PASSWORD must be configured before starting')
    salt = os.urandom(32)
    key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
    return base64.b64encode(salt + key).decode('ascii')


def main():
    password = os.environ.pop('CHANGEDETECTION_PASSWORD', '')
    try:
        os.environ['SALTED_PASS'] = upstream_password(password)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    # No password in argv or logs; exec preserves signal delivery to the server.
    os.execv(sys.executable, [sys.executable, '/app/changedetection.py', '-d', '/datastore'])


if __name__ == '__main__':
    main()
