"""The Docker preflight owns the real child it starts, including early exits."""

import asyncio
import sys

import httpx
import pytest


@pytest.mark.parametrize("interruption", ["timeout", "cancel"])
def test_preflight_reaps_interrupted_child(monkeypatch, interruption):
    import main
    from security import DASHBOARD_API_KEY

    original_exists = main.os.path.exists
    monkeypatch.setattr(
        main.os.path, "exists",
        lambda path: False if path == "/.dockerenv" else original_exists(path),
    )
    original_spawn = asyncio.create_subprocess_exec
    original_wait_for = asyncio.wait_for

    async def exercise():
        spawned = asyncio.Event()
        children = []

        async def spawn(*args, **kwargs):
            assert args == ("docker", "--version")
            # No Docker, host service, shell, network or installation is touched.
            child = await original_spawn(
                sys.executable, "-c", "import time; time.sleep(60)", **kwargs,
            )
            children.append(child)
            spawned.set()
            return child

        async def bounded_wait(awaitable, timeout):
            return await original_wait_for(awaitable, 0.05 if timeout == 5 else timeout)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        monkeypatch.setattr(asyncio, "wait_for", bounded_wait)
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            request = asyncio.create_task(client.get(
                "/api/preflight/docker",
                headers={"Authorization": f"Bearer {DASHBOARD_API_KEY}"},
            ))
            try:
                await original_wait_for(spawned.wait(), 5)
                if interruption == "cancel":
                    request.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await request
                else:
                    response = await original_wait_for(request, 5)
                    assert response.status_code == 200
                    assert response.json() == {"available": False, "error": "Docker check timed out"}
                # returncode is published by asyncio's child watcher after reaping.
                # Assert before fixture cleanup so the old code fails visibly.
                assert children[0].returncode is not None, "preflight leaked its Docker probe child"
            finally:
                if not request.done():
                    request.cancel()
                    try:
                        await request
                    except asyncio.CancelledError:
                        pass
                for child in children:
                    if child.returncode is None:
                        child.kill()
                    await child.communicate()

    asyncio.run(exercise())
