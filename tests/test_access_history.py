import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from app.access import COOKIE, create_user, digest_token
from app.db import connect, initialise
from app.main import create_app

PASSWORD = "local-test-password-only"


@pytest.fixture
def private(tmp_path, monkeypatch):
    monkeypatch.setenv("ISSUEPILOT_AUTH_REQUIRED", "1")
    app = create_app(tmp_path / "private.db")
    with TestClient(app, base_url="https://testserver") as client:
        for role in ("admin", "editor", "viewer"):
            create_user(app.state.db_path, role, PASSWORD, role)
        yield client


def sign_in(client, role="admin"):
    result = client.post(
        "/api/auth/login",
        json={"username": role, "password": PASSWORD},
        headers={"X-IssuePilot-Request": "1"},
    )
    assert result.status_code == 200
    client.headers["X-CSRF-Token"] = result.json()["csrf"]
    return result


@pytest.mark.parametrize(
    "path", ["/api/issues", "/api/stats", "/api/activity", "/api/issues/1/history"]
)
def test_private_reads_require_session(private, path):
    assert private.get(path).status_code == 401
    assert private.get("/api/health").status_code == 200
    assert private.get("/api/auth/session").json() == {"required": True, "user": None}


def test_cookie_csrf_logout_and_no_store(private):
    response = sign_in(private)
    cookie = response.headers["set-cookie"].lower()
    assert all(
        value in cookie
        for value in ("httponly", "secure", "samesite=strict", "max-age=28800")
    )
    assert response.headers["cache-control"] == "no-store"
    assert private.get("/api/auth/session").json()["user"]["role"] == "admin"
    saved_token = private.cookies.get(COOKIE)
    assert (
        private.post(
            "/api/issues",
            json={"title": "Blocked write"},
            headers={"X-CSRF-Token": "wrong"},
        ).status_code
        == 403
    )
    assert private.get("/api/issues").json() == []
    assert (
        private.post("/api/auth/logout", headers={"X-CSRF-Token": "wrong"}).status_code
        == 403
    )
    assert private.post("/api/auth/logout").status_code == 204
    private.cookies.set(COOKIE, saved_token)
    assert private.get("/api/issues").status_code == 401


@pytest.mark.parametrize(
    "role,can_write,can_delete",
    [("admin", True, True), ("editor", True, False), ("viewer", False, False)],
)
def test_role_matrix(private, role, can_write, can_delete):
    sign_in(private)
    issue = private.post("/api/issues", json={"title": "Original issue"}).json()
    sign_in(private, role)
    assert private.get("/api/issues").status_code == 200
    assert private.get(f"/api/issues/{issue['id']}/history").status_code == 200
    assert private.post("/api/issues", json={"title": "Another issue"}).status_code == (
        201 if can_write else 403
    )
    assert private.patch(
        f"/api/issues/{issue['id']}", json={"status": "resolved"}, headers={"X-Issue-Version": "1"}
    ).status_code == (200 if can_write else 403)
    assert private.delete(f"/api/issues/{issue['id']}", headers={"X-Issue-Version": "2"}).status_code == (
        204 if can_delete else 403
    )
    if not can_write:
        assert private.get("/api/issues").json() == [issue]
        assert len(private.get("/api/activity").json()) == 1


def test_expired_and_rotated_sessions(private):
    sign_in(private)
    old = private.cookies.get(COOKIE)
    sign_in(private)
    current = private.cookies.get(COOKIE)
    assert old != current
    with connect(private.app.state.db_path) as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM sessions WHERE token_hash = ?", (digest_token(old),)
            ).fetchone()
            is None
        )
        connection.execute("UPDATE sessions SET expires_at = ?", (time.time() - 1,))
    assert private.get("/api/issues").status_code == 401
    assert (
        private.post("/api/issues", json={"title": "Expired write"}).status_code == 401
    )


def test_login_validation_throttling_and_no_plaintext(private):
    body = {"username": "admin", "password": "incorrect"}
    assert private.post("/api/auth/login", json=body).status_code == 403
    for _ in range(5):
        assert (
            private.post(
                "/api/auth/login", json=body, headers={"X-IssuePilot-Request": "1"}
            ).status_code
            == 401
        )
    assert (
        private.post(
            "/api/auth/login", json=body, headers={"X-IssuePilot-Request": "1"}
        ).status_code
        == 429
    )
    sign_in(private, "editor")
    with connect(private.app.state.db_path) as connection:
        values = [
            row[0] for row in connection.execute("SELECT password_hash FROM users")
        ]
        assert len(set(values)) == 3
        assert not any(PASSWORD in value for value in values)
        assert connection.execute("SELECT token_hash FROM sessions").fetchone()[
            0
        ] != private.cookies.get(COOKIE)


