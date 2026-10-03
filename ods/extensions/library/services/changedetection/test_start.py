"""Credential and exec boundary checks; no application or network starts."""
import importlib.util
import os
from pathlib import Path
from unittest.mock import patch

import pytest

spec = importlib.util.spec_from_file_location('ods_changedetection_start', Path(__file__).with_name('ods-start.py'))
start = importlib.util.module_from_spec(spec)
spec.loader.exec_module(start)


def test_missing_password_never_starts_an_unprotected_server():
    with patch.dict(os.environ, {}, clear=True), patch.object(start.os, 'execv') as execute:
        with pytest.raises(SystemExit, match='must be configured'):
            start.main()
        execute.assert_not_called()


def test_exec_receives_only_upstream_hash_and_no_cleartext_password(capsys):
    import base64
    import hashlib

    password = 'local-ñuvem-🔑-$secret'
    captured = []

    def execute(binary, args):
        assert 'CHANGEDETECTION_PASSWORD' not in os.environ
        assert password not in repr(args)
        assert args == [binary, '/app/changedetection.py', '-d', '/datastore']
        captured.append(base64.b64decode(os.environ['SALTED_PASS']))

    for _ in range(2):
        with patch.dict(os.environ, {'CHANGEDETECTION_PASSWORD': password}, clear=True):
            with patch.object(start.os, 'execv', side_effect=execute):
                start.main()
    for stored in captured:
        # This is the versioned upstream login verification contract.
        assert len(stored) == 64
        assert hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), stored[:32], 100000) == stored[32:]
    assert captured[0][:32] != captured[1][:32]
    assert password not in str(capsys.readouterr())
