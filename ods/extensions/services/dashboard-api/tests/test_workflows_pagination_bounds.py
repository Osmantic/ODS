import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
import sys
from pathlib import Path

dashboard_api_dir = Path(__file__).resolve().parents[1]
if str(dashboard_api_dir) not in sys.path:
    sys.path.insert(0, str(dashboard_api_dir))

from routers.workflows import router
from security import verify_api_key

app = FastAPI()
app.include_router(router)
app.dependency_overrides[verify_api_key] = lambda: "test-api-key"

client = TestClient(app)

def test_workflow_executions_limit_negative():
    resp = client.get("/api/workflows/test-wf/executions?limit=-1")
    assert resp.status_code == 422

def test_workflow_executions_limit_exceeds_max():
    resp = client.get("/api/workflows/test-wf/executions?limit=101")
    assert resp.status_code == 422
