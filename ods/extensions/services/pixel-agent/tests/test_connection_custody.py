"""Root custody of the Source Broker: the kit, the broker's results, its environment and revocation.

test_connection_coordinator.py runs these steps end to end; this file covers the
refusals a working sign-in never reaches.
"""
import sys
if sys.platform == "win32":
    from unittest import SkipTest
    raise SkipTest("POSIX ownership and links")

import json
import os
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "bin"))
from pixel_connections import custody  # noqa: E402

FLOW = "a" * 32
TEMPLATE = ("PIXEL_SOURCE_CALENDAR_ID='primary'\nPIXEL_SOURCE_TIME_ZONE='UTC'\n"
            "PIXEL_SOURCE_GMAIL_QUERY='in:inbox newer_than:30d'\nPIXEL_SOURCE_GMAIL_SENT_QUERY='in:sent'\n"
            "PIXEL_LIMB_EMAIL_ENABLED='0'\nPIXEL_LIMB_CALENDAR_ENABLED='0'\nPIXEL_LIMB_CALENDAR_WRITE_ENABLED='0'\n"
            "PIXEL_CALENDAR_DIRECT_ENABLED='0'\nPIXEL_SOURCE_LABEL='Owner'\\''s calendar'\n")
LIMBS = {"email": True, "calendar": True, "calendarWrite": False, "calendarDirect": False}


@pytest.fixture
def paths(tmp_path, monkeypatch):
    kit = tmp_path / "program" / "pixel_connections" / "source-broker-kit"
    kit.mkdir(parents=True)
    for directory in (kit, kit.parent, kit.parent.parent):
        directory.chmod(0o755)
    for name in custody.KIT_FILES:
        (kit / name).write_text(TEMPLATE if name == "source-broker.env" else f"# {name}\n")
        (kit / name).chmod(0o644)
    value = custody.Paths(install=tmp_path / "opt", state=tmp_path / "state", env=tmp_path / "broker.env",
                          units=tmp_path / "units", kit=kit)
    value.oauth.mkdir(parents=True)
    value.units.mkdir()
    ids = (os.getuid(), os.getgid())
    monkeypatch.setattr(custody, "account", lambda name: ids)
    return value


def result(**changes):
    value = {"schemaVersion": 1, "kind": "pixel-source-oauth-result", "finishedAt": "2026-10-25T20:30:00Z",
             "flowId": FLOW, "outcome": "connected", "errorClass": None, "httpStatus": None, "errorType": None,
             "grantedScopes": ["openid", "https://www.googleapis.com/auth/gmail.readonly"],
             "account": "owner@example.com", **changes}
    return value


def exchanging(paths, written):
    """systemctl start of the exchange unit, leaving this result behind as the broker would."""
    def run(arguments, timeout):
        if written is not None:
            path = paths.oauth / f"{FLOW}.result.json"
            path.write_text(written if isinstance(written, str) else json.dumps(written))
        return 0
    return run


def test_the_kit_is_used_only_while_nobody_else_can_change_it(paths):
    assert custody._kit_bytes(paths, "broker.py") == b"# broker.py\n"
    target = paths.kit / "broker.py"
    for unsafe in (lambda: target.chmod(0o664), lambda: paths.kit.chmod(0o775),
                   lambda: paths.kit.parent.chmod(0o777), lambda: os.link(target, paths.kit.parent / "copy"),
                   lambda: (target.unlink(), target.symlink_to(paths.kit / "broker.env"))):
        unsafe()
        with pytest.raises(custody.CustodyError, match="source-broker-kit-unavailable"):
            custody._kit_bytes(paths, "broker.py")
        for path in (paths.kit, paths.kit.parent):
            path.chmod(0o755)
        if target.is_symlink():
            target.unlink()
        target.write_text("# broker.py\n")
        target.chmod(0o644)
        (paths.kit.parent / "copy").unlink(missing_ok=True)
    assert custody.kit_present(paths)


def test_prepare_never_lets_root_read_the_broker_projection(paths, monkeypatch):
    monkeypatch.setattr(custody, "account", lambda name: (0, 0) if name == "root" else (os.getuid(), os.getgid()))
    with pytest.raises(custody.CustodyError, match="source-broker-reader-unsafe"):
        custody.prepare(paths, lambda arguments, timeout: 0, reader="root")
    assert not paths.install.exists()


