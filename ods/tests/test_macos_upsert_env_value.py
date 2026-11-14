"""Literal key and delimiter-safe updates in macOS upsert_env_value."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

BASH = shutil.which("bash") or "bash"


def extract_helper_func(script_path: Path) -> str:
    lines = script_path.read_text().splitlines()
    start = -1
    brace_count = 0
    collected = []
    for i, line in enumerate(lines):
        if line.startswith("upsert_env_value() {"):
            start = i
            brace_count = 0
        if start != -1:
            collected.append(line)
            brace_count += line.count("{") - line.count("}")
            if brace_count == 0:
                break
    return "\n".join(collected)


def test_upsert_env_value_handles_delimiters_and_literal_keys():
    repo_root = Path(__file__).resolve().parents[1]
    env_gen = repo_root / "installers/macos/lib/env-generator.sh"
    helper_code = extract_helper_func(env_gen)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        env_file = root / ".env"
        env_file.write_text("FOO_BAR=existing_value\n")

        runner = root / "run_test.sh"
        runner.write_text(f"""#!/usr/bin/env bash
set -euo pipefail
{helper_code}

# 1. Setting a key with regex dot should not overwrite FOO_BAR
upsert_env_value "{env_file.as_posix()}" "FOO.BAR" "new_dot_val"

grep -q "FOO_BAR=existing_value" "{env_file.as_posix()}" || {{ echo "FOO_BAR was clobbered by FOO.BAR"; exit 2; }}

# 2. Setting a value containing pipe and ampersand
upsert_env_value "{env_file.as_posix()}" "PIPE_VAR" "http://host|auth&token"

# 3. In-place update of existing value with delimiter
upsert_env_value "{env_file.as_posix()}" "FOO_BAR" "updated|pipe&amp"
""")
        runner.chmod(0o755)

        result = subprocess.run([BASH, runner.as_posix()], capture_output=True, text=True, check=False)
        assert result.returncode == 0, f"stdout: {result.stdout}, stderr: {result.stderr}"

        content = env_file.read_text()
        assert "FOO_BAR=updated|pipe&amp" in content
        assert "FOO.BAR=new_dot_val" in content
        assert "PIPE_VAR=http://host|auth&token" in content


def test_ods_macos_upsert_env_value_handles_delimiters_and_literal_keys():
    """ods-macos.sh carries its own copy of upsert_env_value; it must be
    literal-key and delimiter-safe, matching the hardened env-generator.sh copy.

    Failure modes on the unpatched implementation:
      1. grep -qE "^${key}=" treats the key as an ERE pattern, so a dot in the
         key name matches any character and silently destroys a sibling key.
      2. sed "s|^${key}=.*|${key}=${value}|" uses | as the delimiter, so a
         value containing | or & corrupts the substitution command.
    """
    repo_root = Path(__file__).resolve().parents[1]
    ods_macos = repo_root / "installers/macos/ods-macos.sh"
    helper_code = extract_helper_func(ods_macos)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        env_file = root / ".env"
        env_file.write_text("FOO_BAR=original\n")

        runner = root / "run_macos_test.sh"
        runner.write_text(f"""#!/usr/bin/env bash
set -euo pipefail
{helper_code}

# Case 1: a key with a regex metacharacter must not clobber a sibling key.
# On the buggy impl grep -qE "^FOO.BAR=" matches "FOO_BAR=original" (dot is
# a wildcard), so the sed rewrites FOO_BAR and FOO.BAR is never appended.
upsert_env_value "{env_file.as_posix()}" "FOO.BAR" "dotval"
grep -q "FOO_BAR=original" "{env_file.as_posix()}" || {{ echo "FAIL: FOO_BAR was clobbered by FOO.BAR"; exit 2; }}
grep -q "FOO.BAR=dotval" "{env_file.as_posix()}" || {{ echo "FAIL: FOO.BAR was not written"; exit 2; }}

# Case 2: a value containing the sed delimiter | must not corrupt the update.
# On the buggy impl sed "s|^URL=.*|URL=http://host|auth&tok|" produces
# "bad flag in substitute command" and leaves the value unchanged.
upsert_env_value "{env_file.as_posix()}" "URL" "http://host|auth&tok"
grep -qF "URL=http://host|auth&tok" "{env_file.as_posix()}" || {{ echo "FAIL: URL value was corrupted or missing"; exit 2; }}

# Case 3: in-place update of an existing key with delimiter characters.
upsert_env_value "{env_file.as_posix()}" "FOO_BAR" "new|val&x"
grep -qF "FOO_BAR=new|val&x" "{env_file.as_posix()}" || {{ echo "FAIL: FOO_BAR in-place update corrupted"; exit 2; }}
""")
        runner.chmod(0o755)

        result = subprocess.run([BASH, runner.as_posix()], capture_output=True, text=True, check=False)
        assert result.returncode == 0, (
            f"ods-macos.sh upsert_env_value failed.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        content = env_file.read_text()
        assert "FOO.BAR=dotval" in content
        assert "URL=http://host|auth&tok" in content
        assert "FOO_BAR=new|val&x" in content


def test_upsert_env_value_preserves_recovery_after_failed_restore():
    """Exercise each real helper under partial writes and rollback failures."""
    repo_root = Path(__file__).resolve().parents[1]
    helpers = (
        repo_root / "installers/macos/lib/env-generator.sh",
        repo_root / "installers/macos/ods-macos.sh",
    )
    for helper in helpers:
        helper_code = extract_helper_func(helper)
        for fault in ("write", "restore", "restore-mismatch", "backup-mismatch"):
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                env_file = root / ".env"
                original = b"SECRET=keep-private\nKEY=before\n"
                env_file.write_bytes(original)
                env_file.chmod(0o600)
                inode = env_file.stat().st_ino
                runner = root / "fault.sh"
                runner.write_text(f"""#!/usr/bin/env bash
