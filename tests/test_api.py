from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client(tmp_path: Path):
    app = create_app(tmp_path / "test.db")
    with TestClient(app) as test_client:
        yield test_client


def create_sample_issue(client: TestClient, **overrides):
    payload = {
        "title": "Payment button does not respond",
        "description": "Reproduced in the mobile checkout flow.",
        "priority": "high",
        **overrides,
    }
    return client.post("/api/issues", json=payload)


def test_health_endpoint(client: TestClient):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_and_list_issue(client: TestClient):
    created = create_sample_issue(client)
    assert created.status_code == 201
    assert created.json()["status"] == "open"

    listed = client.get("/api/issues")
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["title"] == "Payment button does not respond"


def test_filter_and_search(client: TestClient):
    create_sample_issue(client, title="Checkout crash", priority="critical")
    create_sample_issue(client, title="Profile typo", priority="low")

    critical = client.get("/api/issues", params={"priority": "critical"})
    assert [issue["title"] for issue in critical.json()] == ["Checkout crash"]

    searched = client.get("/api/issues", params={"q": "profile"})
    assert [issue["title"] for issue in searched.json()] == ["Profile typo"]


def test_update_issue_status(client: TestClient):
    issue_id = create_sample_issue(client).json()["id"]
    response = client.patch(
        f"/api/issues/{issue_id}", json={"status": "resolved"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "resolved"


def test_delete_issue(client: TestClient):
    issue_id = create_sample_issue(client).json()["id"]
    response = client.delete(f"/api/issues/{issue_id}")
    assert response.status_code == 204
    assert client.get("/api/issues").json() == []


def test_stats(client: TestClient):
    first_id = create_sample_issue(client, priority="critical").json()["id"]
    create_sample_issue(client, title="Minor layout issue", priority="low")
    client.patch(f"/api/issues/{first_id}", json={"status": "in_progress"})

    stats = client.get("/api/stats").json()
    assert stats == {
        "total": 2,
        "open": 1,
        "in_progress": 1,
        "resolved": 0,
        "critical": 1,
    }


def test_validation_rejects_short_title(client: TestClient):
    response = client.post(
        "/api/issues",
        json={"title": "No", "description": "", "priority": "medium"},
    )
    assert response.status_code == 422
