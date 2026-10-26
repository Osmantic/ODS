"""Connected apps on the root control socket and the owner-worker pipe: exact frames only."""
from pathlib import Path
import re
import sys

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "bin"))
import pixel_access_protocol as protocol  # noqa: E402
from pixel_connections import state  # noqa: E402

MODULES = ("__init__.py", "state.py", "status.py", "catalog.py", "oauth.py", "custody.py", "planner.py",
           "coordinator.py", "lifecycle.py", "approvals.py")
ACTIONS = ("connection-begin", "connection-complete", "connection-activate", "connection-cancel",
           "connection-disconnect", "connection-sync", "connection-proposals", "connection-approve",
           "connection-decline", "connection-reconcile")
TARGET = {"email": True, "calendar": True, "calendarWrite": False, "calendarDirect": False,
          "userTimezone": "America/Chicago"}
ANSWERS = state.connection_answers(None, google_custody=False)
ANSWERS_PATH = "/home/owner/ods/data/pixel/onboarding.json"


def worker_request(operation, **values):
    request = {"operation": operation, "openclaw": "/opt/openclaw/bin/openclaw",
               "config_sha256": None if operation.endswith("status") else "a" * 64, "confirmed": False}
    request.update(values)
    return request


def test_connection_status_is_one_exact_frame():
    assert protocol.control_request({"operation": "connection-status"}) == {"operation": "connection-status"}
    for extra in ({"request": {}}, {"data_dir_id": "a" * 64}, {"account": "owner@example.com"}):
        with pytest.raises(protocol.ProtocolError):
            protocol.control_request({"operation": "connection-status", **extra})


def test_connection_status_never_reaches_the_owner_worker():
    # Owner-worker operations can carry busy or restart hooks; a status read must not.
    assert "connection-status" not in protocol.KEYS
    assert "connection-status" not in protocol.HOOKS


def test_each_custodian_action_is_a_frame_with_one_request_object():
    for operation in ACTIONS:
        assert protocol.control_request({"operation": operation, "request": {}}) == {"operation": operation,
                                                                                      "request": {}}
        for bad in ({"operation": operation}, {"operation": operation, "request": []},
                    {"operation": operation, "request": {}, "data_dir_id": "a" * 64}):
            with pytest.raises(protocol.ProtocolError):
                protocol.control_request(bad)


def test_only_the_gateway_change_reaches_the_owner_worker_with_hooks():
    assert protocol.HOOKS["connection-owner-status"] == ()
    assert protocol.HOOKS["connection-apply"] == ("busy", "connection-activate")
    assert protocol.HOOKS["connection-recover"] == ("busy", "connection-activate")
    for operation in ACTIONS:
        assert operation not in protocol.KEYS


def test_the_worker_receives_an_exact_target_answers_and_contract_path():
    good = worker_request("connection-apply", transaction_id="b" * 64, connection_target=TARGET,
                          connection_answers=ANSWERS, answers_path=ANSWERS_PATH)
    assert protocol.request(good) == good
    assert protocol.request(worker_request("connection-owner-status", answers_path=ANSWERS_PATH))
    broken = [
        dict(good, connection_target=dict(TARGET, calendarWrite="yes")),
        dict(good, connection_target=dict(TARGET, extra=True)),
        dict(good, connection_answers=dict(ANSWERS, googleAccount="not an address")),
        dict(good, connection_answers={key: value for key, value in ANSWERS.items() if key != "timeZone"}),
        dict(good, answers_path="relative/data/pixel/onboarding.json"),
        dict(good, answers_path="/home/owner/ods/data/pixel/../../../etc/onboarding.json"),
        dict(good, answers_path="/home/owner/ods/data/pixel/other.json"),
        dict(good, transaction_id="not-hex"),
        worker_request("connection-owner-status"),
    ]
    for value in broken:
        with pytest.raises(protocol.ProtocolError):
            protocol.request(value)


def test_worker_results_have_exact_shapes():
    status = {"configSha256": "a" * 64, "envSha256": "b" * 64, "pending": False, "live": TARGET,
              "answers": ANSWERS, "completion": None}
    assert protocol.result("connection-owner-status", status) == status
    done = dict(status, completion={"transactionId": "c" * 64, "outcome": "applied", "configSha256": "a" * 64})
    assert protocol.result("connection-owner-status", done) == done
    for bad in (dict(status, live=dict(TARGET, email="yes")), dict(status, answers={}),
                dict(done, pending=True), dict(status, token="secret")):
        with pytest.raises(protocol.ProtocolError):
            protocol.result("connection-owner-status", bad)
    assert protocol.result("connection-apply", {"status": "runtime-verified", "configSha256": "a" * 64})
    assert protocol.result("connection-recover", {"status": "rolled-back", "configSha256": "a" * 64})
    with pytest.raises(protocol.ProtocolError):
        protocol.result("connection-recover", {"status": "runtime-verified", "configSha256": "a" * 64})
    assert protocol.hook_reply("connection-apply", "connection-activate", "verified") == "verified"
    with pytest.raises(protocol.ProtocolError):
        protocol.hook_reply("connection-apply", "connection-activate", True)


