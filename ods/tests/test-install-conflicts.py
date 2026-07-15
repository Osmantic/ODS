#!/usr/bin/env python3
"""Behavioral tests for scripts/check-install-conflicts.py."""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check-install-conflicts.py"


def load_module():
    spec = importlib.util.spec_from_file_location("check_install_conflicts", SCRIPT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def compose_fixture() -> dict[str, object]:
    return {
        "name": "ods",
        "services": {
            "web": {
                "container_name": "ods-web",
                "volumes": [
                    {
                        "type": "volume",
                        "source": "app-data",
                        "target": "/var/lib/app",
                    }
                ],
                "ports": [
                    {
                        "host_ip": "127.0.0.1",
                        "published": "3000",
                        "target": 8080,
                        "protocol": "tcp",
                    }
                ],
            }
        },
        "networks": {"default": {"name": "ods-network"}},
        "volumes": {"app-data": {"name": "ods_app-data"}},
    }


def container_fixture(
    *,
    name: str,
    running: bool,
    host_port: str,
    project: str = "other",
    working_dir: str = "/srv/other",
    service: str = "web",
    host_ip: str = "127.0.0.1",
    networks: tuple[str, ...] = (),
    volumes: tuple[str, ...] = (),
    bind_sources: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "Name": f"/{name}",
        "State": {
            "Running": running,
            "Status": "running" if running else "exited",
        },
        "Config": {
            "Labels": {
                "com.docker.compose.project": project,
                "com.docker.compose.project.working_dir": working_dir,
                "com.docker.compose.service": service,
            }
        },
        "HostConfig": {
            "PortBindings": {
                "8080/tcp": [
                    {
                        "HostIp": host_ip,
                        "HostPort": host_port,
                    }
                ]
            }
        },
        "NetworkSettings": {
            "Networks": {network: {} for network in networks},
        },
        "Mounts": [
            {
                "Type": "volume",
                "Name": volume,
            }
            for volume in volumes
        ]
        + [
            {
                "Type": "bind",
                "Source": source,
            }
            for source in bind_sources
        ],
    }


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def run_cli(
    directory: Path, compose: object, containers: object
) -> subprocess.CompletedProcess[str]:
    compose_path = directory / "compose.json"
    containers_path = directory / "containers.json"
    listeners_path = directory / "listeners.json"
    report_path = directory / "report.json"
    write_json(compose_path, compose)
    write_json(containers_path, containers)
    write_json(listeners_path, {})
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--source-dir",
            str(ROOT),
            "--install-dir",
            str(directory / "install"),
            "--compose-flags",
            "fixture.yml",
            "--compose-json",
            str(compose_path),
            "--docker-inspect-json",
            str(containers_path),
            "--listeners-json",
            str(listeners_path),
            "--report",
            str(report_path),
        ],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_foreign_running_container_blocks_port() -> None:
    module = load_module()
    claims = module.extract_claims(compose_fixture())
    conflicts, foreign_claims, owned_claims = module.find_docker_conflicts(
        claims=claims,
        containers=[
            container_fixture(
                name="other-web",
                running=True,
                host_port="3000",
            )
        ],
        networks={},
        volumes={},
        source_dir="/srv/ods-source",
        install_dir="/srv/ods",
        update=False,
    )
    assert any(conflict.kind == "docker-port" for conflict in conflicts)
    assert foreign_claims == set(claims.ports)
    assert owned_claims == set()


def test_stopped_container_blocks_restart_collision() -> None:
    module = load_module()
    claims = module.extract_claims(compose_fixture())
    conflicts, _, _ = module.find_docker_conflicts(
        claims=claims,
        containers=[
            container_fixture(
                name="dormant-web",
                running=False,
                host_port="3000",
            )
        ],
        networks={},
        volumes={},
        source_dir="/srv/ods-source",
        install_dir="/srv/ods",
        update=False,
    )
    port_conflict = next(
        conflict for conflict in conflicts if conflict.kind == "docker-port"
    )
    assert port_conflict.state == "exited"
    assert "if restarted" in port_conflict.message


