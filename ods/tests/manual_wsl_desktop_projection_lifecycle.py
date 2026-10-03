#!/usr/bin/env python3
"""Observe Docker Desktop's WSL bind projection lifecycle without touching ODS.

Run manually after external fleet host-lock admission on a WSL Docker Desktop
host with an already-local image containing /bin/sh and sleep. This creates
one disposable bind-only container. It never pulls an image, changes an ODS
container, or unmounts. The local lock below only serializes this experiment;
it does not replace the fleet host lock held on the coordinator.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "installers/lib"))
from wsl_pixel_mount_plan import PROXY_PREFIX, parse_mountinfo  # noqa: E402


LOCK = Path("/tmp/ods-wsl-projection-probe.lock")


def docker(*args: str) -> str:
    result = subprocess.run(
        ["docker", *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=45,
    )
    return result.stdout.strip()


def snapshot(source: Path) -> dict[str, object]:
    identity = source.stat()
    probe_root = "/" + source.relative_to("/mnt/wsl").as_posix()
    rows = parse_mountinfo(Path("/proc/self/mountinfo").read_text(encoding="utf-8"))
    proxies = [row for row in rows if row.target.startswith(PROXY_PREFIX)]
    if len(proxies) > 128:
        raise RuntimeError("Docker Desktop proxy graph is too large for this probe")
    selected = []
    for row in rows:
        if row.target != str(source) and not row.target.startswith(PROXY_PREFIX):
            continue
        try:
            candidate = os.lstat(row.target)
        except OSError:
            candidate = None
        identity_matches = candidate is not None and (
            candidate.st_dev,
            candidate.st_ino,
        ) == (
            identity.st_dev,
            identity.st_ino,
        )
        if not identity_matches and row.root != probe_root:
            continue
        selected.append(
            {
                "id": row.mount_id,
                "root": row.root,
                "target": row.target,
                "device": row.device,
                "optional": list(row.optional),
                "identity_matches": identity_matches,
            }
        )
    return {
        "matched": selected,
        "all_proxy_rows": [
            {
                "id": row.mount_id,
                "root": row.root,
                "target": row.target,
                "device": row.device,
            }
            for row in proxies
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image", required=True, help="already-local image with /bin/sh"
    )
    parser.add_argument("--acknowledge-host-admission", action="store_true")
    args = parser.parse_args()
    if not args.acknowledge_host_admission:
        parser.error("fleet coordinator host-lock admission is required")
    if "microsoft" not in Path("/proc/sys/kernel/osrelease").read_text().lower():
        parser.error("this experiment requires WSL")
    if os.environ.get("DOCKER_HOST", "").startswith(("tcp://", "ssh://")):
        parser.error("remote Docker endpoints are outside this experiment")
    if (
        docker("context", "inspect", "--format", "{{.Endpoints.docker.Host}}")[:7]
        != "unix://"
    ):
        parser.error("Docker context must use a local Unix socket")
    if docker("info", "--format", "{{.OperatingSystem}}") != "Docker Desktop":
        parser.error("selected Docker engine is not Docker Desktop")
    image_id = docker("image", "inspect", "--format", "{{.Id}}", args.image)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        parser.error("the selected image did not resolve to a local image ID")
    try:
        image_volumes = json.loads(
            docker("image", "inspect", "--format", "{{json .Config.Volumes}}", image_id)
        )
    except ValueError:
        parser.error("the selected image has unreadable volume declarations")
    if image_volumes not in (None, {}):
        parser.error("the selected image declares volumes outside this bind-only probe")

    lock = os.open(LOCK, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(lock)
        raise RuntimeError("another local projection experiment is active") from exc

    record: dict[str, object] = {
        "image": args.image,
        "image_id": image_id,
        "stages": {},
        "errors": [],
    }
    container_id = ""
    name = "ods-wsl-projection-probe-" + uuid.uuid4().hex[:12]
    nonce = uuid.uuid4().hex
    parent: Path | None = None
    try:
        parent = Path(tempfile.mkdtemp(prefix="ods-projection-probe-", dir="/mnt/wsl"))
        parent.chmod(0o755)
        source = parent / "socket-source"
        source.mkdir(mode=0o755)
        record["container"] = name
        record["source"] = str(source)
        record["stages"]["before_create"] = snapshot(source)
        if record["stages"]["before_create"]["matched"]:
            raise RuntimeError("disposable source already has a mount projection")

        container_id = docker(
            "create",
            "--name",
            name,
            "--network",
            "none",
            "--restart",
            "no",
            "--label",
            f"ods.wsl.projection.probe={nonce}",
            "--mount",
            f"type=bind,source={source},target=/probe,readonly",
            "--entrypoint",
            "/bin/sh",
            image_id,
            "-c",
            "sleep 600",
        )
        if not re.fullmatch(r"[0-9a-f]{64}", container_id):
            raise RuntimeError("Docker create did not return a full container ID")
        inspected = json.loads(docker("inspect", container_id))
        if (
            not isinstance(inspected, list)
            or len(inspected) != 1
            or not isinstance(inspected[0], dict)
        ):
            raise RuntimeError("disposable container inspect is malformed")
        created = inspected[0]
        host_config = created.get("HostConfig")
        declared = host_config.get("Mounts") if isinstance(host_config, dict) else None
        actual = created.get("Mounts")
        if (
            not isinstance(declared, list)
            or len(declared) != 1
            or not isinstance(declared[0], dict)
            or declared[0].get("Type") != "bind"
            or declared[0].get("Source") != str(source)
            or declared[0].get("Target") != "/probe"
            or declared[0].get("ReadOnly") is not True
            or not isinstance(actual, list)
            or len(actual) != 1
            or not isinstance(actual[0], dict)
            or actual[0].get("Type") != "bind"
            or actual[0].get("Destination") != "/probe"
            or actual[0].get("RW") is not False
        ):
            raise RuntimeError("disposable container has unexpected mounts")
        record["stages"]["after_create"] = snapshot(source)
        docker("start", container_id)
        if docker("inspect", "--format", "{{.State.Running}}", container_id) != "true":
            raise RuntimeError("disposable container did not remain running")
        record["stages"]["after_start"] = snapshot(source)
        before_proxy_targets = {
            row["target"] for row in record["stages"]["before_create"]["all_proxy_rows"]
        }
        started_proxy_targets = {
            row["target"] for row in record["stages"]["after_start"]["all_proxy_rows"]
        }
        if (
            started_proxy_targets == before_proxy_targets
            and not record["stages"]["after_start"]["matched"]
        ):
            raise RuntimeError("no projection was observed; lifecycle is inconclusive")
        docker("stop", "--time", "5", container_id)
        record["stages"]["after_stop"] = snapshot(source)
        # A retained-install rollback may need to restart an owned container
        # after a partial transition. Observe that lifecycle separately from
        # first start; Desktop can retain or recreate a different proxy view.
        docker("start", container_id)
        if docker("inspect", "--format", "{{.State.Running}}", container_id) != "true":
            raise RuntimeError(
                "disposable container did not remain running after restart"
            )
        record["stages"]["after_restart"] = snapshot(source)
        docker("stop", "--time", "5", container_id)
        record["stages"]["after_second_stop"] = snapshot(source)
        docker("rm", container_id)  # No -v: never remove any volume.
        container_id = ""
        record["stages"]["after_remove"] = snapshot(source)
        if record["stages"]["after_remove"]["matched"]:
            raise RuntimeError(
                "Docker Desktop left a projection after container removal"
            )
        after_proxy_targets = {
            row["target"] for row in record["stages"]["after_remove"]["all_proxy_rows"]
        }
        if after_proxy_targets != before_proxy_targets:
            raise RuntimeError("Docker Desktop proxy graph changed outside the probe")
    except (
        OSError,
        RuntimeError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as exc:
        record["errors"].append(str(exc))
    finally:
        try:
            inspected = json.loads(docker("inspect", name))[0]
        except subprocess.CalledProcessError:
            inspected = None
        except (ValueError, IndexError, subprocess.TimeoutExpired) as exc:
            inspected = None
            record["errors"].append(f"disposable container inspect failed: {exc}")
        if inspected is not None:
            if (
                inspected.get("Config", {})
                .get("Labels", {})
                .get("ods.wsl.projection.probe")
                != nonce
            ):
                record["errors"].append(
                    "disposable name resolved to a foreign container"
                )
            else:
                try:
                    docker("rm", "-f", inspected["Id"])
                except (
                    subprocess.CalledProcessError,
                    subprocess.TimeoutExpired,
                ) as exc:
                    record["errors"].append(
                        f"disposable container cleanup failed: {exc}"
                    )
        if parent is not None:
            source = parent / "socket-source"
            if source.exists():
                try:
                    record["stages"]["after_cleanup"] = snapshot(source)
                except (OSError, RuntimeError, ValueError) as exc:
                    record["errors"].append(f"final mount snapshot failed: {exc}")
            try:
                source.rmdir()
                parent.rmdir()
            except OSError as exc:
                record["errors"].append(f"disposable directory cleanup failed: {exc}")
        fcntl.flock(lock, fcntl.LOCK_UN)
        os.close(lock)
    print(json.dumps(record, sort_keys=True))
    return 1 if record["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
