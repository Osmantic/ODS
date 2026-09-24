"""Installer identity/custody tests; no Docker daemon or installed files needed."""

import importlib.util
import json
import os
from pathlib import Path
import py_compile
import stat
import subprocess
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "inspection_distribution", ROOT / "installers/lib/pixel-preview-inspection.py"
)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
IMAGE = "sha256:" + "a" * 64


def config():
    return dict(
        imageId=IMAGE,
        docker="/usr/bin/docker",
        snapshotRoot="/var/lib/ods-pixel-preview",
        ownerUid=1000,
        transport="local",
    )


def image():
    return [
        dict(
            Id=IMAGE,
            Os="linux",
            Architecture="amd64",
            Config={
                "User": "65534:65534",
                "Entrypoint": ["python3", "/source/preview_inspection_capsule.py"],
                "Labels": {
                    "org.osmantic.ods.component": "pixel-preview-inspection",
                    "org.osmantic.ods.inspection.protocol": "1",
                    "org.osmantic.ods.inspection.playwright": "1.62.0",
                },
            },
        )
    ]


@pytest.mark.parametrize(
    "field,value",
    [
        ("imageId", "inspector:latest"),
        ("imageId", IMAGE + " extra"),
        ("docker", "/tmp/docker"),
        ("snapshotRoot", "/tmp/previews"),
        ("ownerUid", True),
        ("ownerUid", 0),
        ("transport", "remote"),
        ("extra", "ignored"),
    ],
)
def test_config_rejects_ambient_authority(field, value):
    value_config = config()
    value_config[field] = value
    with pytest.raises(ValueError):
        module.validate_config(value_config)


@pytest.mark.parametrize(
    "fault", ["id", "os", "architecture", "user", "entrypoint", "label"]
)
def test_image_contract_is_exact(monkeypatch, fault):
    monkeypatch.setattr(module.platform, "machine", lambda: "x86_64")
    value = image()
    if fault in ("id", "os", "architecture"):
        value[0][{"id": "Id", "os": "Os", "architecture": "Architecture"}[fault]] = (
            "wrong"
        )
    elif fault == "user":
        value[0]["Config"]["User"] = "root"
    elif fault == "entrypoint":
        value[0]["Config"]["Entrypoint"] = ["/bin/sh"]
    else:
        value[0]["Config"]["Labels"]["org.osmantic.ods.inspection.protocol"] = "2"
    with pytest.raises(ValueError):
        module.validate_image(value, IMAGE)


def test_source_refuses_symlinks_and_group_write(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"fixed input")
    source.chmod(0o644)
    assert module.source_bytes(source) == b"fixed input"
    link = tmp_path / "link"
    link.symlink_to(source)
    with pytest.raises(OSError):
        module.source_bytes(link)
    source.chmod(0o664)
    with pytest.raises(ValueError):
        module.source_bytes(source)


@pytest.mark.parametrize("fault", [None, "invalid-id", "image-contract"])
def test_build_captures_only_fixed_inputs_and_immutable_id(
    tmp_path, monkeypatch, fault
):
    monkeypatch.setattr(module, "docker_path", lambda transport: "/usr/bin/docker")
    monkeypatch.setattr(module.platform, "machine", lambda: "x86_64")
    for name in module.BUILD_FILES:
        (tmp_path / name).write_bytes(b"fixed input " + name.encode())
        (tmp_path / name).chmod(0o644)
    (tmp_path / "secret-not-a-build-input").write_bytes(b"never copied")
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        assert argv[:3] == ["/usr/bin/docker", "--host", "unix:///var/run/docker.sock"]
        if argv[3] == "build":
            context = Path(argv[-1])
            assert set(path.name for path in context.iterdir()) == set(
                module.BUILD_FILES
            )
            assert not any(
                flag in argv
                for flag in ("--tag", "--network=host", "--secret", "--ssh")
            )
            Path(argv[argv.index("--iidfile") + 1]).write_text(
                "mutable:tag" if fault == "invalid-id" else IMAGE
            )
            return subprocess.CompletedProcess(argv, 0)
        assert argv[3:] == ["image", "inspect", IMAGE]
        value = image()
        if fault == "image-contract":
            value[0]["Config"]["User"] = "root"
        return subprocess.CompletedProcess(argv, 0, json.dumps(value).encode())

    monkeypatch.setattr(module.subprocess, "run", run)
    if fault:
        with pytest.raises(ValueError):
            module.build_config(source=tmp_path, owner_uid=1000, transport="local")
    else:
        assert module.linux_cleanup(source=source, owner_uid=1000) == "validated"
        assert not calls
        assert {
            str(path): path.read_bytes()
            for path in (tmp_path / "installed").rglob("*")
            if path.is_file()
        } == before
        assert (
            module.build_config(source=tmp_path, owner_uid=1000, transport="local")
            == config()
        )
        assert len(calls) == 2


