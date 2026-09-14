"""Unit test suite for updates router resilience."""

import pytest


@pytest.mark.asyncio
async def test_updates_router_import():
    from routers.updates import router
    assert router is not None