def test_the_exchange_result_is_accepted_only_in_its_exact_shape(paths):
    assert custody.exchange(paths, exchanging(paths, result()), FLOW) == {
        "outcome": "connected", "errorClass": None, "account": "owner@example.com",
        "grantedScopes": ["https://www.googleapis.com/auth/gmail.readonly", "openid"]}
    assert not list(paths.oauth.iterdir())
    failed = result(outcome="failed", errorClass="invalid-grant", httpStatus=400, errorType="UpstreamHTTPError",
                    grantedScopes=[], account=None)
    assert custody.exchange(paths, exchanging(paths, failed), FLOW)["errorClass"] == "invalid-grant"
    for broken in (result(flowId="b" * 32), result(errorClass="unknown"),
                   result(outcome="failed"), result(grantedScopes=["https://example.com/all"]),
                   result(account="not an address"), result(kind="pixel-source-oauth-flow"),
                   dict(result(), extra=True), result(errorClass="Has Spaces", outcome="failed"),
                   '{"schemaVersion": 1, "schemaVersion": 1}', "{not json"):
        with pytest.raises(custody.CustodyError, match="source-broker-result-invalid"):
            custody.exchange(paths, exchanging(paths, broken), FLOW)
        assert not list(paths.oauth.iterdir()), "a refused result is still consumed"
    with pytest.raises(custody.CustodyError, match="sign-in-exchange-unavailable"):
        custody.exchange(paths, exchanging(paths, None), FLOW)
    with pytest.raises(custody.CustodyError, match="invalid-flow"):
        custody.exchange(paths, exchanging(paths, None), "../escape")


def test_a_linked_result_is_refused(paths):
    elsewhere = paths.state / "elsewhere.json"
    elsewhere.write_text(json.dumps(result()))

    def run(arguments, timeout):
        os.link(elsewhere, paths.oauth / f"{FLOW}.result.json")
        return 0
    with pytest.raises(custody.CustodyError, match="source-broker-result-unsafe"):
        custody.exchange(paths, run, FLOW)


def test_the_broker_environment_keeps_every_key_and_quote_it_was_given(paths):
    text = custody.connection_env(paths, gateway=LIMBS, calendar_id="owner@example.com", time_zone="America/Chicago",
                                  gmail_window_days=180)
    values = dict(custody.parse_env(text))
    assert list(values) == [key for key, _ in custody.parse_env(TEMPLATE)]
    assert values["PIXEL_SOURCE_GMAIL_QUERY"] == "in:inbox newer_than:180d"
    assert values["PIXEL_SOURCE_GMAIL_SENT_QUERY"] == "in:sent newer_than:180d"
    assert values["PIXEL_SOURCE_LABEL"] == "Owner's calendar"
    assert (values["PIXEL_LIMB_EMAIL_ENABLED"], values["PIXEL_LIMB_CALENDAR_WRITE_ENABLED"]) == ("1", "0")
    assert values["PIXEL_SOURCE_TIME_ZONE"] == "America/Chicago"
    assert custody.connection_env(paths, gateway=LIMBS, calendar_id="primary", time_zone=None,
                                  gmail_window_days=180).count("PIXEL_SOURCE_TIME_ZONE='UTC'") == 1
    for broken in ("KEY=unquoted\n", "KEY='a'\nKEY='b'\n", "lower='x'\n", "KEY='it's'\n"):
        with pytest.raises(custody.CustodyError, match="source-broker-env-invalid"):
            custody.parse_env(broken)
    with pytest.raises(custody.CustodyError, match="source-broker-env-invalid"):
        custody.env_text([("A", "1")], {"B": "2"})


def test_revocation_always_ends_with_no_sign_in_here(paths):
    def broker_revoke(outcome):
        def run(arguments, timeout):
            assert arguments == ["systemctl", "start", "pixel-source-revoke.service"]
            paths.token.unlink()
            (paths.oauth / "revoke.result.json").write_text(json.dumps({
                "schemaVersion": 1, "kind": "pixel-source-oauth-revoke-result", "finishedAt": "2026-10-25T20:30:00Z",
                "outcome": outcome, "errorClass": None if outcome == "revoked" else "network", "httpStatus": None,
                "errorType": None}))
            return 0
        return run
    assert custody.revoke(paths, broker_revoke("revoked")) == "not-connected"
    (paths.units / "pixel-source-revoke.service").write_text("")
    for outcome, expected in (("revoked", "revoked"), ("local-only", "local-only")):
        paths.token.write_text("{}")
        assert custody.revoke(paths, broker_revoke(outcome)) == expected
        assert not paths.token.exists() and not (paths.oauth / "revoke.result.json").exists()
    paths.token.write_text("{}")
    # A broker that never ran still leaves no sign-in behind.
    assert custody.revoke(paths, lambda arguments, timeout: 1) == "local-only"
    assert not paths.token.exists()


def test_stopping_a_broker_that_was_never_prepared_runs_nothing(paths):
    commands = []
    custody.stop(paths, lambda arguments, timeout: commands.append(arguments) or 0)
    assert commands == []


def test_a_new_sign_in_clears_only_sign_in_files(paths):
    for name in (f"{FLOW}.flow.json", f"{'b' * 32}.code.json", f"{'c' * 32}.result.json", "revoke.result.json",
                 "notes.txt"):
        (paths.oauth / name).write_text("{}")
    custody.discard_flows(paths)
    assert sorted(path.name for path in paths.oauth.iterdir()) == ["notes.txt", "revoke.result.json"]
    custody.discard_flows(custody.Paths(state=paths.state / "never-prepared", kit=paths.kit))