def test_current_update_resources_are_ignored() -> None:
    module = load_module()
    claims = module.extract_claims(compose_fixture())
    conflicts, foreign_claims, owned_claims = module.find_docker_conflicts(
        claims=claims,
        containers=[
            container_fixture(
                name="ods-web",
                running=True,
                host_port="3000",
                project="ods",
                working_dir="/srv/ods",
                networks=("ods-network",),
                volumes=("ods_app-data",),
            )
        ],
        networks={
            "ods-network": {
                "Name": "ods-network",
                "Labels": {
                    "com.docker.compose.project": "ods",
                    "com.docker.compose.network": "default",
                },
            }
        },
        volumes={
            "ods_app-data": {
                "Name": "ods_app-data",
                "Labels": {
                    "com.docker.compose.project": "ods",
                    "com.docker.compose.volume": "app-data",
                },
            }
        },
        source_dir="/srv/ods",
        install_dir="/srv/ods",
        update=True,
    )
    assert conflicts == []
    assert foreign_claims == set()
    assert owned_claims == set(claims.ports)


def test_same_project_from_other_directory_is_not_owned() -> None:
    module = load_module()
    claims = module.extract_claims(compose_fixture())
    conflicts, _, _ = module.find_docker_conflicts(
        claims=claims,
        containers=[
            container_fixture(
                name="ods-web",
                running=False,
                host_port="3000",
                project="ods",
                working_dir="/srv/other-ods",
            )
        ],
        networks={},
        volumes={},
        source_dir="/srv/ods-source",
        install_dir="/srv/ods",
        update=True,
    )
    kinds = {conflict.kind for conflict in conflicts}
    assert kinds == {"container-name", "docker-port"}


def test_bind_mount_alone_does_not_prove_update_ownership() -> None:
    module = load_module()
    claims = module.extract_claims(compose_fixture())
    conflicts, _, _ = module.find_docker_conflicts(
        claims=claims,
        containers=[
            container_fixture(
                name="ods-web",
                running=False,
                host_port="3000",
                project="ods",
                working_dir="",
                bind_sources=("/srv/ods/data",),
            )
        ],
        networks={},
        volumes={},
        source_dir="/srv/ods-source",
        install_dir="/srv/ods",
        update=True,
    )
    assert {conflict.kind for conflict in conflicts} == {
        "container-name",
        "docker-port",
    }


def test_update_narrowing_owned_wildcard_binding_is_ignored() -> None:
    module = load_module()
    claims = module.extract_claims(compose_fixture())
    conflicts, foreign_claims, owned_claims = module.find_docker_conflicts(
        claims=claims,
        containers=[
            container_fixture(
                name="ods-web",
                running=True,
                host_port="3000",
                project="ods",
                working_dir="/srv/ods",
                host_ip="0.0.0.0",
            )
        ],
        networks={},
        volumes={},
        source_dir="/srv/ods",
        install_dir="/srv/ods",
        update=True,
    )
    assert conflicts == []
    assert foreign_claims == set()
    assert owned_claims == set(claims.ports)


def test_update_broadening_owned_binding_is_not_assumed_clear() -> None:
    module = load_module()
    fixture = compose_fixture()
    fixture["services"]["web"]["ports"][0]["host_ip"] = "0.0.0.0"
    claims = module.extract_claims(fixture)
    conflicts, foreign_claims, owned_claims = module.find_docker_conflicts(
        claims=claims,
        containers=[
            container_fixture(
                name="ods-web",
                running=True,
                host_port="3000",
                project="ods",
                working_dir="/srv/ods",
                host_ip="127.0.0.1",
            )
        ],
        networks={},
        volumes={},
        source_dir="/srv/ods",
        install_dir="/srv/ods",
        update=True,
    )
    assert conflicts == []
    assert foreign_claims == set()
    assert owned_claims == set()
    host_conflicts = module.find_host_conflicts(
        claims=claims,
        foreign_claims=foreign_claims,
        owned_active_claims=owned_claims,
        listener_fixture={"tcp:0.0.0.0:3000": True},
    )
    assert {conflict.kind for conflict in host_conflicts} == {"host-port"}


