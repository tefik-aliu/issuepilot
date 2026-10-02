from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .access import password_hash, principal, require_write
from .access import router as access_router
from .db import connect, initialise, row_to_dict
from .history import event_dict, record

Priority = Literal["low", "medium", "high", "critical"]
IssueStatus = Literal["open", "in_progress", "resolved"]


class IssueCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=3, max_length=120)
    description: str = Field(default="", max_length=2_000)
    priority: Priority = "medium"


class IssueUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str | None = Field(default=None, min_length=3, max_length=120)
    description: str | None = Field(default=None, max_length=2_000)
    priority: Priority | None = None
    status: IssueStatus | None = None

    @field_validator("title", "description", "priority", "status")
    @classmethod
    def reject_explicit_null(cls, value):
        if value is None:
            raise ValueError("Provided fields cannot be null")
        return value


class Issue(BaseModel):
    id: int
    version: int
    title: str
    description: str
    priority: Priority
    status: IssueStatus
    created_at: str
    updated_at: str


class Stats(BaseModel):
    total: int
    open: int
    in_progress: int
    resolved: int
    critical: int


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_version(existing, expected: str | None) -> None:
    if expected is None:
        raise HTTPException(428, "Read the issue and send its version in X-Issue-Version")
    if not expected.isascii() or not expected.isdecimal() or len(expected) > 19 or int(expected) < 1:
        raise HTTPException(422, "X-Issue-Version must be a positive integer")
    if not existing:
        raise HTTPException(404, "Issue not found")
    if int(expected) != existing["version"]:
        raise HTTPException(409, {
            "code": "version_conflict",
            "message": "This issue changed since you loaded it. Review the latest version before trying again.",
            "current": row_to_dict(existing),
        })


