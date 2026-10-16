import calendar
import json
import threading
import uuid
from datetime import datetime, time, timedelta, timezone

from core.database import database_connection
from core.usb_backup import backup_shift_reports, mounted_usb_drives, save_local_reports


def ended_shift_windows(shifts, since, now):
    day=since.date() - timedelta(days=1)
    while day <= now.date():
        for shift in shifts:
            starts=datetime.combine(day, time.fromisoformat(shift["start_time"]), tzinfo=now.tzinfo)
            ends=datetime.combine(day, time.fromisoformat(shift["end_time"]), tzinfo=now.tzinfo)
            if ends <= starts:
                ends += timedelta(days=1)
            if since <= ends <= now:
                yield shift, starts, ends
        day += timedelta(days=1)


def scheduled_windows(settings, shifts, since, now):
    if not settings["enabled"]:
        return
    if settings["frequency"] == "shift":
        yield from ended_shift_windows(shifts, since, now)
        return
    backup_time=time.fromisoformat(settings["backup_time"])
    if settings["frequency"] == "daily":
        day=since.date()
        while day <= now.date():
            ends=datetime.combine(day, backup_time, tzinfo=now.tzinfo)
            if since <= ends <= now:
                yield {"id":0, "name":"Daily"}, ends-timedelta(days=1), ends
            day += timedelta(days=1)
        return
    def boundary(year, month):
        day=min(settings["month_day"], calendar.monthrange(year, month)[1])
        return datetime(year, month, day, backup_time.hour, backup_time.minute, tzinfo=now.tzinfo)
    month_index=since.year*12+since.month-1
    while month_index <= now.year*12+now.month-1:
        year, month=divmod(month_index, 12)
        ends=boundary(year, month+1)
        previous_year, previous_month=divmod(month_index-1, 12)
        if since <= ends <= now:
            yield {"id":-1, "name":"Monthly"}, boundary(previous_year, previous_month+1), ends
        month_index += 1


def shift_reports(shift, starts, ends, now, *, assign_shift=True):
    from logs.routes import build_capture_report
    params={"shift":"all", "furnace":"all", "custom_time":"1",
            "from_date":starts.date().isoformat(), "to_date":ends.date().isoformat(),
            "start_time":starts.strftime("%H:%M"), "end_time":ends.strftime("%H:%M")}
    reports={}
    for report_type in ("melt", "individual"):
        report=build_capture_report({**params, "report_type":report_type}, "Automatic backup", end_exclusive=True)
        report["shift"]=shift["name"] if assign_shift else "All shifts"
        report["generated_on"]=now.isoformat(timespec="seconds")
        if assign_shift:
            for item in report["items"]:
                item["shift_type"]=shift["name"]
        reports[report_type]=report
    return reports


