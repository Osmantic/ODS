"""Build the fixed preview inspector and bind deployment to its immutable image ID.

This installer performs no browser inspection. Runtime authority is confined to
the separately installed broker and the image ID in its protected configuration.
"""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import pwd
import py_compile
import re
import stat
import subprocess
import sys
import tempfile

BUILD_FILES = (
    "Dockerfile.inspection",
    "preview-inspection.requirements.lock",
    "preview_inspection_protocol.py",
    "preview_inspection_capsule.py",
)
RUNTIME_FILES = (
    "preview_inspection.py",
    "preview_inspection_protocol.py",
    "workspace_preview.py",
    "unix_peer.py",
)
PROGRAM_ROOT = Path("/usr/local/libexec/ods-pixel-inspection")
CONFIG = Path("/etc/ods-pixel-inspection.json")
UNIT = Path("/etc/systemd/system/pixel-preview-inspection.service")
IMAGE_PATTERN = r"sha256:[a-f0-9]{64}"


def source_bytes(path, owner_uid=None):
    path = Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_mode & 0o022
            or before.st_uid not in (0, os.getuid(), owner_uid)
            or not 0 < before.st_size <= 2 * 1024 * 1024
        ):
            raise ValueError("unsafe-inspection-source")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            body = stream.read(2 * 1024 * 1024 + 1)
        after = os.fstat(fd)
        current = path.lstat()
        signature = lambda value: (
            value.st_dev,
            value.st_ino,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )
        if (
            signature(before) != signature(after)
            or signature(after) != signature(current)
            or len(body) != before.st_size
        ):
            raise ValueError("inspection-source-changed")
        return body
    finally:
        os.close(fd)


def docker_path(transport):
    path = Path(
        "/Applications/Docker.app/Contents/Resources/bin/docker"
        if transport == "docker-desktop"
        else "/usr/bin/docker"
    )
    resolved = path.resolve(strict=True)
    info = resolved.stat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_mode & 0o022
        or not info.st_mode & 0o111
    ):
        raise ValueError("unsafe-inspection-docker")
    return str(path)


