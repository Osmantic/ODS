#!/usr/bin/env python3
"""Exercise remote-provider source upgrades without touching a live install."""

from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "remote_provider_compose_selection", ROOT / "scripts/remote-provider-compose-selection.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
SERVICES = MODULE.SERVICES


def fixture() -> tuple[Path, Path, Path, tempfile.TemporaryDirectory]:
    temp = tempfile.TemporaryDirectory()
    base = Path(temp.name)
    source, install = base / "source", base / "install"
    for service_id in SERVICES:
        src = source / "extensions/services" / service_id
        dst = install / "extensions/services" / service_id
        src.mkdir(parents=True)
        dst.mkdir(parents=True)
        shutil.copyfile(ROOT / "extensions/services" / service_id / "compose.yaml.disabled",
                        src / "compose.yaml.disabled")
    secret = install / "data/remote-provider/secrets/provider-api-key"
    secret.parent.mkdir(parents=True)
    secret.write_bytes(b"fixture-secret-unchanged")
    return source, install, secret, temp


def route(install: Path, transport: str, *, enabled: bool = True) -> None:
    route_path = install / "data/remote-provider/routing-state.json"
    route_path.parent.mkdir(parents=True, exist_ok=True)
    route_path.write_text(json.dumps({
        "schema": "ods.remote-routing-state.v1", "enabled": enabled,
        "provider": {"transport": transport} if enabled else None,
    }), encoding="utf-8")


def copy_source(source: Path, install: Path) -> None:
    for service_id in SERVICES:
        src = source / "extensions/services" / service_id / "compose.yaml.disabled"
        dst = install / "extensions/services" / service_id / "compose.yaml.disabled"
        shutil.copyfile(src, dst)


def states(install: Path) -> tuple[bool, bool]:
    return tuple((install / "extensions/services" / sid / "compose.yaml").is_file()
                 for sid in SERVICES)


def exercise(initial: tuple[bool, bool], transport: str | None,
             expected: tuple[bool, bool]) -> None:
    source, install, secret, temp = fixture()
    try:
        for service_id, enabled in zip(SERVICES, initial):
            if enabled:
                (install / "extensions/services" / service_id / "compose.yaml").write_text(
                    "old selected recipe\n", encoding="utf-8")
        if transport:
            route(install, transport)
        before = secret.read_bytes()
        selection = MODULE.inspect(install)
        copy_source(source, install)  # rsync without --delete
        MODULE.apply(install, source, selection)
        assert states(install) == expected
        for service_id, enabled in zip(SERVICES, expected):
            directory = install / "extensions/services" / service_id
            assert (directory / "compose.yaml.disabled").exists() is (not enabled)
            selected = directory / ("compose.yaml" if enabled else "compose.yaml.disabled")
            assert selected.read_bytes() == (
                source / "extensions/services" / service_id / "compose.yaml.disabled").read_bytes()
        assert secret.read_bytes() == before
        # A later rerun must preserve the Library selection without a route.
        if not transport:
            second = MODULE.inspect(install)
            copy_source(source, install)
            MODULE.apply(install, source, second)
            assert states(install) == expected
    finally:
        temp.cleanup()


def main() -> None:
    exercise((False, False), None, (False, False))  # fresh Core
    exercise((False, False), "direct", (True, False))  # legacy active direct route
    exercise((False, False), "ssh", (True, True))  # legacy active SSH route
    exercise((True, False), None, (True, False))  # retained Library choice
    exercise((True, True), None, (True, True))
    for change_after_copy in (False, True):
        source, install, _, temp = fixture()
        try:
            directory = install / "extensions/services" / SERVICES[0]
            disabled = directory / "compose.yaml.disabled"
            active = directory / "compose.yaml"
            shutil.copyfile(source / "extensions/services" / SERVICES[0]
                            / "compose.yaml.disabled", disabled)
            selection = MODULE.inspect(install)
            if not change_after_copy:
                disabled.rename(active)  # Library Add after inspect
            copy_source(source, install)
            if change_after_copy:
                disabled.rename(active)  # Library Add after source copy
            MODULE.apply(install, source, selection)
            assert states(install) == (True, False)
        finally:
            temp.cleanup()
    source, install, _, temp = fixture()
    try:
        directory = install / "extensions/services" / SERVICES[0]
        active = directory / "compose.yaml"
        disabled = directory / "compose.yaml.disabled"
        active.write_text("old selected recipe\n", encoding="utf-8")
        selection = MODULE.inspect(install)
        active.rename(disabled)  # Library Disable after inspect
        copy_source(source, install)
        MODULE.apply(install, source, selection)
        assert states(install) == (False, False)
    finally:
        temp.cleanup()
    for transport, expected in (("direct", (True, False)), ("ssh", (True, True))):
        source, install, secret, temp = fixture()
        try:
            selection = MODULE.inspect(install)  # route is enabled after the snapshot
            copy_source(source, install)
            route(install, transport)
            MODULE.apply(install, source, selection)
            assert states(install) == expected
            assert secret.read_bytes() == b"fixture-secret-unchanged"
        finally:
            temp.cleanup()
    source, install, secret, temp = fixture()
    try:
        external = Path(temp.name) / "external"
        route(external, "ssh")
        selection = MODULE.inspect(install, data_dir=external / "data")
        copy_source(source, install)
        MODULE.apply(install, source, selection, data_dir=external / "data")
        assert states(install) == (True, True)
        assert secret.read_bytes() == b"fixture-secret-unchanged"
        try:
            MODULE.inspect(install, data_dir=Path("relative-data"))
        except ValueError:
            pass
        else:
            raise AssertionError("Relative external data directory must fail closed")
    finally:
        temp.cleanup()
    source, install, secret, temp = fixture()
    try:
        external = Path(temp.name) / "external"
        selection = MODULE.inspect(install, data_dir=external / "data")
        copy_source(source, install)
        route(external, "ssh")  # late route change in the configured external root
        MODULE.apply(install, source, selection, data_dir=external / "data")
        assert states(install) == (True, True)
        assert secret.read_bytes() == b"fixture-secret-unchanged"
    finally:
        temp.cleanup()
    source, install, _, temp = fixture()
    try:
        route_path = install / "data/remote-provider/routing-state.json"
        route_path.write_text("{bad", encoding="utf-8")
        try:
            MODULE.inspect(install)
        except ValueError:
            pass
        else:
            raise AssertionError("Malformed route state must fail closed")
        route_path.unlink()
        directory = install / "extensions/services" / SERVICES[0]
        (directory / "compose.yaml").write_text("selected", encoding="utf-8")
        (directory / "compose.yaml.disabled").write_text("disabled", encoding="utf-8")
        try:
            MODULE.inspect(install)
        except ValueError:
            pass
        else:
            raise AssertionError("Ambiguous markers must fail closed")
        (directory / "compose.yaml").unlink()
        route(install, "direct")
        try:
            MODULE.inspect(install)
        except ValueError:
            pass
        else:
            raise AssertionError("A disabled egress cannot silently drop an active route")
    finally:
        temp.cleanup()
    print("[PASS] remote-provider Compose selection upgrade and custody")


if __name__ == "__main__":
    main()