def test_stale_container_from_current_install_still_blocks() -> None:
    module = load_module()
    claims = module.extract_claims(compose_fixture())
    conflicts, foreign_claims, owned_claims = module.find_docker_conflicts(
        claims=claims,
        containers=[
            container_fixture(
                name="ods-retired-web",
                running=True,
                host_port="3000",
                project="ods",
                working_dir="/srv/ods",
                service="retired-web",
            )
        ],
        networks={},
        volumes={},
        source_dir="/srv/ods",
        install_dir="/srv/ods",
        update=True,
    )
    assert {conflict.kind for conflict in conflicts} == {"docker-port"}
    assert foreign_claims == set(claims.ports)
    assert owned_claims == set()


def test_fresh_install_blocks_exact_network_and_volume_adoption() -> None:
    module = load_module()
    claims = module.extract_claims(compose_fixture())
    conflicts, _, _ = module.find_docker_conflicts(
        claims=claims,
        containers=[],
        networks={"ods-network": {"Name": "ods-network", "Labels": {}}},
        volumes={"ods_app-data": {"Name": "ods_app-data", "Labels": {}}},
        source_dir="/srv/ods-source",
        install_dir="/srv/ods",
        update=False,
    )
    assert {conflict.kind for conflict in conflicts} == {
        "network-name",
        "volume-name",
    }


def test_update_does_not_trust_unreferenced_project_resources() -> None:
    module = load_module()
    claims = module.extract_claims(compose_fixture())
    conflicts, _, _ = module.find_docker_conflicts(
        claims=claims,
        containers=[],
        networks={
            "ods-network": {
                "Name": "ods-network",
                "Labels": {
                    "com.docker.compose.project": "ods",
                    "com.docker.compose.network": "default",
                },
            }
        },
        volumes={
            "ods_app-data": {
                "Name": "ods_app-data",
                "Labels": {
                    "com.docker.compose.project": "ods",
                    "com.docker.compose.volume": "app-data",
                },
            }
        },
        source_dir="/srv/ods",
        install_dir="/srv/ods",
        update=True,
    )
    assert {conflict.kind for conflict in conflicts} == {
        "network-name",
        "volume-name",
    }


def test_unrelated_dormant_volume_is_not_a_conflict() -> None:
    module = load_module()
    claims = module.extract_claims(compose_fixture())
    conflicts, _, _ = module.find_docker_conflicts(
        claims=claims,
        containers=[],
        networks={},
        volumes={},
        source_dir="/srv/ods-source",
        install_dir="/srv/ods",
        update=False,
    )
    assert conflicts == []


def test_unreferenced_top_level_volume_is_not_planned() -> None:
    module = load_module()
    fixture = compose_fixture()
    fixture["volumes"]["unused"] = {"name": "ods_unused"}
    claims = module.extract_claims(fixture)
    assert {claim.name for claim in claims.volumes} == {"ods_app-data"}


def test_zero_replica_service_has_no_runtime_claims() -> None:
    module = load_module()
    fixture = compose_fixture()
    fixture["services"]["disabled"] = {
        "container_name": "ods-disabled",
        "deploy": {"replicas": 0},
        "ports": [
            {
                "host_ip": "127.0.0.1",
                "published": "9999",
                "target": 9999,
                "protocol": "tcp",
            }
        ],
    }
    claims = module.extract_claims(fixture)
    assert "disabled" not in claims.services
    assert all(claim.service != "disabled" for claim in claims.ports)
    assert all(claim.key != "disabled" for claim in claims.containers)


