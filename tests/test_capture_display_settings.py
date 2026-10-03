import gc
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from app import app
from core.database import database_connection, initialize_database


class CaptureDisplaySettingsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.original_path = app.config["DATABASE_PATH"]
        app.config["DATABASE_PATH"] = Path(self.temporary.name) / "test.db"
        initialize_database(app)
        with app.app_context():
            connection = database_connection()
            admin_id = connection.execute("SELECT id FROM users WHERE role='admin'").fetchone()[0]
            operator_id = connection.execute("""
                INSERT INTO users (username, password_hash, role, created_at)
                VALUES ('operator', 'unused', 'user', '2026-10-03')
            """).lastrowid
            connection.execute(
                "INSERT INTO user_permissions (user_id, screen) VALUES (?, 'main')", (operator_id,)
            )
            connection.execute(
                "INSERT INTO user_permissions (user_id, screen) VALUES (?, 'melts')", (operator_id,)
            )
            connection.commit()
            connection.close()
        self.admin_client = app.test_client()
        self.operator_client = app.test_client()
        with self.admin_client.session_transaction() as session:
            session.update(user_id=admin_id, username="admin", role="admin")
        with self.operator_client.session_transaction() as session:
            session.update(user_id=operator_id, username="operator", role="user")

    def tearDown(self):
        self.admin_client = None
        self.operator_client = None
        app.config["DATABASE_PATH"] = self.original_path
        gc.collect()
        self.temporary.cleanup()

    def test_default_and_saved_duration(self):
        self.assertEqual(self.operator_client.get("/api/capture-display-settings").json,
                         {"success_display_seconds": 10})
        response = self.admin_client.put("/api/capture-display-settings", json={
            "success_display_seconds": 25
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.operator_client.get("/api/capture-display-settings").json,
                         {"success_display_seconds": 25})
        self.assertIn(b'id="capture-display-form"', self.admin_client.get("/melt-settings").data)
        self.assertNotIn(b'id="capture-display-form"', self.operator_client.get("/melt-settings").data)

    def test_invalid_durations_are_rejected(self):
        for value in (0, -1, 301, 1.5, "abc", True, None):
            with self.subTest(value=value):
                response = self.admin_client.put("/api/capture-display-settings", json={
                    "success_display_seconds": value
                })
                self.assertEqual(response.status_code, 400)

    def test_operator_cannot_change_duration_and_success_button_is_removed(self):
        self.assertEqual(self.operator_client.put("/api/capture-display-settings", json={
            "success_display_seconds": 5
        }).status_code, 403)
        page = self.operator_client.get("/").data
        self.assertNotIn(b'id="complete-melt-success"', page)
        self.assertIn(b'id="complete-melt-button"', page)

    def test_capture_uses_saved_duration_and_cancel_window(self):
        with app.app_context():
            connection = database_connection()
            furnace_id = connection.execute("""
                INSERT INTO product_images (image_name, image_url, stored_filename,
                    captured_at, created_by, created_at, image_type)
                VALUES ('A', '/a', 'furnace-a', '2026-10-03', 'admin', '2026-10-03', 'furnace')
            """).lastrowid
            product_id = connection.execute("""
                INSERT INTO product_images (image_name, image_url, stored_filename,
                    captured_at, created_by, created_at, image_type, furnace_id)
                VALUES ('Scrap', '/scrap', 'scrap', '2026-10-03', 'admin', '2026-10-03', 'product', ?)
            """, (furnace_id,)).lastrowid
            connection.commit()
            connection.close()

        self.admin_client.put("/api/capture-display-settings", json={"success_display_seconds": 25})
        capture = self.admin_client.post("/api/weight-captures", json={
            "furnace_id": furnace_id, "product_image_id": product_id, "weight": "18"
        })
        self.assertEqual(capture.status_code, 201)
        self.assertEqual(capture.json["success_display_seconds"], 25)
        old_time = (datetime.now().astimezone() - timedelta(seconds=20)).isoformat()
        with app.app_context():
            connection = database_connection()
            connection.execute("UPDATE weight_captures SET captured_at=? WHERE id=?",
                               (old_time, capture.json["id"]))
            connection.commit()
            connection.close()
        self.assertEqual(self.admin_client.post(
            f'/api/weight-captures/{capture.json["id"]}/cancel').status_code, 200)

        self.admin_client.put("/api/capture-display-settings", json={"success_display_seconds": 10})
        expired = self.admin_client.post("/api/weight-captures", json={
            "furnace_id": furnace_id, "product_image_id": product_id, "weight": "18"
        })
        with app.app_context():
            connection = database_connection()
            connection.execute("UPDATE weight_captures SET captured_at=? WHERE id=?",
                               (old_time, expired.json["id"]))
            connection.commit()
            connection.close()
        self.assertEqual(self.admin_client.post(
            f'/api/weight-captures/{expired.json["id"]}/cancel').status_code, 400)


if __name__ == "__main__":
    unittest.main()
