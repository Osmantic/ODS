"""Candidate cleanup code must validate installed sources during an upgrade."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("action", ["validate-linux", "remove-linux"])
@pytest.mark.parametrize("fault", [None, "missing", "symlink", "candidate-rejects"])
def test_candidate_cleanup_uses_candidate_code_and_installed_sources(
    tmp_path, action, fault
):
    candidate = tmp_path / "candidate checkout"
    installed = tmp_path / "old install"
    library = candidate / "lib/pixel-uninstall.sh"
    helper = candidate / "installers/lib/pixel-preview-inspection.py"
    old_helper = installed / "installers/lib/pixel-preview-inspection.py"
    source = installed / "extensions/services/pixel-agent/host"
    for directory in (library.parent, helper.parent, old_helper.parent, source):
        directory.mkdir(parents=True)
    shutil.copyfile(ROOT / "lib/pixel-uninstall.sh", library)
    old_helper.write_text("raise RuntimeError('legacy cleanup must never execute')\n")
    installed_bytes = b"old installed runtime bytes\n"
    (source / "preview_inspection.py").write_bytes(installed_bytes)
    # This dispatch fixture intentionally disagrees with candidate sources.
    # The production validator is separately exercised with real bytecode.
    candidate_source = candidate / "extensions/services/pixel-agent/host"
    candidate_source.mkdir(parents=True)
    (candidate_source / "preview_inspection.py").write_bytes(b"new runtime bytes\n")
    helper.write_text(
        "import json, pathlib, sys\n"
        "source = pathlib.Path(sys.argv[sys.argv.index('--source') + 1])\n"
        "print(json.dumps({'argv': sys.argv, 'isolated': sys.flags.isolated, "
        "'noBytecode': sys.dont_write_bytecode, "
        "'sourceBytes': (source / 'preview_inspection.py').read_text()}))\n"
        + ("raise SystemExit(19)\n" if fault == "candidate-rejects" else "")
    )
    if fault == "missing":
        helper.unlink()
    elif fault == "symlink":
        helper.unlink()
        helper.symlink_to(old_helper)
    command = (
        'source "$1"; sudo() { "$@"; }; _ods_pixel_inspection_cleanup "$2" 1000 "$3"'
    )
    result = subprocess.run(
        ["bash", "-c", command, "test", str(library), str(installed), action],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PYTHONPATH": "/untrusted-module-path",
            "PYTHONHOME": "/untrusted-python-home",
        },
    )
    assert "legacy cleanup must never execute" not in result.stderr
    assert (source / "preview_inspection.py").read_bytes() == installed_bytes
    assert (
        old_helper.read_text()
        == "raise RuntimeError('legacy cleanup must never execute')\n"
    )
    if fault in ("missing", "symlink"):
        assert result.returncode != 0
        assert (
            "Candidate Pixel inspection cleanup helper is missing or unsafe"
            in result.stderr
        )
    else:
        receipt = json.loads(result.stdout)
        assert receipt["argv"] == [
            str(helper),
            action,
            "--source",
            str(source),
            "--owner-uid",
            "1000",
        ]
        assert receipt["isolated"] == 1 and receipt["noBytecode"] is True
        assert receipt["sourceBytes"] == installed_bytes.decode()
        assert result.returncode == (19 if fault == "candidate-rejects" else 0)


def test_uninstall_uses_same_candidate_dispatch_before_and_after_stopping_services():
    text = (ROOT / "lib/pixel-uninstall.sh").read_text()
    validate = text.index(
        '_ods_pixel_inspection_cleanup "$install_dir" "$owner_uid" validate-linux'
    )
    stop = text.index("systemctl disable --now pixel-preview-inspection.service")
    remove = text.index(
        '_ods_pixel_inspection_cleanup "$install_dir" "$owner_uid" remove-linux'
    )
    assert validate < stop < remove
    assert '"$install_dir/installers/lib/pixel-preview-inspection.py"' not in text
