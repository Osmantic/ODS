import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
import sys
import os
from pathlib import Path

dashboard_api_dir = Path(__file__).resolve().parents[1]
if str(dashboard_api_dir) not in sys.path:
    sys.path.insert(0, str(dashboard_api_dir))

os.environ["ODS_SESSION_SECRET"] = "0123456789abcdef0123456789abcdef"

import session_signer
from routers.auth import router

app = FastAPI()
app.include_router(router)

client = TestClient(app)

def test_verify_session_via_bearer():
    token = session_signer.issue(ttl_seconds=3600)
    resp = client.get("/api/auth/verify-session", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is True
    assert data["expires_at"] > 0