def validate_image(image, image_id):
    architecture = {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(
        platform.machine()
    )
    if (
        not re.fullmatch(IMAGE_PATTERN, image_id)
        or not isinstance(image, list)
        or len(image) != 1
        or image[0].get("Id") != image_id
        or image[0].get("Os") != "linux"
        or not architecture
        or image[0].get("Architecture") != architecture
    ):
        raise ValueError("inspection-image-identity-mismatch")
    config = image[0].get("Config", {})
    labels = config.get("Labels", {})
    if (
        config.get("User") != "65534:65534"
        or config.get("Entrypoint")
        != ["python3", "/source/preview_inspection_capsule.py"]
        or labels.get("org.osmantic.ods.component") != "pixel-preview-inspection"
        or labels.get("org.osmantic.ods.inspection.protocol") != "1"
        or labels.get("org.osmantic.ods.inspection.playwright") != "1.62.0"
    ):
        raise ValueError("inspection-image-contract-mismatch")


def validate_config(config):
    native = isinstance(config, dict) and config.get("transport") == "docker-desktop"
    if (
        type(config) is not dict
        or set(config)
        != {
            "imageId",
            "docker",
            "snapshotRoot",
            "ownerUid",
            "transport",
            *(("dockerSocket", "dockerSha256") if native else ()),
        }
        or not isinstance(config["imageId"], str)
        or not re.fullmatch(IMAGE_PATTERN, config["imageId"])
        or type(config["ownerUid"]) is not int
        or config["ownerUid"] <= 0
        or config["transport"] not in ("local", "docker-desktop")
    ):
        raise ValueError("invalid-inspection-config")
    native = config["transport"] == "docker-desktop"
    if (not native and config["docker"] != "/usr/bin/docker") or config[
        "snapshotRoot"
    ] != ("/previews" if native else "/var/lib/ods-pixel-preview"):
        raise ValueError("invalid-inspection-config-paths")
    if native and (
        not isinstance(config["docker"], str)
        or not re.fullmatch(
            r"(?:/Applications/Docker\.app/Contents/Resources/bin/docker|/(?:opt/homebrew|usr/local)/Cellar/docker/(?!\.{1,2}/)[A-Za-z0-9._+-]+/bin/docker)",
            config["docker"],
        )
        or not isinstance(config["dockerSocket"], str)
        or not re.fullmatch(
            r"/Users/(?!\.{1,2}/)[A-Za-z0-9._-]+/(?:\.docker/run/docker\.sock|\.colima/[A-Za-z0-9_-]+/docker\.sock)",
            config["dockerSocket"],
        )
        or not isinstance(config["dockerSha256"], str)
        or not re.fullmatch("[a-f0-9]{64}", config["dockerSha256"])
    ):
        raise ValueError("invalid-inspection-native-binding")
    return config


def native_binding(*, docker_binary, docker_host, owner_uid):
    if (
        not isinstance(docker_binary, str)
        or not isinstance(docker_host, str)
        or not docker_host.startswith("unix://")
    ):
        raise ValueError("explicit-native-inspection-transport-required")
    docker = str(Path(docker_binary).resolve(strict=True))
    endpoint = docker_host
    home = Path(pwd.getpwuid(owner_uid).pw_dir)
    if not re.fullmatch(
        re.escape(str(home))
        + r"/(?:\.docker/run/docker\.sock|\.colima/[A-Za-z0-9_-]+/docker\.sock)",
        endpoint[7:],
    ):
        raise ValueError("native-inspection-socket-owner-mismatch")
    fd = os.open(docker, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid not in (0, owner_uid)
            or info.st_nlink != 1
            or info.st_mode & 0o022
            or not info.st_mode & 0o111
            or not 0 < info.st_size <= 128 * 1024 * 1024
        ):
            raise ValueError("unsafe-inspection-docker")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            body = stream.read(128 * 1024 * 1024 + 1)
        after = os.fstat(fd)

        def signature(value):
            return (
                value.st_dev,
                value.st_ino,
                value.st_size,
                value.st_mtime_ns,
                value.st_ctime_ns,
            )

        if (
            len(body) != info.st_size
            or signature(info) != signature(after)
            or signature(after) != signature(Path(docker).lstat())
        ):
            raise ValueError("inspection-docker-changed")
    finally:
        os.close(fd)
    socket_info = Path(endpoint[7:]).stat()
    if not stat.S_ISSOCK(socket_info.st_mode) or socket_info.st_uid != owner_uid:
        raise ValueError("unsafe-native-inspection-socket")
    binding = {
        "dockerSocket": endpoint[7:],
        "dockerSha256": hashlib.sha256(body).hexdigest(),
    }
    validate_config(
        dict(
            imageId="sha256:" + "0" * 64,
            docker=docker,
            ownerUid=owner_uid,
            transport="docker-desktop",
            snapshotRoot="/previews",
            **binding,
        )
    )
    return docker, endpoint, binding


def build_config(*, source, owner_uid, transport, docker_binary=None, docker_host=None):
    if (
        type(owner_uid) is not int
        or owner_uid <= 0
        or transport not in ("local", "docker-desktop")
    ):
        raise ValueError("inspection-owner-and-transport-required")
    native = transport == "docker-desktop"
    binding = {}
    if native:
        docker, endpoint, binding = native_binding(
            docker_binary=docker_binary, docker_host=docker_host, owner_uid=owner_uid
        )
    else:
        if docker_binary is not None or docker_host is not None:
            raise ValueError("local-inspection-transport-is-fixed")
        docker, endpoint = docker_path(transport), "unix:///var/run/docker.sock"
    argv = [docker, "--host", endpoint]
    environment = {"PATH": "/usr/bin:/bin", "HOME": pwd.getpwuid(owner_uid).pw_dir}
    snapshots = {name: source_bytes(Path(source) / name) for name in BUILD_FILES}
    with tempfile.TemporaryDirectory(prefix="ods-inspection-build-") as temporary:
        root = Path(temporary)
        context = root / "context"
        context.mkdir(mode=0o700)
        for name, body in snapshots.items():
            (context / name).write_bytes(body)
        identity = root / "image.id"
        # A fresh private context contains exactly reviewed build inputs. No tag
        # or caller-supplied Docker arguments can select the runtime image.
        subprocess.run(
            [
                *argv,
                "build",
                "--iidfile",
                str(identity),
                "--file",
                str(context / "Dockerfile.inspection"),
                str(context),
            ],
            check=True,
            timeout=1800,
            stdout=sys.stderr,
            env=environment,
        )
        image_id = identity.read_text().strip()
        if not re.fullmatch(IMAGE_PATTERN, image_id):
            raise ValueError("inspection-image-id-required")
        result = subprocess.run(
            [*argv, "image", "inspect", image_id],
            check=True,
            capture_output=True,
            timeout=30,
            env=environment,
        )
        validate_image(json.loads(result.stdout), image_id)
    return validate_config(
        {
            "imageId": image_id,
            "docker": docker,
            "ownerUid": owner_uid,
            "transport": transport,
            **binding,
            "snapshotRoot": "/previews"
            if transport == "docker-desktop"
            else "/var/lib/ods-pixel-preview",
        }
    )


def protected_parent(path, *, create=False):
    path = Path(path)
    for parent in [*reversed(path.parents), path]:
        if not parent.exists() and create:
            parent.mkdir(mode=0o755)
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError("unsafe-inspection-install-directory")


def protected_file(path):
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) != 0o644
        or info.st_nlink != 1
    ):
        raise ValueError("unsafe-inspection-installed-file")
    return path.read_bytes()


