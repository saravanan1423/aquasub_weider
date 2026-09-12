import json
from datetime import datetime

from flask import session

from core.database import database_connection


def _text(value):
    if value is None:
        return None
    if isinstance(value, (dict, list, tuple, set)):
        return json.dumps(sorted(value) if isinstance(value, set) else value, ensure_ascii=False, sort_keys=True)
    return str(value)


def record_changes(module, record_id, action, before, after):
    """Write one audit row for each changed field."""
    before = before or {}
    after = after or {}
    rows = []
    for field in sorted(set(before) | set(after)):
        old_value, new_value = _text(before.get(field)), _text(after.get(field))
        if old_value == new_value:
            continue
        rows.append((
            module, str(record_id) if record_id is not None else None, action,
            field, old_value, new_value, session.get("user_id"),
            session.get("username", "Unknown"),
            datetime.now().astimezone().isoformat(timespec="milliseconds"),
        ))
    if rows:
        with database_connection() as connection:
            connection.executemany("""
                INSERT INTO audit_logs (
                    module,record_id,action,field_name,old_value,new_value,
                    changed_by_user_id,changed_by_username,changed_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
            """, rows)