def test_mac_endpoint_is_owner_bound_not_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(
        module,
        "native_binding",
        lambda **kw: (
            (
                "/opt/homebrew/Cellar/docker/29.4.3/bin/docker",
                "unix:///Users/approved-owner/.colima/ods-fleet/docker.sock",
                {
                    "dockerSocket": "/Users/approved-owner/.colima/ods-fleet/docker.sock",
                    "dockerSha256": "b" * 64,
                },
            )
            if kw
            == {
                "docker_binary": "/opt/homebrew/bin/docker",
                "docker_host": "unix:///Users/approved-owner/.colima/ods-fleet/docker.sock",
                "owner_uid": 501,
            }
            else pytest.fail("wrong approved transport")
        ),
    )
    monkeypatch.setattr(
        module.pwd,
        "getpwuid",
        lambda uid: SimpleNamespace(pw_dir="/Users/approved-owner"),
    )
    monkeypatch.setattr(module.platform, "machine", lambda: "arm64")
    monkeypatch.setenv("DOCKER_HOST", "tcp://untrusted.invalid:2375")
    monkeypatch.setenv("DOCKER_CONTEXT", "untrusted-context")
    for name in module.BUILD_FILES:
        (tmp_path / name).write_bytes(b"fixed input")
        (tmp_path / name).chmod(0o644)

    def run(argv, **kw):
        assert argv[1:3] == [
            "--host",
            "unix:///Users/approved-owner/.colima/ods-fleet/docker.sock",
        ]
        if argv[3] == "build":
            Path(argv[argv.index("--iidfile") + 1]).write_text(IMAGE)
            return subprocess.CompletedProcess(argv, 0)
        value = image()
        value[0]["Architecture"] = "arm64"
        return subprocess.CompletedProcess(argv, 0, json.dumps(value).encode())

    monkeypatch.setattr(module.subprocess, "run", run)
    assert (
        module.build_config(
            source=tmp_path,
            owner_uid=501,
            transport="docker-desktop",
            docker_binary="/opt/homebrew/bin/docker",
            docker_host="unix:///Users/approved-owner/.colima/ods-fleet/docker.sock",
        )["snapshotRoot"]
        == "/previews"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("docker", "/tmp/docker"),
        ("docker", "/opt/homebrew/Cellar/docker/../bin/docker"),
        ("dockerSocket", "tcp://remote:2375"),
        ("dockerSocket", "/Users/owner/.colima/../docker.sock"),
        ("dockerSocket", "/tmp/docker.sock"),
        ("dockerSha256", "mutable"),
        ("dockerSha256", "a" * 63),
    ],
)
def test_native_configuration_binds_only_reviewable_local_transport(field, value):
    document = dict(
        imageId=IMAGE,
        docker="/opt/homebrew/Cellar/docker/29.4.3/bin/docker",
        dockerSocket="/Users/owner/.colima/ods-fleet/docker.sock",
        dockerSha256="b" * 64,
        ownerUid=501,
        transport="docker-desktop",
        snapshotRoot="/previews",
    )
    assert module.validate_config(document) == document
    document[field] = value
    with pytest.raises(ValueError):
        module.validate_config(document)


def test_native_transport_requires_explicit_arguments(monkeypatch, tmp_path):
    monkeypatch.setenv("DOCKER_HOST", "unix:///Users/owner/.docker/run/docker.sock")
    with pytest.raises(ValueError, match="explicit-native"):
        module.build_config(source=tmp_path, owner_uid=501, transport="docker-desktop")


@pytest.mark.parametrize("fault", ["symlink", "owner", "mode", "hardlink"])
def test_installed_file_custody_rejects_replacement(fault):
    mode, uid, links = stat.S_IFREG | 0o644, 0, 1
    if fault == "symlink":
        mode = stat.S_IFLNK | 0o777
    if fault == "owner":
        uid = 1000
    if fault == "mode":
        mode = stat.S_IFREG | 0o664
    if fault == "hardlink":
        links = 2
    path = SimpleNamespace(
        lstat=lambda: SimpleNamespace(st_mode=mode, st_uid=uid, st_nlink=links)
    )
    with pytest.raises(ValueError):
        module.protected_file(path)