def install_linux(*, source, config):
    if os.geteuid() != 0 or sys.platform != "linux":
        raise ValueError("linux-root-required")
    validate_config(config)
    if config["transport"] != "local":
        raise ValueError("local-inspection-transport-required")
    snapshots = {
        PROGRAM_ROOT / name: source_bytes(Path(source) / name, config["ownerUid"])
        for name in RUNTIME_FILES
    }
    snapshots[UNIT] = source_bytes(Path(source) / UNIT.name, config["ownerUid"])
    snapshots[CONFIG] = (json.dumps(config, sort_keys=True) + "\n").encode()
    for path in snapshots:
        protected_parent(path.parent, create=True)
        if os.path.lexists(path):
            protected_file(path)
    for path, body in snapshots.items():
        fd, temporary = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(body)
                os.fchmod(stream.fileno(), 0o644)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.lexists(temporary):
                os.unlink(temporary)


def linux_cleanup(*, source, owner_uid, remove=False):
    """Validate every known artifact before removing any; never prune images.

    Images are content-addressed cache shared by installer generations. A
    partial first install is removable only when every present byte is ours.
    """
    if os.geteuid() != 0 or sys.platform != "linux":
        raise ValueError("linux-root-required")
    expected = {
        PROGRAM_ROOT / name: source_bytes(Path(source) / name, owner_uid)
        for name in RUNTIME_FILES
    }
    expected[UNIT] = source_bytes(Path(source) / UNIT.name, owner_uid)
    present = []
    for path in (*expected, CONFIG):
        protected_parent(
            path.parent if path.parent != PROGRAM_ROOT else PROGRAM_ROOT.parent
        )
        if os.path.lexists(path):
            protected_parent(path.parent)
            body = protected_file(path)
            if path == CONFIG:
                config = validate_config(json.loads(body))
                if config["transport"] != "local" or config["ownerUid"] != owner_uid:
                    raise ValueError("inspection-cleanup-owner-mismatch")
            elif body != expected[path]:
                raise ValueError("inspection-cleanup-source-mismatch")
            present.append(path)
    cache_root = PROGRAM_ROOT / "__pycache__"
    caches = []
    if os.path.lexists(PROGRAM_ROOT):
        protected_parent(PROGRAM_ROOT)
        if set(PROGRAM_ROOT.iterdir()) - set(expected) - {cache_root}:
            raise ValueError("unexpected-inspection-installed-file")
        if os.path.lexists(cache_root):
            protected_parent(cache_root)
            known_caches = {
                Path(importlib.util.cache_from_source(str(path), optimization="")): path
                for path in present
                if path.parent == PROGRAM_ROOT
            }
            for path in cache_root.iterdir():
                if path not in known_caches:
                    # Never guess at caches from another Python version or
                    # remove arbitrary operator files under a familiar name.
                    raise ValueError("unexpected-inspection-bytecode")
                body = protected_file(path)
                # Recompile validated, installed source without executing it or
                # deserializing cache bytes. Older health checks created these
                # timestamp caches despite the service disabling bytecode.
                with tempfile.TemporaryDirectory(
                    prefix="ods-inspection-cache-"
                ) as temporary:
                    candidate = Path(temporary) / "expected.pyc"
                    py_compile.compile(
                        str(known_caches[path]),
                        cfile=str(candidate),
                        doraise=True,
                        optimize=0,
                        invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP,
                    )
                    if body != candidate.read_bytes():
                        raise ValueError("inspection-bytecode-source-mismatch")
                caches.append(path)
    if UNIT in present and len(present) != len(expected) + 1:
        raise ValueError("incomplete-inspection-service")
    if remove:
        result = subprocess.run(
            ["/usr/bin/systemctl", "is-active", "--quiet", UNIT.name], timeout=30
        )
        if result.returncode not in (3, 4):
            raise ValueError("inspection-service-not-stopped")
        for path in (*caches, *present):
            path.unlink()
        if os.path.lexists(cache_root):
            cache_root.rmdir()
        if PROGRAM_ROOT.exists():
            PROGRAM_ROOT.rmdir()
    return "removed" if remove else "validated"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--source", required=True)
    build.add_argument("--owner-uid", type=int, required=True)
    build.add_argument(
        "--transport", choices=("local", "docker-desktop"), required=True
    )
    install = sub.add_parser("install-linux")
    install.add_argument("--source", required=True)
    for name in ("validate-linux", "remove-linux"):
        cleanup = sub.add_parser(name)
        cleanup.add_argument("--source", required=True)
        cleanup.add_argument("--owner-uid", type=int, required=True)
    args = parser.parse_args()
    if args.command == "build":
        print(
            json.dumps(
                build_config(
                    source=args.source,
                    owner_uid=args.owner_uid,
                    transport=args.transport,
                ),
                sort_keys=True,
            )
        )
    elif args.command == "install-linux":
        install_linux(source=args.source, config=json.load(sys.stdin))
    else:
        print(
            linux_cleanup(
                source=args.source,
                owner_uid=args.owner_uid,
                remove=args.command == "remove-linux",
            )
        )


if __name__ == "__main__":
    main()
