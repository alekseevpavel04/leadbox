from fastapi.testclient import TestClient

from leadbox.main import app


def test_healthz():
    response = TestClient(app).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_main_app_serves_crm():
    client = TestClient(app, base_url="https://testserver", follow_redirects=False)
    assert client.get("/").headers["location"] == "/login"
    assert client.get("/login").status_code == 200
