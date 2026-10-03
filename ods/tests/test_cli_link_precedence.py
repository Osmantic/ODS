"""Check the command actually launched after rebinding an installed CLI."""
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("user_first", [True, False])
@pytest.mark.parametrize("sudo_available", [True, False])
def test_existing_cli_precedence_survives_install_move(tmp_path, user_first, sudo_available):
    old = tmp_path / "old install"
    new = tmp_path / "new install"
    owner = tmp_path / "owner home"
    user_bin = owner / ".local/bin"
    system_bin = tmp_path / "system bin"
    for directory in [old, new, user_bin, system_bin]:
        directory.mkdir(parents=True)
    for directory, label in [(old, "old"), (new, "new")]:
        script = directory / "ods-cli"
        script.write_text("#!/bin/sh\nprintf '%s\n' " + label + "\n")
        script.chmod(0o755)
    (user_bin / "ods").symlink_to(old / "ods-cli")
    (system_bin / "ods").symlink_to(old / "ods-cli")
    bins = [user_bin, system_bin] if user_first else [system_bin, user_bin]
    env = {**os.environ, "PATH": os.pathsep.join(map(str, bins)) + os.pathsep + os.environ["PATH"],
           "ODS_CLI_SYSTEM_LINK": str(system_bin / "ods"),
           "TEST_SUDO_AVAILABLE": "true" if sudo_available else "false"}
    result = subprocess.run(["bash", "-c", """
set -euo pipefail
source "$1/installers/lib/cli-link.sh"
ods_sudo_available() { [[ "$TEST_SUDO_AVAILABLE" == true ]]; }
ods_sudo() { "$@"; }
ods_bind_cli_command "$2" "$3"
ods
""", "cli-test", str(ROOT), str(new), str(owner)], env=env, text=True, capture_output=True, check=False)
    if not user_first and not sudo_available:
        assert result.returncode != 0
        assert result.stdout == ""
        assert (system_bin / "ods").resolve() == old / "ods-cli"
        return
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1] == "new"


def test_unmanaged_shadow_is_preserved_and_not_reported_as_success(tmp_path):
    new = tmp_path / "new"
    owner = tmp_path / "home"
    foreign_bin = tmp_path / "foreign"
    system_bin = tmp_path / "system"
    for directory in [new, owner, foreign_bin, system_bin]:
        directory.mkdir()
    executable = new / "ods-cli"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    shadow = foreign_bin / "ods"
    shadow.write_text("#!/bin/sh\necho foreign\n")
    shadow.chmod(0o755)
    original = shadow.read_bytes()
    result = subprocess.run(["bash", "-c", """
source "$1/installers/lib/cli-link.sh"
ods_sudo_available() { return 0; }
ods_sudo() { "$@"; }
ods_bind_cli_command "$2" "$3"
""", "cli-test", str(ROOT), str(new), str(owner)], env={
        **os.environ, "PATH": str(foreign_bin) + os.pathsep + os.environ["PATH"],
        "ODS_CLI_SYSTEM_LINK": str(system_bin / "ods"),
    }, text=True, capture_output=True, check=False)
    assert result.returncode != 0
    assert result.stdout == ""
    assert shadow.read_bytes() == original
