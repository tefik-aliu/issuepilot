from __future__ import annotations

import os
from pathlib import Path

from .db import connect, initialise
from .main import utc_now

SAMPLES = [
    (
        "Checkout freezes after payment",
        "The loading state remains visible after a successful card authorisation.",
        "critical",
        "open",
    ),
    (
        "Profile image is cropped incorrectly",
        "Portrait images lose the top section on smaller screens.",
        "medium",
        "in_progress",
    ),
    (
        "Improve empty search message",
        "Explain that filters may be hiding results and provide a reset action.",
        "low",
        "resolved",
    ),
]


def main() -> None:
    db_path = Path(os.getenv("ISSUEPILOT_DB", "data/issuepilot.db")).resolve()
    initialise(db_path)
    now = utc_now()

    with connect(db_path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM issues").fetchone()[0]
        if count:
            print(f"Database already contains {count} issue(s); no demo data added.")
            return
        connection.executemany(
            """
            INSERT INTO issues (title, description, priority, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [(*sample, now, now) for sample in SAMPLES],
        )
    print(f"Added {len(SAMPLES)} demo issues to {db_path}")


if __name__ == "__main__":
    main()
