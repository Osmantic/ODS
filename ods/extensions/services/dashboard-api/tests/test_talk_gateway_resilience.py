"""Unit test suite for Talk / Voice service gateway resilience."""

import pytest


@pytest.mark.asyncio
async def test_talk_router_import():
    from routers.talk import router
    assert router is not None
