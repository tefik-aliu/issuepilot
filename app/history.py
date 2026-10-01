import json
from datetime import datetime, timezone


def record(connection, actor, action, issue_id, before, after):
    connection.execute(
        "INSERT INTO issue_events (issue_id, actor, action, occurred_at, before_json, after_json) VALUES (?, ?, ?, ?, ?, ?)",
        (
            issue_id,
            actor["username"],
            action,
            datetime.now(timezone.utc).isoformat(),
            json.dumps(dict(before)) if before is not None else None,
            json.dumps(dict(after)) if after is not None else None,
        ),
    )


def event_dict(row):
    result = dict(row)
    result["before"] = json.loads(result.pop("before_json") or "null")
    result["after"] = json.loads(result.pop("after_json") or "null")
    return result