def test_history_snapshots_survive_deletion(private):
    sign_in(private, "editor")
    original = private.post("/api/issues", json={"title": "Checkout issue"}).json()
    url = f"/api/issues/{original['id']}"
    updated = private.patch(
        url, json={"status": "resolved", "description": "Fixed the cause"}, headers={"X-Issue-Version": "1"}
    ).json()
    sign_in(private)
    assert private.delete(url, headers={"X-Issue-Version": str(updated["version"])}).status_code == 204
    events = private.get(url + "/history").json()
    assert [e["action"] for e in events] == ["deleted", "updated", "created"]
    assert [e["actor"] for e in events] == ["admin", "editor", "editor"]
    assert events[0]["before"] == updated and events[0]["after"] is None
    assert events[1]["before"] == original and events[1]["after"] == updated
    assert events[2]["before"] is None and events[2]["after"] == original
    page = private.get("/api/activity?limit=2").json()
    older = private.get(f"/api/activity?before={page[-1]['id']}&limit=2").json()
    assert page + older == events
    assert private.get("/api/activity?limit=101").status_code == 422
    assert private.get("/api/issues/999/history").status_code == 404


@pytest.mark.parametrize("operation", ["create", "update", "delete"])
def test_audit_failure_rolls_back_issue_mutation(private, monkeypatch, operation):
    sign_in(private)
    original = private.post("/api/issues", json={"title": "Keep this issue"}).json()

    def fail(*args):
        raise sqlite3.OperationalError("Simulated audit storage failure")

    monkeypatch.setattr("app.main.record", fail)
    with pytest.raises(sqlite3.OperationalError):
        if operation == "create":
            private.post("/api/issues", json={"title": "Must not persist"})
        elif operation == "update":
            private.patch(f"/api/issues/{original['id']}", json={"status": "resolved"}, headers={"X-Issue-Version": "1"})
        else:
            private.delete(f"/api/issues/{original['id']}", headers={"X-Issue-Version": "1"})
    assert private.get("/api/issues").json() == [original]
    assert len(private.get("/api/activity").json()) == 1


def test_existing_database_migrates_without_invented_history(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE issues (id INTEGER PRIMARY KEY, title TEXT, description TEXT, priority TEXT, status TEXT, created_at TEXT, updated_at TEXT)"
        )
        connection.execute(
            "INSERT INTO issues VALUES (1, 'Old issue', '', 'low', 'open', '2026-01-01', '2026-01-01')"
        )
    initialise(path)
    initialise(path)
    with TestClient(create_app(path)) as client:
        assert client.get("/api/issues").json()[0]["title"] == "Old issue"
        assert client.get("/api/issues/1/history").json() == []
        client.patch("/api/issues/1", json={"status": "resolved"}, headers={"X-Issue-Version": "1"})
        assert client.get("/api/activity").json()[0]["actor"] == "Public demo"
        assert client.get("/api/auth/session").json()["required"] is False


def test_invalid_access_configuration_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("ISSUEPILOT_AUTH_REQUIRED", "true")
    with pytest.raises(ValueError):
        create_app(tmp_path / "bad.db")


def test_anonymous_mutations_and_missing_csrf_are_rejected(private):
    assert private.post("/api/issues", json={"title": "Not allowed"}).status_code == 401
    assert (
        private.patch("/api/issues/1", json={"status": "resolved"}, headers={"X-Issue-Version": "1"}).status_code == 401
    )
    assert private.delete("/api/issues/1").status_code == 401
    sign_in(private)
    del private.headers["X-CSRF-Token"]
    assert private.post("/api/issues", json={"title": "Not allowed"}).status_code == 403
    assert private.get("/api/activity").json() == []


def test_reset_during_login_cannot_create_stale_session(private, monkeypatch):
    from app import access

    original = access.password_hash

    def reset_during_verification(password, salt=None):
        result = original(password, salt)
        with connect(private.app.state.db_path) as connection:
            connection.execute(
                "UPDATE users SET password_hash = ? WHERE username = 'admin'",
                (original("replacement-password"),),
            )
        return result

    monkeypatch.setattr(access, "password_hash", reset_during_verification)
    result = private.post(
        "/api/auth/login",
        json={"username": "admin", "password": PASSWORD},
        headers={"X-IssuePilot-Request": "1"},
    )
    assert result.status_code == 401
    with connect(private.app.state.db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


@pytest.mark.parametrize("action", ["reset", "role"])
def test_cli_changes_revoke_sessions(private, monkeypatch, action):
    from app.users import main

    sign_in(private, "editor")
    monkeypatch.setenv("ISSUEPILOT_DB", str(private.app.state.db_path))
    args = ["users", action, "editor"] + (
        ["--role", "viewer"] if action == "role" else []
    )
    monkeypatch.setattr("sys.argv", args)
    monkeypatch.setattr("getpass.getpass", lambda prompt: "changed-test-password")
    main()
    assert private.get("/api/issues").status_code == 401
    password = "changed-test-password" if action == "reset" else PASSWORD
    result = private.post(
        "/api/auth/login",
        json={"username": "editor", "password": password},
        headers={"X-IssuePilot-Request": "1"},
    )
    assert result.status_code == 200
    assert result.json()["role"] == ("viewer" if action == "role" else "editor")
