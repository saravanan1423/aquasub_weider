import gc
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import app
from core.database import database_connection, initialize_database


class WeightRefreshSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.original_path = app.config["DATABASE_PATH"]
        app.config["DATABASE_PATH"] = Path(self.temporary.name) / "test.db"
        connection = sqlite3.connect(app.config["DATABASE_PATH"])
        connection.execute("""CREATE TABLE serial_settings (
            id INTEGER PRIMARY KEY, port TEXT NOT NULL, baud_rate INTEGER NOT NULL,
            data_bits INTEGER NOT NULL, parity TEXT NOT NULL, stop_bits TEXT NOT NULL,
            start_marker TEXT NOT NULL, end_marker TEXT NOT NULL,
            start_address INTEGER NOT NULL, end_address INTEGER NOT NULL,
            frame_timeout REAL NOT NULL, created_by TEXT NOT NULL, updated_at TEXT NOT NULL
        )""")
        connection.execute("""INSERT INTO serial_settings VALUES
            (1, '/dev/ttyUSB0', 9600, 8, 'none', '1', '$', ']', 5, 10, 1, 'admin', '2026-10-03')""")
        connection.commit()
        connection.close()
        initialize_database(app)
        with app.app_context():
            connection = database_connection()
            admin_id = connection.execute("SELECT id FROM users WHERE role='admin'").fetchone()[0]
            connection.close()
        self.client = app.test_client()
        with self.client.session_transaction() as session:
            session.update(user_id=admin_id, username="admin", role="admin")

    def tearDown(self):
        self.client = None
        app.config["DATABASE_PATH"] = self.original_path
        gc.collect()
        self.temporary.cleanup()

    def test_existing_settings_get_fast_defaults(self):
        settings = self.client.get("/api/settings").json
        self.assertEqual(settings["refresh_interval_ms"], 200)
        self.assertEqual(settings["frame_gap_ms"], 250)

    def test_timings_are_saved_and_validated(self):
        settings = self.client.get("/api/settings").json
        settings.update(refresh_interval_ms=100, frame_gap_ms=50)
        self.assertEqual(self.client.post("/api/settings", json=settings).status_code, 200)
        saved = self.client.get("/api/settings").json
        self.assertEqual(saved["refresh_interval_ms"], 100)
        self.assertEqual(saved["frame_gap_ms"], 50)
        for field, value in (("refresh_interval_ms", 99), ("refresh_interval_ms", 2001),
                             ("frame_gap_ms", 49), ("frame_gap_ms", 1001)):
            invalid = {**settings, field: value}
            self.assertEqual(self.client.post("/api/settings", json=invalid).status_code, 400)
        self.assertEqual(self.client.get("/api/settings").json["refresh_interval_ms"], 100)


if __name__ == "__main__":
    unittest.main()
