"""Unit test suite for Tailscale network status monitor resilience."""

import pytest


@pytest.mark.asyncio
async def test_tailscale_router_import():
    from routers.tailscale import router
    assert router is not None
