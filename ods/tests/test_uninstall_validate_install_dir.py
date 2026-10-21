#!/usr/bin/env python3
"""Regression tests for validate_requested_install_dir in ods-uninstall.sh."""

from collections.abc import Callable
import hashlib
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ODS = Path(__file__).resolve().parents[1]
UNINSTALLER = ODS / "ods-uninstall.sh"


def _extract_function() -> str:
    content = UNINSTALLER.read_text(encoding="utf-8")
    pattern = re.compile(r"^validate_requested_install_dir\(\)\s*\{[\s\S]*?\n\}", re.MULTILINE)
    matches = pattern.findall(content)
    assert len(matches) == 1, f"Expected exactly 1 match for validate_requested_install_dir, found {len(matches)}"
    return matches[0]


FUNC_DEF = _extract_function()


def _populate_valid_tree(target: Path, use_base_compose: bool = True) -> None:
    target.mkdir(parents=True, exist_ok=True)
    (target / ".env").write_text("ODS_TIER=1\n", encoding="utf-8")
    (target / "ods-cli").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (target / "ods-uninstall.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    compose_name = "docker-compose.base.yml" if use_base_compose else "docker-compose.yml"
    (target / compose_name).write_text("services: {}\n", encoding="utf-8")


def _snapshot_directory(path: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    if not path.exists():
        return snapshot
    for item in sorted(path.rglob("*")):
        rel = str(item.relative_to(path))
        if item.is_symlink():
            snapshot[rel] = f"symlink:{os.readlink(item)}"
        elif item.is_file():
            snapshot[rel] = f"file:{hashlib.sha256(item.read_bytes()).hexdigest()}"
        elif item.is_dir():
            snapshot[rel] = "dir"
    return snapshot


def _run_guard(
    setup_fn: Callable[[Path, Path, Path], str | tuple[str, Path]],
    expected_rc: int,
    bash_bin: str = "bash",
    idempotent: bool = False,
) -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        sdir = root / "script_dir"
        hdir = root / "home_dir"
        sdir.mkdir()
        hdir.mkdir()

        res = setup_fn(root, sdir, hdir)
        target_arg, cwd = res if isinstance(res, tuple) else (res, None)

        def _exec() -> None:
            snap_before = _snapshot_directory(root)
            env = os.environ.copy()
            env["TEST_SCRIPT_DIR"] = str(sdir)
            env["TEST_HOME"] = str(hdir)
            script_body = (
                'set -u\n'
                'SCRIPT_DIR="$TEST_SCRIPT_DIR"\n'
                'HOME="$TEST_HOME"\n'
                f'{FUNC_DEF}\n'
                'validate_requested_install_dir "$1"\n'
            )
            proc = subprocess.run(
                [bash_bin, "-c", script_body, "_", target_arg],
                env=env,
                cwd=str(cwd) if cwd else None,
                capture_output=True,
                text=True,
                check=False,
            )
            snap_after = _snapshot_directory(root)

            if expected_rc == 0:
                assert proc.returncode == 0, f"Expected 0, got {proc.returncode} (stderr: {proc.stderr.strip()})"
                target_path = Path(target_arg.rstrip("/")).resolve()
                assert proc.stdout.strip() == str(target_path), f"Expected stdout '{target_path}', got '{proc.stdout.strip()}'"
            else:
                assert proc.returncode != 0, f"Expected non-zero for target '{target_arg}', got 0 (stdout: {proc.stdout.strip()})"

            assert snap_before == snap_after, "Fixture state mutated during execution"

        _exec()
        if idempotent:
            _exec()


def _run_tree_case(
    target_name: str,
    mutator: Callable[[Path, Path], None] | None = None,
    expected_rc: int = 1,
    bash_bin: str = "bash",
    use_base_compose: bool = True,
    suffix: str = "",
    idempotent: bool = False,
) -> None:
    def setup(root: Path, sdir: Path, hdir: Path) -> str:
        target = root / target_name
        _populate_valid_tree(target, use_base_compose=use_base_compose)
        if mutator:
            mutator(target, root)
        return str(target) + suffix

    _run_guard(setup, expected_rc=expected_rc, bash_bin=bash_bin, idempotent=idempotent)


# --- Accept Cases (5 total) ---

def test_valid_base_compose(bash_bin: str = "bash") -> None:
    _run_tree_case("valid_base", expected_rc=0, bash_bin=bash_bin)


def test_valid_alt_compose(bash_bin: str = "bash") -> None:
    _run_tree_case("valid_alt", expected_rc=0, bash_bin=bash_bin, use_base_compose=False)


def test_path_with_spaces_accepted(bash_bin: str = "bash") -> None:
    _run_tree_case("ods space install target", expected_rc=0, bash_bin=bash_bin)


def test_path_with_trailing_slash_accepted(bash_bin: str = "bash") -> None:
    _run_tree_case("valid_trailing", expected_rc=0, bash_bin=bash_bin, suffix="/")


def test_idempotent_execution(bash_bin: str = "bash") -> None:
    _run_tree_case("valid_idempotent", expected_rc=0, bash_bin=bash_bin, idempotent=True)


# --- Rejection Cases (17 total) ---

def test_empty_string_rejected(bash_bin: str = "bash") -> None:
    _run_guard(lambda r, s, h: "", expected_rc=1, bash_bin=bash_bin)


def test_relative_path_rejected(bash_bin: str = "bash") -> None:
    def setup(root: Path, sdir: Path, hdir: Path) -> tuple[str, Path]:
        _populate_valid_tree(root / "rel_target")
        return "rel_target", root
    _run_guard(setup, expected_rc=1, bash_bin=bash_bin)


def test_nonexistent_path_rejected(bash_bin: str = "bash") -> None:
    _run_guard(lambda r, s, h: str(r / "does_not_exist"), expected_rc=1, bash_bin=bash_bin)


def test_regular_file_rejected(bash_bin: str = "bash") -> None:
    def setup(root: Path, sdir: Path, hdir: Path) -> str:
        target = root / "regular_file.txt"
        target.write_text("not a directory\n", encoding="utf-8")
        return str(target)
    _run_guard(setup, expected_rc=1, bash_bin=bash_bin)


def test_symlink_directory_rejected(bash_bin: str = "bash") -> None:
    def setup(root: Path, sdir: Path, hdir: Path) -> str:
        valid = root / "valid_target"
        _populate_valid_tree(valid, use_base_compose=True)
        sym = root / "symlink_dir"
        sym.symlink_to(valid)
        return str(sym)
    _run_guard(setup, expected_rc=1, bash_bin=bash_bin)


def test_root_directory_rejected(bash_bin: str = "bash") -> None:
    _run_guard(lambda r, s, h: "/", expected_rc=1, bash_bin=bash_bin)


def test_home_directory_rejected(bash_bin: str = "bash") -> None:
    def setup(root: Path, sdir: Path, hdir: Path) -> str:
        _populate_valid_tree(hdir, use_base_compose=True)
        return str(hdir)
    _run_guard(setup, expected_rc=1, bash_bin=bash_bin)


def test_script_dir_rejected(bash_bin: str = "bash") -> None:
    def setup(root: Path, sdir: Path, hdir: Path) -> str:
        _populate_valid_tree(sdir, use_base_compose=True)
        return str(sdir)
    _run_guard(setup, expected_rc=1, bash_bin=bash_bin)


def test_missing_env_rejected(bash_bin: str = "bash") -> None:
    _run_tree_case("no_env", lambda t, r: (t / ".env").unlink(), bash_bin=bash_bin)


def test_symlink_env_rejected(bash_bin: str = "bash") -> None:
    def _symlink_env(t: Path, r: Path) -> None:
        (t / ".env").unlink()
        (r / "ext.env").write_text("ODS_TIER=1\n", encoding="utf-8")
        (t / ".env").symlink_to(r / "ext.env")
    _run_tree_case("sym_env", _symlink_env, bash_bin=bash_bin)


def test_missing_ods_cli_rejected(bash_bin: str = "bash") -> None:
    _run_tree_case("no_cli", lambda t, r: (t / "ods-cli").unlink(), bash_bin=bash_bin)


def test_symlink_ods_cli_rejected(bash_bin: str = "bash") -> None:
    def _symlink_cli(t: Path, r: Path) -> None:
        (t / "ods-cli").unlink()
        (r / "ext_cli").write_text("#!/bin/sh\n", encoding="utf-8")
        (t / "ods-cli").symlink_to(r / "ext_cli")
    _run_tree_case("sym_cli", _symlink_cli, bash_bin=bash_bin)


def test_missing_uninstaller_rejected(bash_bin: str = "bash") -> None:
    _run_tree_case("no_uninst", lambda t, r: (t / "ods-uninstall.sh").unlink(), bash_bin=bash_bin)


def test_symlink_uninstaller_rejected(bash_bin: str = "bash") -> None:
    def _symlink_uninst(t: Path, r: Path) -> None:
        (t / "ods-uninstall.sh").unlink()
        (r / "ext_uninst").write_text("#!/bin/sh\n", encoding="utf-8")
        (t / "ods-uninstall.sh").symlink_to(r / "ext_uninst")
    _run_tree_case("sym_uninst", _symlink_uninst, bash_bin=bash_bin)


def test_missing_both_compose_rejected(bash_bin: str = "bash") -> None:
    _run_tree_case("no_compose", lambda t, r: (t / "docker-compose.base.yml").unlink(), bash_bin=bash_bin)


def test_symlink_compose_base_rejected(bash_bin: str = "bash") -> None:
    def _symlink_base(t: Path, r: Path) -> None:
        (t / "docker-compose.base.yml").unlink()
        (r / "ext_base.yml").write_text("services: {}\n", encoding="utf-8")
        (t / "docker-compose.base.yml").symlink_to(r / "ext_base.yml")
    _run_tree_case("sym_compose_base", _symlink_base, bash_bin=bash_bin)


def test_symlink_compose_alt_rejected(bash_bin: str = "bash") -> None:
    def _symlink_alt(t: Path, r: Path) -> None:
        (t / "docker-compose.yml").unlink()
        (r / "ext_alt.yml").write_text("services: {}\n", encoding="utf-8")
        (t / "docker-compose.yml").symlink_to(r / "ext_alt.yml")
    _run_tree_case("sym_compose_alt", _symlink_alt, bash_bin=bash_bin, use_base_compose=False)


ALL_TESTS = [
    # 5 Accept Cases
    test_valid_base_compose,
    test_valid_alt_compose,
    test_path_with_spaces_accepted,
    test_path_with_trailing_slash_accepted,
    test_idempotent_execution,
    # 17 Rejection Cases
    test_empty_string_rejected,
    test_relative_path_rejected,
    test_nonexistent_path_rejected,
    test_regular_file_rejected,
    test_symlink_directory_rejected,
    test_root_directory_rejected,
    test_home_directory_rejected,
    test_script_dir_rejected,
    test_missing_env_rejected,
    test_symlink_env_rejected,
    test_missing_ods_cli_rejected,
    test_symlink_ods_cli_rejected,
    test_missing_uninstaller_rejected,
    test_symlink_uninstaller_rejected,
    test_missing_both_compose_rejected,
    test_symlink_compose_base_rejected,
    test_symlink_compose_alt_rejected,
]


def run_all(bash_bin: str = "bash") -> None:
    for test_fn in ALL_TESTS:
        test_fn(bash_bin=bash_bin)


if __name__ == "__main__":
    selected_bash = sys.argv[1] if len(sys.argv) > 1 else "bash"
    run_all(bash_bin=selected_bash)
    print(f"[PASS] All {len(ALL_TESTS)} validate_requested_install_dir regression tests passed under {selected_bash}.")