def test_planned_port_and_name_collisions_are_reported() -> None:
    module = load_module()
    fixture = compose_fixture()
    fixture["services"]["api"] = {
        "container_name": "ods-web",
        "ports": [
            {
                "host_ip": "0.0.0.0",
                "published": "3000",
                "target": 9000,
                "protocol": "tcp",
            }
        ],
    }
    claims = module.extract_claims(fixture)
    conflicts = module.find_planned_conflicts(claims)
    assert {conflict.kind for conflict in conflicts} == {
        "planned-container-name",
        "planned-port",
    }


def test_invalid_and_oversized_port_ranges_fail_closed() -> None:
    module = load_module()
    for published in ("invalid", "1-4096"):
        fixture = compose_fixture()
        fixture["services"]["web"]["ports"][0]["published"] = published
        try:
            module.extract_claims(fixture)
        except module.ProbeError:
            continue
        raise AssertionError(f"invalid published port was accepted: {published}")


def test_compose_env_overrides_fill_empty_required_values_and_redact() -> None:
    module = load_module()
    with tempfile.TemporaryDirectory() as directory:
        base_path = Path(directory) / "base.env"
        base_path.write_text(
            "WEBUI_SECRET=existing-secret\nN8N_PASS=\n",
            encoding="utf-8",
        )
        temp_path, redaction_values = module.build_compose_env_file(
            str(base_path),
            ["N8N_PASS=override-secret", "BIND_ADDRESS=0.0.0.0"],
        )
        try:
            rendered = Path(temp_path).read_text(encoding="utf-8")
        finally:
            os.unlink(temp_path)

    assert "N8N_PASS=override-secret" in rendered
    assert "WEBUI_SECRET=existing-secret" in rendered
    redacted = module.redact(
        "existing-secret override-secret",
        redaction_values,
    )
    assert redacted == "[redacted] [redacted]"


def test_invalid_env_override_does_not_echo_value() -> None:
    module = load_module()
    secret = "do-not-print-this"
    try:
        module.parse_env_override(f"WEBUI_SECRET={secret}\nSECOND=value")
    except module.ProbeError as exc:
        assert secret not in str(exc)
    else:
        raise AssertionError("multiline environment override was accepted")


def test_ipv6_wildcard_is_canonicalized() -> None:
    module = load_module()
    assert module.normalize_host_ip("[::]") == "::"


def test_host_listener_is_reported_without_docker_owner() -> None:
    module = load_module()
    claims = module.extract_claims(compose_fixture())
    conflicts = module.find_host_conflicts(
        claims=claims,
        foreign_claims=set(),
        owned_active_claims=set(),
        listener_fixture={
            "tcp:127.0.0.1:3000": {
                "present": True,
                "detail": "native service",
            }
        },
    )
    assert len(conflicts) == 1
    assert conflicts[0].kind == "host-port"
    assert "native service" in conflicts[0].message


def test_host_listener_owned_by_current_update_is_ignored() -> None:
    module = load_module()
    claims = module.extract_claims(compose_fixture())
    conflicts = module.find_host_conflicts(
        claims=claims,
        foreign_claims=set(),
        owned_active_claims=set(claims.ports),
        listener_fixture={"tcp:127.0.0.1:3000": True},
    )
    assert conflicts == []


def test_real_tcp_and_udp_listeners_are_detected() -> None:
    module = load_module()
    for socket_type, protocol in (
        (socket.SOCK_STREAM, "tcp"),
        (socket.SOCK_DGRAM, "udp"),
    ):
        with socket.socket(socket.AF_INET, socket_type) as listener:
            listener.bind(("127.0.0.1", 0))
            if protocol == "tcp":
                listener.listen(1)
            port = listener.getsockname()[1]
            claim = module.PortClaim(
                service=f"fixture-{protocol}",
                host_ip="127.0.0.1",
                port=port,
                protocol=protocol,
            )
            assert module.socket_listener_present(claim)


