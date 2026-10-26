"""Connected-apps status on the root control socket: one exact read-only frame."""
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "bin"))
import pixel_access_protocol as protocol  # noqa: E402

MODULES = ("__init__.py", "state.py", "status.py", "catalog.py")


def test_connection_status_is_one_exact_frame():
    assert protocol.control_request({"operation": "connection-status"}) == {"operation": "connection-status"}
    for extra in ({"request": {}}, {"data_dir_id": "a" * 64}, {"account": "owner@example.com"}):
        with pytest.raises(protocol.ProtocolError):
            protocol.control_request({"operation": "connection-status", **extra})


def test_connection_status_never_reaches_the_owner_worker():
    # Owner-worker operations can carry busy or restart hooks; a status read must not.
    assert "connection-status" not in protocol.KEYS
    assert "connection-status" not in protocol.HOOKS


def test_every_custody_list_names_the_connection_modules():
    installer = (ROOT / "installers/lib/pixel-host-install.sh").read_text(encoding="utf-8")
    server = (ROOT / "extensions/services/pixel-agent/host/access_mode_server.py").read_text(encoding="utf-8")
    uninstaller = (ROOT / "lib/pixel-uninstall.sh").read_text(encoding="utf-8")
    fixture = (ROOT / "tests/test-pixel-uninstall.sh").read_text(encoding="utf-8")
    assert "connections_package = target / 'pixel_connections'" in installer
    assert "for name in ('__init__.py', 'state.py', 'status.py', 'catalog.py'):" in installer
    assert 'for name in ("__init__.py", "state.py", "status.py", "catalog.py"):' in server
    assert 'protected(PROGRAM / "pixel_connections" / name)' in server
    assert 'pathlib.PurePosixPath("pixel_connections")' in uninstaller
    assert "*CONNECTION_MODULES" in uninstaller
    for name in MODULES:
        assert (ROOT / "bin/pixel_connections" / name).is_file()
        assert f'"bin/pixel_connections/{name}"' in fixture


def test_the_root_service_answers_from_the_status_module_on_linux_only():
    server = (ROOT / "extensions/services/pixel-agent/host/access_mode_server.py").read_text(encoding="utf-8")
    branch = server.split('elif request == {"operation": "connection-status"}', 1)[1].split("elif ", 1)[0]
    assert 'platform.system() == "Linux"' in branch
    assert "from pixel_connections.status import report as connection_report" in branch
    assert "connection_report(surface=surface)" in branch
