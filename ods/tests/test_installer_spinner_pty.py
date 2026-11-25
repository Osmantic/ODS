"""Run the real spinner on a PTY and reject physical-row overflow."""
import errno
import fcntl
import os
from pathlib import Path
import pty
import re
import select
import signal
import struct
import subprocess
import termios
import tempfile
import time
import unicodedata
import unittest


UI = Path(__file__).resolve().parents[1] / "installers/lib/ui.sh"
CSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
SCRIPT = r'''
set -uo pipefail
ODS_UI_MODE=cinematic; INTERACTIVE=true; LOG_FILE=/dev/null
GRN=$'\033[32m'; BGRN=$'\033[1;32m'; DGRN=$'\033[2;32m'; NC=$'\033[0m'
AMB=$'\033[33m'; RED=$'\033[31m'
source "$1"
ods_select_lore_messages() { LORE_MESSAGES=("$TEST_LORE"); }
command sleep 60 & task_pid=$!
trap 'kill -TERM "$task_pid" 2>/dev/null || :; wait "$task_pid" 2>/dev/null || :' EXIT
ticks=0
resize_columns=$3
sleep() {
  ticks=$((ticks+1))
  if ((ticks==16 && resize_columns>0)); then
    stty cols "$resize_columns" <&1
    printf '\r\nRESIZED\r\n'
  fi
  if ((ticks>=32)); then
    kill -TERM "$task_pid"
    wait "$task_pid" 2>/dev/null || :
  fi
}
rc=0
spin_task "$task_pid" "$2" "${4:-}" 10 || rc=$?
printf '\r\nEXIT=%s\r\n' "$rc"
'''


def cells(text):
    """Independent width estimate; combining characters do not consume cells."""
    return sum(0 if unicodedata.combining(char) else
               2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
               for char in text)


def run_pty(columns, label, lore, locale="C.UTF-8", resize=0, part=""):
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, columns, 0, 0))
    proc = subprocess.Popen(
        ["bash", "-c", SCRIPT, "spinner-test", str(UI), label, str(resize), part],
        stdin=subprocess.DEVNULL, stdout=slave, stderr=slave, start_new_session=True,
        env={**os.environ, "LC_ALL": locale, "LANG": locale, "TERM": "xterm",
             "COLUMNS": "160", "TEST_LORE": lore},
    )
    os.close(slave)
    data = bytearray()
    deadline = time.monotonic() + 15
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.1)
            if ready:
                try:
                    chunk = os.read(master, 65536)
                except OSError as error:
                    if error.errno == errno.EIO:
                        break
                    raise
                if not chunk:
                    break
                data.extend(chunk)
            elif proc.poll() is not None:
                break
        else:
            os.killpg(proc.pid, signal.SIGKILL)
            raise AssertionError("spinner did not terminate")
    finally:
        os.close(master)
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=3)
    return proc.returncode, data.decode("utf-8")


class SpinnerTerminalTests(unittest.TestCase):
    def check_frames(self, columns, label, lore, locale="C.UTF-8", resize=0, part=""):
        code, raw = run_pty(columns, label, lore, locale, resize, part)
        self.assertEqual(code, 0, raw[-1000:])
        self.assertIn("EXIT=143", raw)
        self.assertNotIn("\t", raw)
        plain = CSI.sub("", raw)
        for row in re.split(r"[\r\n]", plain):
            if row == "RESIZED":
                columns = resize
            elif row and not row.startswith("EXIT="):
                self.assertLessEqual(cells(row), columns - 1, repr(row))
        return raw

    def test_long_labels_and_lore_fit_actual_output_terminal(self):
        # Deliberately wrong COLUMNS in run_pty proves the real PTY is queried.
        for width in (1, 2, 8, 12, 20, 40, 80, 160):
            with self.subTest(width=width):
                self.check_frames(
                    width,
                    "Downloading Qwen3.5-2B-Q4_K_M.gguf -- 397 MB / 1221 MB (32%)",
                    "A long lore sentence that must stay on its physical row. " * 3,
                )

    def test_unicode_and_control_characters_are_bounded(self):
        for locale in ("C.UTF-8", "C"):
            with self.subTest(locale=locale):
                self.check_frames(
                    40, "Downloading 日本語 café 😀 e\u0301 " * 4,
                    "Wide 日本語 😀\ttext\nwith\x1b[31m controls " * 3, locale,
                )

    def test_new_frames_follow_a_terminal_width_change(self):
        self.check_frames(80, "Long download label " * 5, "Long lore " * 12, resize=20)

    def test_long_model_names_do_not_hide_live_progress(self):
        with tempfile.NamedTemporaryFile() as part:
            part.truncate(5 * 1024 * 1024)
            for width in (20, 40, 80):
                with self.subTest(width=width):
                    raw = self.check_frames(
                        width, "Downloading a very long model filename.gguf",
                        "Ordinary lore", part=part.name,
                    )
                    self.assertIn("50%", raw)
                    if width >= 40:
                        self.assertIn("5 MB / 10 MB (50%)", raw)

    def test_cinematic_lore_returns_to_progress_row(self):
        raw = self.check_frames(80, "Downloading model.gguf", "Ordinary lore")
        self.assertIn("« Ordinary lore »", raw)
        self.assertIn("\n\x1b[K\x1b[A\r", raw)
        self.assertIn("\x1b[A", raw)


if __name__ == "__main__":
    unittest.main()
