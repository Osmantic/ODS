import time
import pytest
from pathlib import Path
import sys

dashboard_api_dir = Path(__file__).resolve().parents[1]
if str(dashboard_api_dir) not in sys.path:
    sys.path.insert(0, str(dashboard_api_dir))

import session_signer

@pytest.fixture(autouse=True)
def configure_secret():
    session_signer._set_secret_for_tests("test-cryptographic-secret-32-bytes-long!")
    yield
    session_signer._set_secret_for_tests("")

def test_valid_token_verification():
    token = session_signer.issue(ttl_seconds=3600)
    ok, reason = session_signer.verify(token)
    assert ok is True
    assert reason == "ok"

def test_tampered_signature_rejection():
    token = session_signer.issue(ttl_seconds=3600)
    parts = token.split(".")
    tampered_sig = parts[2][:-1] + ("A" if parts[2][-1] != "A" else "B")
    tampered_token = f"{parts[0]}.{parts[1]}.{tampered_sig}"
    ok, reason = session_signer.verify(tampered_token)
    assert ok is False
    assert reason == "bad-signature"

def test_expired_timestamp_rejection():
    random_id = "test_rand_id_12345"
    expired_time = int(time.time()) - 100
    payload = f"{random_id}.{expired_time}"
    sig = session_signer._sign(payload)
    expired_token = f"{payload}.{sig}"
    ok, reason = session_signer.verify(expired_token)
    assert ok is False
    assert reason == "expired"

def test_malformed_token_formats():
    assert session_signer.verify("")[0] is False
    assert session_signer.verify("only-one-part")[0] is False
    assert session_signer.verify("part1.part2")[0] is False
    assert session_signer.verify("part1.not-an-int.part3")[0] is False

def test_token_with_surrounding_whitespace():
    token = session_signer.issue(ttl_seconds=3600)
    padded_token = f"   {token}  \n"
    ok, reason = session_signer.verify(padded_token)
    assert ok is True
    assert reason == "ok"
