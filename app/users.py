"""Local administrator CLI; passwords are prompted, never command arguments."""

import argparse
import getpass
import os
import sqlite3
from pathlib import Path

from .access import create_user, password_hash
from .db import connect, initialise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["create", "reset", "role"])
    parser.add_argument("username")
    parser.add_argument("--role", choices=["admin", "editor", "viewer"])
    args = parser.parse_args()
    path = Path(os.getenv("ISSUEPILOT_DB", "data/issuepilot.db")).resolve()
    initialise(path)
    try:
        if args.action == "create":
            if not args.role:
                parser.error("create requires --role")
            password = getpass.getpass("Password (12–256 characters): ")
            if password != getpass.getpass("Repeat password: "):
                raise ValueError("Passwords do not match")
            create_user(path, args.username, password, args.role)
        else:
            with connect(path) as connection:
                row = connection.execute(
                    "SELECT id FROM users WHERE username = ?", (args.username.lower(),)
                ).fetchone()
                if not row:
                    raise ValueError("User not found")
                if args.action == "reset":
                    password = getpass.getpass("New password (12–256 characters): ")
                    if not 12 <= len(password) <= 256:
                        raise ValueError("Password must be 12–256 characters")
                    if password != getpass.getpass("Repeat password: "):
                        raise ValueError("Passwords do not match")
                    connection.execute(
                        "UPDATE users SET password_hash = ? WHERE id = ?",
                        (password_hash(password), row["id"]),
                    )
                else:
                    if not args.role:
                        parser.error("role requires --role")
                    connection.execute(
                        "UPDATE users SET role = ? WHERE id = ?", (args.role, row["id"])
                    )
                connection.execute(
                    "DELETE FROM sessions WHERE user_id = ?", (row["id"],)
                )
    except (ValueError, sqlite3.IntegrityError) as error:
        parser.exit(1, f"{error}\n")
    print("User saved. Existing sessions revoked after password or role changes.")


if __name__ == "__main__":
    main()
