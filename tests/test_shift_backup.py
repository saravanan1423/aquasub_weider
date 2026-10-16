import csv
import io
import json
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import test_usb_backup
from app import app
from core.database import database_connection, initialize_database
from core.shift_backup import ShiftBackupWorker, backup_status, start_shift_backup_worker
from core.usb_backup import backup_shift_reports


class ShiftBackupTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_usb_backup.UsbBackupTests()
        self.fixture.setUp()
        self.original_reports=app.config.get("REPORT_STORAGE_DIR")
        app.config["REPORT_STORAGE_DIR"]=Path(self.fixture.temporary.name) / "reports"
        self.day=datetime(2026, 10, 12, tzinfo=datetime.now().astimezone().tzinfo)
        self.worker=ShiftBackupWorker(app)
        with app.app_context():
            connection=database_connection()
            self.shift_id=connection.execute("INSERT INTO shifts (name,start_time,end_time,created_by,updated_at) VALUES ('Day','08:00','16:00','admin',?)", (self.day.isoformat(),)).lastrowid
            connection.execute("UPDATE weight_captures SET captured_at=?", ((self.day + timedelta(hours=10)).isoformat(),))
            self.add_capture(connection, "Aluminium", 5, self.day + timedelta(hours=11))
            self.add_capture(connection, "Copper", 3, self.day + timedelta(hours=16))
            connection.commit()
            connection.close()

    def tearDown(self):
        if self.original_reports is None:
            app.config.pop("REPORT_STORAGE_DIR",None)
        else:
            app.config["REPORT_STORAGE_DIR"]=self.original_reports
        self.fixture.tearDown()

    def add_capture(self, connection, product, weight, at):
        connection.execute("""INSERT INTO weight_captures
            (product_image_id,image_name,image_url,weight,furnace_id,furnace_name,melt_number,captured_at,captured_by_username)
            VALUES (1,?,'',?,'1','A','A1',?,'operator')""", (product, str(weight), at.isoformat()))

    def jobs(self):
        with app.app_context():
            connection=database_connection()
            rows=connection.execute("SELECT * FROM automatic_backup_jobs ORDER BY id").fetchall()
            connection.close()
        return [dict(row) for row in rows]

    def tick(self, at, plugged=False, worker=None):
        drives=[self.fixture.drive] if plugged else []
        with patch("core.shift_backup.mounted_usb_drives", return_value=drives), patch("core.usb_backup.mounted_usb_drives", return_value=drives), patch.object(Path, "is_mount", return_value=True):
            (worker or self.worker).tick(at)

    def enqueue(self):
        self.tick(self.day + timedelta(hours=7))
        self.tick(self.day + timedelta(hours=16))

    def test_job_waits_for_usb_and_freezes_shift_data_with_exclusive_end(self):
        self.enqueue()
        job=self.jobs()[0]
        self.assertEqual(job["status"], "pending")
        self.assertEqual(job["attempts"], 0)
        reports=json.loads(job["reports_json"])
        self.assertEqual(reports["melt"]["total_net_weight"], 7)
        self.assertEqual(reports["individual"]["total_products"], 2)
        self.assertEqual(reports["melt"]["shift"], "Day")
        self.assertNotIn("Copper", [row["image_name"] for row in reports["individual"]["items"]])
        with app.app_context():
            connection=database_connection()
            connection.execute("UPDATE weight_captures SET weight='999'")
            connection.execute("DELETE FROM shifts")
            connection.commit()
            connection.close()
        self.tick(self.day + timedelta(hours=17), plugged=True)
        saved=self.jobs()[0]
        self.assertEqual(saved["status"], "completed")
        folder=Path(saved["folder"])
        self.assertEqual(len(list(folder.glob("*.csv"))), 2)
        self.assertEqual(len(list(folder.glob("*.pdf"))), 2)
        rows=list(csv.reader(io.StringIO((folder / "Melt_Summary.csv").read_text(encoding="utf-8-sig"))))
        self.assertEqual(rows[5][4], "7.000")
        for file in folder.glob("*.pdf"):
            self.assertTrue(file.read_bytes().startswith(b"%PDF"))

    def test_usb_present_at_shift_end_exports_and_repeated_ticks_do_not_duplicate(self):
        self.tick(self.day + timedelta(hours=7), plugged=True)
        self.tick(self.day + timedelta(hours=16), plugged=True)
        self.tick(self.day + timedelta(hours=16, minutes=1), plugged=True)
        self.assertEqual(len(self.jobs()), 1)
        self.assertEqual(self.jobs()[0]["attempts"], 1)
        self.assertEqual(len(list((self.fixture.drive_root / "Weider Reports").iterdir())), 1)
        with app.app_context():
            connection=database_connection()
            self.assertTrue(all(row[0] is None for row in connection.execute("SELECT melt_completed_at FROM weight_captures")))
            connection.close()

    def test_restart_recovers_pending_and_shift_ends_missed_while_offline(self):
        self.enqueue()
        initialize_database(app)
        restarted=ShiftBackupWorker(app)
        self.tick(self.day + timedelta(days=1, hours=16), worker=restarted)
        self.assertEqual(len(self.jobs()), 2)
        self.assertTrue(all(job["status"] == "pending" for job in self.jobs()))
        self.tick(self.day + timedelta(days=1, hours=16, minutes=1), plugged=True, worker=restarted)
        self.assertTrue(all(job["status"] == "completed" for job in self.jobs()))

    def test_overnight_shift_is_backed_up_after_next_day_end(self):
        with app.app_context():
            connection=database_connection()
            connection.execute("UPDATE shifts SET name='Night',start_time='22:00',end_time='06:00'")
            connection.execute("DELETE FROM weight_captures")
            self.add_capture(connection, "Brass", 2, self.day + timedelta(hours=23))
            self.add_capture(connection, "Aluminium", 5, self.day + timedelta(days=1, hours=1))
            self.add_capture(connection, "Copper", 3, self.day + timedelta(days=1, hours=6))
            connection.commit()
            connection.close()
        self.tick(self.day + timedelta(hours=21))
        self.tick(self.day + timedelta(days=1, hours=6))
        reports=json.loads(self.jobs()[0]["reports_json"])
        self.assertEqual(reports["melt"]["total_net_weight"], 7)
        self.assertEqual(reports["individual"]["total_products"], 2)
        self.assertEqual(reports["melt"]["shift"], "Night")

    def test_empty_shift_exports_zero_totals(self):
        with app.app_context():
            connection=database_connection()
            connection.execute("DELETE FROM weight_captures")
            connection.commit()
            connection.close()
        self.enqueue()
        self.tick(self.day + timedelta(hours=17), plugged=True)
        job=self.jobs()[0]
        self.assertEqual(job["status"], "completed")
        self.assertEqual(json.loads(job["reports_json"])["melt"]["total_net_weight"], 0)

    def test_failed_write_remains_pending_and_retries(self):
        self.enqueue()
        with patch("core.shift_backup.backup_shift_reports", side_effect=OSError("USB removed")), self.assertLogs(app.logger, level="WARNING"):
            self.tick(self.day + timedelta(hours=17), plugged=True)
        self.assertEqual(self.jobs()[0]["status"], "pending")
        self.assertEqual(self.jobs()[0]["last_error"], "USB removed")
        self.tick(self.day + timedelta(hours=17, minutes=1), plugged=True)
        self.assertEqual(self.jobs()[0]["status"], "completed")
        self.assertEqual(self.jobs()[0]["last_error"], "")

    def test_power_loss_after_files_written_does_not_write_a_second_copy(self):
        self.enqueue()
        job=self.jobs()[0]
        with patch("core.usb_backup.mounted_usb_drives", return_value=[self.fixture.drive]), patch.object(Path, "is_mount", return_value=True):
            backup_shift_reports(json.loads(job["reports_json"]), self.fixture.drive["id"], job["job_key"])
        with app.app_context():
            connection=database_connection()
            expired=(self.day + timedelta(hours=16)).astimezone(timezone.utc).isoformat()
            connection.execute("UPDATE automatic_backup_jobs SET status='processing',lock_token='crashed',lease_until=?", (expired,))
            connection.commit()
            connection.close()
        with patch("core.usb_backup.report_pdf", side_effect=AssertionError("Should reuse verified files")):
            self.tick(self.day + timedelta(hours=17), plugged=True, worker=ShiftBackupWorker(app))
        self.assertEqual(self.jobs()[0]["status"], "completed")
        self.assertEqual(len(list((self.fixture.drive_root / "Weider Reports").iterdir())), 1)

    def test_active_lease_prevents_second_worker_export(self):
        self.enqueue()
        other=ShiftBackupWorker(app)
        now=self.day + timedelta(hours=17)
        def save(*args):
            other.tick(now)
            return {"drive":"TEST USB", "folder":str(self.fixture.drive_root)}
        with patch("core.shift_backup.backup_shift_reports", side_effect=save) as writer:
            self.tick(now, plugged=True)
        self.assertEqual(writer.call_count, 1)
        self.assertEqual(self.jobs()[0]["status"], "completed")

    def test_no_initial_historical_backfill_and_clock_rollback_is_safe(self):
        self.tick(self.day + timedelta(hours=17))
        self.assertEqual(self.jobs(), [])
        self.tick(self.day + timedelta(hours=15))
        self.assertEqual(self.jobs(), [])
        self.tick(self.day + timedelta(days=1, hours=16))
        self.assertEqual(len(self.jobs()), 1)

    def test_status_endpoint_lists_pending_and_completed_jobs(self):
        self.enqueue()
        response=self.fixture.client.get("/api/automatic-backups")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["pending"], 1)
        self.tick(self.day + timedelta(hours=17), plugged=True)
        with app.app_context():
            status=backup_status()
        self.assertEqual(status["pending"], 0)
        self.assertEqual(status["last_completed"]["shift_name"], "Day")

    def test_worker_start_is_idempotent_and_stops_cleanly(self):
        fake_app=SimpleNamespace(extensions={}, logger=MagicMock())
        seen=threading.Event()
        with patch.object(ShiftBackupWorker, "tick", side_effect=lambda:seen.set()):
            worker=start_shift_backup_worker(fake_app)
            try:
                self.assertTrue(seen.wait(timeout=1))
                self.assertIs(start_shift_backup_worker(fake_app), worker)
            finally:
                worker.stop()
        self.assertFalse(worker.thread.is_alive())


if __name__ == "__main__":
    unittest.main()
