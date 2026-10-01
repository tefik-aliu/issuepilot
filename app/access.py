"""Single-workspace access control. Demo mode is deliberately public."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .db import connect

COOKIE = "issuepilot_session"
SESSION_SECONDS = 8 * 60 * 60


def password_hash(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(
        password.encode(),
        salt=bytes.fromhex(salt),
        n=32768,
        r=8,
        p=3,
        maxmem=64 * 1024 * 1024,
    ).hex()
    return f"{salt}:{digest}"


def create_user(db_path: Path, username: str, password: str, role: str) -> None:
    if role not in {"admin", "editor", "viewer"}:
        raise ValueError("Role must be admin, editor or viewer")
    if not 3 <= len(username) <= 64 or not all(
        c.isascii() and (c.isalnum() or c in "._-") for c in username
    ):
        raise ValueError(
            "Username must be 3–64 ASCII letters, digits, dots, underscores or hyphens"
        )
    if not 12 <= len(password) <= 256:
        raise ValueError("Password must be 12–256 characters")
    with connect(db_path) as connection:
        connection.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            (username.lower(), password_hash(password), role),
        )


def digest_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def principal(request: Request) -> dict:
    if not request.app.state.auth_required:
        return {"username": "Public demo", "role": "admin", "csrf": ""}
    token = request.cookies.get(COOKIE, "")
    with connect(request.app.state.db_path) as connection:
        row = connection.execute(
            "SELECT users.username, users.role, sessions.csrf FROM sessions "
            "JOIN users ON users.id = sessions.user_id WHERE token_hash = ? AND expires_at > ?",
            (digest_token(token), time.time()),
        ).fetchone()
    if not row:
        raise HTTPException(401, "Sign in to access this workspace")
    return dict(row)


def require_write(request: Request, *, admin: bool = False) -> dict:
    actor = principal(request)
    if actor["role"] not in ({"admin"} if admin else {"admin", "editor"}):
        raise HTTPException(403, "Your role does not allow this action")
    verify_csrf(request, actor)
    return actor


def verify_csrf(request: Request, actor: dict) -> None:
    if request.app.state.auth_required and not hmac.compare_digest(
        request.headers.get("X-CSRF-Token", ""), actor["csrf"]
    ):
        raise HTTPException(403, "Refresh the page before making changes")


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


router = APIRouter(prefix="/api/auth", tags=["Access"])


@router.get("/session")
def session(request: Request) -> dict:
    try:
        actor = principal(request)
    except HTTPException:
        actor = None
    return {"required": request.app.state.auth_required, "user": actor}


@router.post("/login")
def login(payload: Login, request: Request, response: Response) -> dict:
    if not request.app.state.auth_required:
        raise HTTPException(400, "This is a public demo; sign-in is disabled")
    # A custom header cannot be sent by a cross-origin HTML form. No CORS is enabled.
    if request.headers.get("X-IssuePilot-Request") != "1":
        raise HTTPException(403, "Use the sign-in form")
    now = time.time()
    username = payload.username.strip().lower()
    # Persist the throttle and lock the short transaction to coordinate workers.
    # Bound total hashing work too; deploy behind a reverse-proxy rate limiter.
    with connect(request.app.state.db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "DELETE FROM login_attempts WHERE attempted_at <= ?", (now - 300,)
        )
        attempts = connection.execute(
            "SELECT COUNT(*) AS total, SUM(username = ?) AS account FROM login_attempts",
            (username,),
        ).fetchone()
        blocked = attempts["total"] >= 100 or (attempts["account"] or 0) >= 5
        if not blocked:
            connection.execute(
                "INSERT INTO login_attempts VALUES (?, ?)", (username, now)
            )
        row = connection.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
    if blocked:
        raise HTTPException(
            429,
            "Too many attempts. Try again in five minutes.",
            headers={"Retry-After": "300"},
        )
    stored = row["password_hash"] if row else request.app.state.dummy_password_hash
    if (
        not hmac.compare_digest(
            password_hash(payload.password, stored.split(":")[0]), stored
        )
        or not row
    ):
        raise HTTPException(401, "Incorrect username or password")
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    with connect(request.app.state.db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute(
            "SELECT password_hash, role FROM users WHERE id = ?", (row["id"],)
        ).fetchone()
        if (
            not current
            or current["password_hash"] != stored
            or current["role"] != row["role"]
        ):
            raise HTTPException(401, "Account changed. Sign in again.")
        connection.execute(
            "DELETE FROM sessions WHERE expires_at <= ? OR token_hash = ?",
            (now, digest_token(request.cookies.get(COOKIE, ""))),
        )
        connection.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?)",
            (digest_token(token), row["id"], csrf, now + SESSION_SECONDS),
        )
    response.set_cookie(
        COOKIE,
        token,
        max_age=SESSION_SECONDS,
        httponly=True,
        secure=request.app.state.secure_cookies,
        samesite="strict",
        path="/",
    )
    return {"username": row["username"], "role": row["role"], "csrf": csrf}


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response) -> None:
    actor = principal(request)
    verify_csrf(request, actor)
    with connect(request.app.state.db_path) as connection:
        connection.execute(
            "DELETE FROM sessions WHERE token_hash = ?",
            (digest_token(request.cookies.get(COOKIE, "")),),
        )
    response.delete_cookie(
        COOKIE,
        path="/",
        secure=request.app.state.secure_cookies,
        httponly=True,
        samesite="strict",
    )
