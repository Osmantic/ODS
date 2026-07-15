#!/usr/bin/env python3
"""Detect host and Docker claims that would conflict with an ODS install."""

from __future__ import annotations

import argparse
import errno
import json
import os
import re
import shlex
import socket
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence


EXIT_CONFLICT = 1
EXIT_PROBE_ERROR = 2
COMMAND_TIMEOUT_SECONDS = 120
MAX_PUBLISHED_PORT_RANGE = 1024

COMPOSE_REQUIRED_VALUES = {
    "N8N_PASS": "conflict-check",
    "N8N_USER": "conflict-check@example.invalid",
    "OPENCLAW_TOKEN": "conflict-check",
    "SEARXNG_SECRET": "conflict-check",
    "WEBUI_SECRET": "conflict-check",
}

ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SENSITIVE_ENV_MARKERS = ("API_KEY", "_KEY", "PASS", "PASSWORD", "SECRET", "TOKEN")


@dataclass(frozen=True)
class PortClaim:
    service: str
    host_ip: str
    port: int
    protocol: str


@dataclass(frozen=True)
class NamedClaim:
    key: str
    name: str


@dataclass(frozen=True)
class ComposeClaims:
    project: str
    services: tuple[str, ...]
    ports: tuple[PortClaim, ...]
    containers: tuple[NamedClaim, ...]
    networks: tuple[NamedClaim, ...]
    volumes: tuple[NamedClaim, ...]


@dataclass(frozen=True)
class Conflict:
    kind: str
    message: str
    resource: str
    service: str = ""
    state: str = ""


