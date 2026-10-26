"""Google sign-in as the custodian builds it: the owner's Desktop client, PKCE, and the redirect.

The broker's own handoff check runs against what the custodian writes, so the two
can never drift apart.
"""
import base64
from datetime import datetime, timezone
import hashlib
import importlib.util
from pathlib import Path
import sys
import unittest
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
from pixel_connections import oauth  # noqa: E402

SPEC = importlib.util.spec_from_file_location("pixel_source_broker_handoff",
                                              ROOT / "vendor/pixel/deploy/source-broker/broker.py")
BROKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BROKER)

INSTALLED = {"client_id": "123456789012-abcdefghijklmnopqrstuvwxyz012345.apps.googleusercontent.com",
             "client_secret": "test-secret-test", "project_id": "ods-owner-calendar",
             "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token",
             "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
             "redirect_uris": ["http://localhost"]}
CLIENT = {"clientId": INSTALLED["client_id"], "clientSecret": INSTALLED["client_secret"],
          "projectId": INSTALLED["project_id"]}
SCOPES = ["openid", "email", "https://www.googleapis.com/auth/calendar.events.readonly"]
STATE = "s" * 43


def flow(**changes):
    options = dict(google_client=CLIENT, scopes=SCOPES, redirect_port=53682, now=datetime.now(timezone.utc))
    options.update(changes)
    return oauth.new_flow(**options)


class ClientFileTests(unittest.TestCase):
    def test_the_desktop_client_file_google_downloads_is_accepted(self):
        self.assertEqual(oauth.parse_client({"installed": INSTALLED}), CLIENT)
        minimal = {key: INSTALLED[key] for key in ("client_id", "client_secret", "project_id", "auth_uri", "token_uri")}
        self.assertEqual(oauth.parse_client({"installed": minimal}), CLIENT)
        self.assertEqual(oauth.client(CLIENT), CLIENT)

    def test_each_wrong_file_names_its_fix(self):
        for value, code in (
                ({"web": INSTALLED}, "client-not-desktop"),
                ({"installed": dict(INSTALLED, redirect_uris=["https://example.com/callback"])}, "client-not-desktop"),
                ({"installed": dict(INSTALLED, token_uri="https://example.com/token")}, "client-invalid"),
                ({"installed": dict(INSTALLED, client_id="not-a-google-client")}, "client-invalid"),
                ({"installed": dict(INSTALLED, client_secret="short")}, "client-invalid"),
                ({"installed": dict(INSTALLED, project_id="UPPER")}, "client-invalid"),
                ({"installed": dict(INSTALLED, javascript_origins=[])}, "client-invalid"),
                ({"installed": INSTALLED, "web": INSTALLED}, "client-invalid"),
                ({"type": "service_account"}, "client-invalid"), ([], "client-invalid")):
            with self.subTest(code=code, value=str(value)[:60]):
                with self.assertRaises(oauth.OAuthError) as raised:
                    oauth.parse_client(value)
                self.assertEqual(raised.exception.code, code)
        with self.assertRaises(oauth.OAuthError):
            oauth.client(dict(CLIENT, extra="x"))


