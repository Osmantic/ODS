#!/usr/bin/env python3
"""Fail-closed, cross-process selection guard for ``ods enable/disable``.

This helper owns the final dependency check and Compose-file rename. For CLI
disable it also stops the service under that lock, so a dependent cannot be
enabled between the stop and the rename.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised by Windows CI
    fcntl = None

try:
    import msvcrt
except ImportError:  # pragma: no cover - exercised by POSIX CI
    msvcrt = None

MAX_YAML_BYTES = 1024 * 1024
SERVICE_ID = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")
SELECTION_HISTORY = ".extensions-ever-selected.json"


class SelectionError(Exception):
    """A selection cannot be committed without risking the installed stack."""


def _remember_selected(install_dir: Path, service_ids: set[str]) -> None:
    """Keep prior user selection when a Compose marker is turned off.

    Called under the graph-wide selection lock while these services are still
    selected. The receipt records only IDs, never credentials or service data.
    An interrupted disable may leave an extra remembered ID, but that ID was
    selected at the time of the write, so it is still truthful history.
    """
    if not service_ids:
        return
    if any(SERVICE_ID.fullmatch(service_id) is None for service_id in service_ids):
        raise SelectionError("Invalid extension selection history ID")
    directory = install_dir / "data"
    try:
        info = directory.lstat()
    except OSError as exc:
        raise SelectionError("Cannot inspect extension selection history directory") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise SelectionError("Invalid extension selection history directory")
    path = directory / SELECTION_HISTORY
    try:
        selected = path.lstat()
    except FileNotFoundError:
        raw = None
    except OSError as exc:
        raise SelectionError("Cannot inspect extension selection history") from exc
    else:
        if not stat.S_ISREG(selected.st_mode):
            raise SelectionError("Invalid extension selection history")
        raw = _read_bounded_file(path)
    if raw is not None:
        try:
            document = json.loads(raw)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise SelectionError("Invalid extension selection history") from exc
        old = document.get("ever_selected") if isinstance(document, dict) and document.get("schema_version") == 1 else None
        if (not isinstance(old, list) or len(old) > 4096
                or any(not isinstance(item, str) or SERVICE_ID.fullmatch(item) is None for item in old)):
            raise SelectionError("Invalid extension selection history")
        remembered = set(old)
    else:
        remembered = set()
    if service_ids <= remembered:
        return
    remembered.update(service_ids)
    payload = json.dumps({"schema_version": 1, "ever_selected": sorted(remembered)}, separators=(",", ":")) + "\n"
    if len(payload) > MAX_YAML_BYTES:
        raise SelectionError("Extension selection history is too large")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         prefix=".extensions-ever-selected-", dir=directory,
                                         delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.chmod(temporary, 0o644)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise SelectionError("Could not save extension selection history; selection unchanged") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _remember_selected_best_effort(install_dir: Path, service_ids: set[str]) -> None:
    """Selection history is UI metadata and must not veto a safe disable."""
    try:
        _remember_selected(install_dir, service_ids)
    except SelectionError:
        # Preserve an unreadable/corrupt receipt for diagnosis. The catalog
        # must present missing history as unknown rather than infer absence.
        print("WARNING: Could not record prior Library selection history", file=sys.stderr)


def _read_bounded_file(path: Path) -> bytes:
    """Read a bounded regular file without following its final symlink."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            selected = os.fstat(stream.fileno())
            if not stat.S_ISREG(selected.st_mode) or selected.st_size > MAX_YAML_BYTES:
                raise SelectionError(f"Invalid selected file: {path}")
            raw = stream.read(MAX_YAML_BYTES + 1)
        if len(raw) > MAX_YAML_BYTES:
            raise SelectionError(f"Selected file is too large: {path}")
        return raw
    except OSError as exc:
        raise SelectionError(f"Cannot inspect selected file: {path}") from exc


def _assert_open_webui_selected(install_dir: Path, service_id: str) -> None:
    """A selected extension cannot depend on a profiled-out base WebUI."""
    try:
        lines = _read_bounded_file(install_dir / ".env").decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise SelectionError("Installed WebUI selection is unreadable") from exc
    selections = []
    for line in lines:
        key, separator, value = line.partition("=")
        if not separator or key.strip() != "ENABLE_OPEN_WEBUI":
            continue
        normalized = value.strip()
        if len(normalized) >= 2 and normalized[0] in {'"', "'"} and normalized[-1] == normalized[0]:
            normalized = normalized[1:-1]
        normalized = normalized.lower()
        if normalized not in {"true", "false"}:
            raise SelectionError("Installed WebUI selection is invalid")
        selections.append(normalized)
    if len(selections) > 1:
        raise SelectionError("Installed WebUI selection is ambiguous")
    # Legacy installs without the key use the base Compose default (true).
    if selections == ["false"]:
        raise SelectionError(f"Cannot enable {service_id}; Open WebUI is disabled. Add it from the Library first")