class ProbeError(RuntimeError):
    """Raised when the detector cannot establish authoritative runtime state."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Render the planned Compose stack and reject host/Docker claims "
            "owned by another install."
        )
    )
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--install-dir", required=True)
    parser.add_argument("--compose-command", default="docker compose")
    parser.add_argument("--compose-flags", required=True)
    parser.add_argument("--docker-command", default="docker")
    parser.add_argument("--env-file", default="")
    parser.add_argument("--env-override", action="append", default=[])
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--report", default="")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--compose-json", default=argparse.SUPPRESS)
    parser.add_argument("--docker-inspect-json", default=argparse.SUPPRESS)
    parser.add_argument("--network-inspect-json", default=argparse.SUPPRESS)
    parser.add_argument("--volume-inspect-json", default=argparse.SUPPRESS)
    parser.add_argument("--listeners-json", default=argparse.SUPPRESS)
    return parser.parse_args()


def normalize_path(value: str) -> str:
    if not value:
        return ""
    return os.path.realpath(os.path.abspath(os.path.expanduser(value)))


def read_json(path: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProbeError(f"Could not read fixture JSON {path}: {exc}") from exc


def split_command(value: str, label: str) -> list[str]:
    try:
        command = shlex.split(value)
    except ValueError as exc:
        raise ProbeError(f"Could not parse {label}: {exc}") from exc
    if not command:
        raise ProbeError(f"{label} is empty")
    return command


def redact(text: str, values: Iterable[str]) -> str:
    redacted = text
    for value in sorted(
        {item for item in values if len(item) >= 4}, key=len, reverse=True
    ):
        redacted = redacted.replace(value, "[redacted]")
    return redacted


def parse_env_assignment(raw_line: str) -> Optional[tuple[str, str]]:
    line = raw_line.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("export "):
        line = line[7:].lstrip()
    if "=" not in line:
        return None
    key, value = line.split("=", 1)
    key = key.strip()
    if not ENV_KEY_RE.fullmatch(key):
        return None
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return key, value


def env_value(lines: Sequence[str], key: str) -> str:
    value = ""
    for raw_line in lines:
        assignment = parse_env_assignment(raw_line)
        if assignment and assignment[0] == key:
            value = assignment[1]
    return value


def parse_env_override(value: str) -> tuple[str, str]:
    if "\n" in value or "\r" in value or "=" not in value:
        raise ProbeError("Invalid environment override; expected KEY=VALUE on one line")
    key, override_value = value.split("=", 1)
    if not ENV_KEY_RE.fullmatch(key):
        raise ProbeError(f"Invalid environment override key: {key!r}")
    return key, override_value


def build_compose_env_file(
    base_path: str,
    overrides: Sequence[str] = (),
) -> tuple[str, list[str]]:
    lines: list[str] = []
    if base_path:
        try:
            lines = Path(base_path).read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ProbeError(
                f"Could not read Compose environment file {base_path}: {exc}"
            ) from exc

    for override in overrides:
        key, value = parse_env_override(override)
        lines.append(f"{key}={value}")

    for key, value in COMPOSE_REQUIRED_VALUES.items():
        if not env_value(lines, key):
            lines.append(f"{key}={value}")

    secret_values = []
    for raw_line in lines:
        assignment = parse_env_assignment(raw_line)
        if not assignment:
            continue
        key, value = assignment
        if value and any(marker in key.upper() for marker in SENSITIVE_ENV_MARKERS):
            secret_values.append(value)

    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="ods-conflict-env.",
        dir=os.environ.get("TMPDIR") or None,
        delete=False,
    )
    try:
        os.chmod(handle.name, 0o600)
        handle.write("\n".join(lines))
        handle.write("\n")
    finally:
        handle.close()
    return handle.name, secret_values


def run_command(
    command: Sequence[str],
    *,
    cwd: Optional[str] = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            list(command),
            cwd=cwd,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(
            f"Command timed out after {COMMAND_TIMEOUT_SECONDS}s: {command[0]}"
        ) from exc
    except OSError as exc:
        raise ProbeError(f"Could not run {command[0]}: {exc}") from exc
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        summary = detail[-1] if detail else f"exit {result.returncode}"
        raise ProbeError(f"Command failed: {' '.join(command[:3])}: {summary}")
    return result


def render_compose_config(
    *,
    source_dir: str,
    compose_command: str,
    compose_flags: str,
    env_file: str,
    env_overrides: Sequence[str] = (),
) -> Mapping[str, Any]:
    command = split_command(compose_command, "Compose command")
    flags = split_command(compose_flags, "Compose flags")
    temp_env, redaction_values = build_compose_env_file(env_file, env_overrides)
    try:
        base = command + ["--env-file", temp_env] + flags + ["config"]
        json_result = run_command(
            base + ["--format", "json"], cwd=source_dir, check=False
        )
        if json_result.returncode == 0:
            try:
                payload = json.loads(json_result.stdout)
            except json.JSONDecodeError as exc:
                raise ProbeError(f"Compose returned invalid JSON: {exc}") from exc
            if not isinstance(payload, Mapping):
                raise ProbeError("Compose config JSON is not an object")
            return payload

        yaml_result = run_command(base, cwd=source_dir, check=False)
        if yaml_result.returncode != 0:
            detail = redact(
                (
                    yaml_result.stderr or json_result.stderr or yaml_result.stdout
                ).strip(),
                redaction_values,
            )
            summary = detail.splitlines()[-1] if detail else "unknown Compose error"
            raise ProbeError(f"Could not render Compose config: {summary}")
        try:
            import yaml
        except ImportError as exc:
            raise ProbeError(
                "Compose does not support JSON config output and PyYAML is unavailable"
            ) from exc
        try:
            payload = yaml.safe_load(yaml_result.stdout)
        except yaml.YAMLError as exc:
            detail = redact(str(exc), redaction_values)
            raise ProbeError(f"Compose returned invalid YAML: {detail}") from exc
        if not isinstance(payload, Mapping):
            raise ProbeError("Compose config YAML is not an object")
        return payload
    finally:
        try:
            os.unlink(temp_env)
        except OSError:
            pass


def normalize_host_ip(value: Any) -> str:
    host_ip = str(value or "").strip()
    if not host_ip:
        return "0.0.0.0"
    if host_ip == "[::]":
        return "::"
    return host_ip


def expand_published_ports(value: Any) -> tuple[int, ...]:
    text = str(value or "").strip()
    if not text or text == "0":
        return ()
    if "-" not in text:
        try:
            port = int(text)
        except ValueError as exc:
            raise ProbeError(f"Invalid published port: {text!r}") from exc
        if not 0 < port <= 65535:
            raise ProbeError(f"Published port is outside 1-65535: {text!r}")
        return (port,)
    if text.count("-") != 1:
        raise ProbeError(f"Invalid published port range: {text!r}")
    start_text, end_text = text.split("-", 1)
    try:
        start = int(start_text)
        end = int(end_text)
    except ValueError as exc:
        raise ProbeError(f"Invalid published port range: {text!r}") from exc
    if start < 1 or end > 65535 or end < start:
        raise ProbeError(f"Published port range is outside 1-65535: {text!r}")
    if end - start + 1 > MAX_PUBLISHED_PORT_RANGE:
        raise ProbeError(
            f"Published port range is too large to inspect safely: {text!r}"
        )
    return tuple(range(start, end + 1))


def parse_short_port(value: str) -> tuple[str, tuple[int, ...], str]:
    protocol = "tcp"
    raw = value
    if "/" in raw:
        raw, protocol = raw.rsplit("/", 1)
    parts = raw.split(":")
    if len(parts) == 1:
        return "0.0.0.0", (), protocol
    if len(parts) == 2:
        host_ip = "0.0.0.0"
        published = parts[0]
    else:
        host_ip = ":".join(parts[:-2]) or "0.0.0.0"
        published = parts[-2]
    return (
        normalize_host_ip(host_ip.strip("[]")),
        expand_published_ports(published),
        protocol,
    )


def extract_claims(config: Mapping[str, Any]) -> ComposeClaims:
    project = str(config.get("name") or "default")
    active_services: set[str] = set()
    ports: set[PortClaim] = set()
    containers: set[NamedClaim] = set()
    used_network_keys: set[str] = set()
    used_volume_keys: set[str] = set()

    raw_top_networks = config.get("networks") or {}
    network_keys = (
        {str(key) for key in raw_top_networks}
        if isinstance(raw_top_networks, Mapping)
        else set()
    )
    raw_top_volumes = config.get("volumes") or {}
    volume_keys = (
        {str(key) for key in raw_top_volumes}
        if isinstance(raw_top_volumes, Mapping)
        else set()
    )

    services = config.get("services")
    if not isinstance(services, Mapping):
        raise ProbeError("Compose config has no services object")
    for service, raw_definition in services.items():
        if not isinstance(raw_definition, Mapping):
            continue
        definition = raw_definition
        deploy = definition.get("deploy")
        replicas = deploy.get("replicas") if isinstance(deploy, Mapping) else None
        scale = definition.get("scale")
        if str(replicas).strip() == "0" or str(scale).strip() == "0":
            continue
        active_services.add(str(service))

        container_name = str(definition.get("container_name") or "").lstrip("/")
        if container_name:
            containers.add(NamedClaim(key=str(service), name=container_name))

        raw_ports = definition.get("ports") or ()
        if not isinstance(raw_ports, Sequence) or isinstance(raw_ports, (str, bytes)):
            continue
        for raw_port in raw_ports:
            if isinstance(raw_port, Mapping):
                protocol = str(raw_port.get("protocol") or "tcp").lower()
                host_ip = normalize_host_ip(raw_port.get("host_ip"))
                for published in expand_published_ports(raw_port.get("published")):
                    ports.add(
                        PortClaim(
                            service=str(service),
                            host_ip=host_ip,
                            port=published,
                            protocol=protocol,
                        )
                    )
            elif isinstance(raw_port, str):
                host_ip, published_ports, protocol = parse_short_port(raw_port)
                for published in published_ports:
                    ports.add(
                        PortClaim(
                            service=str(service),
                            host_ip=host_ip,
                            port=published,
                            protocol=protocol.lower(),
                        )
                    )

        raw_networks = definition.get("networks")
        if isinstance(raw_networks, Mapping):
            used_network_keys.update(str(key) for key in raw_networks)
        elif isinstance(raw_networks, Sequence) and not isinstance(
            raw_networks, (str, bytes)
        ):
            used_network_keys.update(str(key) for key in raw_networks)
        elif (
            raw_networks is None
            and "default" in network_keys
            and not definition.get("network_mode")
        ):
            used_network_keys.add("default")

        raw_volumes = definition.get("volumes") or ()
        if isinstance(raw_volumes, Sequence) and not isinstance(
            raw_volumes, (str, bytes)
        ):
            for raw_volume in raw_volumes:
                source = ""
                if isinstance(raw_volume, Mapping):
                    volume_type = str(raw_volume.get("type") or "")
                    if volume_type not in {"", "volume"}:
                        continue
                    source = str(raw_volume.get("source") or "")
                elif isinstance(raw_volume, str) and ":" in raw_volume:
                    source = raw_volume.split(":", 1)[0]
                if source in volume_keys:
                    used_volume_keys.add(source)

    def named_top_level(
        kind: str,
        used_keys: set[str],
    ) -> tuple[NamedClaim, ...]:
        result: set[NamedClaim] = set()
        raw_items = config.get(kind) or {}
        if not isinstance(raw_items, Mapping):
            return ()
        for key, raw_item in raw_items.items():
            if str(key) not in used_keys:
                continue
            item = raw_item if isinstance(raw_item, Mapping) else {}
            external = item.get("external")
            if external is True or isinstance(external, Mapping):
                continue
            default_name = f"{project}_{key}"
            name = str(item.get("name") or default_name)
            if name:
                result.add(NamedClaim(key=str(key), name=name))
        return tuple(sorted(result, key=lambda claim: (claim.name, claim.key)))

    return ComposeClaims(
        project=project,
        services=tuple(sorted(active_services)),
        ports=tuple(
            sorted(ports, key=lambda claim: (claim.port, claim.protocol, claim.service))
        ),
        containers=tuple(sorted(containers, key=lambda claim: (claim.name, claim.key))),
        networks=named_top_level("networks", used_network_keys),
        volumes=named_top_level("volumes", used_volume_keys),
    )


def inspect_docker_resources(
    command: Sequence[str],
    *,
    kind: str,
    names: Sequence[str],
) -> dict[str, Mapping[str, Any]]:
    if not names:
        return {}

    list_result = run_command(
        [*command, kind, "ls", "--format", "{{.Name}}"],
    )
    existing_names = {
        line.strip() for line in list_result.stdout.splitlines() if line.strip()
    }
    inspect_names = sorted(set(names) & existing_names)
    resources: dict[str, Mapping[str, Any]] = {}

    for index in range(0, len(inspect_names), 100):
        batch = inspect_names[index : index + 100]
        inspect_result = run_command([*command, kind, "inspect", *batch])
        try:
            payload = json.loads(inspect_result.stdout)
        except json.JSONDecodeError as exc:
            raise ProbeError(
                f"Docker returned invalid {kind} inspect JSON: {exc}"
            ) from exc
        if not isinstance(payload, list):
            raise ProbeError(f"Docker {kind} inspect result is not an array")
        for resource in payload:
            if not isinstance(resource, Mapping):
                continue
            name = str(resource.get("Name") or "")
            if name:
                resources[name] = resource

    missing = set(inspect_names) - set(resources)
    if missing:
        raise ProbeError(
            f"Docker {kind} inspection did not return: {', '.join(sorted(missing))}"
        )
    return resources


def docker_state(
    docker_command: str,
    claims: ComposeClaims,
) -> tuple[
    list[Mapping[str, Any]],
    dict[str, Mapping[str, Any]],
    dict[str, Mapping[str, Any]],
]:
    command = split_command(docker_command, "Docker command")
    ps_result = run_command(command + ["ps", "-aq"])
    container_ids = [
        line.strip() for line in ps_result.stdout.splitlines() if line.strip()
    ]
    containers: list[Mapping[str, Any]] = []
    for index in range(0, len(container_ids), 100):
        inspect_result = run_command(
            command + ["inspect", *container_ids[index : index + 100]]
        )
        try:
            payload = json.loads(inspect_result.stdout)
        except json.JSONDecodeError as exc:
            raise ProbeError(f"Docker returned invalid container JSON: {exc}") from exc
        if not isinstance(payload, list):
            raise ProbeError("Docker container inspect result is not an array")
        containers.extend(item for item in payload if isinstance(item, Mapping))

    networks = inspect_docker_resources(
        command,
        kind="network",
        names=[claim.name for claim in claims.networks],
    )
    volumes = inspect_docker_resources(
        command,
        kind="volume",
        names=[claim.name for claim in claims.volumes],
    )
    return containers, networks, volumes


def labels_for(resource: Mapping[str, Any]) -> Mapping[str, Any]:
    config = resource.get("Config")
    if isinstance(config, Mapping):
        labels = config.get("Labels")
        if isinstance(labels, Mapping):
            return labels
    labels = resource.get("Labels")
    return labels if isinstance(labels, Mapping) else {}


def container_name(container: Mapping[str, Any]) -> str:
    return str(container.get("Name") or "").lstrip("/")


def container_status(container: Mapping[str, Any]) -> str:
    state = container.get("State")
    if isinstance(state, Mapping):
        return str(
            state.get("Status") or ("running" if state.get("Running") else "stopped")
        )
    return "unknown"


def container_is_running(container: Mapping[str, Any]) -> bool:
    state = container.get("State")
    return bool(state.get("Running")) if isinstance(state, Mapping) else False


def container_service(container: Mapping[str, Any]) -> str:
    labels = labels_for(container)
    return str(labels.get("com.docker.compose.service") or "")


def container_owned_by_current_install(
    container: Mapping[str, Any],
    *,
    claims: ComposeClaims,
    allowed_roots: Sequence[str],
    update: bool,
) -> bool:
    if not update:
        return False
    labels = labels_for(container)
    if str(labels.get("com.docker.compose.project") or "") != claims.project:
        return False

    working_dir = str(labels.get("com.docker.compose.project.working_dir") or "")
    normalized_working_dir = normalize_path(working_dir)
    if normalized_working_dir in allowed_roots:
        return True

    config_files = str(labels.get("com.docker.compose.project.config_files") or "")
    for config_file in config_files.split(","):
        candidate = config_file.strip()
        if not candidate:
            continue
        if not os.path.isabs(candidate) and working_dir:
            candidate = os.path.join(working_dir, candidate)
        normalized_parent = normalize_path(os.path.dirname(candidate))
        if normalized_parent in allowed_roots:
            return True
    return False


def container_is_planned(
    container: Mapping[str, Any],
    *,
    claims: ComposeClaims,
    planned_names: Mapping[str, NamedClaim],
) -> bool:
    service = container_service(container)
    if service and service in claims.services:
        return True
    return container_name(container) in planned_names


def container_resource_references(
    container: Mapping[str, Any],
) -> tuple[set[str], set[str]]:
    networks: set[str] = set()
    volumes: set[str] = set()

    network_settings = container.get("NetworkSettings")
    if isinstance(network_settings, Mapping):
        raw_networks = network_settings.get("Networks")
        if isinstance(raw_networks, Mapping):
            networks.update(str(name) for name in raw_networks)

    mounts = container.get("Mounts")
    if isinstance(mounts, Sequence) and not isinstance(mounts, (str, bytes)):
        for mount in mounts:
            if not isinstance(mount, Mapping):
                continue
            if str(mount.get("Type") or "") != "volume":
                continue
            name = str(mount.get("Name") or "")
            if name:
                volumes.add(name)

    return networks, volumes


def host_ip_overlap(planned: str, existing: str) -> bool:
    planned_ip = normalize_host_ip(planned)
    existing_ip = normalize_host_ip(existing)
    wildcards = {"0.0.0.0", "::", "[::]"}
    if planned_ip in wildcards or existing_ip in wildcards:
        return True
    return planned_ip == existing_ip


def existing_binding_covers_planned(existing: str, planned: str) -> bool:
    existing_ip = normalize_host_ip(existing)
    planned_ip = normalize_host_ip(planned)
    if existing_ip == planned_ip:
        return True
    if existing_ip == "0.0.0.0":
        return ":" not in planned_ip
    if existing_ip == "::":
        return ":" in planned_ip
    return False


def find_planned_conflicts(claims: ComposeClaims) -> list[Conflict]:
    conflicts: list[Conflict] = []

    for index, left in enumerate(claims.ports):
        for right in claims.ports[index + 1 :]:
            if left.protocol != right.protocol or left.port != right.port:
                continue
            if not host_ip_overlap(left.host_ip, right.host_ip):
                continue
            services = (
                left.service
                if left.service == right.service
                else f"{left.service} and {right.service}"
            )
            conflicts.append(
                Conflict(
                    kind="planned-port",
                    resource=f"{left.port}/{left.protocol}",
                    service=services,
                    state="planned",
                    message=(
                        f"Planned services {services} both publish "
                        f"{left.port}/{left.protocol} on overlapping host addresses "
                        f"{left.host_ip} and {right.host_ip}."
                    ),
                )
            )

    named_claim_groups = (
        ("planned-container-name", "container", claims.containers),
        ("planned-network-name", "network", claims.networks),
        ("planned-volume-name", "volume", claims.volumes),
    )
    for kind, label, named_claims in named_claim_groups:
        by_name: dict[str, set[str]] = {}
        for claim in named_claims:
            by_name.setdefault(claim.name, set()).add(claim.key)
        for name, keys in by_name.items():
            if len(keys) < 2:
                continue
            key_list = ", ".join(sorted(keys))
            conflicts.append(
                Conflict(
                    kind=kind,
                    resource=name,
                    service=key_list,
                    state="planned",
                    message=(
                        f"Planned {label} name {name} is claimed by multiple "
                        f"Compose keys: {key_list}."
                    ),
                )
            )

    return sorted(
        conflicts,
        key=lambda conflict: (
            conflict.kind,
            conflict.resource,
            conflict.service,
        ),
    )


def container_bindings(
    container: Mapping[str, Any],
) -> Iterable[tuple[str, int, str]]:
    host_config = container.get("HostConfig")
    if not isinstance(host_config, Mapping):
        return ()
    raw_bindings = host_config.get("PortBindings")
    if not isinstance(raw_bindings, Mapping):
        return ()

    bindings: list[tuple[str, int, str]] = []
    for container_port, raw_hosts in raw_bindings.items():
        protocol = "tcp"
        if "/" in str(container_port):
            _, protocol = str(container_port).rsplit("/", 1)
        if not isinstance(raw_hosts, Sequence) or isinstance(raw_hosts, (str, bytes)):
            continue
        for raw_host in raw_hosts:
            if not isinstance(raw_host, Mapping):
                continue
            ports = expand_published_ports(raw_host.get("HostPort"))
            if not ports:
                continue
            host_ip = normalize_host_ip(raw_host.get("HostIp"))
            for port in ports:
                bindings.append((host_ip, port, protocol.lower()))
    return bindings


def resource_owned_by_current_install(
    resource: Mapping[str, Any],
    *,
    claim: NamedClaim,
    claims: ComposeClaims,
    referenced_names: set[str],
    label_key: str,
    update: bool,
) -> bool:
    if not update or claim.name not in referenced_names:
        return False
    labels = labels_for(resource)
    return (
        str(labels.get("com.docker.compose.project") or "") == claims.project
        and str(labels.get(label_key) or "") == claim.key
    )


def find_docker_conflicts(
    *,
    claims: ComposeClaims,
    containers: Sequence[Mapping[str, Any]],
    networks: Mapping[str, Mapping[str, Any]],
    volumes: Mapping[str, Mapping[str, Any]],
    source_dir: str,
    install_dir: str,
    update: bool,
) -> tuple[list[Conflict], set[PortClaim], set[PortClaim]]:
    conflicts: list[Conflict] = []
    foreign_claims: set[PortClaim] = set()
    owned_active_claims: set[PortClaim] = set()
    owned_networks: set[str] = set()
    owned_volumes: set[str] = set()
    allowed_roots = tuple(
        root
        for root in {
            normalize_path(source_dir),
            normalize_path(install_dir),
        }
        if root
    )
    planned_names = {claim.name: claim for claim in claims.containers}

    for container in containers:
        name = container_name(container)
        status = container_status(container)
        owned = container_owned_by_current_install(
            container,
            claims=claims,
            allowed_roots=allowed_roots,
            update=update,
        )
        if owned:
            container_networks, container_volumes = container_resource_references(
                container
            )
            owned_networks.update(container_networks)
            owned_volumes.update(container_volumes)

        if owned and container_is_planned(
            container,
            claims=claims,
            planned_names=planned_names,
        ):
            if container_is_running(container):
                for existing_ip, existing_port, protocol in container_bindings(
                    container
                ):
                    for claim in claims.ports:
                        if (
                            claim.protocol == protocol
                            and claim.port == existing_port
                            and existing_binding_covers_planned(
                                existing_ip,
                                claim.host_ip,
                            )
                        ):
                            owned_active_claims.add(claim)
            continue

        if name in planned_names:
            claim = planned_names[name]
            conflicts.append(
                Conflict(
                    kind="container-name",
                    resource=name,
                    service=claim.key,
                    state=status,
                    message=(
                        f"Container name {name} required by {claim.key} already belongs "
                        f"to a {status} container whose install ownership cannot be verified."
                    ),
                )
            )

        for existing_ip, existing_port, protocol in container_bindings(container):
            for claim in claims.ports:
                if claim.protocol != protocol or claim.port != existing_port:
                    continue
                if not host_ip_overlap(claim.host_ip, existing_ip):
                    continue
                foreign_claims.add(claim)
                if container_is_running(container):
                    description = "publishes"
                else:
                    description = "would reclaim if restarted"
                conflicts.append(
                    Conflict(
                        kind="docker-port",
                        resource=f"{existing_ip}:{existing_port}/{protocol}",
                        service=claim.service,
                        state=status,
                        message=(
                            f"Port {claim.port}/{protocol} required by {claim.service} "
                            f"is configured on {status} container {name or '<unnamed>'} "
                            f"({description} {existing_ip}:{existing_port})."
                        ),
                    )
                )

    network_claims = {claim.name: claim for claim in claims.networks}
    for name, resource in networks.items():
        claim = network_claims[name]
        if resource_owned_by_current_install(
            resource,
            claim=claim,
            claims=claims,
            referenced_names=owned_networks,
            label_key="com.docker.compose.network",
            update=update,
        ):
            continue
        if update:
            detail = (
                "its ownership could not be tied to this install, so Compose "
                "would reuse it"
            )
        else:
            detail = "a new install would reuse it"
        conflicts.append(
            Conflict(
                kind="network-name",
                resource=name,
                service=claim.key,
                message=(
                    f"Docker network {name} already exists; {detail} as the "
                    f"planned {claim.key} network."
                ),
            )
        )

    volume_claims = {claim.name: claim for claim in claims.volumes}
    for name, resource in volumes.items():
        claim = volume_claims[name]
        if resource_owned_by_current_install(
            resource,
            claim=claim,
            claims=claims,
            referenced_names=owned_volumes,
            label_key="com.docker.compose.volume",
            update=update,
        ):
            continue
        if update:
            detail = (
                f"its ownership as the planned {claim.key} volume could not "
                "be tied to this install"
            )
        else:
            detail = f"a new install would adopt it for {claim.key}"
        conflicts.append(
            Conflict(
                kind="volume-name",
                resource=name,
                service=claim.key,
                message=f"Docker volume {name} already exists; {detail}.",
            )
        )

    unique = {
        (conflict.kind, conflict.resource, conflict.service, conflict.state): conflict
        for conflict in conflicts
    }
    ordered = sorted(
        unique.values(),
        key=lambda conflict: (
            conflict.kind,
            conflict.resource,
            conflict.service,
        ),
    )
    return ordered, foreign_claims, owned_active_claims


def socket_listener_present(claim: PortClaim) -> bool:
    if claim.protocol == "tcp":
        socket_type = socket.SOCK_STREAM
    elif claim.protocol == "udp":
        socket_type = socket.SOCK_DGRAM
    else:
        raise ProbeError(f"Unsupported published-port protocol: {claim.protocol}")

    host = normalize_host_ip(claim.host_ip)
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    address: tuple[Any, ...]
    if family == socket.AF_INET6:
        address = (host.strip("[]"), claim.port, 0, 0)
    else:
        address = (host, claim.port)

    probe = socket.socket(family, socket_type)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind(address)
        return False
    except OSError as exc:
        if exc.errno in {errno.EADDRINUSE, errno.EACCES, errno.EPERM}:
            if exc.errno == errno.EADDRINUSE:
                return True
            if claim.protocol == "tcp":
                return connect_probe(claim)
            raise ProbeError(
                f"Permission denied probing {host}:{claim.port}/{claim.protocol}"
            ) from exc
        raise
    finally:
        probe.close()


def connect_probe(claim: PortClaim) -> bool:
    candidates: list[str]
    host_ip = normalize_host_ip(claim.host_ip)
    if host_ip == "0.0.0.0":
        candidates = ["127.0.0.1"]
        try:
            candidates.extend(
                item[4][0]
                for item in socket.getaddrinfo(
                    socket.gethostname(),
                    claim.port,
                    type=socket.SOCK_STREAM,
                )
                if item[0] == socket.AF_INET
            )
        except OSError:
            pass
    elif host_ip == "::":
        candidates = ["::1"]
    else:
        candidates = [host_ip.strip("[]")]

    for host in dict.fromkeys(candidates):
        try:
            with socket.create_connection((host, claim.port), timeout=0.15):
                return True
        except OSError:
            continue
    return False


def find_host_conflicts(
    *,
    claims: ComposeClaims,
    foreign_claims: set[PortClaim],
    owned_active_claims: set[PortClaim],
    listener_fixture: Optional[Mapping[str, Any]] = None,
) -> list[Conflict]:
    conflicts: list[Conflict] = []
    for claim in claims.ports:
        if claim in foreign_claims or claim in owned_active_claims:
            continue
        fixture_key = f"{claim.protocol}:{claim.host_ip}:{claim.port}"
        if listener_fixture is None:
            try:
                present = socket_listener_present(claim)
            except OSError as exc:
                raise ProbeError(
                    f"Could not probe {claim.host_ip}:{claim.port}/{claim.protocol}: {exc}"
                ) from exc
            detail = ""
        else:
            raw = listener_fixture.get(fixture_key, False)
            if isinstance(raw, Mapping):
                present = bool(raw.get("present"))
                detail = str(raw.get("detail") or "")
            else:
                present = bool(raw)
                detail = ""
        if not present:
            continue
        suffix = f" ({detail})" if detail else ""
        conflicts.append(
            Conflict(
                kind="host-port",
                resource=f"{claim.host_ip}:{claim.port}/{claim.protocol}",
                service=claim.service,
                state="listening",
                message=(
                    f"Port {claim.port}/{claim.protocol} required by {claim.service} "
                    f"already has a host listener on {claim.host_ip}{suffix}."
                ),
            )
        )
    return conflicts


def fixture_resource_map(payload: Any, label: str) -> dict[str, Mapping[str, Any]]:
    if payload is None:
        return {}
    if isinstance(payload, Mapping):
        return {
            str(name): resource
            for name, resource in payload.items()
            if isinstance(resource, Mapping)
        }
    if isinstance(payload, list):
        result: dict[str, Mapping[str, Any]] = {}
        for resource in payload:
            if not isinstance(resource, Mapping):
                continue
            name = str(resource.get("Name") or "")
            if name:
                result[name] = resource
        return result
    raise ProbeError(f"{label} fixture must be an object or array")


def write_report(
    path: str,
    *,
    claims: Optional[ComposeClaims],
    conflicts: Sequence[Conflict],
    status: str,
    error: str = "",
) -> None:
    if not path:
        return
    payload = {
        "schema_version": "1",
        "kind": "ods-install-conflicts",
        "status": status,
        "error": error,
        "claims": {
            "project": claims.project if claims else "",
            "services": list(claims.services) if claims else [],
            "ports": [asdict(item) for item in claims.ports] if claims else [],
            "containers": [asdict(item) for item in claims.containers]
            if claims
            else [],
            "networks": [asdict(item) for item in claims.networks] if claims else [],
            "volumes": [asdict(item) for item in claims.volumes] if claims else [],
        },
        "conflicts": [asdict(item) for item in conflicts],
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=f".{target.name}.",
        dir=target.parent,
        text=True,
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            handle.write(json.dumps(payload, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, target)
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary_path)
        except OSError:
            pass
        raise


def record_report(
    path: str,
    *,
    claims: Optional[ComposeClaims],
    conflicts: Sequence[Conflict],
    status: str,
    error: str = "",
) -> str:
    try:
        write_report(
            path,
            claims=claims,
            conflicts=conflicts,
            status=status,
            error=error,
        )
    except Exception as exc:
        return f"Could not write conflict report {path}: {exc}"
    return ""


def print_summary(
    *,
    claims: ComposeClaims,
    conflicts: Sequence[Conflict],
    report_path: str,
) -> None:
    if conflicts:
        print(f"[error] {len(conflicts)} install conflict(s) detected:")
        for conflict in conflicts:
            print(f"  - {conflict.message}")
        print("")
        print("Resolve the conflicting claims before installation.")
        print(
            "Use ODS_ALLOW_CONFLICTS=1 only after you have intentionally "
            "isolated ports and data ownership."
        )
    else:
        print(
            "[  ok ] No install conflicts detected "
            f"({len(claims.ports)} ports, {len(claims.containers)} container names, "
            f"{len(claims.networks)} networks, {len(claims.volumes)} volumes checked)."
        )
    if report_path:
        print(f"[ods] Conflict report: {report_path}")


def main() -> int:
    args = parse_args()
    claims: Optional[ComposeClaims] = None
    conflicts: list[Conflict] = []
    try:
        compose_json = getattr(args, "compose_json", "")
        if compose_json:
            payload = read_json(compose_json)
            if not isinstance(payload, Mapping):
                raise ProbeError("Compose fixture is not an object")
        else:
            payload = render_compose_config(
                source_dir=args.source_dir,
                compose_command=args.compose_command,
                compose_flags=args.compose_flags,
                env_file=args.env_file,
                env_overrides=args.env_override,
            )
        claims = extract_claims(payload)
        conflicts = find_planned_conflicts(claims)

        docker_fixture = getattr(args, "docker_inspect_json", "")
        network_fixture = getattr(args, "network_inspect_json", "")
        volume_fixture = getattr(args, "volume_inspect_json", "")
        if docker_fixture or network_fixture or volume_fixture:
            containers_payload = read_json(docker_fixture) if docker_fixture else []
            if not isinstance(containers_payload, list):
                raise ProbeError("Container fixture is not an array")
            containers = [
                item for item in containers_payload if isinstance(item, Mapping)
            ]
            networks = fixture_resource_map(
                read_json(network_fixture) if network_fixture else {},
                "Network",
            )
            volumes = fixture_resource_map(
                read_json(volume_fixture) if volume_fixture else {},
                "Volume",
            )
        else:
            containers, networks, volumes = docker_state(
                args.docker_command,
                claims,
            )

        docker_conflicts, foreign_claims, owned_active_claims = find_docker_conflicts(
            claims=claims,
            containers=containers,
            networks=networks,
            volumes=volumes,
            source_dir=args.source_dir,
            install_dir=args.install_dir,
            update=args.update,
        )
        listener_fixture_path = getattr(args, "listeners_json", "")
        listener_fixture_payload: Optional[Mapping[str, Any]] = None
        if listener_fixture_path:
            raw_listener_fixture = read_json(listener_fixture_path)
            if not isinstance(raw_listener_fixture, Mapping):
                raise ProbeError("Listener fixture is not an object")
            listener_fixture_payload = raw_listener_fixture
        host_conflicts = find_host_conflicts(
            claims=claims,
            foreign_claims=foreign_claims,
            owned_active_claims=owned_active_claims,
            listener_fixture=listener_fixture_payload,
        )
        conflicts = sorted(
            [*conflicts, *docker_conflicts, *host_conflicts],
            key=lambda conflict: (
                conflict.kind,
                conflict.resource,
                conflict.service,
            ),
        )
        status = "conflict" if conflicts else "clear"
        report_error = record_report(
            args.report,
            claims=claims,
            conflicts=conflicts,
            status=status,
        )
        if report_error:
            raise ProbeError(report_error)
        if not args.quiet:
            print_summary(
                claims=claims,
                conflicts=conflicts,
                report_path=args.report,
            )
        return EXIT_CONFLICT if conflicts else 0
    except ProbeError as exc:
        message = str(exc)
        report_error = record_report(
            args.report,
            claims=claims,
            conflicts=conflicts,
            status="probe-error",
            error=message,
        )
        if not args.quiet:
            print(f"[error] Install conflict detection could not complete: {message}")
            if args.report:
                if report_error:
                    print(f"[error] {report_error}")
                else:
                    print(f"[ods] Conflict report: {args.report}")
        return EXIT_PROBE_ERROR
    except Exception as exc:
        message = f"Internal detector error ({type(exc).__name__})"
        report_error = record_report(
            args.report,
            claims=claims,
            conflicts=conflicts,
            status="probe-error",
            error=message,
        )
        if not args.quiet:
            print(f"[error] Install conflict detection could not complete: {message}")
            if args.report:
                if report_error:
                    print(f"[error] {report_error}")
                else:
                    print(f"[ods] Conflict report: {args.report}")
        return EXIT_PROBE_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