class ConsentFlowTests(unittest.TestCase):
    def test_the_consent_address_carries_only_the_challenge(self):
        handoff, public = flow(login_hint="owner@example.com")
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(public["authUrl"]).query, strict_parsing=True)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(handoff["codeVerifier"].encode()).digest()).rstrip(b"=")
        self.assertEqual({key: value[0] for key, value in query.items()}, {
            "client_id": CLIENT["clientId"], "redirect_uri": "http://127.0.0.1:53682", "response_type": "code",
            "scope": " ".join(SCOPES), "state": handoff["state"], "code_challenge": challenge.decode(),
            "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent",
            "include_granted_scopes": "true", "login_hint": "owner@example.com"})
        self.assertTrue(public["authUrl"].startswith(oauth.GOOGLE_AUTH_URI + "?"))
        for secret in (handoff["codeVerifier"], CLIENT["clientSecret"]):
            self.assertNotIn(secret, str(public))
        self.assertEqual(public["stateHash"], hashlib.sha256(handoff["state"].encode()).hexdigest())
        self.assertEqual(public["redirectUri"], handoff["redirectUri"])

    def test_each_flow_is_fresh_and_lasts_fifteen_minutes(self):
        now = datetime(2026, 10, 25, 20, 30, tzinfo=timezone.utc)
        first, public = flow(now=now)
        second, _ = flow(now=now)
        for key in ("flowId", "state", "codeVerifier"):
            self.assertNotEqual(first[key], second[key], key)
        self.assertEqual(public["expiresAt"], "2026-10-25T20:45:00Z")
        self.assertNotIn("login_hint", public["authUrl"])

    def test_the_broker_accepts_exactly_the_handoff_the_custodian_writes(self):
        handoff, public = flow()
        completion = {"schemaVersion": 1, "kind": "pixel-source-oauth-code", "flowId": public["flowId"],
                      "state": handoff["state"], "code": "4/0AfJohX-fixture"}
        accepted = BROKER.validated_oauth_flow(handoff, completion, public["flowId"])
        self.assertEqual(accepted["client"], {"client_id": CLIENT["clientId"], "client_secret": CLIENT["clientSecret"]})
        self.assertEqual((accepted["verifier"], accepted["redirectUri"], accepted["requestedScopes"]),
                         (handoff["codeVerifier"], "http://127.0.0.1:53682", SCOPES))
        with self.assertRaises(BROKER.SourceUnavailable) as raised:
            BROKER.validated_oauth_flow(handoff, dict(completion, state="t" * 43), public["flowId"])
        self.assertEqual(raised.exception.error_class, "state-mismatch")

    def test_an_unusable_request_never_becomes_a_flow(self):
        for changes, code in ((dict(redirect_port=80), "invalid-redirect"),
                              (dict(redirect_port="53682"), "invalid-redirect"),
                              (dict(login_hint="not-an-address"), "invalid-account-hint"),
                              (dict(scopes=[]), "invalid-scopes"), (dict(scopes=["openid", "openid"]), "invalid-scopes")):
            with self.subTest(code=code), self.assertRaises(oauth.OAuthError) as raised:
                flow(**changes)
            self.assertEqual(raised.exception.code, code)


class RedirectTests(unittest.TestCase):
    def test_googles_redirect_gives_the_code_or_a_plain_error(self):
        self.assertEqual(oauth.callback_fields(f"state={STATE}&code=4/0Af-x&scope=openid&authuser=0&prompt=consent"),
                         {"state": STATE, "code": "4/0Af-x"})
        self.assertEqual(oauth.callback_fields(f"error=access_denied&state={STATE}"),
                         {"state": STATE, "error": "access-denied"})
        self.assertEqual(oauth.callback_fields(f"error=server_error&error_description=x&state={STATE}"),
                         {"state": STATE, "error": "consent-failed"})

    def test_anything_else_is_not_a_redirect(self):
        for query in ("", "code=4/x", "state=short&code=4/x", f"state={STATE}", f"state={STATE}&code=",
                      f"state={STATE}&code=4/x&next=https://example.com", f"state={STATE}&state={STATE}&code=4/x",
                      f"state={STATE}&code=4/x" + "&scope=openid" * 12, "x" * 9000, f"state={STATE}&code=4%0Ax"):
            with self.subTest(query=query[:50]), self.assertRaises(oauth.OAuthError) as raised:
                oauth.callback_fields(query)
            self.assertEqual(raised.exception.code, "invalid-callback")

    def test_only_this_flows_state_completes_it(self):
        expected = hashlib.sha256(STATE.encode()).hexdigest()
        self.assertEqual(oauth.parse_callback(f"state={STATE}&code=4/x", expected_state_hash=expected)["code"], "4/x")
        with self.assertRaises(oauth.OAuthError) as raised:
            oauth.parse_callback(f"state={'t' * 43}&code=4/x", expected_state_hash=expected)
        self.assertEqual(raised.exception.code, "state-mismatch")

    def test_a_pasted_address_must_be_the_loopback_redirect(self):
        for text in (f"http://127.0.0.1:53682/?state={STATE}&code=4/x", f"  http://localhost:53682?state={STATE}&code=4/x\n"):
            self.assertEqual(oauth.pasted_query(text), f"state={STATE}&code=4/x")
        for text in (f"https://127.0.0.1:53682/?state={STATE}", f"http://example.com:53682/?state={STATE}",
                     f"http://127.0.0.1:53682/callback?state={STATE}", f"http://127.0.0.1/?state={STATE}",
                     "http://127.0.0.1:53682/", "x" * 9000, None):
            with self.subTest(text=str(text)[:50]), self.assertRaises(oauth.OAuthError) as raised:
                oauth.pasted_query(text)
            self.assertEqual(raised.exception.code, "address-not-from-sign-in")


if __name__ == "__main__":
    unittest.main()