def _read_yaml(path: Path, *, compose: bool = False) -> object:
    try:
        import yaml
    except ImportError as exc:
        raise SelectionError("PyYAML is required to inspect extension dependencies") from exc
    try:
        raw = _read_bounded_file(path).decode("utf-8")
        if not compose:
            return yaml.safe_load(raw)

        class _ComposeSelectionLoader(yaml.SafeLoader):
            pass

        def construct_override(loader, node):
            # Docker Compose's !override changes overlay merging, not the
            # contents needed for this fragment's dependency/service scan.
            if isinstance(node, yaml.SequenceNode):
                return loader.construct_sequence(node, deep=True)
            if isinstance(node, yaml.MappingNode):
                return loader.construct_mapping(node, deep=True)
            if isinstance(node, yaml.ScalarNode):
                return loader.construct_scalar(node)
            raise yaml.constructor.ConstructorError(None, None, "invalid !override node", node.start_mark)

        _ComposeSelectionLoader.add_constructor("!override", construct_override)
        return yaml.load(raw, Loader=_ComposeSelectionLoader)  # noqa: S506 - SafeLoader subclass
    except (UnicodeError, yaml.YAMLError) as exc:
        raise SelectionError(f"Cannot inspect selected file: {path}") from exc


def _read_json(path: Path) -> object:
    try:
        return json.loads(_read_bounded_file(path).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SelectionError(f"Cannot inspect selected file: {path}") from exc


def _assert_remote_route_allows_disable(
    install_dir: Path, service_id: str, data_dir: Path | None = None,
) -> None:
    """Keep an active remote route's required services selected.

    This check runs under the shared extensions lock for both Dashboard and CLI
    operations. A configured route is disabled through remote-provider setup,
    not by stopping its transport underneath LiteLLM.
    """
    if service_id not in {"remote-provider-egress", "remote-provider-ssh-tunnel"}:
        return
    default_data_dir = install_dir / "data"
    if data_dir is None:
        configured = os.environ.get("ODS_DATA_DIR")
        data_dir = Path(configured) if configured else default_data_dir
    if not default_data_dir.is_absolute() or not data_dir.is_absolute():
        raise SelectionError("Invalid remote-provider data directory")
    # Inspect the default root as well as a configured external root. A stale
    # process environment must not hide an active route in the installed root.
    route_roots = ((default_data_dir,) if data_dir == default_data_dir
                   else (default_data_dir, data_dir))
    for root in route_roots:
        route_dir = root / "remote-provider"
        try:
            directory = route_dir.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise SelectionError("Cannot inspect remote-provider route directory") from exc
        if not stat.S_ISDIR(directory.st_mode):
            raise SelectionError("Invalid remote-provider route directory")
        route_path = route_dir / "routing-state.json"
        try:
            selected = route_path.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise SelectionError("Cannot inspect remote-provider route state") from exc
        if not stat.S_ISREG(selected.st_mode) or selected.st_nlink != 1:
            raise SelectionError("Invalid remote-provider route state")
        route = _read_json(route_path)
        if (not isinstance(route, dict)
                or route.get("schema") != "ods.remote-routing-state.v1"
                or type(route.get("enabled")) is not bool):
            raise SelectionError("Invalid remote-provider route state")
        if not route["enabled"]:
            continue
        provider = route.get("provider")
        transport = provider.get("transport") if isinstance(provider, dict) else None
        if transport not in {"direct", "ssh"}:
            raise SelectionError("Invalid remote-provider route transport")
        if service_id == "remote-provider-egress" or transport == "ssh":
            raise SelectionError(
                f"Active {transport} remote-provider route requires {service_id}; "
                "disable the route first"
            )


def _manifest_dependencies(directory: Path) -> set[str]:
    for name in ("manifest.yaml", "manifest.yml", "manifest.json"):
        manifest = directory / name
        try:
            selected = manifest.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise SelectionError(f"Cannot inspect dependency manifest: {manifest}") from exc
        if not stat.S_ISREG(selected.st_mode):
            raise SelectionError(f"Invalid dependency manifest: {manifest}")
        document = _read_json(manifest) if manifest.suffix == ".json" else _read_yaml(manifest)
        if not isinstance(document, dict) or not isinstance(document.get("service"), dict):
            raise SelectionError(f"Invalid dependency manifest: {manifest}")
        dependencies = document["service"].get("depends_on", [])
        if not isinstance(dependencies, list) or any(
            not isinstance(dep, str) or SERVICE_ID.fullmatch(dep) is None
            for dep in dependencies
        ):
            raise SelectionError(f"Invalid dependencies in manifest: {manifest}")
        return set(dependencies)
    return set()


def _compose_details(path: Path) -> tuple[set[str], set[str]]:
    document = _read_yaml(path, compose=True)
    services = document.get("services") if isinstance(document, dict) else None
    if not isinstance(services, dict):
        raise SelectionError(f"Invalid selected Compose services: {path}")
    service_names = set(services)
    if any(not isinstance(name, str) or not name for name in service_names):
        raise SelectionError(f"Invalid selected Compose service name: {path}")
    dependencies: set[str] = set()
    for definition in services.values():
        if not isinstance(definition, dict):
            raise SelectionError(f"Invalid selected Compose service: {path}")
        declared = definition.get("depends_on", [])
        if isinstance(declared, list):
            names = declared
        elif isinstance(declared, dict) and all(isinstance(value, dict) for value in declared.values()):
            names = declared.keys()
        else:
            raise SelectionError(f"Invalid selected Compose dependencies: {path}")
        for name in names:
            if not isinstance(name, str) or not name or "$" in name:
                raise SelectionError(f"Invalid selected Compose dependency: {path}")
            dependencies.add(name)
    return service_names, dependencies


def _compose_dependencies(path: Path) -> set[str]:
    return _compose_details(path)[1]


def _enabled_dependents(install_dir: Path, service_id: str) -> list[str]:
    """Inspect every enabled fragment the Compose resolver can select."""
    dependents: list[str] = []
    roots = (
        install_dir / "data" / "user-extensions",
        install_dir / "extensions" / "services",
    )
    for root in roots:
        try:
            root_stat = root.lstat()
            if not stat.S_ISDIR(root_stat.st_mode):
                raise SelectionError(f"Invalid extension root: {root}")
            peers = sorted(root.iterdir())
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise SelectionError(f"Cannot inspect extension root: {root}") from exc
        for peer in peers:
            if peer.name == service_id:
                continue
            try:
                peer_stat = peer.lstat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise SelectionError(f"Cannot inspect extension peer: {peer}") from exc
            if stat.S_ISLNK(peer_stat.st_mode):
                raise SelectionError(f"Invalid extension peer: {peer}")
            if not stat.S_ISDIR(peer_stat.st_mode):
                continue
            compose = peer / "compose.yaml"
            try:
                compose_stat = compose.lstat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise SelectionError(f"Cannot inspect selected Compose file: {compose}") from exc
            if not stat.S_ISREG(compose_stat.st_mode):
                raise SelectionError(f"Invalid selected Compose file: {compose}")
            dependencies = _manifest_dependencies(peer)
            dependencies.update(_compose_dependencies(compose))
            if service_id in dependencies and peer.name not in dependents:
                dependents.append(peer.name)
    return dependents


def _find_target_dir(install_dir: Path, service_id: str) -> Path | None:
    for root in (install_dir / "data" / "user-extensions", install_dir / "extensions" / "services"):
        directory = root / service_id
        try:
            selected = directory.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise SelectionError(f"Cannot inspect extension: {service_id}") from exc
        if not stat.S_ISDIR(selected.st_mode):
            raise SelectionError(f"Invalid extension directory: {service_id}")
        return directory
    return None


def _target_dir(install_dir: Path, service_id: str) -> Path:
    directory = _find_target_dir(install_dir, service_id)
    if directory is not None:
        return directory
    raise SelectionError(f"Unknown extension: {service_id}")


@contextlib.contextmanager
def _selection_lock(install_dir: Path, timeout: float):
    if fcntl is None and msvcrt is None:
        raise SelectionError("Extension selection requires a file-lock runtime")
    lock_path = install_dir / "data" / ".extensions-lock"
    try:
        descriptor = os.open(
            lock_path,
            os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
            0o600,
        )
    except OSError as exc:
        raise SelectionError(f"Cannot open shared extensions lock: {lock_path}") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise SelectionError(f"Invalid shared extensions lock: {lock_path}")
        if msvcrt is not None:
            # Match Dashboard's one-byte Windows lock for local processes.
            # Docker Desktop bind-mount interoperability needs live acceptance.
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
        deadline = time.monotonic() + timeout
        while True:
            try:
                if fcntl is not None:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                break
            except (BlockingIOError, PermissionError) as exc:
                if time.monotonic() >= deadline:
                    raise SelectionError(f"Timed out waiting for extensions lock: {lock_path}") from exc
                time.sleep(0.05)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            else:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    finally:
        os.close(descriptor)


def _assert_no_dependents(install_dir: Path, service_id: str) -> None:
    dependents = _enabled_dependents(install_dir, service_id)
    if dependents:
        raise SelectionError(
            f"These enabled extensions depend on {service_id}: {', '.join(dependents)}. "
            "Disable them first."
        )


def _selection_enabled(directory: Path) -> bool:
    enabled = directory / "compose.yaml"
    disabled = directory / "compose.yaml.disabled"
    try:
        enabled_stat = enabled.lstat()
    except FileNotFoundError:
        enabled_stat = None
    try:
        disabled_stat = disabled.lstat()
    except FileNotFoundError:
        disabled_stat = None
    if enabled_stat is not None and not stat.S_ISREG(enabled_stat.st_mode):
        raise SelectionError(f"Invalid selected Compose file: {enabled}")
    if disabled_stat is not None and not stat.S_ISREG(disabled_stat.st_mode):
        raise SelectionError(f"Invalid disabled Compose file: {disabled}")
    if enabled_stat is not None and disabled_stat is not None:
        raise SelectionError(f"Conflicting Compose selection files: {directory.name}")
    return enabled_stat is not None


def _assert_prerequisites_enabled(
    install_dir: Path, service_id: str, directory: Path, compose_path: Path,
    core_services: set[str],
) -> None:
    manifest_deps = _manifest_dependencies(directory)
    fragment_services, compose_deps = _compose_details(compose_path)
    missing: list[str] = []
    for dep in sorted(manifest_deps | (compose_deps - fragment_services)):
        if dep == service_id:
            raise SelectionError(f"Circular dependency for {service_id}")
        if dep in fragment_services:
            continue
        # Core services can be profiled out in gateway-only mode; their
        # manifest category is a CLI policy. A user shadow must not inherit it.
        if dep in core_services:
            try:
                (install_dir / "data" / "user-extensions" / dep).lstat()
            except FileNotFoundError:
                if dep == "open-webui":
                    _assert_open_webui_selected(install_dir, service_id)
                continue
        dep_dir = _find_target_dir(install_dir, dep)
        if dep_dir is None:
            # Compose can refer to a base service outside the extension tree;
            # the Compose resolver validates that graph. Manifest deps name
            # extensions and must resolve here.
            if dep in manifest_deps:
                missing.append(dep)
            continue
        if not _selection_enabled(dep_dir):
            missing.append(dep)
    if missing:
        raise SelectionError(
            f"Cannot enable {service_id}; disabled prerequisites: {', '.join(missing)}"
        )


def _refresh_compose_flags(
    install_dir: Path, tier: str, gpu_backend: str, gpu_count: str, ods_mode: str,
) -> None:
    """Rebuild the persisted stack while the shared selection lock is held."""
    cache = install_dir / ".compose-flags"
    try:
        cache.unlink(missing_ok=True)
    except OSError as exc:
        print(f"WARNING: Cannot invalidate Compose cache: {cache}: {exc}", file=sys.stderr)
        return
    resolver = install_dir / "scripts" / "resolve-compose-stack.sh"
    if not resolver.is_file() or not os.access(resolver, os.X_OK):
        return
    bash = shutil.which("bash")
    if bash is None:
        print("WARNING: Could not regenerate the compose stack cache: bash is missing", file=sys.stderr)
        return
    try:
        result = subprocess.run(
            [bash, str(resolver), "--script-dir", str(install_dir),
             "--tier", tier, "--gpu-backend", gpu_backend,
             "--gpu-count", gpu_count, "--ods-mode", ods_mode],
            cwd=install_dir, capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"WARNING: Could not regenerate the compose stack cache: {exc}", file=sys.stderr)
        return
    if result.returncode != 0 or not result.stdout.strip().startswith("-f "):
        print("WARNING: Could not regenerate the compose stack cache; 'ods start' may fail until this is resolved.", file=sys.stderr)
        if result.stderr.strip():
            print(result.stderr.rstrip(), file=sys.stderr)
        return
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", prefix=".compose-flags-",
            dir=install_dir, delete=False,
        ) as stream:
            temporary = stream.name
            stream.write(result.stdout)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, cache)
    except OSError as exc:
        print(f"WARNING: Cannot save Compose cache: {cache}: {exc}", file=sys.stderr)
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError as exc:
                print(f"WARNING: Cannot remove temporary Compose cache: {exc}", file=sys.stderr)
    for line in result.stderr.splitlines():
        if line.startswith("WARNING:"):
            print(line, file=sys.stderr)


