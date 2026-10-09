import gc
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scale_stub import stable_scale_snapshot

from app import app
from core.database import database_connection, initialize_database


class ThresholdTests(unittest.TestCase):
    def setUp(self):
        scale_patch = patch("main_screen.routes.monitor.snapshot", side_effect=stable_scale_snapshot)
        scale_patch.start()
        self.addCleanup(scale_patch.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.original_path = app.config["DATABASE_PATH"]
        app.config["DATABASE_PATH"] = Path(self.temporary.name) / "test.db"
        with sqlite3.connect(app.config["DATABASE_PATH"]) as connection:
            connection.execute("""
                CREATE TABLE device_settings (
                    id INTEGER PRIMARY KEY, device_id TEXT NOT NULL,
                    location TEXT NOT NULL, installed_date TEXT NOT NULL,
                    individual_threshold_kg TEXT, melt_threshold_kg TEXT,
                    updated_at TEXT NOT NULL
                )
            """)
            connection.execute(
                "INSERT INTO device_settings VALUES (1, '00123', 'Plant A', '2026-10-03', '8', '10', '2026-10-03')"
            )
        initialize_database(app)
        with app.app_context():
            connection = database_connection()
            self.admin_id = connection.execute("SELECT id FROM users WHERE role='admin'").fetchone()[0]
            captured_at = "2026-10-03T00:00:00"
            self.furnace_id = connection.execute("""
                INSERT INTO product_images (
                    image_name, image_url, stored_filename, captured_at,
                    created_by, created_at, image_type
                ) VALUES ('Furnace A', '/a', 'test-furnace', ?, 'admin', ?, 'furnace')
            """, (captured_at, captured_at)).lastrowid
            self.product_id = connection.execute("""
                INSERT INTO product_images (
                    image_name, image_url, stored_filename, captured_at,
                    created_by, created_at, image_type, furnace_id
                ) VALUES ('Aluminium', '/p', 'test-product', ?, 'admin', ?, 'product', ?)
            """, (captured_at, captured_at, self.furnace_id)).lastrowid
            connection.commit()
            connection.close()
        self.client = app.test_client()
        with self.client.session_transaction() as session:
            session.update(user_id=self.admin_id, username="admin")

    def tearDown(self):
        self.client = None
        app.config["DATABASE_PATH"] = self.original_path
        gc.collect()
        self.temporary.cleanup()

    def capture(self, weight, melt=None):
        payload = {
            "furnace_id": self.furnace_id,
            "product_image_id": self.product_id,
            "weight": weight,
        }
        if melt:
            payload.update(melt_number=melt["melt_number"], melt_serial=melt["melt_serial"])
        return self.client.post("/api/weight-captures", json=payload)

    def test_legacy_settings_migrate_and_limits_block_only_excess(self):
        settings = self.client.get("/api/melt-threshold-settings").json
        self.assertEqual(settings, {"individual_threshold_kg": "8", "melt_threshold_kg": "10"})
        self.assertNotIn("individual_threshold_kg", self.client.get("/api/device-settings").json)
        self.assertIn(b'id="melt-threshold-form"', self.client.get("/melt-settings").data)
        self.assertNotIn(b'id="melt-threshold-form"', self.client.get("/device-settings").data)
        first = self.capture("8")
        self.assertEqual(first.status_code, 201)
        over_individual = self.capture("8.001", first.json)
        self.assertEqual(over_individual.status_code, 409)
        self.assertEqual(over_individual.json["code"], "threshold_exceeded")
        self.assertIn("Contact admin", over_individual.json["error"])
        at_total = self.capture("2", first.json)
        self.assertEqual(at_total.status_code, 201)
        self.assertEqual(at_total.json["melt_total_weight"], 10)
        over_total = self.capture("0.001", first.json)
        self.assertEqual(over_total.status_code, 409)
        self.assertIn("Contact admin", over_total.json["error"])
        with app.app_context():
            connection = database_connection()
            self.assertEqual(connection.execute("SELECT count(*) FROM weight_captures").fetchone()[0], 2)
            connection.close()

    def test_invalid_thresholds_are_rejected(self):
        for value in ("0", "-1", "1.0001", "abc"):
            with self.subTest(value=value):
                response = self.client.put(
                    "/api/melt-threshold-settings", json={"individual_threshold_kg": value}
                )
                self.assertEqual(response.status_code, 400)

    def test_admin_can_update_limits_without_changing_device_details(self):
        response = self.client.put("/api/melt-threshold-settings", json={
            "individual_threshold_kg": "9.5", "melt_threshold_kg": "12"
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/melt-threshold-settings").json, {
            "individual_threshold_kg": "9.5", "melt_threshold_kg": "12"
        })
        device = self.client.get("/api/device-settings").json
        self.assertEqual(device["device_id"], "00123")
        self.assertNotIn("melt_threshold_kg", device)

    def test_unconfigured_limits_keep_existing_capture_behavior(self):
        response = self.client.put("/api/melt-threshold-settings", json={
            "individual_threshold_kg": "", "melt_threshold_kg": ""
        })
        self.assertEqual(response.status_code, 200)
        response = self.capture("1000")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["melt_total_weight"], 1000)

    def test_only_admin_can_edit_thresholds(self):
        with app.app_context():
            connection = database_connection()
            user_id = connection.execute("""
                INSERT INTO users (username, password_hash, role, created_at)
                VALUES ('operator', 'unused', 'user', '2026-10-03')
            """).lastrowid
            connection.execute(
                "INSERT INTO user_permissions (user_id, screen) VALUES (?, 'melts')", (user_id,)
            )
            connection.commit()
            connection.close()
        with self.client.session_transaction() as session:
            session.update(user_id=user_id, username="operator")
        self.assertEqual(self.client.get("/api/melt-threshold-settings").status_code, 403)
        self.assertEqual(self.client.put("/api/melt-threshold-settings", json={}).status_code, 403)
        page = self.client.get("/melt-settings")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn(b'id="melt-threshold-form"', page.data)


if __name__ == "__main__":
    unittest.main()
