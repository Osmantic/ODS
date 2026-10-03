import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "bootstrap-upgrade.sh"
BASH = shutil.which("bash")

FUNCS = [
    "write_status",
    "model_sha256",
    "verify_model_integrity",
    "start_download_monitor",
    "stop_download_monitor",
]


def extract_function(text, name):
    start = text.index(f"{name}() {{")
    end = text.index("\n}\n\n", start) + len("\n}\n")
    return text[start:end]


def build_harness(tmpdir):
    text = SCRIPT.read_text()
    parts = ["#!/usr/bin/env bash\n", "set -u\n"]
    parts.append('log() { printf "%s\\n" "$*" >&2; }\n')
    parts.append('MONITOR_PID=""\n')
    parts.append('monitor_download() { while :; do sleep 0.05; done; }\n')
    for name in FUNCS:
        parts.append(extract_function(text, name))
        parts.append("\n")
    harness = Path(tmpdir) / "harness.sh"
    harness.write_text("".join(parts))
    return harness


def run_bash(harness, script, env=None, timeout=30):
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(
        [BASH, "-c", harness.read_text() + "\n" + script],
        capture_output=True, text=True, env=full_env, timeout=timeout,
    )


class BootstrapStatusChecksumTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ods-boot-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.harness = build_harness(self.tmp)

    def test_write_status_concurrent_atomic_json(self):
        status_file = self.tmp / "status.json"
        script = textwrap.dedent(f'''
            set -e
            STATUS_FILE="{status_file}"
            FULL_GGUF_FILE='model"quote.gguf'
            export STATUS_FILE FULL_GGUF_FILE
            for i in $(seq 1 20); do
                ( write_status "downloading" "$i" "$i" "100" "0" "eta$i" ) &
            done
            wait
            ls -1 "{status_file}".tmp.* 2>/dev/null && exit 1
            exit 0
        ''')
        r = run_bash(self.harness, script)
        self.assertEqual(r.returncode, 0, r.stderr)
        leftovers = list(self.tmp.glob("status.json.tmp.*"))
        self.assertEqual(leftovers, [])
        data = json.loads(status_file.read_text())
        self.assertEqual(data["status"], "downloading")
        self.assertEqual(data["model"], 'model"quote.gguf')
        self.assertIn("updatedAt", data)

    def test_write_status_reader_never_sees_partial(self):
        status_file = self.tmp / "status.json"
        script = textwrap.dedent(f'''
            set -e
            STATUS_FILE="{status_file}"
            FULL_GGUF_FILE="m.gguf"
            export STATUS_FILE FULL_GGUF_FILE
            write_status "downloading" 0 0 100 0 ""
            (
              for i in $(seq 1 200); do
                write_status "downloading" "$i" "$i" 100 0 "e$i"
              done
            ) &
            writer=$!
            for i in $(seq 1 200); do
              if [[ -f "$STATUS_FILE" ]]; then
                python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$STATUS_FILE" || exit 1
              fi
            done
            wait $writer
            python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$STATUS_FILE"
        ''')
        r = run_bash(self.harness, script, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_stop_download_monitor_joins_before_final_state(self):
        status_file = self.tmp / "status.json"
        script = textwrap.dedent(f'''
            set -e
            STATUS_FILE="{status_file}"
            FULL_GGUF_FILE="m.gguf"
            export STATUS_FILE FULL_GGUF_FILE
            write_status "downloading" 0 0 100 0 ""
            (
              for i in $(seq 1 50); do
                write_status "downloading" "$i" "$i" 100 0 "e$i"
                sleep 0.02
              done
            ) &
            MONITOR_PID=$!
            sleep 0.1
            stop_download_monitor
            write_status "verifying" 100 100 100 0 ""
            python3 -c "import json,sys; d=json.load(open(sys.argv[1])); assert d['status']=='verifying', d" "$STATUS_FILE"
        ''')
        r = run_bash(self.harness, script, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_model_sha256_known_file(self):
        target = self.tmp / "hello.txt"
        target.write_bytes(b"hello\n")
        script = textwrap.dedent(f'''
            set -e
            model_sha256 "{target}"
        ''')
        r = run_bash(self.harness, script)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(
            r.stdout.strip(),
            "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03",
        )

    def test_verify_model_integrity_mismatch(self):
        target = self.tmp / "hello.txt"
        target.write_bytes(b"hello\n")
        script = textwrap.dedent(f'''
            set -e
            FULL_GGUF_SHA256="deadbeef"
            export FULL_GGUF_SHA256
            if verify_model_integrity "{target}"; then
              echo "unexpected-success"
              exit 1
            fi
            echo "rejected"
        ''')
        r = run_bash(self.harness, script)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("rejected", r.stdout)

    def test_verify_model_integrity_no_hasher_fails(self):
        target = self.tmp / "hello.txt"
        target.write_bytes(b"hello\n")
        empty_bin = self.tmp / "emptybin"
        empty_bin.mkdir()
        script = textwrap.dedent(f'''
            set -e
            FULL_GGUF_SHA256="anything"
            export FULL_GGUF_SHA256
            if verify_model_integrity "{target}"; then
              echo "unexpected-success"
              exit 1
            fi
            echo "rejected"
        ''')
        env = {"PATH": str(empty_bin)}
        r = run_bash(self.harness, script, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("rejected", r.stdout)

    def test_model_sha256_windows_native_uppercase_crlf(self):
        target = self.tmp / "hello.txt"
        target.write_bytes(b"hello\n")
        expected = "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03"
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        (bin_dir / "uname").write_text("#!/usr/bin/env bash\necho MINGW64_NT-10.0\n")
        (bin_dir / "uname").chmod(0o755)
        (bin_dir / "cygpath").write_text(
            "#!/usr/bin/env bash\nprintf 'C:\\\\fake\\\\%s\\n' \"$(basename \"$1\")\"\n"
        )
        (bin_dir / "cygpath").chmod(0o755)
        (bin_dir / "powershell.exe").write_text(
            "#!/usr/bin/env bash\nprintf '%s\\r\\n' \"$(echo '" + expected.upper() + "')\"\n"
        )
        (bin_dir / "powershell.exe").chmod(0o755)
        script = textwrap.dedent(f'''
            set -e
            model_sha256 "{target}"
        ''')
        env = {"PATH": f"{bin_dir}:{os.environ['PATH']}"}
        r = run_bash(self.harness, script, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), expected)

    def test_model_sha256_windows_invalid_digest_fails(self):
        target = self.tmp / "hello.txt"
        target.write_bytes(b"hello\n")
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        (bin_dir / "uname").write_text("#!/usr/bin/env bash\necho MSYS_NT-10.0\n")
        (bin_dir / "uname").chmod(0o755)
        (bin_dir / "cygpath").write_text(
            "#!/usr/bin/env bash\nprintf 'C:\\\\fake\\\\%s\\n' \"$(basename \"$1\")\"\n"
        )
        (bin_dir / "cygpath").chmod(0o755)
        (bin_dir / "powershell.exe").write_text(
            "#!/usr/bin/env bash\nprintf 'not-a-hash\\r\\n'\n"
        )
        (bin_dir / "powershell.exe").chmod(0o755)
        script = textwrap.dedent(f'''
            if model_sha256 "{target}"; then
              echo "unexpected-success"
              exit 1
            fi
            echo "rejected"
        ''')
        env = {"PATH": f"{bin_dir}:{os.environ['PATH']}"}
        r = run_bash(self.harness, script, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("rejected", r.stdout)

    def test_model_sha256_windows_nonzero_exit_fails(self):
        target = self.tmp / "hello.txt"
        target.write_bytes(b"hello\n")
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        (bin_dir / "uname").write_text("#!/usr/bin/env bash\necho CYGWIN_NT-10.0\n")
        (bin_dir / "uname").chmod(0o755)
        (bin_dir / "cygpath").write_text(
            "#!/usr/bin/env bash\nprintf 'C:\\\\fake\\\\%s\\n' \"$(basename \"$1\")\"\n"
        )
        (bin_dir / "cygpath").chmod(0o755)
        (bin_dir / "powershell.exe").write_text(
            "#!/usr/bin/env bash\necho 'boom' >&2\nexit 1\n"
        )
        (bin_dir / "powershell.exe").chmod(0o755)
        script = textwrap.dedent(f'''
            if model_sha256 "{target}"; then
              echo "unexpected-success"
              exit 1
            fi
            echo "rejected"
        ''')
        env = {"PATH": f"{bin_dir}:{os.environ['PATH']}"}
        r = run_bash(self.harness, script, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("rejected", r.stdout)

    def test_model_sha256_path_with_spaces_apostrophe_ampersand(self):
        weird_dir = self.tmp / "weird dir '& more"
        weird_dir.mkdir()
        target = weird_dir / "hello file.txt"
        target.write_bytes(b"hello\n")
        expected = "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03"
        script = textwrap.dedent(f'''
            set -e
            model_sha256 "{target}"
        ''')
        r = run_bash(self.harness, script)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), expected)

    def test_native_path_is_only_environment_data(self):
        target = self.tmp / "model ' & $(touch PWNED).gguf"
        tools = self.tmp / "native-tools"
        tools.mkdir()
        expected = "a" * 64
        scripts = {
            "uname": "echo MINGW64_NT-10.0",
            "cygpath": 'test "$1" = -w || exit 1; printf "%s" "$2"',
            "powershell.exe": (
                'printf "%s" "$ODS_SHA_PATH" > "$PATH_RECEIPT"\n'
                'printf "%s\\n" "$@" > "$ARG_RECEIPT"\n'
                f"printf '%s\\r\\n' '{expected.upper()}'"
            ),
        }
        for name, body in scripts.items():
            tool = tools / name
            tool.write_text(f"#!{BASH}\n{body}\n")
            tool.chmod(0o755)
        path_receipt = self.tmp / "path.txt"
        arg_receipt = self.tmp / "args.txt"
        r = run_bash(self.harness, 'model_sha256 "$TARGET"', env={
            "PATH": f"{tools}:{os.environ['PATH']}",
            "TARGET": str(target), "PATH_RECEIPT": path_receipt.as_posix(),
            "ARG_RECEIPT": arg_receipt.as_posix(),
        })
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), expected)
        self.assertEqual(path_receipt.read_text(), str(target))
        self.assertNotIn(str(target), arg_receipt.read_text())
        self.assertIn("$env:ODS_SHA_PATH", arg_receipt.read_text())


if __name__ == "__main__":
    unittest.main()
