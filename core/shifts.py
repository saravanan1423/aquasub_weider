from datetime import datetime, timedelta


def active_shift(connection, now=None):
    now = now or datetime.now().astimezone()
    current_minutes = now.hour * 60 + now.minute
    rows = connection.execute(
        "SELECT id,name,start_time,end_time FROM shifts ORDER BY start_time,name"
    ).fetchall()
    for row in rows:
        start_hour, start_minute = map(int, row["start_time"].split(":"))
        end_hour, end_minute = map(int, row["end_time"].split(":"))
        start_minutes = start_hour * 60 + start_minute
        end_minutes = end_hour * 60 + end_minute
        active = (
            start_minutes <= current_minutes < end_minutes
            if start_minutes < end_minutes
            else current_minutes >= start_minutes or current_minutes < end_minutes
        )
        if not active:
            continue
        ends_at = now.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
        if start_minutes > end_minutes and current_minutes >= start_minutes:
            ends_at += timedelta(days=1)
        return {
            "active": True,
            "id": row["id"],
            "name": row["name"],
            "start_time": row["start_time"],
            "end_time": row["end_time"],
            "ends_at": ends_at.isoformat(),
            "seconds_until_end": max(0, int((ends_at - now).total_seconds())),
        }
    return {"active": False}