def test_cli_writes_private_clear_report() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        result = run_cli(root, compose_fixture(), [])
        report_path = root / "report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        mode = stat.S_IMODE(report_path.stat().st_mode)

    assert result.returncode == 0, result.stdout + result.stderr
    assert report["status"] == "clear"
    assert report["conflicts"] == []
    assert mode == 0o600


def test_cli_conflict_exit_and_report() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        result = run_cli(
            root,
            compose_fixture(),
            [
                container_fixture(
                    name="foreign-web",
                    running=False,
                    host_port="3000",
                )
            ],
        )
        report = json.loads((root / "report.json").read_text(encoding="utf-8"))

    assert result.returncode == 1
    assert report["status"] == "conflict"
    assert {item["kind"] for item in report["conflicts"]} == {"docker-port"}


def test_cli_probe_error_fails_closed_and_reports() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        result = run_cli(root, [], [])
        report = json.loads((root / "report.json").read_text(encoding="utf-8"))

    assert result.returncode == 2
    assert report["status"] == "probe-error"
    assert "Compose fixture is not an object" in report["error"]


def test_cli_unexpected_error_fails_closed_and_reports() -> None:
    module = load_module()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        compose_path = root / "compose.json"
        containers_path = root / "containers.json"
        listeners_path = root / "listeners.json"
        report_path = root / "report.json"
        write_json(compose_path, compose_fixture())
        write_json(containers_path, [])
        write_json(listeners_path, {})

        def raise_unexpected(_payload):
            raise RuntimeError("unexpected test failure")

        module.extract_claims = raise_unexpected
        original_argv = sys.argv
        sys.argv = [
            str(SCRIPT),
            "--source-dir",
            str(ROOT),
            "--install-dir",
            str(root / "install"),
            "--compose-flags",
            "fixture.yml",
            "--compose-json",
            str(compose_path),
            "--docker-inspect-json",
            str(containers_path),
            "--listeners-json",
            str(listeners_path),
            "--report",
            str(report_path),
            "--quiet",
        ]
        try:
            status = module.main()
        finally:
            sys.argv = original_argv

        report = json.loads(report_path.read_text(encoding="utf-8"))

    assert status == 2
    assert report["status"] == "probe-error"
    assert "RuntimeError" in report["error"]


def main() -> int:
    tests = [
        test_foreign_running_container_blocks_port,
        test_stopped_container_blocks_restart_collision,
        test_current_update_resources_are_ignored,
        test_same_project_from_other_directory_is_not_owned,
        test_bind_mount_alone_does_not_prove_update_ownership,
        test_update_narrowing_owned_wildcard_binding_is_ignored,
        test_update_broadening_owned_binding_is_not_assumed_clear,
        test_stale_container_from_current_install_still_blocks,
        test_fresh_install_blocks_exact_network_and_volume_adoption,
        test_update_does_not_trust_unreferenced_project_resources,
        test_unrelated_dormant_volume_is_not_a_conflict,
        test_unreferenced_top_level_volume_is_not_planned,
        test_zero_replica_service_has_no_runtime_claims,
        test_planned_port_and_name_collisions_are_reported,
        test_invalid_and_oversized_port_ranges_fail_closed,
        test_compose_env_overrides_fill_empty_required_values_and_redact,
        test_invalid_env_override_does_not_echo_value,
        test_ipv6_wildcard_is_canonicalized,
        test_host_listener_is_reported_without_docker_owner,
        test_host_listener_owned_by_current_update_is_ignored,
        test_real_tcp_and_udp_listeners_are_detected,
        test_cli_writes_private_clear_report,
        test_cli_conflict_exit_and_report,
        test_cli_probe_error_fails_closed_and_reports,
        test_cli_unexpected_error_fails_closed_and_reports,
    ]
    for test in tests:
        test()
        print(f"[PASS] {test.__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
