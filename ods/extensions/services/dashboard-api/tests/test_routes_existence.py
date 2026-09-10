import pytest
from fastapi.testclient import TestClient
from ods.extensions.services.dashboard_api.main import app

client = TestClient(app)


@pytest.mark.parametrize(
    "endpoint",
    [
        "/api/test/llm",
        "/api/test/rag",
        "/api/test/workflows",
        "/api/test/voice",
    ],
)
def test_test_endpoints(endpoint):
    # Using a dummy key as the test environment usually mocks verify_api_key
    # or we can set a known one.
    response = client.get(endpoint, headers={"X-API-Key": "test-key"})
    # Since we are testing that the routes exist and are reachable
    assert response.status_code in (200, 401)
    if response.status_code == 200:
        assert response.json() == {"success": True}


if __name__ == "__main__":
    pytest.main([__file__])
