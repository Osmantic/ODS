"""Unit test suite for model catalog resolver resilience."""

import pytest


@pytest.mark.asyncio
async def test_models_router_import():
    from routers.models import router
    assert router is not None
