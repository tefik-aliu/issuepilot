# Access and issue history

IssuePilot has two modes for one SQLite workspace. The default is a **shared public demo**: anyone can read, create, edit and delete sample issues. Never store private information in that mode. Set `ISSUEPILOT_AUTH_REQUIRED=1` **before** running a private installation. Invalid values fail startup instead of silently disabling authentication.

## Private installation

Use the same `ISSUEPILOT_DB` path for the CLI and the server. Back up an existing database first; startup adds tables and preserves existing issues. Earlier edits are not reconstructed.

```powershell
$env:ISSUEPILOT_DB = "data/private.db"
$env:ISSUEPILOT_AUTH_REQUIRED = "1"
python -m app.users create owner --role admin
python -m app.users create teammate --role editor
python -m app.users create reviewer --role viewer
# Passwords are entered at a hidden prompt; no default credentials are supplied.
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Serve the private deployment over HTTPS through a reverse proxy. Session cookies are `Secure` by default. **For local HTTP testing only**, set `$env:ISSUEPILOT_COOKIE_SECURE = "0"` before starting. Leave it unset or `1` for HTTPS. A persistent disk and restricted filesystem permissions are required to retain and protect the SQLite file. Do not reuse the shared demo database for private data.

| Role | Read issues, statistics, history | Create / edit | Delete |
|---|---|---|---|
| Viewer | Yes | No | No |
| Editor | Yes | Yes | No |
| Admin | Yes | Yes | Yes |

User provisioning stays in the local administrator CLI; the web admin role does not grant shell access or user management. This deliberately avoids exposing public registration or an account-management API.

```powershell
python -m app.users reset teammate
python -m app.users role teammate --role viewer
```

Password resets and role changes revoke that user's sessions. The CLI prompts for password confirmation and enforces 12–256 characters. Usernames are case-insensitive ASCII names. Database access remains an administrator capability; the audit log is not tamper-proof against someone with disk access.

## Browser and API contract

- `GET /api/auth/session`: mode plus the current user and CSRF token, or `user: null`.
- `POST /api/auth/login`: JSON `username` and `password`, with `X-IssuePilot-Request: 1`. Returns the user's role and CSRF token; sets an HttpOnly, SameSite=Strict cookie.
- `POST /api/auth/logout`: session cookie plus `X-CSRF-Token`; revokes the session and clears the cookie.
- Issue mutations require both the session cookie and `X-CSRF-Token` in private mode. API clients should retain the cookie and obtain the CSRF token from login/session. Swagger does not automatically add this header.
- Private reads return 401 without a valid session. Insufficient role or invalid CSRF returns 403. Health, the static sign-in page and API schema remain public; they contain no issue data.
- `GET /api/issues/{id}/history` and `GET /api/activity`: newest first, `limit` 1–100 (default 20), `before=<last event id>` for older results. Deleted-issue history remains available.

Authentication responses and issue API responses use `Cache-Control: no-store`. Session cookies last eight hours, are revoked at logout and rotated when signing in again from the same browser. Passwords use salted scrypt; session secrets are stored as SHA-256 digests. CSRF tokens live in the session and browser memory, not local storage. No cross-origin API access is enabled.

Login attempts are persisted and bounded to five per username and 100 total per five minutes, including successful sign-ins. A SQLite write transaction coordinates the counters across workers. This basic throttle can temporarily lock an account under attack; a production deployment also needs reverse-proxy rate limiting and monitoring.

## Why store history in the same transaction?

Each successful API create, update or delete writes an event with actor, UTC time and before/after snapshots in the **same SQLite transaction**. If the event write fails, the issue mutation rolls back. Updates acquire a write lock before reading the previous state so the recorded snapshot matches the state being replaced. Deletion retains its history; seeded or migrated issues start without fabricated events.

The UI shows field differences, actor and time. Event data is inserted with `textContent`, including strings that look like HTML. Snapshot history retains previous descriptions and deleted content: deleting an issue is not data erasure. For a real retention policy, implement an explicit administrator purge and backup policy.

## Scope and tradeoffs

This is a single-workspace portfolio implementation, not a reviewed enterprise identity service. It has no multi-tenancy, MFA, email recovery, invitations or SSO. Local provisioning keeps the account lifecycle small and inspectable. SQLite makes issue/history transactions straightforward but serializes writes; PostgreSQL is a later option if measured load warrants it. Full snapshots trade storage for a simple, readable history. Public demo events have the actor `Public demo`; they do not identify people.

## Reproduce validation

```bash
pytest -m "not e2e"
```

`tests/test_access_history.py` exercises anonymous access, the role matrix, CSRF, cookie flags, session expiry/rotation/logout, login throttling, migration, and history rollback on simulated storage failure. `tests/test_browser_access.py` starts isolated local demo/private servers and tests browser sign-in, role controls, history, logout, literal HTML rendering and mobile overflow. Enable it with:

```powershell
playwright install chromium
$env:RUN_BROWSER_ACCESS = "1"
pytest tests/test_browser_access.py
# Optional on a workstation with Edge installed:
$env:PLAYWRIGHT_CHANNEL = "msedge"
```
