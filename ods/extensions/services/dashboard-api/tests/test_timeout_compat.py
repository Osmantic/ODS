"""Run unchanged on Python 3.10 and 3.11+: expiry is not caller cancellation."""
import asyncio
import pytest
from fastapi import HTTPException
from routers import pixel, pixel_sharing


@pytest.mark.parametrize("module", [pixel, pixel_sharing])
def test_timeout_expires_and_runs_cleanup(module):
    async def run():
        cleaned = []
        with pytest.raises(asyncio.TimeoutError):
            async with module.async_timeout(0.01):
                try:
                    await asyncio.sleep(30)
                finally:
                    cleaned.append(True)
        assert cleaned == [True]
        async with module.async_timeout(1):
            await asyncio.sleep(0)
    asyncio.run(run())


@pytest.mark.parametrize("module", [pixel, pixel_sharing])
def test_external_cancel_stays_cancelled(module):
    async def run():
        entered = asyncio.Event()
        async def operation():
            async with module.async_timeout(30):
                entered.set()
                await asyncio.sleep(30)
        task = asyncio.create_task(operation())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())


def test_sharing_body_maps_python310_timeout_to_bounded_error(monkeypatch):
    timeout = pixel_sharing.async_timeout
    monkeypatch.setattr(pixel_sharing, "async_timeout", lambda _: timeout(0.01))
    class SlowRequest:
        async def stream(self):
            await asyncio.sleep(30)
            yield b"{}"
    with pytest.raises(HTTPException) as error:
        asyncio.run(pixel_sharing._body(SlowRequest(), "issue"))
    assert error.value.status_code == 400
