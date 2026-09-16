import pytest
import sys
from pathlib import Path

dashboard_api_dir = Path(__file__).resolve().parents[1]
if str(dashboard_api_dir) not in sys.path:
    sys.path.insert(0, str(dashboard_api_dir))

from routers.updates import _build_version_result

def test_prerelease_version_parsing():
    # Comparing 0.3.0-rc1 against 0.3.1
    res = _build_version_result("0.3.0-rc1", {"latest": "0.3.1"})
    assert res["update_available"] is True

    # Comparing 0.3.2 against 0.3.2-rc2
    res2 = _build_version_result("0.3.2", {"latest": "0.3.2"})
    assert res2["update_available"] is False
