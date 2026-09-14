"""Unit test suite for usage metrics router resilience."""

import pytest


@pytest.mark.asyncio
async def test_usage_router_import():
    from routers.usage import router
    assert router is not None
