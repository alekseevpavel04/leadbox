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


def test_healthz_answers_head_for_uptime_monitors():
    assert TestClient(app).head("/healthz").status_code == 200


def test_api_docs_are_not_public():
    client = TestClient(app)
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404
