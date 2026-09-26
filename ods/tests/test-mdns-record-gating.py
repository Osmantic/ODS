#!/usr/bin/env python3
"""The announcer must not publish records that cannot answer.

bin/ods-mdns.py already refuses to publish direct-port SRV records when
BIND_ADDRESS is loopback-only, on the grounds that they "would point LAN
clients at unreachable endpoints". These tests hold the rest of the record set
to the same standard:

  * the ods-proxy subdomain records depend on Caddy listening, so they must
    not be published when the ods-proxy extension is disabled;
  * Hermes publishes no host port at all (its compose declares only
    `expose: 9119`), so a direct SRV on 9119 can never connect.

zeroconf is imported lazily inside main(), so these run without it installed.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MDNS_SCRIPT = ROOT / "bin" / "ods-mdns.py"


def load_mdns():
    spec = importlib.util.spec_from_file_location("ods_mdns_under_test", MDNS_SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not load {MDNS_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # ServiceInfo is None until the lazy zeroconf import in main(); stub it so
    # record construction is observable without the package present.
    module.ServiceInfo = type(
        "ServiceInfoStub", (), {"__init__": lambda self, **kw: self.__dict__.update(kw)}
    )
    return module


def build(module, *, bind: str, proxy_enabled: bool):
    """Run _build_services against a disposable install root."""
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        proxy_dir = root / "extensions" / "services" / "ods-proxy"
        proxy_dir.mkdir(parents=True)
        # Enablement is on-disk: compose.yaml present vs .disabled.
        name = "compose.yaml" if proxy_enabled else "compose.yaml.disabled"
        (proxy_dir / name).write_text("services: {}\n", encoding="utf-8")
        module.INSTALL_DIR = root
        env = {"BIND_ADDRESS": bind, "HERMES_PORT": "9119", "ODS_PROXY_PORT": "80",
               "DASHBOARD_PORT": "3001", "WEBUI_PORT": "3000",
               "DASHBOARD_API_PORT": "3002"}
        return module._build_services(env, "ods", "192.0.2.10")


def names(infos) -> list[str]:
    return [i.name.split("._http")[0] for i in infos]


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_proxy_records_are_skipped_when_proxy_is_disabled(module) -> None:
    infos = build(module, bind="0.0.0.0", proxy_enabled=False)
    proxy = [n for n in names(infos) if n.startswith("ods-proxy-")]
    assert_true(proxy == [], f"proxy records published while ods-proxy is disabled: {proxy}")


def test_proxy_records_are_published_when_proxy_is_enabled(module) -> None:
    infos = build(module, bind="0.0.0.0", proxy_enabled=True)
    proxy = [n for n in names(infos) if n.startswith("ods-proxy-")]
    assert_true(len(proxy) == 7, f"expected the full proxy subdomain set, got {proxy}")


def test_nothing_is_published_when_nothing_is_reachable(module) -> None:
    """Loopback bind plus no proxy means no LAN endpoint exists at all."""
    infos = build(module, bind="127.0.0.1", proxy_enabled=False)
    assert_true(infos == [], f"records published with no reachable endpoint: {names(infos)}")


def test_no_direct_hermes_record_is_ever_published(module) -> None:
    """Hermes binds no host port, so a direct SRV points at a closed port."""
    for bind in ("0.0.0.0", "127.0.0.1"):
        for proxy_enabled in (True, False):
            infos = build(module, bind=bind, proxy_enabled=proxy_enabled)
            direct_hermes = [n for n in names(infos) if n == "ods-hermes"]
            assert_true(
                direct_hermes == [],
                f"direct Hermes SRV published (bind={bind}, proxy={proxy_enabled})",
            )


def test_hermes_is_still_reachable_through_the_proxy_record(module) -> None:
    """Removing the direct record must not remove the supported LAN path."""
    infos = build(module, bind="0.0.0.0", proxy_enabled=True)
    assert_true("ods-proxy-hermes" in names(infos),
                "hermes.<device>.local must still be announced via the proxy")


def main() -> int:
    module = load_mdns()
    tests = [
        test_proxy_records_are_skipped_when_proxy_is_disabled,
        test_proxy_records_are_published_when_proxy_is_enabled,
        test_nothing_is_published_when_nothing_is_reachable,
        test_no_direct_hermes_record_is_ever_published,
        test_hermes_is_still_reachable_through_the_proxy_record,
    ]
    for test in tests:
        test(module)
        print(f"[PASS] {test.__name__}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        raise SystemExit(1)
