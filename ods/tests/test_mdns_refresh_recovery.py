"""Exercise the announcer's polling boundary with real on-disk configuration."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("failed_record", [1, 3, 7])
def test_reverting_config_after_failed_registration_republishes_original(tmp_path, monkeypatch, failed_record):
    spec = importlib.util.spec_from_file_location(
        "mdns_recovery", Path(__file__).resolve().parents[1] / "bin" / "ods-mdns.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    env_file = tmp_path / ".env"
    proxy = tmp_path / "extensions" / "services" / "ods-proxy"
    proxy.mkdir(parents=True)
    (proxy / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setattr(module, "INSTALL_DIR", tmp_path)
    monkeypatch.setattr(module, "ENV_FILE", env_file)
    monkeypatch.setattr(module, "_get_local_ip", lambda: "192.0.2.10")
    monkeypatch.setattr(module, "ServiceInfo", lambda **fields: SimpleNamespace(**fields))
    monkeypatch.setattr(module, "IPVersion", SimpleNamespace(V4Only="v4"))
    transports = []

    class Transport:
        def __init__(self, **_options):
            self.records = []
            self.closed = False
            self.calls = 0
            transports.append(self)

        def register_service(self, info):
            self.calls += 1
            if len(transports) == 2 and self.calls == failed_record:
                raise OSError("injected network failure while publishing changed config")
            self.records.append(info)

        def unregister_service(self, info):
            self.records.remove(info)

        def close(self):
            self.closed = True

    monkeypatch.setattr(module, "Zeroconf", Transport)
    original = "ODS_DEVICE_NAME=office\nODS_PROXY_PORT=8080\nBIND_ADDRESS=127.0.0.1\n"
    env_file.write_text(original, encoding="utf-8")
    announcer = module.Announcer()
    try:
        announcer.refresh()
        assert len(transports[0].records) == 7
        announcer.refresh()
        assert len(transports) == 1, "unchanged successful config should avoid re-registration"

        env_file.write_text(original.replace("office", "studio").replace("8080", "8090"), encoding="utf-8")
        with pytest.raises(OSError, match="injected network failure"):
            announcer.refresh()
        assert transports[0].closed
        assert transports[0].records == []

        # The normal next poll after the operator reverts their failed edit.
        env_file.write_text(original, encoding="utf-8")
        announcer.refresh()
        assert len(transports) == 3, "rollback was mistaken for an already published config"
        assert transports[1].closed
        assert transports[1].records == []
        records = transports[2].records
        assert {info.server for info in records} == {
            "office.local.", "chat.office.local.", "dashboard.office.local.",
            "auth.office.local.", "api.office.local.", "hermes.office.local.", "talk.office.local.",
        }
        assert all(info.port == 8080 for info in records)
        announcer.refresh()
        assert len(transports) == 3
    finally:
        announcer.shutdown()
    assert all(transport.closed and not transport.records for transport in transports)
