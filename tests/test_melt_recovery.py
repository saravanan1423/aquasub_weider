import gc
import tempfile
import unittest
from pathlib import Path

from app import app
from core.database import database_connection, initialize_database


class MeltRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.original_path = app.config["DATABASE_PATH"]
        app.config["DATABASE_PATH"] = Path(self.temporary.name) / "test.db"
        initialize_database(app)
        with app.app_context():
            connection = database_connection()
            self.admin_id = connection.execute("SELECT id FROM users WHERE role='admin'").fetchone()[0]
            self.furnaces, self.products = [], []
            for name in ("A", "B"):
                furnace = connection.execute("INSERT INTO product_images (image_name,image_url,stored_filename,captured_at,created_by,created_at,image_type) VALUES (?, '/furnace', ?, '2026-10-09', 'admin', '2026-10-09', 'furnace')", (name, f"furnace-{name}")).lastrowid
                product = connection.execute("INSERT INTO product_images (image_name,image_url,stored_filename,captured_at,created_by,created_at,image_type,furnace_id) VALUES ('Aluminium', '/product', ?, '2026-10-09', 'admin', '2026-10-09', 'product', ?)", (f"product-{name}", furnace)).lastrowid
                self.furnaces.append(furnace)
                self.products.append(product)
            self.operator_id = connection.execute("INSERT INTO users (username,password_hash,role,created_at) VALUES ('operator','unused','user','2026-10-09')").lastrowid
            connection.execute("INSERT INTO user_permissions (user_id,screen) VALUES (?, 'main')", (self.operator_id,))
            connection.commit()
            connection.close()
        self.client = self.new_client(self.admin_id, "admin")

    def tearDown(self):
        self.client = None
        app.config["DATABASE_PATH"] = self.original_path
        gc.collect()
        self.temporary.cleanup()

    def new_client(self, user_id, username):
        client = app.test_client()
        with client.session_transaction() as session:
            session.update(user_id=user_id, username=username)
        return client

    def capture(self, weight, furnace=0, client=None, **extra):
        response = (client or self.client).post("/api/weight-captures", json={
            "product_image_id": self.products[furnace], "furnace_id": self.furnaces[furnace],
            "weight": str(weight), **extra,
        })
        self.assertEqual(response.status_code, 201, response.json)
        return response.json

    def complete(self, melt, client=None):
        return (client or self.client).post("/api/melts/complete", json={
            "furnace_id": melt["furnace_id"], "melt_number": melt["melt_number"],
            "capture_ids": melt["capture_ids"],
        })

    def test_restart_restores_melt_and_capture_without_browser_state_continues_it(self):
        first = self.capture(5)
        second = self.capture(2)
        initialize_database(app)
        self.client = self.new_client(self.admin_id, "admin")
        recovered = self.client.get("/api/melts/unfinished").json["melt"]
        self.assertEqual(recovered["melt_number"], "A1")
        self.assertEqual(recovered["melt_total_weight"], 7)
        self.assertEqual(recovered["capture_ids"], [first["id"], second["id"]])
        self.assertEqual(recovered["last_product_id"], self.products[0])
        continued = self.capture(3)
        self.assertEqual(continued["melt_number"], "A1")
        self.assertEqual(continued["melt_count"], 3)
        self.assertEqual(continued["melt_total_weight"], 10)

    def test_only_explicit_completion_advances_serial_and_stale_capture_is_rejected(self):
        melt = self.capture(7)
        self.assertEqual(self.complete(melt).status_code, 200)
        self.assertIsNone(self.client.get("/api/melts/unfinished").json["melt"])
        stale = self.client.post("/api/weight-captures", json={
            "furnace_id": self.furnaces[0], "product_image_id": self.products[0],
            "weight": "1", "melt_number": "A1", "melt_serial": 1,
        })
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.client.post(f"/api/weight-captures/{melt['id']}/cancel").status_code, 409)
        self.assertEqual(self.capture(2)["melt_number"], "A2")

    def test_latest_open_melt_is_recovered_and_furnace_lookup_finds_other_open_melt(self):
        self.capture(5)
        b = self.capture(2, furnace=1)
        self.assertEqual(self.client.get("/api/melts/unfinished").json["melt"]["melt_number"], "B1")
        a = self.client.get(f"/api/melts/unfinished?furnace_id={self.furnaces[0]}").json["melt"]
        self.assertEqual(a["melt_number"], "A1")
        self.assertEqual(self.complete(b).status_code, 200)
        self.assertEqual(self.client.get("/api/melts/unfinished").json["melt"]["melt_number"], "A1")

    def test_next_operator_can_continue_and_complete_same_melt(self):
        first = self.capture(5)
        operator = self.new_client(self.operator_id, "operator")
        self.assertEqual(operator.get("/api/melts/unfinished").json["melt"]["melt_number"], "A1")
        melt = self.capture(2, client=operator)
        self.assertEqual(self.complete(melt, client=operator).status_code, 200)
        with app.app_context():
            connection = database_connection()
            rows = connection.execute("SELECT id,captured_by_username,melt_completed_at FROM weight_captures ORDER BY id").fetchall()
            connection.close()
        self.assertEqual(rows[0]["id"], first["id"])
        self.assertEqual([row["captured_by_username"] for row in rows], ["admin", "operator"])
        self.assertTrue(all(row["melt_completed_at"] for row in rows))

    def test_stale_completion_cannot_partially_complete_melt(self):
        first = self.capture(5)
        self.capture(2)
        self.assertEqual(self.complete(first).status_code, 409)
        recovered = self.client.get("/api/melts/unfinished").json["melt"]
        self.assertEqual(recovered["melt_count"], 2)
        self.assertEqual(self.complete(recovered).status_code, 200)

    def test_no_captures_or_cancelled_only_capture_has_no_open_melt(self):
        self.assertIsNone(self.client.get("/api/melts/unfinished").json["melt"])
        melt = self.capture(5)
        self.assertEqual(self.client.post(f"/api/weight-captures/{melt['id']}/cancel").status_code, 200)
        self.assertIsNone(self.client.get("/api/melts/unfinished").json["melt"])

    def test_recovered_total_enforces_limit_without_browser_melt_fields(self):
        self.client.put("/api/melt-threshold-settings", json={"melt_threshold_kg": "7"})
        self.capture(5)
        self.client = self.new_client(self.admin_id, "admin")
        response = self.client.post("/api/weight-captures", json={
            "furnace_id": self.furnaces[0], "product_image_id": self.products[0], "weight": "3",
        })
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json["code"], "threshold_exceeded")
        recovered = self.client.get("/api/melts/unfinished").json["melt"]
        self.assertEqual(recovered["melt_number"], "A1")
        self.assertEqual(recovered["melt_count"], 1)
        self.assertEqual(recovered["melt_total_weight"], 5)


if __name__ == "__main__":
    unittest.main()