def _stop_for_disable(
    install_dir: Path, service_id: str, mode: str, compose_flags: str,
    service_names: set[str] | None = None,
    preserve_restart_policy: bool = False,
) -> None:
    if mode == "compose":
        try:
            flags = shlex.split(compose_flags)
        except ValueError as exc:
            raise SelectionError("Invalid Compose flags; selection unchanged") from exc
        if (not flags or len(flags) % 2 or any(flag != "-f" for flag in flags[::2])
                or any(not path or path.startswith("-") for path in flags[1::2])):
            raise SelectionError("Invalid Compose flags; selection unchanged")
        command = ["docker", "compose", *flags, "stop", service_id]
    elif mode == "owned":
        names = sorted(service_names) if service_names is not None else [service_id]
        # An unfiltered owned-stop request stops every ODS container. Never
        # turn a fragment with no services into an installation-wide stop.
        if not names:
            return
        if any(SERVICE_ID.fullmatch(name) is None for name in names):
            raise SelectionError(f"Invalid Compose service for {service_id}; selection unchanged")
        helper = install_dir / "scripts" / "stop-owned-containers.py"
        if not helper.is_file():
            raise SelectionError(f"Owned-container stop helper missing: {helper}")
        command = [sys.executable, str(helper), "--install-dir", str(install_dir)]
        if preserve_restart_policy:
            command.append("--preserve-restart-policy")
        for name in names:
            command.extend(("--service", name))
    else:
        raise SelectionError("Invalid stop mode; selection unchanged")
    try:
        stopped = subprocess.run(
            command, cwd=install_dir, capture_output=True, text=True,
            timeout=360, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SelectionError(f"Could not confirm stop for {service_id}; selection unchanged") from exc
    if stopped.returncode != 0:
        raise SelectionError(
            f"Could not confirm stop for {service_id}; selection unchanged: "
            f"{stopped.stderr.strip()[:500]}"
        )


def run(
    action: str, install_dir: Path, service_id: str, timeout: float = 15.0,
    core_services: set[str] | None = None,
    tier: str = "1", gpu_backend: str = "nvidia", gpu_count: str = "1",
    ods_mode: str = "local",
    stop_mode: str | None = None, compose_flags: str = "",
    data_dir: Path | None = None,
) -> str:
    if action not in ("check-disable", "disable", "enable"):
        raise SelectionError("Invalid selection action")
    if SERVICE_ID.fullmatch(service_id) is None:
        raise SelectionError("Invalid service id")
    if timeout <= 0 or timeout > 120:
        raise SelectionError("Invalid lock timeout")
    if stop_mode is not None and action != "disable":
        raise SelectionError("Container stop is only supported for disable")
    if compose_flags and stop_mode != "compose":
        raise SelectionError("Compose flags require Compose stop mode")
    core_services = set(core_services or ())
    if any(SERVICE_ID.fullmatch(core) is None for core in core_services):
        raise SelectionError("Invalid core service id")
    with _selection_lock(install_dir, timeout):
        directory = _target_dir(install_dir, service_id)
        if action != "enable":
            _assert_no_dependents(install_dir, service_id)
            _assert_remote_route_allows_disable(install_dir, service_id, data_dir)
        enabled = directory / "compose.yaml"
        disabled = directory / "compose.yaml.disabled"
        try:
            enabled_stat = enabled.lstat()
        except FileNotFoundError:
            enabled_stat = None
        try:
            disabled_stat = disabled.lstat()
        except FileNotFoundError:
            disabled_stat = None
        if enabled_stat is not None and not stat.S_ISREG(enabled_stat.st_mode):
            raise SelectionError(f"Invalid selected Compose file: {enabled}")
        if disabled_stat is not None and not stat.S_ISREG(disabled_stat.st_mode):
            raise SelectionError(f"Invalid disabled Compose file: {disabled}")
        if enabled_stat is not None and disabled_stat is not None:
            if action != "enable" or _read_bounded_file(enabled) != _read_bounded_file(disabled):
                raise SelectionError(f"Conflicting Compose selection files for {service_id}")
            _assert_prerequisites_enabled(install_dir, service_id, directory, enabled,
                                          core_services)
            try:
                disabled.unlink()
            except OSError as exc:
                raise SelectionError(f"Could not remove stale disabled marker for {service_id}") from exc
            disabled_stat = None
        cache = install_dir / ".compose-flags"
        if action == "enable":
            if enabled_stat is not None:
                _assert_prerequisites_enabled(install_dir, service_id, directory, enabled,
                                              core_services)
                _refresh_compose_flags(install_dir, tier, gpu_backend, gpu_count, ods_mode)
                return "already-enabled"
            if disabled_stat is None:
                raise SelectionError(f"No Compose selection file for {service_id}")
            _assert_prerequisites_enabled(install_dir, service_id, directory, disabled,
                                          core_services)
            try:
                cache.unlink(missing_ok=True)
                os.replace(disabled, enabled)
            except OSError as exc:
                raise SelectionError(f"Could not enable {service_id}; disabled file remains recoverable") from exc
            _refresh_compose_flags(install_dir, tier, gpu_backend, gpu_count, ods_mode)
            return "enabled"
        if enabled_stat is None:
            if disabled_stat is not None:
                return "already-disabled"
            raise SelectionError(f"No Compose selection file for {service_id}")
        if action == "check-disable":
            return "ready"
        _remember_selected_best_effort(install_dir, {service_id})
        if stop_mode is not None:
            _stop_for_disable(install_dir, service_id, stop_mode, compose_flags)
        try:
            cache.unlink(missing_ok=True)
            os.replace(enabled, disabled)
        except OSError as exc:
            if stop_mode == "compose":
                try:
                    restarted = subprocess.run(
                        ["docker", "compose", *shlex.split(compose_flags), "start", service_id],
                        cwd=install_dir, capture_output=True, text=True, timeout=360,
                        check=False,
                    )
                    if restarted.returncode != 0:
                        print(f"WARNING: {service_id} remains selected but could not be "
                              "restarted; run 'ods start' to recover.", file=sys.stderr)
                except (OSError, subprocess.TimeoutExpired):
                    print(f"WARNING: {service_id} remains selected but could not be "
                          "restarted; run 'ods start' to recover.", file=sys.stderr)
            elif stop_mode == "owned":
                print(f"WARNING: {service_id} remains selected but was stopped during recovery; "
                      "repair Compose validation, then run 'ods start' to recover.",
                      file=sys.stderr)
            raise SelectionError(f"Could not disable {service_id}; selected file remains recoverable") from exc
        return "disabled"


def _preset_entries(path: Path) -> dict[str, bool]:
    try:
        selected = path.lstat()
    except OSError as exc:
        raise SelectionError(f"Cannot inspect preset extensions list: {path}") from exc
    if not stat.S_ISREG(selected.st_mode):
        raise SelectionError(f"Invalid preset extensions list: {path}")
    try:
        raw = _read_bounded_file(path).decode("utf-8")
    except UnicodeError as exc:
        raise SelectionError("Preset extensions list is not UTF-8") from exc
    entries: dict[str, bool] = {}
    for number, line in enumerate(raw.splitlines(), 1):
        if not line:
            continue
        state, separator, service_id = line.partition(":")
        if (not separator or state not in ("enabled", "disabled")
                or SERVICE_ID.fullmatch(service_id) is None):
            raise SelectionError(f"Invalid preset extension entry on line {number}")
        enabled = state == "enabled"
        if service_id in entries and entries[service_id] != enabled:
            raise SelectionError(f"Conflicting preset states for {service_id}")
        entries[service_id] = enabled
    return entries


def _extension_directories(install_dir: Path) -> dict[str, Path]:
    directories: dict[str, Path] = {}
    for root in (install_dir / "data" / "user-extensions",
                 install_dir / "extensions" / "services"):
        try:
            root_stat = root.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(root_stat.st_mode):
            raise SelectionError(f"Invalid extension root: {root}")
        try:
            peers = sorted(root.iterdir())
        except OSError as exc:
            raise SelectionError(f"Cannot inspect extension root: {root}") from exc
        for peer in peers:
            if peer.name in directories or SERVICE_ID.fullmatch(peer.name) is None:
                continue
            try:
                peer_stat = peer.lstat()
            except OSError as exc:
                raise SelectionError(f"Cannot inspect extension peer: {peer}") from exc
            if stat.S_ISLNK(peer_stat.st_mode):
                raise SelectionError(f"Invalid extension peer: {peer}")
            if stat.S_ISDIR(peer_stat.st_mode):
                directories[peer.name] = peer
    return directories


def _preset_dependencies(
    install_dir: Path, service_id: str, directory: Path,
    compose_path: Path, directories: dict[str, Path], core_services: set[str],
    *, require_dependencies: bool,
) -> set[str]:
    manifest_deps = _manifest_dependencies(directory)
    fragment_services, compose_deps = _compose_details(compose_path)
    dependencies: set[str] = set()
    for dep in manifest_deps | (compose_deps - fragment_services):
        if dep == service_id:
            raise SelectionError(f"Circular dependency for {service_id}")
        if dep in fragment_services:
            continue
        if dep in core_services:
            try:
                (install_dir / "data" / "user-extensions" / dep).lstat()
            except FileNotFoundError:
                if dep == "open-webui" and require_dependencies:
                    _assert_open_webui_selected(install_dir, service_id)
                continue
        if dep in directories:
            dependencies.add(dep)
        elif dep in manifest_deps:
            raise SelectionError(f"Missing prerequisite {dep} for {service_id}")
    return dependencies


def _preset_graph(
    install_dir: Path, directories: dict[str, Path], current: dict[str, bool],
    states: dict[str, bool], core_services: set[str], *, require_dependencies: bool,
) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    for service_id, enabled in states.items():
        if not enabled:
            continue
        directory = directories[service_id]
        # A marker rename preserves file contents. Read the *current* source
        # file even when the requested state is different.
        compose_path = directory / ("compose.yaml" if current[service_id]
                                    else "compose.yaml.disabled")
        if not compose_path.is_file() or compose_path.is_symlink():
            raise SelectionError(f"Missing selected Compose file for {service_id}")
        dependencies = _preset_dependencies(
            install_dir, service_id, directory, compose_path, directories, core_services,
            require_dependencies=require_dependencies,
        )
        if require_dependencies:
            missing = sorted(dep for dep in dependencies if not states.get(dep, False))
            if missing:
                raise SelectionError(
                    f"Preset would leave {service_id} without prerequisites: "
                    f"{', '.join(missing)}"
                )
        graph[service_id] = dependencies
    return graph


def _dependency_order(graph: dict[str, set[str]]) -> list[str]:
    order: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(service_id: str) -> None:
        if service_id in visiting:
            raise SelectionError(f"Circular preset dependencies involving {service_id}")
        if service_id in visited:
            return
        visiting.add(service_id)
        for dep in sorted(graph[service_id]):
            if dep in graph:
                visit(dep)
        visiting.remove(service_id)
        visited.add(service_id)
        order.append(service_id)

    for service_id in sorted(graph):
        visit(service_id)
    return order


def _image_providers(path: Path) -> set[str]:
    """Find services whose selected fragment supplies the runnable image."""
    document = _read_yaml(path, compose=True)
    definitions = document.get("services") if isinstance(document, dict) else None
    if not isinstance(definitions, dict):
        raise SelectionError(f"Invalid selected Compose services: {path}")
    return {name for name, definition in definitions.items()
            if isinstance(definition, dict) and ("image" in definition or "build" in definition)}


def _base_compose_services(
    install_dir: Path, compose_flags: str,
) -> tuple[set[str], set[str], dict[str, set[str]], dict[str, set[str]]]:
    """Reserve base services and attribute selected extension overlays."""
    try:
        flags = shlex.split(compose_flags)
    except ValueError as exc:
        raise SelectionError("Invalid current Compose flags") from exc
    # The native Windows installer saves this explicit environment file before
    # its Compose overlays.  Keep the exception exact: accepting arbitrary
    # Compose options here would make the base-service custody check ambiguous.
    root = install_dir.resolve(strict=True)
    if flags[:1] == ["--env-file"]:
        # Inspect the saved spelling too: shlex turns `.\\env` into `.env`,
        # while Docker would receive a different path.
        if (len(flags) < 2 or flags[1] != ".env"
                or re.match(r"\A--env-file[ \t]+\.env[ \t]+", compose_flags) is None):
            raise SelectionError("Invalid current Compose flags")
        env_file = root / ".env"
        if env_file.is_symlink() or not env_file.is_file():
            raise SelectionError("Invalid current Compose environment file")
        flags = flags[2:]
    if (not flags or len(flags) % 2
            or any(flag != "-f" for flag in flags[::2])):
        raise SelectionError("Invalid current Compose flags")
    services: set[str] = set()
    providers: set[str] = set()
    extension_overlays: dict[str, set[str]] = {}
    extension_providers: dict[str, set[str]] = {}
    selected_base = False
    for name in flags[1::2]:
        path = Path(name)
        if not path.is_absolute():
            path = root / path
        if path.is_symlink() or not path.is_file():
            raise SelectionError(f"Invalid selected Compose file: {path}")
        path = path.resolve(strict=True)
        if not path.is_relative_to(root):
            raise SelectionError(f"Selected Compose file is outside the install: {path}")
        parts = path.relative_to(root).parts
        if parts[:2] == ("extensions", "services") or parts[:2] == ("data", "user-extensions"):
            # A service such as ComfyUI has an empty compose.yaml marker and
            # declares its container only in a selected GPU overlay. Attribute
            # that service to its extension for disable/stop accounting.
            if len(parts) == 4 and path.name != "compose.yaml" and SERVICE_ID.fullmatch(parts[2]):
                names, _ = _compose_details(path)
                extension_overlays.setdefault(parts[2], set()).update(names)
                extension_providers.setdefault(parts[2], set()).update(_image_providers(path))
            continue
        selected_base = True
        fragment_services, _ = _compose_details(path)
        services.update(fragment_services)
        providers.update(_image_providers(path))
    if not selected_base:
        raise SelectionError("Current Compose flags contain no base overlay")
    return services, services - providers, extension_overlays, extension_providers


def restore_preset(
    install_dir: Path, preset_file: Path, timeout: float = 15.0,
    core_services: set[str] | None = None,
    compose_flags: str | None = None,
    strict: bool = False,
    expected_sha256: dict[str, str] | None = None,
    data_dir: Path | None = None,
) -> tuple[int, int, list[str]]:
    """Restore markers from a valid selection, keeping dependencies valid per move."""
    if timeout <= 0 or timeout > 120:
        raise SelectionError("Invalid lock timeout")
    core_services = set(core_services or ())
    if any(SERVICE_ID.fullmatch(service_id) is None for service_id in core_services):
        raise SelectionError("Invalid core service id")
    entries = _preset_entries(preset_file)
    if expected_sha256 is not None and (
        not isinstance(expected_sha256, dict)
        or set(expected_sha256) != set(entries)
        or any(not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None
               for value in expected_sha256.values())
        or any(not enabled for enabled in entries.values())
    ):
        raise SelectionError("Invalid expected Compose digests")
    if any(service_id in core_services for service_id in entries):
        raise SelectionError("Presets cannot change core services")
    with _selection_lock(install_dir, timeout):
        directories = _extension_directories(install_dir)
        skipped = sorted(service_id for service_id in entries if service_id not in directories)
        if strict and skipped:
            raise SelectionError(
                f"Extension is unavailable: {', '.join(skipped)}; selection unchanged"
            )
        current: dict[str, bool] = {}
        for service_id, directory in directories.items():
            current[service_id] = _selection_enabled(directory)
        if expected_sha256 is not None:
            # Dashboard policy validation happened before the host RPC. Bind
            # that result to the exact selected bytes under this same lock,
            # including services that a concurrent CLI already enabled.
            for service_id, expected in expected_sha256.items():
                directory = directories.get(service_id)
                if directory is None:
                    raise SelectionError(f"Extension is unavailable: {service_id}")
                selected = directory / (
                    "compose.yaml" if current[service_id] else "compose.yaml.disabled"
                )
                actual = hashlib.sha256(_read_bounded_file(selected)).hexdigest()
                if actual != expected:
                    raise SelectionError(
                        f"Compose content changed since validation: {service_id}"
                    )
        for service_id in entries:
            if service_id not in directories:
                continue
            directory = directories[service_id]
            if not ((directory / "compose.yaml").exists()
                    or (directory / "compose.yaml.disabled").exists()):
                raise SelectionError(f"No Compose selection file for {service_id}")
        desired = current.copy()
        desired.update({service_id: enabled for service_id, enabled in entries.items()
                        if service_id in directories})
        current_graph = _preset_graph(
            install_dir, directories, current, current, core_services,
            # Observe a legacy or manually changed broken selection so an
            # enable preset can repair it. The desired graph still has to be
            # valid before any marker is moved.
            require_dependencies=False,
        )
        desired_graph = _preset_graph(
            install_dir, directories, current, desired, core_services,
            require_dependencies=True,
        )
        # Compose overlays can contribute to an existing service (Langfuse
        # also declares litellm). Stopping that shared service when only the
        # extension is removed would interrupt Core.
        if compose_flags is None:
            raise SelectionError("Preset restore requires current Compose flags")
        (base_services, base_needs_provider, extension_overlays,
         extension_providers) = _base_compose_services(install_dir, compose_flags)
        selected_fragments = {
            service_id: _compose_details(directories[service_id] / "compose.yaml")[0]
            for service_id in current_graph
        }
        selected_providers = {
            service_id: _image_providers(directories[service_id] / "compose.yaml")
            for service_id in current_graph
        }
        for service_id in current_graph:
            selected_fragments[service_id].update(extension_overlays.get(service_id, set()))
            selected_providers[service_id].update(extension_providers.get(service_id, set()))
        disable_order = [service_id for service_id in reversed(_dependency_order(current_graph))
                         if current[service_id] and not desired[service_id]]
        enable_order = [service_id for service_id in _dependency_order(desired_graph)
                        if not current[service_id] and desired[service_id]]
        # A selected base overlay can augment a service whose runnable image
        # lives in an extension (the external LiteLLM overlays do this). Check
        # the whole requested selection before stopping or moving any marker.
        # A newly enabled provider is not counted: disable-first ordering would
        # otherwise leave an invalid intermediate stack.
        retained_providers = set().union(*(
            providers for service_id, providers in selected_providers.items()
            if desired[service_id]
        ))
        for service_id in disable_order:
            _assert_remote_route_allows_disable(install_dir, service_id, data_dir)
            missing = (selected_providers[service_id] & base_needs_provider) - retained_providers
            if missing:
                raise SelectionError(
                    f"Selected base overlay requires {', '.join(sorted(missing))}; "
                    "selection unchanged"
                )
        enabled_count = 0
        disabled_count = 0
        cache = install_dir / ".compose-flags"
        if cache.is_dir():
            raise SelectionError(f"Compose cache is a directory: {cache}")
        operations = [(service_id, False) for service_id in disable_order]
        operations.extend((service_id, True) for service_id in enable_order)
        _remember_selected_best_effort(install_dir, set(disable_order))
        if not operations:
            try:
                cache.unlink(missing_ok=True)
            except OSError as exc:
                raise SelectionError("Cannot invalidate Compose cache; selection unchanged") from exc
        for service_id, enable in operations:
            directory = directories[service_id]
            source = directory / ("compose.yaml.disabled" if enable else "compose.yaml")
            target = directory / ("compose.yaml" if enable else "compose.yaml.disabled")
            try:
                if not enable:
                    shared_services = base_services.copy()
                    for other_id, names in selected_fragments.items():
                        if other_id != service_id:
                            shared_services.update(names)
                    exclusive_services = selected_fragments[service_id] - shared_services
                    _stop_for_disable(
                        install_dir, service_id, "owned", "", exclusive_services,
                        preserve_restart_policy=True,
                    )
                cache.unlink(missing_ok=True)
                os.replace(source, target)
            except SelectionError as exc:
                raise SelectionError(
                    f"Preset restore stopped after {enabled_count} enabled and "
                    f"{disabled_count} disabled; {service_id} marker was not changed: {exc}"
                ) from exc
            except OSError as exc:
                raise SelectionError(
                    f"Preset restore stopped after {enabled_count} enabled and "
                    f"{disabled_count} disabled; {service_id} marker was not changed "
                    f"(its container may have stopped): {exc}"
                ) from exc
            if enable:
                enabled_count += 1
            else:
                selected_fragments.pop(service_id)
                selected_providers.pop(service_id)
                disabled_count += 1
        return enabled_count, disabled_count, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check-disable", "disable", "enable", "restore-preset"))
    parser.add_argument("--install-dir", type=Path, required=True)
    parser.add_argument("--service-id")
    parser.add_argument("--preset-file", type=Path)
    parser.add_argument("--lock-timeout", type=float, default=15.0)
    parser.add_argument("--core-service", action="append", default=[])
    parser.add_argument("--tier", default="1")
    parser.add_argument("--gpu-backend", default="nvidia")
    parser.add_argument("--gpu-count", default="1")
    parser.add_argument("--ods-mode", default="local")
    parser.add_argument("--stop-mode", choices=("compose", "owned"))
    parser.add_argument("--compose-flags", default="")
    args = parser.parse_args()
    try:
        if args.action == "restore-preset":
            if args.preset_file is None or args.service_id is not None:
                raise SelectionError("Preset restore requires only --preset-file")
            enabled, disabled, skipped = restore_preset(
                args.install_dir, args.preset_file, args.lock_timeout,
                set(args.core_service), args.compose_flags or None,
            )
            for service_id in skipped:
                print(f"WARNING: Preset skipped unavailable extension {service_id}",
                      file=sys.stderr)
            print(f"{enabled} {disabled}")
            return 0
        if args.service_id is None or args.preset_file is not None:
            raise SelectionError("Service selection requires only --service-id")
        print(run(args.action, args.install_dir, args.service_id, args.lock_timeout,
                  set(args.core_service), args.tier, args.gpu_backend,
                  args.gpu_count, args.ods_mode, args.stop_mode,
                  args.compose_flags))
    except SelectionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