def test_every_custody_list_names_the_connection_modules():
    installer = (ROOT / "installers/lib/pixel-host-install.sh").read_text(encoding="utf-8")
    server = (ROOT / "extensions/services/pixel-agent/host/access_mode_server.py").read_text(encoding="utf-8")
    worker = (ROOT / "extensions/services/pixel-agent/host/access_mode_worker.py").read_text(encoding="utf-8")
    uninstaller = (ROOT / "lib/pixel-uninstall.sh").read_text(encoding="utf-8")
    fixture = (ROOT / "tests/test-pixel-uninstall.sh").read_text(encoding="utf-8")
    assert "connections_package = target / 'pixel_connections'" in installer
    assert "'access_release_transaction.py', 'connection_transaction.py'):" in installer
    assert 'protected(PROGRAM / "connection_transaction.py")' in server
    assert 'directory / "pixel_connections" / "planner.py"' in worker
    assert 'pathlib.PurePosixPath("pixel_connections/source-broker-kit")' in uninstaller
    assert '"connection.json": 8192' in uninstaller
    for name in MODULES:
        assert (ROOT / "bin/pixel_connections" / name).is_file()
        assert f"'{name}'" in installer.split("connections_package = target / 'pixel_connections'", 1)[1].split("config_dir", 1)[0]
        assert f'"{name}"' in server.split('"pixel_connections" / name', 1)[0].rsplit("for name in", 1)[1]
        assert f'"{name.removesuffix(".py")}' in uninstaller.split("CONNECTION_MODULES = ", 1)[1].split("SOURCE_BROKER_KIT", 1)[0] \
            or name == "__init__.py"
        assert f'"bin/pixel_connections/{name}"' in fixture


def test_full_access_pauses_portals_own_calendar_changes_around_every_mode_change():
    server = (ROOT / "extensions/services/pixel-agent/host/access_mode_server.py").read_text(encoding="utf-8")
    branch = server.split('request["operation"] == "change":', 1)[1].split("\n                elif ", 1)[0]
    # Paused before Portal gains Full Access, so the broker never runs a change made under it.
    assert 'request["request"].get("mode") == "full-access"' in branch
    assert branch.index("pause_for_full_access(adapter)") < branch.index('adapter.change(request["request"])')
    # Followed after every attempt, so a refused or rolled-back change resumes them too.
    assert "follow_connections(adapter)" in branch.split("finally:", 1)[1]
    helper = server.split("def follow_connections(adapter):", 1)[1].split("\ndef ", 1)[0]
    assert "follow_access_mode(adapter)" in helper and '"connection-activate"' in helper
    assert "threading.Thread(target=catch_up, daemon=True).start()" in helper


def test_the_installer_prepares_the_kit_before_the_broker_and_converges_before_comparing():
    from pixel_connections import custody
    installer = (ROOT / "installers/lib/pixel-host-install.sh").read_text(encoding="utf-8")
    kit = installer.split("_ods_pixel_install_source_broker_kit() {", 1)[1].split("\n}\n", 1)[0]
    # The kit lands beside the installed custody module, where custody.KIT looks for it.
    assert "target = pathlib.Path('/usr/local/libexec/ods-pixel-access')" in installer
    assert "pathlib.Path('/usr/local/libexec/ods-pixel-access/pixel_connections/source-broker-kit')" in kit
    assert custody.KIT.parts[-2:] == ("pixel_connections", "source-broker-kit")
    units = re.search(r"units = \(([^)]*)\)", kit).group(1)
    assert tuple(re.findall(r"'([^']+)'", units)) == custody.UNITS
    assert set(re.findall(r"'([a-z.-]+)': root /", kit)) == {"broker.py", "source-broker.env"}
    reuse, configure = installer.split('if [[ "$reuse_active" == true ]]; then', 1)[1].split("\n    else\n", 1)
    for branch in (reuse, configure):
        assert (branch.index('_ods_pixel_install_source_broker_kit "$owner"')
                < branch.index('_ods_pixel_install_source_broker_if_connected "$owner"'))
    same_source = configure.split('if [[ "$same_verified_source" == true ]]; then', 1)[1]
    assert same_source.index("pixel_connections/converge.py") < same_source.index(
        "_ods_pixel_candidate_config_matches_live")


def test_the_root_service_answers_from_the_status_module_on_linux_only():
    server = (ROOT / "extensions/services/pixel-agent/host/access_mode_server.py").read_text(encoding="utf-8")
    branch = server.split('elif request == {"operation": "connection-status"}', 1)[1].split("elif ", 1)[0]
    assert 'platform.system() == "Linux"' in branch
    assert "from pixel_connections.status import report as connection_report" in branch
    assert "connection_report(surface=surface)" in branch
    actions = server.split('elif request["operation"].startswith("connection-")', 1)[1].split("elif ", 1)[0]
    assert 'platform.system() == "Linux"' in actions
    assert 'adapter.connection_control(request["operation"], request["request"])' in actions