def create_app(db_path: str | Path | None = None) -> FastAPI:
    resolved_db_path = Path(
        db_path or os.getenv("ISSUEPILOT_DB", "data/issuepilot.db")
    ).resolve()
    static_dir = Path(__file__).parent / "static"

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        initialise(resolved_db_path)
        yield

    app = FastAPI(
        title="IssuePilot API",
        version="1.2.0",
        description="A compact issue tracker built as a full-stack portfolio project.",
        lifespan=lifespan,
    )
    app.state.db_path = resolved_db_path
    auth_setting = os.getenv("ISSUEPILOT_AUTH_REQUIRED", "0")
    cookie_setting = os.getenv("ISSUEPILOT_COOKIE_SECURE", "1")
    if auth_setting not in {"0", "1"} or cookie_setting not in {"0", "1"}:
        raise ValueError(
            "ISSUEPILOT_AUTH_REQUIRED and ISSUEPILOT_COOKIE_SECURE must be 0 or 1"
        )
    app.state.auth_required = auth_setting == "1"
    app.state.secure_cookies = os.getenv("ISSUEPILOT_COOKIE_SECURE", "1") != "0"
    app.state.dummy_password_hash = password_hash(os.urandom(32).hex())
    app.include_router(access_router)

    @app.middleware("http")
    async def private_responses(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    @app.get("/api/activity")
    def activity(
        request: Request,
        before: int | None = Query(None, ge=1),
        limit: int = Query(20, ge=1, le=100),
    ):
        principal(request)
        with connect(app.state.db_path) as connection:
            rows = connection.execute(
                "SELECT * FROM issue_events WHERE (? IS NULL OR id < ?) ORDER BY id DESC LIMIT ?",
                (before, before, limit),
            ).fetchall()
        return [event_dict(row) for row in rows]

    @app.get("/api/issues/{issue_id}/history")
    def history(
        issue_id: int,
        request: Request,
        before: int | None = Query(None, ge=1),
        limit: int = Query(20, ge=1, le=100),
    ):
        principal(request)
        with connect(app.state.db_path) as connection:
            exists = connection.execute(
                "SELECT 1 FROM issues WHERE id = ? UNION ALL SELECT 1 FROM issue_events WHERE issue_id = ? LIMIT 1",
                (issue_id, issue_id),
            ).fetchone()
            if not exists:
                raise HTTPException(404, "Issue not found")
            rows = connection.execute(
                "SELECT * FROM issue_events WHERE issue_id = ? AND (? IS NULL OR id < ?) ORDER BY id DESC LIMIT ?",
                (issue_id, before, before, limit),
            ).fetchall()
        return [event_dict(row) for row in rows]

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/issues", response_model=list[Issue])
    def list_issues(
        request: Request,
        issue_status: IssueStatus | None = Query(default=None, alias="status"),
        priority: Priority | None = None,
        q: str | None = Query(default=None, max_length=100),
    ) -> list[dict]:
        principal(request)
        clauses: list[str] = []
        params: list[str] = []

        if issue_status:
            clauses.append("status = ?")
            params.append(issue_status)
        if priority:
            clauses.append("priority = ?")
            params.append(priority)
        if q:
            clauses.append("(LOWER(title) LIKE ? OR LOWER(description) LIKE ?)")
            search = f"%{q.lower()}%"
            params.extend([search, search])

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"""
            SELECT * FROM issues
            {where}
            ORDER BY
                CASE priority
                    WHEN 'critical' THEN 1
                    WHEN 'high' THEN 2
                    WHEN 'medium' THEN 3
                    ELSE 4
                END,
                created_at DESC
        """
        with connect(app.state.db_path) as connection:
            rows = connection.execute(query, params).fetchall()
        return [row_to_dict(row) for row in rows]

    @app.post("/api/issues", response_model=Issue, status_code=status.HTTP_201_CREATED)
    def create_issue(payload: IssueCreate, request: Request) -> dict:
        actor = require_write(request)
        now = utc_now()
        with connect(app.state.db_path) as connection:
            cursor = connection.execute(
                """
                INSERT INTO issues (title, description, priority, status, created_at, updated_at)
                VALUES (?, ?, ?, 'open', ?, ?)
                """,
                (
                    payload.title.strip(),
                    payload.description.strip(),
                    payload.priority,
                    now,
                    now,
                ),
            )
            issue_id = cursor.lastrowid
            row = connection.execute(
                "SELECT * FROM issues WHERE id = ?", (issue_id,)
            ).fetchone()
            record(connection, actor, "created", issue_id, None, row)
        return row_to_dict(row)

    @app.patch("/api/issues/{issue_id}", response_model=Issue)
    def update_issue(issue_id: int, payload: IssueUpdate, request: Request,
                     x_issue_version: str | None = Header(default=None)) -> dict:
        actor = require_write(request)
        updates = payload.model_dump(exclude_unset=True)
        if not updates:
            raise HTTPException(status_code=400, detail="No fields supplied")

        for field in ("title", "description"):
            if field in updates and updates[field] is not None:
                updates[field] = updates[field].strip()

        updates["updated_at"] = utc_now()
        assignments = ", ".join(f"{column} = ?" for column in updates)
        values = list(updates.values()) + [issue_id]

        with connect(app.state.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM issues WHERE id = ?", (issue_id,)
            ).fetchone()
            check_version(existing, x_issue_version)
            connection.execute(f"UPDATE issues SET {assignments}, version = version + 1 WHERE id = ?", values)
            row = connection.execute(
                "SELECT * FROM issues WHERE id = ?", (issue_id,)
            ).fetchone()
            record(connection, actor, "updated", issue_id, existing, row)
        return row_to_dict(row)

    @app.delete("/api/issues/{issue_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_issue(issue_id: int, request: Request,
                     x_issue_version: str | None = Header(default=None)) -> Response:
        actor = require_write(request, admin=True)
        with connect(app.state.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM issues WHERE id = ?", (issue_id,)
            ).fetchone()
            check_version(existing, x_issue_version)
            connection.execute("DELETE FROM issues WHERE id = ?", (issue_id,))
            record(connection, actor, "deleted", issue_id, existing, None)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/api/stats", response_model=Stats)
    def stats(request: Request) -> dict[str, int]:
        principal(request)
        with connect(app.state.db_path) as connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS open,
                    SUM(CASE WHEN status = 'in_progress' THEN 1 ELSE 0 END) AS in_progress,
                    SUM(CASE WHEN status = 'resolved' THEN 1 ELSE 0 END) AS resolved,
                    SUM(CASE WHEN priority = 'critical' THEN 1 ELSE 0 END) AS critical
                FROM issues
                """
            ).fetchone()
        return {key: int(row[key] or 0) for key in row.keys()}

    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    return app


app = create_app()
