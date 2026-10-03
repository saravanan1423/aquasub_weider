import gc
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import app
from core.database import database_connection, initialize_database
from werkzeug.security import generate_password_hash


class LoginDropdownTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.original_path = app.config["DATABASE_PATH"]
        app.config["DATABASE_PATH"] = Path(self.temporary.name) / "test.db"
        connection = sqlite3.connect(app.config["DATABASE_PATH"])
        connection.execute("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                created_at TEXT NOT NULL
            )
        """)
        connection.execute(
            "INSERT INTO users (username,password_hash,role,created_at) VALUES (?,?,?,?)",
            ("existing", generate_password_hash("existing123"), "user", "2026-10-03"),
        )
        connection.commit()
        connection.close()
        initialize_database(app)
        with app.app_context():
            connection = database_connection()
            self.admin_id = connection.execute("SELECT id FROM users WHERE role='admin'").fetchone()[0]
            connection.close()
        self.admin_client = app.test_client()
        self.login_client = app.test_client()
        with self.admin_client.session_transaction() as session:
            session.update(user_id=self.admin_id, username="admin")

    def tearDown(self):
        self.admin_client = None
        self.login_client = None
        app.config["DATABASE_PATH"] = self.original_path
        gc.collect()
        self.temporary.cleanup()

    def test_existing_users_are_hidden_until_selected(self):
        page = self.login_client.get("/login")
        self.assertIn(b'<input id="username" name="username" type="text"', page.data)
        self.assertNotIn(b'data-username="admin"', page.data)
        self.assertNotIn(b'data-username="existing"', page.data)
        users = self.admin_client.get("/api/users").json
        existing = next(user for user in users if user["username"] == "existing")
        self.assertFalse(existing["show_in_login"])

        response = self.admin_client.put(f'/api/users/{existing["id"]}', json={
            "username": "existing", "password": "", "permissions": [], "show_in_login": True
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'data-username="existing"', self.login_client.get("/login").data)

        response = self.admin_client.put(f'/api/users/{existing["id"]}', json={
            "username": "existing", "password": "", "permissions": [], "show_in_login": False
        })
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b'data-username="existing"', self.login_client.get("/login").data)

    def test_new_user_visibility_and_login(self):
        response = self.admin_client.post("/api/users", json={
            "username": "operator", "password": "operator123",
            "permissions": ["main"], "show_in_login": True,
        })
        self.assertEqual(response.status_code, 201)
        self.assertIn(b'data-username="operator"', self.login_client.get("/login").data)
        failed = self.login_client.post("/login", data={
            "username": "operator", "password": "wrong"
        })
        self.assertEqual(failed.status_code, 200)
        self.assertIn(b'name="username" type="text" value="operator"', failed.data)
        success = self.login_client.post("/login", data={
            "username": "operator", "password": "operator123"
        })
        self.assertEqual(success.status_code, 302)
        self.assertEqual(success.headers["Location"], "/")

    def test_admin_can_sign_in_by_typing_username(self):
        response = self.login_client.post("/login", data={
            "username": "admin", "password": "admin@123"
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")

    def test_visibility_must_be_boolean(self):
        response = self.admin_client.post("/api/users", json={
            "username": "operator", "password": "operator123",
            "permissions": [], "show_in_login": "false",
        })
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
