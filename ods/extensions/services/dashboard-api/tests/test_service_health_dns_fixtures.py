import pytest
from pathlib import Path
import sys

dashboard_api_dir = Path(__file__).resolve().parents[1]
if str(dashboard_api_dir) not in sys.path:
    sys.path.insert(0, str(dashboard_api_dir))

from service_health_dns import ServiceHealthResolver

@pytest.mark.asyncio
async def test_resolver_lifecycle():
    resolver = ServiceHealthResolver(workers=2)
    assert resolver._closed is False
    await resolver.close()
    assert resolver._closed is True

@pytest.mark.asyncio
async def test_closed_resolver_raises_runtime_error():
    resolver = ServiceHealthResolver(workers=2)
    await resolver.close()
    with pytest.raises(RuntimeError, match="closed"):
        await resolver.resolve("127.0.0.1", 80)
