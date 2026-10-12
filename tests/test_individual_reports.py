import csv
import io
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

import test_usb_backup
from app import app
from core.database import database_connection
from core.report_export import INDIVIDUAL_COLUMNS, report_csv, report_pdf, report_rows
from logs.routes import build_capture_report


class IndividualReportTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_usb_backup.UsbBackupTests()
        self.fixture.setUp()
        self.params = self.fixture.payload()
        self.params["report_type"] = "individual"
        day = self.params["from_date"]
        with app.app_context():
            connection = database_connection()
            connection.execute("UPDATE weight_captures SET captured_at=?", (f"{day}T10:00:00",))
            for product, weight, furnace, melt, hour, operator in (
                ("Aluminium", "5", "A", "A1", "11", "operator2"),
                ("Copper", ".5", "B", "B1", "13", "operator"),
            ):
                connection.execute("""INSERT INTO weight_captures
                    (product_image_id,image_name,image_url,weight,furnace_id,furnace_name,
                     melt_number,captured_at,captured_by_username)
                    VALUES (1,?,'',?,?,?,?,?,?)""",
                    (product, weight, "1" if furnace == "A" else "2", furnace,
                     melt, f"{day}T{hour}:00:00", operator))
            self.shift_id = connection.execute("INSERT INTO shifts (name,start_time,end_time,created_by,updated_at) VALUES ('Day','08:00','12:00','admin',?)", (day,)).lastrowid
            connection.commit()
            connection.close()

    def tearDown(self):
        self.fixture.tearDown()

    def build(self, **filters):
        with app.app_context():
            return build_capture_report({**self.params, **filters}, "admin")

    def test_each_capture_is_separate_with_correct_totals_and_operators(self):
        report = self.build()
        self.assertEqual([item["image_name"] for item in report["items"]], ["Copper", "Aluminium", "Brass"])
        self.assertEqual([item["melt_number"] for item in report["items"]], ["B1", "A1", "A1"])
        self.assertEqual(report["total_products"], 3)
        self.assertEqual(report["total_melts"], 2)
        self.assertEqual(report["total_net_weight"], 7.5)
        rows = report_rows(report)
        self.assertEqual(rows[1][3:7], ["Aluminium", "5.000", "Day", "operator2"])
        self.assertEqual(rows[0][4], "0.500")

    def test_melt_summary_remains_default_and_grouped(self):
        report = self.build(report_type="melt")
        self.assertEqual(len(report["items"]), 2)
        a = next(item for item in report["items"] if item["melt_number"] == "A1")
        self.assertEqual((a["product_count"], a["total_weight"]), (2, 7))
        with app.app_context():
            params = dict(self.params)
            params.pop("report_type")
            self.assertEqual(build_capture_report(params, "admin")["report_type"], "melt")

    def test_shared_filters_apply_to_individual_rows(self):
        report = self.build(furnace="1", shift=str(self.shift_id))
        self.assertEqual(len(report["items"]), 2)
        self.assertEqual(report["total_net_weight"], 7)
        report = self.build(custom_time="1", start_time="10:30", end_time="11:30")
        self.assertEqual([item["image_name"] for item in report["items"]], ["Aluminium"])
        self.assertEqual(report["total_net_weight"], 5)

    def test_csv_pdf_and_empty_reports(self):
        for filters in ({}, {"from_date": "2000-01-01", "to_date": "2000-01-01"}):
            with self.subTest(filters=filters):
                report = self.build(**filters)
                rows = list(csv.reader(io.StringIO(report_csv(report).decode("utf-8-sig"))))
                self.assertEqual(rows[0][0], "INDIVIDUAL CAPTURES REPORT")
                self.assertEqual(rows[4], list(INDIVIDUAL_COLUMNS))
                self.assertEqual(rows[-1][5], "captures")
                self.assertTrue(report_pdf(report).startswith(b"%PDF"))
                self.assertEqual(len(rows[5:-2]), len(report["items"]))

    def test_qr_download_contains_individual_rows(self):
        with patch("logs.routes.report_share_host", return_value="192.168.1.50"):
            response = self.fixture.client.get("/api/capture-report", query_string=self.params)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["report_type"], "individual")
        self.assertEqual(len(response.json["items"]), 3)
        self.assertEqual(self.fixture.client.get(response.json["qr_url"]).mimetype, "image/png")
        download = app.test_client().get(urlsplit(response.json["download_url"]).path)
        rows = list(csv.reader(io.StringIO(download.data.decode("utf-8-sig"))))
        self.assertEqual([row[3] for row in rows[5:8]], ["Copper", "Aluminium", "Brass"])

    def test_usb_backup_uses_selected_type(self):
        with patch("core.usb_backup.mounted_usb_drives", return_value=[self.fixture.drive]), patch.object(Path, "is_mount", return_value=True):
            response = self.fixture.client.post("/api/report-usb-backup", json=self.params)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["total_products"], 3)
        folder = Path(response.json["folder"])
        self.assertTrue(folder.name.startswith("Individual_Captures_Report_"))
        csv_file, pdf_file = [folder / name for name in response.json["files"]]
        self.assertIn("INDIVIDUAL CAPTURES REPORT", csv_file.read_text(encoding="utf-8-sig"))
        self.assertTrue(pdf_file.read_bytes().startswith(b"%PDF"))

    def test_invalid_report_type_is_rejected(self):
        payload = {**self.params, "report_type": "unknown"}
        self.assertEqual(self.fixture.client.get("/api/capture-report", query_string=payload).status_code, 400)
        self.assertEqual(self.fixture.client.post("/api/report-usb-backup", json=payload).status_code, 400)
        payload["report_type"] = []
        self.assertEqual(self.fixture.client.post("/api/report-usb-backup", json=payload).status_code, 400)

    def test_logs_operator_can_generate_individual_report(self):
        with app.app_context():
            connection = database_connection()
            user_id = connection.execute("INSERT INTO users (username,password_hash,role,created_at) VALUES ('report-operator','unused','user','2026-10-12')").lastrowid
            connection.execute("INSERT INTO user_permissions (user_id,screen) VALUES (?, 'logs')", (user_id,))
            connection.commit()
            connection.close()
        with self.fixture.client.session_transaction() as session:
            session.update(user_id=user_id, username="report-operator", role="user")
        page = self.fixture.client.get("/logs")
        self.assertIn(b'id="report-type"', page.data)
        self.assertIn(b'id="report-output"', page.data)
        with patch("logs.routes.report_share_host", return_value="192.168.1.50"):
            response = self.fixture.client.get("/api/capture-report", query_string=self.params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json["items"]), 3)
        self.assertEqual(response.json["generated_by"], "report-operator")


if __name__ == "__main__":
    unittest.main()