def test_publisher_stays_without_docker_and_broker_is_narrow():
    host = ROOT / "extensions/services/pixel-agent/host"
    publisher = (host / "pixel-workspace-preview.service").read_text()
    broker = (host / "pixel-preview-inspection.service").read_text()
    assert "docker.sock" not in publisher and "Group=docker" not in publisher
    assert "RuntimeDirectoryMode=0750" in broker
    assert "CapabilityBoundingSet=CAP_DAC_READ_SEARCH" in broker
    assert "RestrictAddressFamilies=AF_UNIX" in broker
    assert "ProtectSystem=strict" in broker
    installer = (ROOT / "installers/lib/pixel-host-install.sh").read_text()
    assert (
        "python3 -B /usr/local/libexec/ods-pixel-inspection/preview_inspection.py health"
        in installer
    )
    capsule = (host / "Dockerfile.inspection").read_text()
    assert (
        "@sha256:" in capsule
        and "--require-hashes" in capsule
        and "--only-shell chromium" in capsule
    )
    assert "USER 65534:65534" in capsule


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "cache",
        "empty-cache",
        "active",
        "foreign-file",
        "changed-source",
        "wrong-owner",
        "incomplete",
        "changed-cache",
        "foreign-cache",
        "old-python-cache",
        "stale-cache",
        "cache-symlink",
        "cache-hardlink",
        "cache-writable",
        "cache-directory-symlink",
    ],
)
def test_linux_uninstall_validates_all_artifacts_before_deleting_any(
    tmp_path, monkeypatch, fault
):
    source = tmp_path / "source"
    source.mkdir()
    program = tmp_path / "installed/program"
    unit = tmp_path / "installed/systemd/pixel-preview-inspection.service"
    config_path = tmp_path / "installed/etc/config.json"
    monkeypatch.setattr(module, "PROGRAM_ROOT", program)
    monkeypatch.setattr(module, "UNIT", unit)
    monkeypatch.setattr(module, "CONFIG", config_path)
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(module.sys, "platform", "linux")

    # The independent custody tests above exercise owner/mode/type checks.
    # This fixture isolates transaction ordering in an ordinary user's tempdir.
    def parents(path, create=False):
        if create:
            path.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            raise ValueError("unsafe-inspection-install-directory")

    monkeypatch.setattr(module, "protected_parent", parents)

    def protected(path):
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o644
            or info.st_nlink != 1
        ):
            raise ValueError("unsafe-inspection-installed-file")
        return path.read_bytes()

    monkeypatch.setattr(module, "protected_file", protected)
    for name in (*module.RUNTIME_FILES, unit.name):
        (source / name).write_bytes(b"# reviewed " + name.encode() + b"\nVALUE = 1\n")
        (source / name).chmod(0o644)
    module.install_linux(source=source, config=config())
    if fault == "foreign-file":
        (program / "operator-file").write_bytes(b"not ours")
    if fault == "changed-source":
        (program / module.RUNTIME_FILES[0]).write_bytes(b"changed")
    if fault == "wrong-owner":
        value = config()
        value["ownerUid"] = 1001
        config_path.write_text(json.dumps(value))
    if fault == "incomplete":
        (program / module.RUNTIME_FILES[0]).unlink()
    cache_root = program / "__pycache__"
    if fault and "cache" in fault:
        if fault == "empty-cache":
            cache_root.mkdir()
        else:
            installed = program / module.RUNTIME_FILES[1]
            cache = Path(
                py_compile.compile(
                    str(installed),
                    doraise=True,
                    optimize=0,
                    invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP,
                )
            )
            cache.chmod(0o644)
            if fault == "changed-cache":
                cache.write_bytes(cache.read_bytes()[:-1] + b"x")
            if fault == "foreign-cache":
                (cache_root / "operator-file").write_bytes(b"not ours")
            if fault == "old-python-cache":
                cache.rename(cache_root / "preview_inspection_protocol.cpython-999.pyc")
            if fault == "stale-cache":
                body = cache.read_bytes()
                cache.write_bytes(body[:8] + b"\x00" * 4 + body[12:])
            if fault == "cache-symlink":
                cache.unlink()
                cache.symlink_to(installed)
            if fault == "cache-hardlink":
                os.link(cache, tmp_path / "outside-cache")
            if fault == "cache-writable":
                cache.chmod(0o666)
            if fault == "cache-directory-symlink":
                moved = tmp_path / "outside-directory"
                cache_root.rename(moved)
                cache_root.symlink_to(moved, target_is_directory=True)
    before = {
        str(path): path.read_bytes()
        for path in (tmp_path / "installed").rglob("*")
        if path.is_file()
    }
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0 if fault == "active" else 3)

    monkeypatch.setattr(module.subprocess, "run", run)
    if fault not in (None, "cache", "empty-cache"):
        with pytest.raises(ValueError):
            module.linux_cleanup(source=source, owner_uid=1000, remove=True)
        after = {
            str(path): path.read_bytes()
            for path in (tmp_path / "installed").rglob("*")
            if path.is_file()
        }
        assert after == before
        if fault != "active":
            assert not calls
    else:
        assert (
            module.linux_cleanup(source=source, owner_uid=1000, remove=True)
            == "removed"
        )
        assert not program.exists() and not unit.exists() and not config_path.exists()
        assert calls == [["/usr/bin/systemctl", "is-active", "--quiet", unit.name]]
