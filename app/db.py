from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    priority TEXT NOT NULL CHECK(priority IN ('low', 'medium', 'high', 'critical')),
    status TEXT NOT NULL CHECK(status IN ('open', 'in_progress', 'resolved')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1
 );
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin', 'editor', 'viewer'))
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf TEXT NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS login_attempts (username TEXT NOT NULL, attempted_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS issue_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('created', 'updated', 'deleted')),
    occurred_at TEXT NOT NULL,
    before_json TEXT,
    after_json TEXT
);
CREATE INDEX IF NOT EXISTS issue_events_issue ON issue_events(issue_id, id);
"""


class Connection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path, factory=Connection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialise(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as connection:
        connection.executescript(SCHEMA)
        # Serialize schema inspection and migration across starting workers.
        connection.execute("BEGIN IMMEDIATE")
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(issues)")}
        if "version" not in columns:
            connection.execute("ALTER TABLE issues ADD COLUMN version INTEGER NOT NULL DEFAULT 1")


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}
