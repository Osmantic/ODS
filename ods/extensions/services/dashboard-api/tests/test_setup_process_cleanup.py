"""Exercise diagnostic stream cleanup with real Bash and Python descendants."""

import asyncio
import os
import signal
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux process-lifecycle integration")


def running(pid):
    try:
        status = Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1]
        return not status.startswith("Z ")
    except (FileNotFoundError, ProcessLookupError):
        return False


@pytest.mark.parametrize("parent_waits", [True, False], ids=["waiting-shell", "exited-shell"])
@pytest.mark.parametrize("close_mode", ["close", "cancel"])
def test_diagnostic_disconnect_stops_descendants(tmp_path, monkeypatch, parent_waits, close_mode):
    from routers import setup

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    child = tmp_path / "diagnostic_child.py"
    child.write_text("import os,time\nprint(os.getpid(), flush=True)\ntime.sleep(30)\n")
    script = scripts / "ods-test-functional.sh"
    script.write_text('#!/bin/bash\n"$DIAG_PYTHON" "$DIAG_CHILD" &\n' + ("wait\n" if parent_waits else "exit 0\n"))
    monkeypatch.setattr(setup, "INSTALL_DIR", str(tmp_path))
    monkeypatch.setenv("DIAG_PYTHON", sys.executable)
    monkeypatch.setenv("DIAG_CHILD", str(child))

    async def scenario():
        response = await setup.run_setup_diagnostics(api_key="fixture")
        iterator = response.body_iterator
        child_pid = int((await asyncio.wait_for(anext(iterator), timeout=3)).strip())
        assert running(child_pid)
        if close_mode == "close":
            stopping = asyncio.create_task(iterator.aclose())
        else:
            stopping = asyncio.create_task(anext(iterator))
            await asyncio.sleep(0)
            stopping.cancel()
        try:
            try:
                await asyncio.wait_for(asyncio.shield(stopping), timeout=0.75)
            except asyncio.CancelledError:
                pass
            except asyncio.TimeoutError:
                pytest.fail("diagnostic descendant kept the closed stream alive")
            for _ in range(50):
                if not running(child_pid):
                    break
                await asyncio.sleep(0.01)
            assert not running(child_pid), "diagnostic child survived the disconnected response"
        finally:
            # This PID came directly from the child started above; baseline
            # failures must not leave the 30-second fixture running.
            if running(child_pid):
                try:
                    os.kill(child_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            await asyncio.wait_for(asyncio.gather(stopping, return_exceptions=True), timeout=3)
            await iterator.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("returncode", [0, 3])
def test_completed_diagnostic_preserves_real_exit_sentinel(tmp_path, monkeypatch, returncode):
    from routers import setup

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "ods-test-functional.sh").write_text(
        f"#!/bin/bash\nprintf 'fixture finished\\n'\nexit {returncode}\n"
    )
    monkeypatch.setattr(setup, "INSTALL_DIR", str(tmp_path))

    async def scenario():
        response = await setup.run_setup_diagnostics(api_key="fixture")
        chunks = [chunk async for chunk in response.body_iterator]
        text = "".join(chunks)
        assert text.startswith("fixture finished\n")
        assert text.count("__ODS_RESULT__:") == 1
        status = "PASS" if returncode == 0 else "FAIL"
        assert text.endswith(f"__ODS_RESULT__:{status}:{returncode}\n")

    asyncio.run(scenario())