set -euo pipefail
{helper_code}
fault='{fault}'
cat() {{ printf 'PARTIAL'; return 1; }}
cp() {{
    if [[ "$1" == */previous ]]; then
        [[ "$fault" == restore ]] && return 1
        if [[ "$fault" == restore-mismatch ]]; then
            printf 'BAD-RESTORE' > "$2"
            return 0
        fi
    elif [[ "$fault" == backup-mismatch ]]; then
        printf 'BAD-BACKUP' > "$2"
        return 0
    fi
    command cp "$@"
}}
if upsert_env_value '{env_file.as_posix()}' KEY after; then
    echo 'injected fault incorrectly succeeded' >&2
    exit 90
fi
""")
                result = subprocess.run(
                    [BASH, runner.as_posix()], capture_output=True, text=True, check=False
                )
                assert result.returncode == 0, (helper, fault, result.stderr)
                assert env_file.stat().st_ino == inode
                retained = list(root.glob(".env.stage.*/previous"))
                if fault in ("restore", "restore-mismatch"):
                    assert len(retained) == 1, (helper, fault, result.stderr)
                    backup = retained[0]
                    assert backup.read_bytes() == original
                    assert "original retained at" in result.stderr
                    assert backup.parent.name in result.stderr
                    assert sorted(p.name for p in backup.parent.iterdir()) == ["previous"]
                    if os.name != "nt":
                        assert backup.stat().st_mode & 0o777 == 0o600
                        assert backup.parent.stat().st_mode & 0o777 == 0o700
                else:
                    assert env_file.read_bytes() == original
                    assert not list(root.glob(".env.stage.*"))
                assert "keep-private" not in result.stderr


def extract_read_env_value_func(script_path: Path) -> str:
    lines = script_path.read_text().splitlines()
    start = -1
    brace_count = 0
    collected = []
    for i, line in enumerate(lines):
        if line.startswith("read_env_value() {"):
            start = i
            brace_count = 0
        if start != -1:
            collected.append(line)
            brace_count += line.count("{") - line.count("}")
            if brace_count == 0:
                break
    return "\n".join(collected)


def test_read_env_value_selects_last_assignment():
    repo_root = Path(__file__).resolve().parents[1]
    env_gen = repo_root / "installers/macos/lib/env-generator.sh"
    helper_code = extract_read_env_value_func(env_gen)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        env_file = root / ".env"
        content = (
            "OPENAI_API_KEY=\r\n"
            "# Owner appended key per FAQ:\r\n"
            "OPENAI_API_KEY=sk-owner-secret-key\r\n"
            "COMPLEX_VAL=foo=bar=baz\r\n"
            "CRLF_KEY=crlf_value\r\n"
        )
        env_file.write_bytes(content.encode("utf-8"))

        runner = root / "run_test.sh"
        runner.write_text(f"""#!/usr/bin/env bash
set -euo pipefail
{helper_code}

# Test 1: duplicate assignment selects last (owner key over empty placeholder)
val1="$(read_env_value "{env_file.as_posix()}" "OPENAI_API_KEY")"
[[ "$val1" == "sk-owner-secret-key" ]] || {{ echo "Expected sk-owner-secret-key, got: $val1"; exit 1; }}

# Test 2: value containing = signs is preserved
val2="$(read_env_value "{env_file.as_posix()}" "COMPLEX_VAL")"
[[ "$val2" == "foo=bar=baz" ]] || {{ echo "Expected foo=bar=baz, got: $val2"; exit 2; }}

# Test 3: CRLF line endings have \\r stripped
val3="$(read_env_value "{env_file.as_posix()}" "CRLF_KEY")"
[[ "$val3" == "crlf_value" ]] || {{ echo "Expected crlf_value, got: $val3"; exit 3; }}

# Test 4: missing key returns empty string with exit 0
val4="$(read_env_value "{env_file.as_posix()}" "NONEXISTENT_KEY")"
[[ -z "$val4" ]] || {{ echo "Expected empty string for missing key, got: $val4"; exit 4; }}

# Test 5: missing file returns empty string with exit 0
val5="$(read_env_value "{root.as_posix()}/missing.env" "OPENAI_API_KEY")"
[[ -z "$val5" ]] || {{ echo "Expected empty string for missing file, got: $val5"; exit 5; }}

echo "ALL_TESTS_PASSED"
""")
        runner.chmod(0o755)

        result = subprocess.run([BASH, runner.as_posix()], capture_output=True, text=True, check=False)
        assert result.returncode == 0, f"stdout: {result.stdout}, stderr: {result.stderr}"
        assert "ALL_TESTS_PASSED" in result.stdout


if __name__ == "__main__":
    test_upsert_env_value_handles_delimiters_and_literal_keys()
    print("env-generator.sh: passed.")
    test_ods_macos_upsert_env_value_handles_delimiters_and_literal_keys()
    print("ods-macos.sh: passed.")
    test_upsert_env_value_preserves_recovery_after_failed_restore()
    print("Both helpers: write/backup/restore fault custody passed.")
    test_read_env_value_selects_last_assignment()
    print("read_env_value: passed.")
