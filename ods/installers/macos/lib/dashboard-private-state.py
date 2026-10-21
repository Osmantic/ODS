#!/usr/bin/env python3
"""Migrate the two Mac dashboard SQLite stores off the shared host bind.

First migration requires stopped API writers. Existing completed volumes are
never recopied from the retained legacy host directories. This helper also
exports the image store at the existing backup path and participates in the
backup utility's all-selected-path restore transaction.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import uuid

HERE = Path(__file__).resolve().parent
RECIPE = "installers/macos/dashboard-private-state.compose.yaml"
STATE = "data/.dashboard-private-stores"
PROJECT = "com.docker.compose.project"
SERVICE = "com.docker.compose.service"
TOKEN = "io.ods.private-store.token"
INSTALL = "io.ods.private-store.install"
spec = importlib.util.spec_from_file_location("private_store_worker", HERE / "dashboard-private-state-worker.py")
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


def flags_for(root, value):
    flags = shlex.split(value)
    if not flags or len(flags) % 2:
        raise ValueError("private-store-compose-selection")
    for option, relative in zip(flags[::2], flags[1::2]):
        path = root / relative
        if option != "-f" or not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValueError("private-store-compose-selection")
    return flags


def selected_flags(root, value):
    flags = flags_for(root, value)
    if RECIPE not in flags[1::2]:
        # Keep explicit user overrides last; resolved configuration validation
        # below refuses a conflicting private-store mount rather than ignoring it.
        flags[2:2] = ["-f", RECIPE]
    if not (root / RECIPE).is_file():
        raise ValueError("private-store-recipe-missing")
    return shlex.join(flags)


def write_record(path, value):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        worker.sync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def read_record(path):
    worker.private(path)
    return json.loads(path.read_text())


def legacy_identity(path, kind):
    if not os.path.lexists(path):
        return None
    database = worker.check_files(path, kind)
    entries = {}
    for suffix in ("", "-wal", "-shm", "-journal"):
        candidate = Path(str(database) + suffix)
        if os.path.lexists(candidate):
            info = worker.private(candidate)
            entries[suffix] = {"device": info.st_dev, "inode": info.st_ino,
                               "bytes": info.st_size, "sha256": worker.digest(candidate)}
    return entries


@contextlib.contextmanager
def locked(root):
    directory = root / STATE
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    worker.private(directory, directory=True)
    lock_path = directory / "lock"
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        worker.private(lock_path)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield directory
    finally:
        os.close(fd)


def selection(document):
    project = document.get("name", "")
    if not re.fullmatch("[a-z0-9][a-z0-9_-]*", project):
        raise ValueError("private-store-project")
    service = document.get("services", {}).get("dashboard-api", {})
    if service.get("user", "1000") not in ("1000", "1000:1000", "odser"):
        raise ValueError("private-store-api-user")
    volumes = {}
    for kind in worker.STORES:
        key = "dashboard-" + kind
        definition = document.get("volumes", {}).get(key, {})
        name = project + "_" + key
        if (set(definition) - {"name"} or definition.get("name", name) != name):
            raise ValueError("private-store-volume-definition")
        mounts = [item for item in service.get("volumes", [])
                  if item.get("target") == "/data/" + kind]
        if (len(mounts) != 1 or mounts[0].get("type") != "volume"
                or mounts[0].get("source") != key or mounts[0].get("read_only", False)
                or mounts[0].get("volume", {}) != {"nocopy": True}):
            raise ValueError("private-store-mount-definition")
        volumes[kind] = name
    # The broad shared bind remains the authority for the legacy source.
    data = [item for item in service.get("volumes", []) if item.get("target") == "/data"]
    if len(data) != 1 or data[0].get("type") != "bind":
        raise ValueError("private-store-data-definition")
    return project, volumes, service.get("image") or project + "-dashboard-api", data[0]["source"]


def needs_stores(document, selected):
    if selected is None:
        return True
    remaining, seen = [selected], set()
    services = document.get("services", {})
    while remaining:
        name = remaining.pop()
        if name in seen:
            continue
        if name == "dashboard-api":
            return True
        seen.add(name)
        if name not in services:
            raise ValueError("private-store-selected-service-missing")
        service = services[name]
        remaining.extend(service.get("depends_on") or [])
        for key in ("network_mode", "pid", "ipc"):
            if str(service.get(key, "")).startswith("service:"):
                remaining.append(service[key].split(":", 1)[1])
        for key in ("links", "volumes_from"):
            remaining.extend(value.split(":", 1)[0] for value in service.get(key, [])
                             if not value.startswith("container:"))
    return False


class Stores:
    def __init__(self, root, flags, *, build=False, frozen=None, service=None, native=None):
        self.root = root
        self.env = dict(os.environ)
        self.binary = self.env["ODS_NATIVE_LIFECYCLE_DOCKER"] if native else "docker"
        self.compose_prefix = (["--project-directory", str(root), "--project-name", native["project"],
                                "--env-file", "/dev/null"] if native else [])
        self.install = hashlib.sha256(str(root).encode()).hexdigest()
        if frozen:
            if frozen["install"] != self.install:
                raise ValueError("private-store-restore-install-changed")
            endpoint = frozen["endpoint"]
        else:
            endpoint = self.env.get("DOCKER_HOST")
            if not endpoint:
                context = self.env.get("DOCKER_CONTEXT") or self.docker("context", "show").strip()
                endpoint = json.loads(self.docker("context", "inspect", context))[0]["Endpoints"]["docker"]["Host"]
        if not endpoint.startswith("unix:///"):
            raise ValueError("private-store-local-docker-required")
        self.endpoint = endpoint
        self.env.pop("DOCKER_CONTEXT", None)
        self.env["DOCKER_HOST"] = endpoint
        self.daemon = self.docker("info", "--format", "{{.ID}}").strip()
        if not self.daemon:
            raise ValueError("private-store-daemon-identity")
        if frozen:
            if frozen["daemon"] != self.daemon:
                raise ValueError("private-store-restore-daemon-changed")
            self.project, self.volumes, self.image = frozen["project"], frozen["volumes"], frozen["image"]
            return
        self.flags = flags_for(root, flags)
        config = json.loads(self.docker("compose", *self.flags, "config", "--format", "json"))
        self.requires_stores = needs_stores(config, service)
        self.project, self.volumes, image, source = selection(config)
        if Path(source) != root / "data":
            raise ValueError("private-store-legacy-source-selection")
        if not self.requires_stores:
            return
        # Pin the resolved image ID for every transfer worker. Ordinary start
        # and update may build a missing local API image; restart never builds.
        found = self.docker("image", "inspect", image, missing=True)
        if found is None and build:
            self.docker("compose", *self.flags, "build", "dashboard-api")
            found = self.docker("image", "inspect", image)
        if found is None:
            raise ValueError("private-store-api-image-missing: run ods start to build it")
        self.image = json.loads(found)[0]["Id"]
        if not re.fullmatch("sha256:[a-f0-9]{64}", self.image):
            raise ValueError("private-store-api-image-identity")

    def docker(self, *args, missing=False):
        command = [self.binary, *args]
        if args[0] == "compose":
            command[2:2] = self.compose_prefix
        result = subprocess.run(command, cwd=self.root, env=self.env, capture_output=True, check=False)
        if result.returncode:
            # Do not log expanded Compose/environment output.
            if missing and len(args) == 3:
                if args[:2] == ("volume", "inspect"):
                    absent = ("Error response from daemon: get " + args[2] + ": no such volume").encode()
                    if (result.returncode == 1 and result.stdout.strip() == b"[]"
                            and result.stderr.strip() == absent):
                        return None
                elif args[:2] == ("image", "inspect") and b"No such" in result.stderr:
                    return None
            raise ValueError("private-store-docker-" + args[0])
        return result.stdout.decode()

    def consumers(self, *, stopped=False):
        identities = set()
        for name in self.volumes.values():
            identities.update(self.docker("ps", "-a", "-q", "--no-trunc", "--filter", "volume=" + name).split())
        identities.update(self.docker("ps", "-a", "-q", "--no-trunc", "--filter",
                                     "label=" + PROJECT + "=" + self.project,
                                     "--filter", "label=" + SERVICE + "=dashboard-api").split())
        for identity in identities:
            row = json.loads(self.docker("inspect", identity))[0]
            labels = row.get("Config", {}).get("Labels") or {}
            if (labels.get(PROJECT) != self.project or labels.get(SERVICE) != "dashboard-api"
                    or labels.get("com.docker.compose.project.working_dir") != str(self.root)):
                raise ValueError("private-store-foreign-consumer")
            if stopped and (row.get("State", {}).get("Running") or row.get("State", {}).get("Restarting")):
                raise ValueError("Private dashboard migration/restore needs stopped writers. Run ods stop, then ods start for migration; retry restore after stopping.")

    def record(self, directory, kind):
        path = directory / (kind + ".json")
        record = read_record(path) if os.path.lexists(path) else None
        if record is not None and (record.get("kind") != kind or record.get("install") != self.install
                or record.get("volume") != self.volumes[kind]
                or record.get("daemon") != self.daemon
                or not re.fullmatch("[a-f0-9]{32}", record.get("token", ""))):
            raise ValueError("private-store-receipt-selection")
        return path, record

    def volume(self, kind, record):
        raw = self.docker("volume", "inspect", self.volumes[kind], missing=True)
        if raw is None:
            return None
        row = json.loads(raw)[0]
        labels = row.get("Labels") or {}
        if (record is None or row.get("Driver") != "local" or row.get("Options")
                or labels.get(PROJECT) != self.project
                or labels.get("com.docker.compose.volume") != "dashboard-" + kind
                or labels.get(TOKEN) != record["token"] or labels.get(INSTALL) != self.install):
            raise ValueError("private-store-unrecognized-volume; retain it and the migration receipt")
        return row

    def transfer(self, kind, action, token, *, payload=None, output=None, expected=""):
        arguments = ["run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
                     "--security-opt", "no-new-privileges:true", "--tmpfs", "/tmp:rw,nosuid,nodev",
                     "--mount", "type=volume,src=" + self.volumes[kind] + ",dst=/store,volume-nocopy",
                     "--user", "0:0" if action == "init" else "1000:1000"]
        if action == "init":
            arguments.extend(["--cap-add", "CHOWN", "--cap-add", "FOWNER", "--cap-add", "DAC_OVERRIDE"])
        arguments.extend(["--entrypoint", "python3", "-i", self.image, "-c",
                          (HERE / "dashboard-private-state-worker.py").read_text(), action, kind, token, expected])
        with contextlib.ExitStack() as stack:
            incoming = stack.enter_context(payload.open("rb")) if payload else subprocess.DEVNULL
            outgoing = stack.enter_context(output.open("xb")) if output else subprocess.DEVNULL
            if output:
                os.chmod(output, 0o600)
            result = subprocess.run([self.binary, *arguments], cwd=self.root, stdin=incoming,
                                    stdout=outgoing, stderr=subprocess.PIPE, env=self.env, check=False)
        if result.returncode:
            raise ValueError("private-store-transfer-" + action + "; staging retained, retry the same operation after checking stopped writers")

    def prepare(self, directory):
        if (directory / "image-restore-active.json").exists():
            raise ValueError("private-store-restore-unfinished; finish or roll back the retained image restore before starting")
        self.consumers()
        for kind in worker.STORES:
            path, record = self.record(directory, kind)
            volume = self.volume(kind, record)
            if record and record.get("complete"):
                if volume is None:
                    raise ValueError("private-store-completed-volume-missing; restore from backup, not stale legacy data")
                self.transfer(kind, "check", record["token"])
                continue
            self.consumers(stopped=True)
            source = self.root / "data" / kind
            if record and record.get("initialized") and volume is not None:
                try:
                    self.transfer(kind, "check", record["token"])
                except ValueError:
                    pass
                else:
                    # The volume marker was published, but the final host
                    # receipt may have been interrupted. Never replay a copy.
                    record["complete"] = True
                    write_record(path, record)
                    continue
            if record is None:
                record = {"kind": kind, "volume": self.volumes[kind], "install": self.install,
                          "token": uuid.uuid4().hex, "complete": False, "daemon": self.daemon}
                snapshot = directory / (kind + "-" + record["token"] + ".sqlite3")
                if source.exists() or source.is_symlink():
                    record["sha256"] = worker.snapshot(source, kind, snapshot)
                else:
                    empty = directory / (kind + "-" + record["token"])
                    empty.mkdir(mode=0o700)
                    record["sha256"] = worker.snapshot(empty, kind, snapshot)
                    empty.rmdir()
                record["legacy"] = legacy_identity(source, kind)
                write_record(path, record)
            elif legacy_identity(source, kind) != record["legacy"]:
                raise ValueError("private-store-legacy-changed-after-snapshot; keep writers stopped and reconcile retained migration before retrying")
            snapshot = directory / (kind + "-" + record["token"] + ".sqlite3")
            worker.private(snapshot)
            if worker.digest(snapshot) != record["sha256"]:
                raise ValueError("private-store-migration-snapshot-changed")
            if volume is None:
                self.docker("volume", "create", "--label", PROJECT + "=" + self.project,
                            "--label", "com.docker.compose.volume=dashboard-" + kind,
                            "--label", TOKEN + "=" + record["token"], "--label", INSTALL + "=" + self.install,
                            self.volumes[kind])
                self.volume(kind, record)
                self.transfer(kind, "init", record["token"])
            elif not record.get("initialized"):
                # A completed import can outlive its host receipt write. Check
                # it before considering the sole permitted empty-volume init.
                try:
                    self.transfer(kind, "check", record["token"])
                except ValueError:
                    self.transfer(kind, "init", record["token"])
            record["initialized"] = True
            write_record(path, record)
            self.transfer(kind, "import", record["token"], payload=snapshot, expected=record["sha256"])
            self.transfer(kind, "check", record["token"])
            record["complete"] = True
            write_record(path, record)

    def ready(self, directory):
        _, record = self.record(directory, "pixel-images")
        if not record or not record.get("complete") or self.volume("pixel-images", record) is None:
            raise ValueError("private-image-store-not-prepared; run ods start after stopping legacy writers")
        self.consumers()
        self.transfer("pixel-images", "check", record["token"])
        return record

    def export(self, directory, destination):
        record = self.ready(directory)
        destination.mkdir(mode=0o700, parents=True, exist_ok=True)
        worker.private(destination, directory=True)
        self.transfer("pixel-images", "export", record["token"], output=destination / worker.STORES["pixel-images"])

    def restore(self, directory, action, transaction, source=None):
        self.ready(directory)
        self.consumers(stopped=True)
        worker.private(transaction, directory=True)
        path = transaction / "private-image-restore.json"
        active = directory / "image-restore-active.json"
        if action == "stage":
            if active.exists():
                raise ValueError("private-store-restore-unfinished; recover the retained restore first")
            snapshot = transaction / "private-images.sqlite3"
            sha = worker.snapshot(source, "pixel-images", snapshot)
            record = {"token": uuid.uuid4().hex, "sha256": sha, "volume": self.volumes["pixel-images"],
                      "install": self.install, "binding": {"install": self.install, "endpoint": self.endpoint,
                      "daemon": self.daemon, "project": self.project, "volumes": self.volumes, "image": self.image}}
            write_record(path, record)
            write_record(active, {"path": str(transaction), "token": record["token"]})
        else:
            record = read_record(path)
            snapshot = transaction / "private-images.sqlite3"
            if read_record(active) != {"path": str(transaction), "token": record["token"]}:
                raise ValueError("private-store-restore-custody-changed")
        if record["volume"] != self.volumes["pixel-images"] or record["install"] != self.install:
            raise ValueError("private-store-restore-selection-changed")
        self.transfer("pixel-images", action, record["token"],
                      payload=snapshot if action == "stage" else None, expected=record["sha256"])
        if action in {"commit", "rollback"}:
            active.unlink()
            worker.sync_dir(directory)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("flags", "prepare", "launch", "export", "stage", "publish", "rollback", "commit"))
    parser.add_argument("--install-dir", type=Path, required=True)
    parser.add_argument("--flags")
    parser.add_argument("--build-missing", action="store_true")
    parser.add_argument("--path", type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--operation", choices=("start", "restart", "update", "install"))
    parser.add_argument("--service")
    parser.add_argument("--native-lifecycle", action="store_true")
    args = parser.parse_args()
    if args.service is not None and not re.fullmatch("[A-Za-z0-9][A-Za-z0-9_.-]*", args.service):
        raise ValueError("private-store-service-name")
    root = args.install_dir.absolute()
    if root.resolve() != root or not root.is_dir():
        raise ValueError("private-store-install-path")
    frozen = None
    if args.action in {"publish", "rollback", "commit"}:
        frozen = read_record(args.path / "private-image-restore.json")["binding"]
    flags = "" if frozen else args.flags if args.flags is not None else (root / ".compose-flags").read_text().strip()
    if args.action == "flags":
        print(selected_flags(root, flags))
        return
    native = None
    lifecycle = None
    if args.native_lifecycle:
        if args.action != "launch" or args.operation not in {"start", "restart", "update"}:
            raise ValueError("private-store-native-operation")
        spec = importlib.util.spec_from_file_location("private_native_lifecycle", HERE / "pixel-native-lifecycle.py")
        lifecycle = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lifecycle)
        native = lifecycle.verify_child(root, args.operation, args.service or "")
        expected = ["-f", str(Path(native["path"]).relative_to(root))]
        if shlex.split(flags) != expected:
            raise ValueError("private-store-native-frozen-flags")
    if frozen is None and native is None:
        # Older installed caches lack this new recipe. All callers, including
        # backup/restore, must resolve the same selection as the Mac CLI.
        flags = selected_flags(root, flags)
    with locked(root) as directory:
        stores = Stores(root, flags, build=not native and (args.build_missing or args.operation in {"start", "update"}),
                        frozen=frozen, service=args.service if args.action == "launch" else None, native=native)
        if args.action in {"prepare", "launch"}:
            if stores.requires_stores:
                stores.prepare(directory)
            if args.action == "launch":
                options = {"start": [], "update": ["--force-recreate"],
                           "restart": ["--force-recreate", "--no-build", "--pull", "never"],
                           "install": ["--remove-orphans", "--no-build", "--pull", "never"]}
                if args.operation not in options:
                    raise ValueError("private-store-launch-operation")
                if native is not None:
                    # Parent already prepared and pinned images under admission.
                    # Verify again after storage preparation; replay only its
                    # exact recipe, Docker binary, project and empty env file.
                    if lifecycle.verify_child(root, args.operation, args.service or "") != native:
                        raise ValueError("private-store-native-selection-changed")
                    options[args.operation] = ["--wait", "--wait-timeout", "120", "--no-build", "--pull", "never"]
                    if args.operation != "start":
                        options[args.operation].append("--force-recreate")
                # Keep the lock through Compose. A concurrent restore cannot
                # pass its stopped-writer check between prepare and API start.
                result = subprocess.run([stores.binary, "compose", *stores.compose_prefix, *stores.flags, "up", "-d",
                                         *options[args.operation], *([args.service] if args.service else [])],
                                        cwd=root, env=stores.env, check=False)
                if result.returncode:
                    raise ValueError("private-store-compose-start-failed")
        elif args.action == "export":
            stores.export(directory, args.path.absolute())
        else:
            stores.restore(directory, args.action, args.path.absolute(), args.source.absolute() if args.source else None)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError) as error:
        # Only our fixed categories are public. OS/JSON/SQLite errors can name
        # private files; never forward those strings into installer logs.
        message = str(error) if type(error) is ValueError and str(error).startswith(("private-", "Private dashboard")) else "private-store-operation-refused"
        print(message, file=sys.stderr)
        sys.exit(1)
