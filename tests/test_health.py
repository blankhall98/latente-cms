from fastapi.testclient import TestClient
from app.main import app
from app.core.settings import settings

client = TestClient(app)

def test_ping():
    r = client.get(f"{settings.API_V1_STR}/health/ping")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
