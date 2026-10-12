import gc
import csv
import io
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from app import app
from core.database import database_connection, initialize_database


class UsbBackupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.original_path = app.config["DATABASE_PATH"]
        app.config["DATABASE_PATH"] = Path(self.temporary.name) / "test.db"
        initialize_database(app)
        self.client = app.test_client()
        with app.app_context():
            connection = database_connection()
            admin_id = connection.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
            connection.execute("INSERT INTO weight_captures (product_image_id,image_name,image_url,weight,furnace_id,furnace_name,melt_number,captured_at,captured_by_username) VALUES (1,'Brass','', '2', '1', 'A', 'A1', ?, 'operator')", (datetime.now().astimezone().isoformat(timespec="seconds"),))
            connection.commit()
            connection.close()
        with self.client.session_transaction() as session:
            session.update(user_id=admin_id, username="admin", role="admin")
        self.drive_root = Path(self.temporary.name) / "usb"
        self.drive_root.mkdir()
        self.drive = {"id": "sda1@test", "label": "TEST USB", "mountpoint": str(self.drive_root), "free_bytes": 1024 * 1024 * 1024}

    def tearDown(self):
        self.client = None
        app.config["DATABASE_PATH"] = self.original_path
        gc.collect()
        self.temporary.cleanup()

    def payload(self):
        today = datetime.now().astimezone().date().isoformat()
        return {"drive_id": self.drive["id"], "from_date": today, "to_date": today,
                "shift": "all", "furnace": "all", "custom_time": "0"}

    def test_backup_writes_matching_csv_and_pdf(self):
        with patch("core.usb_backup.mounted_usb_drives", return_value=[self.drive]), patch.object(Path, "is_mount", return_value=True):
            response = self.client.post("/api/report-usb-backup", json=self.payload())
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["total_melts"], 1)
        folder = Path(response.json["folder"])
        self.assertEqual(folder.parent, self.drive_root / "Weider Reports")
        self.assertEqual(set(response.json["files"]), {"Melt_Summary.csv", "Melt_Summary.pdf", "Individual_Captures.csv", "Individual_Captures.pdf"})
        csv_file, pdf_file = folder / "Melt_Summary.csv", folder / "Melt_Summary.pdf"
        self.assertIn("Brass", (folder / "Individual_Captures.csv").read_text(encoding="utf-8-sig"))
        self.assertTrue((folder / "Individual_Captures.pdf").read_bytes().startswith(b"%PDF"))
        self.assertIn("A1", csv_file.read_text(encoding="utf-8-sig"))
        self.assertIn("2.000", csv_file.read_text(encoding="utf-8-sig"))
        self.assertTrue(pdf_file.read_bytes().startswith(b"%PDF"))
        self.assertGreater(pdf_file.stat().st_size, 1000)

    def test_backup_is_in_log_report_dialog_not_device_settings(self):
        device = self.client.get("/device-settings")
        self.assertEqual(device.status_code, 200)
        self.assertNotIn(b'USB Backup', device.data)
        self.assertNotIn(b'id="backup-drive"', device.data)
        logs = self.client.get("/logs")
        self.assertEqual(logs.status_code, 200)
        self.assertIn(b'id="report-output"', logs.data)
        self.assertIn(b'USB backup (CSV + PDF)', logs.data)
        self.assertIn(b'id="backup-drive"', logs.data)

    def test_unlisted_drive_is_rejected(self):
        with patch("core.usb_backup.mounted_usb_drives", return_value=[]):
            response = self.client.post("/api/report-usb-backup", json=self.payload())
        self.assertEqual(response.status_code, 400)
        self.assertFalse((self.drive_root / "Weider Reports").exists())

    def test_empty_database_exports_headers_and_zero_totals(self):
        with app.app_context():
            connection = database_connection()
            connection.execute("DELETE FROM weight_captures")
            connection.commit()
            connection.close()
        self.assert_empty_backup(self.payload())

    def test_filters_without_matches_export_empty_report(self):
        payload = self.payload()
        payload.update(from_date="2000-01-01", to_date="2000-01-01")
        self.assert_empty_backup(payload)

    def assert_empty_backup(self, payload):
        with patch("core.usb_backup.mounted_usb_drives", return_value=[self.drive]), patch.object(Path, "is_mount", return_value=True):
            response = self.client.post("/api/report-usb-backup", json=payload)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["total_melts"], 0)
        self.assertEqual(response.json["total_weight"], 0)
        folder = Path(response.json["folder"])
        csv_file, pdf_file = folder / "Melt_Summary.csv", folder / "Melt_Summary.pdf"
        individual_rows = list(csv.reader(io.StringIO((folder / "Individual_Captures.csv").read_text(encoding="utf-8-sig"))))
        self.assertEqual(individual_rows[5], [])
        self.assertTrue((folder / "Individual_Captures.pdf").read_bytes().startswith(b"%PDF"))
        rows = list(csv.reader(io.StringIO(csv_file.read_text(encoding="utf-8-sig"))))
        self.assertEqual(rows[4][0], "S.No")
        self.assertEqual(rows[5], [])
        self.assertEqual(rows[6][:6], ["Grand total", "0", "melts", "0.000 kg", "0", "products"])
        self.assertTrue(pdf_file.read_bytes().startswith(b"%PDF"))
        self.assertGreater(pdf_file.stat().st_size, 1000)

    def test_missing_pdf_dependency_returns_useful_json_error(self):
        with patch("core.usb_backup.mounted_usb_drives", return_value=[self.drive]), patch("core.usb_backup.report_pdf", side_effect=ModuleNotFoundError("No module named 'reportlab'")), self.assertLogs(app.logger, level="ERROR"):
            response = self.client.post("/api/report-usb-backup", json=self.payload())
        self.assertEqual(response.status_code, 503)
        self.assertIn("PDF export is unavailable", response.json["error"])
        self.assertFalse((self.drive_root / "Weider Reports").exists())

    def test_pdf_failure_returns_json_error(self):
        with patch("core.usb_backup.mounted_usb_drives", return_value=[self.drive]), patch("core.usb_backup.report_pdf", side_effect=RuntimeError("PDF generation failed")), self.assertLogs(app.logger, level="ERROR"):
            response = self.client.post("/api/report-usb-backup", json=self.payload())
        self.assertEqual(response.status_code, 500)
        self.assertIn("Could not create the backup report", response.json["error"])

    def login_operator(self, logs_access=True):
        with app.app_context():
            connection = database_connection()
            user_id = connection.execute("INSERT INTO users (username,password_hash,role,created_at) VALUES ('operator','unused','user','2026-10-08')").lastrowid
            if logs_access:
                connection.execute("INSERT INTO user_permissions (user_id,screen) VALUES (?, 'logs')", (user_id,))
            connection.commit()
            connection.close()
        with self.client.session_transaction() as session:
            session.update(user_id=user_id, username="operator", role="user")

    def test_operator_with_logs_access_can_backup(self):
        self.login_operator()
        logs = self.client.get("/logs")
        self.assertEqual(logs.status_code, 200)
        self.assertIn(b'id="report-output"', logs.data)
        self.assertIn(b'id="backup-drive"', logs.data)
        self.assertNotIn(b'id="edit-log-dialog"', logs.data)
        with patch("core.usb_backup.mounted_usb_drives", return_value=[self.drive]), patch.object(Path, "is_mount", return_value=True):
            self.assertEqual(self.client.get("/api/usb-drives").json["drives"], [self.drive])
            for report_type in ("melt", "individual"):
                response = self.client.post("/api/report-usb-backup", json={**self.payload(), "report_type": report_type})
                self.assertEqual(response.status_code, 200, response.json)
                self.assertTrue(all((Path(response.json["folder"]) / filename).exists() for filename in response.json["files"]))
        self.assertEqual(self.client.get("/device-settings").status_code, 403)
        self.assertEqual(self.client.get("/users").status_code, 403)

    def test_backup_requires_logs_access(self):
        self.login_operator(logs_access=False)
        self.assertEqual(self.client.get("/api/usb-drives").status_code, 403)
        self.assertEqual(self.client.post("/api/report-usb-backup", json=self.payload()).status_code, 403)
        self.assertEqual(self.client.get("/logs").status_code, 403)

    def test_backup_requires_login(self):
        with self.client.session_transaction() as session:
            session.clear()
        self.assertEqual(self.client.get("/api/usb-drives").status_code, 401)
        self.assertEqual(self.client.post("/api/report-usb-backup", json=self.payload()).status_code, 401)


if __name__ == "__main__":
    unittest.main()
