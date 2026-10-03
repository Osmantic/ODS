#!/usr/bin/env python3
"""Exercise backup/restore CLI receipts when an rsync transfer fails once."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ODS = Path(__file__).resolve().parents[1]
REAL_RSYNC = shutil.which("rsync")


@unittest.skipUnless(REAL_RSYNC and shutil.which("jq"), "rsync and jq are required")
class TransferFailures(unittest.TestCase):
    def test_transfer_status_reaches_backup_and_restore(self):
        for action in ("backup", "restore"):
            for modern, fail in ((False, True), (True, True), (False, False)):
                with self.subTest(action=action, modern=modern, fail=fail):
                    with tempfile.TemporaryDirectory(prefix="ods-rsync-status-") as tmp:
                        self.check_transfer(Path(tmp), action, modern, fail)

    def check_transfer(self, tmp, action, modern, fail):
        stack = tmp / "stack"
        (stack / "data/open-webui").mkdir(parents=True)
        (stack / "data/open-webui/chat.db").write_text("chat history")
        (stack / "lib").mkdir()
        for name in ("rsync.sh", "backup-paths.sh"):
            shutil.copy2(ODS / "lib" / name, stack / "lib" / name)
        env = {**os.environ, "ODS_DIR": str(stack)}
        if action == "restore":
            created = subprocess.run(
                ["bash", str(ODS / "ods-backup.sh"), "--type", "user-data"],
                env=env, capture_output=True, text=True, check=True,
            )
            self.assertIn("Backup complete:", created.stdout)
            backup_id = next((stack / ".backups").iterdir()).name
            (stack / "data/open-webui/chat.db").write_text("changed after backup")
            command = ["bash", str(ODS / "ods-restore.sh"), "-f", backup_id]
        else:
            command = ["bash", str(ODS / "ods-backup.sh"), "--type", "user-data"]

        bindir = tmp / "bin"
        bindir.mkdir()
        calls = tmp / "transfers"
        wrapper = bindir / "rsync"
        wrapper.write_text("""#!/usr/bin/env python3
import os, pathlib, sys
if sys.argv[1:] == ['--help']:
    print('info=progress2' if os.environ['ODS_TEST_MODERN'] == '1' else '--progress')
    raise SystemExit(0)
# Checksum verification is a read-only dry run, not a retried transfer.
if '--dry-run' in sys.argv[1:]:
    os.execv(os.environ['ODS_TEST_REAL_RSYNC'], ['rsync', *sys.argv[1:]])
calls = pathlib.Path(os.environ['ODS_TEST_TRANSFERS'])
first = not calls.exists()
with calls.open('a') as stream:
    stream.write('transfer\\n')
if first and os.environ['ODS_TEST_FAIL'] == '1':
    print('rsync: source vanished during transfer (code 24)', file=sys.stderr)
    raise SystemExit(24)
os.execv(os.environ['ODS_TEST_REAL_RSYNC'], ['rsync', *sys.argv[1:]])
""")
        wrapper.chmod(0o755)
        env.update(
            PATH=f"{bindir}:{env['PATH']}", ODS_TEST_REAL_RSYNC=REAL_RSYNC,
            ODS_TEST_TRANSFERS=str(calls), ODS_TEST_MODERN=str(int(modern)),
            ODS_TEST_FAIL=str(int(fail)),
        )
        result = subprocess.run(command, env=env, text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 24 if fail else 0, result.stdout + result.stderr)
        self.assertEqual(calls.read_text().splitlines(), ["transfer"])
        if fail:
            self.assertIn("source vanished during transfer (code 24)", result.stderr)
            self.assertNotIn("Backup complete:", result.stdout)
            self.assertNotIn("Restore complete", result.stdout)
        elif action == "restore":
            self.assertEqual((stack / "data/open-webui/chat.db").read_text(), "chat history")
        else:
            saved = next((stack / ".backups").glob("*/data/open-webui/chat.db"))
            self.assertEqual(saved.read_text(), "chat history")


if __name__ == "__main__":
    unittest.main()
