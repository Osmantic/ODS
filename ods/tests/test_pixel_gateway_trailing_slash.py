"""Provider baseUrl target resolution in Pixel runtime gateway."""

import asyncio
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
from pixel_provider.runtime_gateway import pinned_target


@pytest.mark.asyncio
async def test_pinned_target_normalizes_trailing_slash():
    # 1. Base URL with trailing slash on path
    provider_with_slash = {"baseUrl": "http://127.0.0.1:11434/v1/"}
    url, headers, extensions = await pinned_target(provider_with_slash)
    assert url == "http://127.0.0.1:11434/v1/chat/completions"
    assert "//chat/completions" not in url

    # 2. Base URL without trailing slash
    provider_no_slash = {"baseUrl": "http://127.0.0.1:11434/v1"}
    url, headers, extensions = await pinned_target(provider_no_slash)
    assert url == "http://127.0.0.1:11434/v1/chat/completions"

    # 3. Base URL with root slash
    provider_root_slash = {"baseUrl": "http://127.0.0.1:11434/"}
    url, headers, extensions = await pinned_target(provider_root_slash)
    assert url == "http://127.0.0.1:11434/chat/completions"


if __name__ == "__main__":
    asyncio.run(test_pinned_target_normalizes_trailing_slash())
    print("test_pixel_gateway_trailing_slash passed.")
