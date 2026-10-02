# Two people, one issue

An issue can be open in two browsers. Alice changes `open` to `in_progress`;
Bob's older browser still says `open`. Previously, Bob could silently replace
Alice's status. A transaction alone did not prevent that: it serialized writes,
but did not check which version the user had read.

## Contract (API 1.2)

Every issue response includes an integer `version`, starting at 1. Send that
value in `X-Issue-Version` for PATCH and DELETE. Read it from the response;
do not hard-code it or assume your cached issue is current.

```http
PATCH /api/issues/42
Content-Type: application/json
X-Issue-Version: 1

{"status": "in_progress"}
```

A successful update increments the version and returns the complete issue.
A stale version returns **409**, with `detail.code = "version_conflict"` and
`detail.current` containing the latest issue. No issue or history row changes.
Missing versions return **428**; invalid versions return **422**; a deleted
issue returns **404** when a valid version is supplied. Access checks happen
before looking up the issue or disclosing a conflict.

This intentionally changes the write contract. Older API clients must send the
version. Empty PATCH and invalid payloads retain their existing 400/422 errors.
Existing databases are migrated at startup without reseeding or creating
fictional history for older issues. They begin at version 1.

## Storage and interface

SQLite `BEGIN IMMEDIATE` holds the write lock while the server reads and checks
the version, changes the row, and writes its history snapshot. Either the whole
transaction commits or it rolls back, including the version increment.

The browser stops stale actions, shows the current status and the rejected
choice, and moves keyboard focus to **Load latest issues**. That button only
reloads data; it never retries the rejected action. The user reviews the latest
issue and its history before choosing again. Deletion follows the same rule.

## Verification

- `tests/test_concurrency.py`: stale update and delete, missing/invalid versions,
  a fresh-version retry, and two independent clients racing to write version 1.
- `tests/test_access_history.py`: role restrictions, migration, and rollback if
  the history write fails (including the version).
- `tests/test_browser_access.py`: two isolated browser sessions, conflict focus,
  explicit review and retry, stale deletion, demo and private modes.

Run `pytest -m "not e2e"` and then
`RUN_BROWSER_ACCESS=1 pytest tests/test_browser_access.py` (PowerShell:
`$env:RUN_BROWSER_ACCESS="1"` before the second command).

## Limits

This is a small single-workspace portfolio application. Conflict checking is
per issue, so even edits to different fields conflict. There is no automatic
merge, real-time synchronization or distributed database claim. SQLite's writer
lock is appropriate for this small demo; a larger deployment would require
separate load testing and a different storage/concurrency assessment.