class ShiftBackupWorker:
    def __init__(self, app, interval=15):
        self.app=app
        self.interval=interval
        self.stopping=threading.Event()
        self.thread=None

    def enqueue_completed_shifts(self, now):
        with database_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state=connection.execute("SELECT last_checked_at FROM automatic_backup_state WHERE id=1").fetchone()
            since=datetime.fromisoformat(state["last_checked_at"]).astimezone(now.tzinfo) if state else now
            if now < since:
                return
            shifts=connection.execute("SELECT id,name,start_time,end_time FROM shifts ORDER BY id").fetchall()
            settings=connection.execute("SELECT * FROM backup_settings WHERE id=1").fetchone()
            for shift, starts, ends in scheduled_windows(settings, shifts, since, now):
                exists=connection.execute("SELECT 1 FROM automatic_backup_jobs WHERE shift_id=? AND starts_at=? AND ends_at=?", (shift["id"], starts.isoformat(), ends.isoformat())).fetchone()
                if exists:
                    continue
                reports=shift_reports(shift, starts, ends, now, assign_shift=settings["frequency"] == "shift")
                for report in reports.values():
                    report["backup_schedule"]=settings["frequency"]
                connection.execute("""INSERT INTO automatic_backup_jobs
                    (job_key,shift_id,shift_name,starts_at,ends_at,reports_json,schedule_type)
                    VALUES (?,?,?,?,?,?,?)""", (uuid.uuid4().hex, shift["id"], shift["name"], starts.isoformat(), ends.isoformat(), json.dumps(reports), settings["frequency"]))
            connection.execute("""INSERT INTO automatic_backup_state (id,started_at,last_checked_at)
                VALUES (1,?,?) ON CONFLICT(id) DO UPDATE SET last_checked_at=excluded.last_checked_at""",
                (now.isoformat(), now.isoformat()))

    def process_pending(self, now):
        drives=sorted(mounted_usb_drives(), key=lambda drive:(-drive["free_bytes"], drive["id"]))
        cutoff=now.astimezone(timezone.utc).isoformat()
        with database_connection() as connection:
            jobs=connection.execute("""SELECT * FROM automatic_backup_jobs
                WHERE (status='pending' AND (local_saved_at IS NULL OR ?))
                   OR (status='processing' AND lease_until<?)
                ORDER BY ends_at,id LIMIT 3""", (bool(drives), cutoff)).fetchall()
        for job in jobs:
            token=uuid.uuid4().hex
            lease=(now.astimezone(timezone.utc) + timedelta(minutes=5)).isoformat()
            with database_connection() as connection:
                claimed=connection.execute("""UPDATE automatic_backup_jobs
                    SET status='processing',lock_token=?,lease_until=?
                    WHERE id=? AND (status='pending' OR (status='processing' AND lease_until<?))""",
                    (token, lease, job["id"], cutoff)).rowcount
            if not claimed:
                continue
            result=None
            last_error=""
            local_ready=False
            try:
                reports=json.loads(job["reports_json"])
                local_folder=save_local_reports(reports, job["job_key"])
                local_ready=True
                with database_connection() as connection:
                    connection.execute("""UPDATE automatic_backup_jobs SET local_folder=?,local_saved_at=COALESCE(local_saved_at,?)
                        WHERE id=? AND lock_token=?""", (local_folder,now.isoformat(),job["id"],token))
                    if drives:
                        connection.execute("UPDATE automatic_backup_jobs SET attempts=attempts+1 WHERE id=? AND lock_token=?", (job["id"],token))
                for drive in drives:
                    try:
                        result=backup_shift_reports(reports, drive["id"], job["job_key"], local_folder)
                        break
                    except Exception as error:
                        last_error=str(error)[:500] or "USB report backup failed"
            except Exception as error:
                last_error=str(error)[:500] or "Could not save the local report"
                if not local_ready:
                    with database_connection() as connection:
                        connection.execute("UPDATE automatic_backup_jobs SET local_saved_at=NULL WHERE id=? AND lock_token=?", (job["id"],token))
            with database_connection() as connection:
                if result:
                    connection.execute("""UPDATE automatic_backup_jobs
                        SET status='completed',completed_at=?,drive=?,folder=?,last_error='',lock_token=NULL,lease_until=NULL
                        WHERE id=? AND lock_token=?""",
                        (now.isoformat(), result["drive"], result["folder"], job["id"], token))
                else:
                    connection.execute("""UPDATE automatic_backup_jobs
                        SET status='pending',last_error=?,lock_token=NULL,lease_until=NULL
                        WHERE id=? AND lock_token=?""", (last_error, job["id"], token))
                    if job["last_error"] != last_error:
                        self.app.logger.warning("Shift backup %s pending: %s", job["id"], last_error)

    def tick(self, now=None):
        now=(now or datetime.now().astimezone()).astimezone()
        with self.app.app_context():
            self.enqueue_completed_shifts(now)
            self.process_pending(now)

    def run_forever(self):
        while not self.stopping.is_set():
            try:
                self.tick()
            except Exception:
                self.app.logger.exception("Automatic backup check failed; it will retry")
            self.stopping.wait(self.interval)

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stopping.clear()
        self.thread=threading.Thread(target=self.run_forever, name="shift-usb-backup", daemon=True)
        self.thread.start()

    def stop(self):
        self.stopping.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=3)


def start_shift_backup_worker(app):
    worker=app.extensions.get("shift_backup_worker")
    if worker is None:
        worker=ShiftBackupWorker(app)
        app.extensions["shift_backup_worker"]=worker
    worker.start()
    return worker


def backup_status():
    with database_connection() as connection:
        counts=connection.execute("SELECT status,count(*) AS total FROM automatic_backup_jobs GROUP BY status").fetchall()
        latest=connection.execute("SELECT shift_name,ends_at,completed_at,drive FROM automatic_backup_jobs WHERE status='completed' ORDER BY completed_at DESC,id DESC LIMIT 1").fetchone()
        error=connection.execute("SELECT last_error FROM automatic_backup_jobs WHERE status='pending' AND last_error!='' ORDER BY id DESC LIMIT 1").fetchone()
        state=connection.execute("SELECT started_at,last_checked_at FROM automatic_backup_state WHERE id=1").fetchone()
        settings=connection.execute("SELECT enabled,frequency,backup_time,month_day FROM backup_settings WHERE id=1").fetchone()
        local_pending=connection.execute("SELECT count(*) FROM automatic_backup_jobs WHERE status='pending' AND local_saved_at IS NOT NULL").fetchone()[0]
    totals={row["status"]:row["total"] for row in counts}
    return {"pending":totals.get("pending", 0), "processing":totals.get("processing", 0),
            "last_completed":dict(latest) if latest else None, "last_error":error["last_error"] if error else "",
            "monitoring":dict(state) if state else None, "settings":dict(settings), "local_pending":local_pending}
