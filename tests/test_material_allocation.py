import gc
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app
from core.database import database_connection, initialize_database
from scale_stub import stable_scale_snapshot


class MaterialAllocationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.original_path = app.config["DATABASE_PATH"]
        app.config["DATABASE_PATH"] = Path(self.temporary.name) / "test.db"
        initialize_database(app)
        scale = patch("main_screen.routes.monitor.snapshot", side_effect=stable_scale_snapshot)
        scale.start()
        self.addCleanup(scale.stop)
        with app.app_context():
            connection = database_connection()
            self.admin = connection.execute("SELECT id FROM users WHERE role='admin'").fetchone()[0]
            self.furnaces, self.products = [], []
            for name in ("A", "B"):
                furnace = connection.execute("""
                    INSERT INTO product_images (image_name,image_url,stored_filename,captured_at,created_by,created_at,image_type)
                    VALUES (?, '/f', ?, '2026-10-14', 'admin', '2026-10-14', 'furnace')
                """, (name, f"f-{name}")).lastrowid
                self.furnaces.append(furnace)
                products = []
                for material in ("Aluminium", "Brass"):
                    products.append(connection.execute("""
                        INSERT INTO product_images (image_name,image_url,stored_filename,captured_at,created_by,created_at,image_type,furnace_id)
                        VALUES (?, '/p', ?, '2026-10-14', 'admin', '2026-10-14', 'product', ?)
                    """, (material, f"{name}-{material}", furnace)).lastrowid)
                self.products.append(products)
            self.operator = connection.execute("INSERT INTO users (username,password_hash,role,created_at) VALUES ('operator','unused','user','2026-10-14')").lastrowid
            connection.execute("INSERT INTO user_permissions VALUES (?, 'main')", (self.operator,))
            connection.commit()
            connection.close()
        self.client = app.test_client()
        self.login(self.admin, "admin")

    def tearDown(self):
        self.client = None
        app.config["DATABASE_PATH"] = self.original_path
        gc.collect()
        self.temporary.cleanup()

    def login(self, user, name):
        with self.client.session_transaction() as session:
            session.update(user_id=user, username=name)

    def context(self, furnace=0):
        return self.client.get(f"/api/material-allocations/{self.furnaces[furnace]}").json

    def allocate(self, materials=None, number="A1"):
        return self.client.put(f"/api/material-allocations/{self.furnaces[0]}", json={
            "melt_number": number,
            "materials": materials if materials is not None else [{"product_image_id": self.products[0][0], "allocated_kg": "5"}],
        })

    def capture(self, weight, product=0, furnace=0):
        return self.client.post("/api/weight-captures", json={
            "furnace_id": self.furnaces[furnace], "product_image_id": self.products[furnace][product], "weight": str(weight),
        })

    def test_cumulative_limit_exact_boundary_and_unselected_material(self):
        self.assertEqual(self.allocate().status_code, 200)
        self.assertEqual(self.capture("3").status_code, 201)
        rejected = self.capture("2.001")
        self.assertEqual(rejected.status_code, 409)
        self.assertIn("above its allocated 5 kg", rejected.json["error"])
        self.assertEqual(rejected.json["code"], "allocation_exceeded")
        self.assertEqual(self.capture("2").status_code, 201)
        self.assertEqual(self.capture("1", product=1).status_code, 409)
        context = self.context()
        self.assertEqual(context["materials"][0]["captured_kg"], "5")
        self.assertIsNone(context["materials"][1]["allocated_kg"])
        missing = self.capture("10", furnace=1)
        self.assertEqual(missing.status_code, 409)
        self.assertEqual(missing.json["code"], "allocation_required")

    def test_allocation_persists_and_next_melt_is_independent(self):
        self.allocate()
        melt = self.capture("5").json
        initialize_database(app)
        self.assertTrue(self.context()["configured"])
        self.assertEqual(self.context()["melt_number"], "A1")
        response = self.client.post("/api/melts/complete", json={
            "furnace_id": self.furnaces[0], "melt_number": "A1", "capture_ids": melt["capture_ids"],
        })
        self.assertEqual(response.status_code, 200)
        pending = self.context()
        self.assertEqual(pending["melt_number"], "A1")
        self.assertEqual(pending["status"], "awaiting_verification")
        self.assertEqual(self.capture("1").status_code, 409)
        initialize_database(app)
        self.assertEqual(self.context()["status"], "awaiting_verification")
        self.assertEqual(self.verify(pending).status_code, 200)
        self.assertEqual(self.context()["melt_number"], "A2")
        self.assertFalse(self.context()["configured"])
        self.assertEqual(self.allocate(number="A1").status_code, 409)
        self.assertEqual(self.allocate(number="A2").status_code, 200)

    def test_validation_and_existing_captures(self):
        for value in ("0", "-1", "abc", "1.0001", None):
            self.assertEqual(self.allocate([{"product_image_id": self.products[0][0], "allocated_kg": value}]).status_code, 400)
        self.assertEqual(self.allocate([{"product_image_id": self.products[1][0], "allocated_kg": "5"}]).status_code, 400)
        self.allocate()
        self.capture("4")
        self.assertEqual(self.allocate([]).status_code, 400)
        self.assertEqual(self.allocate([{"product_image_id": self.products[0][0], "allocated_kg": "3"}]).status_code, 400)
        self.assertEqual(self.allocate().status_code, 200)

    def test_cancellation_releases_allowance(self):
        self.allocate()
        melt = self.capture("5").json
        self.assertEqual(self.client.post(f'/api/weight-captures/{melt["id"]}/cancel').status_code, 200)
        self.assertEqual(self.context()["materials"][0]["captured_kg"], "0")
        self.assertEqual(self.capture("5").status_code, 201)

    def test_permissions_and_empty_selection(self):
        self.assertEqual(self.client.get("/material-allocation").status_code, 200)
        self.assertIn(b'Melt Material Allocation', self.client.get("/settings").data)
        self.assertEqual(self.allocate([]).status_code, 200)
        self.login(self.operator, "operator")
        self.assertEqual(self.client.get("/material-allocation").status_code, 403)
        self.assertEqual(self.allocate().status_code, 403)
        self.assertTrue(self.context()["configured"])
        self.assertEqual(self.capture("1").status_code, 409)

    def verify(self, context):
        return self.client.post(f"/api/material-allocations/{self.furnaces[0]}/complete", json={
            "melt_number": context["melt_number"], "main_completed_at": context["main_completed_at"],
        })

    def main_complete(self, melt):
        return self.client.post("/api/melts/complete", json={
            "furnace_id": self.furnaces[0], "melt_number": melt["melt_number"], "capture_ids": melt["capture_ids"],
        })

    def continue_melt(self, context):
        return self.client.post(f"/api/material-allocations/{self.furnaces[0]}/continue", json={
            "melt_number": context["melt_number"], "main_completed_at": context["main_completed_at"],
        })

    def test_continue_preserves_allocation_and_captures_and_allows_remaining_weight(self):
        self.allocate()
        melt = self.capture("3").json
        self.assertEqual(self.main_complete(melt).status_code, 200)
        pending = self.context()
        response = self.continue_melt(pending)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["melt_number"], "A1")
        self.assertEqual(response.json["status"], "allocated")
        self.assertIsNone(response.json["main_completed_at"])
        self.assertEqual(response.json["materials"][0]["allocated_kg"], "5")
        self.assertEqual(response.json["total_captured_kg"], "3")
        with app.app_context(), database_connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM weight_captures WHERE melt_completed_at IS NOT NULL").fetchone()[0], 0)
        initialize_database(app)
        self.assertEqual(self.context()["melt_number"], "A1")
        self.assertEqual(self.capture("2.001").status_code, 409)
        next_capture = self.capture("2")
        self.assertEqual(next_capture.status_code, 201, next_capture.json)
        self.assertEqual(next_capture.json["melt_number"], "A1")
        self.assertEqual(next_capture.json["melt_total_weight"], 5)
        self.assertEqual(next_capture.json["melt_count"], 2)
        self.assertEqual(self.main_complete(next_capture.json).status_code, 200)
        self.assertEqual(self.verify(self.context()).status_code, 200)
        self.assertEqual(self.context()["melt_number"], "A2")

    def test_continue_rejects_active_stale_and_verified_melts(self):
        self.allocate()
        self.assertEqual(self.continue_melt(self.context()).status_code, 409)
        melt = self.capture("3").json
        self.main_complete(melt)
        pending = self.context()
        stale = {**pending, "main_completed_at": "2000-01-01"}
        self.assertEqual(self.continue_melt(stale).status_code, 409)
        self.assertEqual(self.context()["status"], "awaiting_verification")
        self.assertEqual(self.continue_melt(pending).status_code, 200)
        self.assertEqual(self.continue_melt(pending).status_code, 409)
        self.assertEqual(self.verify(pending).status_code, 409)
        self.main_complete(melt)
        self.verify(self.context())
        self.assertEqual(self.continue_melt(pending).status_code, 409)

    def test_continue_requires_allocation_permission(self):
        self.allocate()
        melt = self.capture("3").json
        self.main_complete(melt)
        pending = self.context()
        self.login(self.operator, "operator")
        self.assertEqual(self.continue_melt(pending).status_code, 403)
        with app.app_context(), database_connection() as connection:
            connection.execute("INSERT INTO user_permissions VALUES (?, 'allocation')", (self.operator,))
        self.assertEqual(self.continue_melt(pending).status_code, 200)

    def test_increase_reopens_same_melt_and_final_review_advances(self):
        self.allocate()
        melt = self.capture("5").json
        self.assertEqual(self.verify(self.context()).status_code, 409)
        self.assertEqual(self.main_complete(melt).status_code, 200)
        pending = self.context()
        self.assertEqual(self.allocate().status_code, 400)
        self.assertEqual(self.allocate([{"product_image_id": self.products[0][0], "allocated_kg": "7"}]).status_code, 200)
        self.assertEqual(self.context()["status"], "allocated")
        self.assertEqual(self.verify(pending).status_code, 409)
        melt = self.capture("2").json
        self.assertEqual(melt["melt_number"], "A1")
        self.assertEqual(melt["melt_total_weight"], 7)
        self.assertEqual(melt["melt_count"], 2)
        self.assertEqual(self.main_complete(melt).status_code, 200)
        self.assertEqual(self.verify(pending).status_code, 409)
        self.assertEqual(self.verify(self.context()).status_code, 200)
        self.assertEqual(self.context()["melt_number"], "A2")
        self.assertEqual(self.verify(pending).status_code, 409)
        self.assertEqual(self.capture("1").status_code, 409)

    def test_operator_can_request_completion_but_cannot_verify_or_reopen(self):
        self.allocate()
        self.login(self.operator, "operator")
        melt = self.capture("3").json
        self.assertEqual(self.main_complete(melt).status_code, 200)
        pending = self.context()
        self.assertEqual(pending["status"], "awaiting_verification")
        self.assertEqual(self.verify(pending).status_code, 403)
        self.assertEqual(self.allocate().status_code, 403)
        self.assertEqual(self.capture("1").status_code, 409)

    def test_allocator_can_read_images_without_product_edit_permission(self):
        with app.app_context():
            connection = database_connection()
            connection.execute("DELETE FROM user_permissions WHERE user_id=?", (self.operator,))
            connection.execute("INSERT INTO user_permissions VALUES (?, 'allocation')", (self.operator,))
            connection.commit()
            connection.close()
        self.login(self.operator, "operator")
        self.assertEqual(self.client.get("/api/product-images").status_code, 200)
        self.assertEqual(self.client.post("/api/product-images").status_code, 403)
        self.assertEqual(self.client.get("/material-allocation").status_code, 200)
        self.assertEqual(self.allocate().status_code, 200)
        self.assertEqual(self.client.get("/melt-settings").status_code, 403)

    def test_decimal_allocation_and_capture_limit_are_exact(self):
        response = self.allocate([{"product_image_id": self.products[0][0], "allocated_kg": "5.125"}])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["materials"][0]["allocated_kg"], "5.125")
        self.assertEqual(self.capture("2.5").status_code, 201)
        self.assertEqual(self.capture("2.625").status_code, 201)
        self.assertEqual(self.context()["materials"][0]["captured_kg"], "5.125")
        self.assertEqual(self.capture("0.001").status_code, 409)


if __name__ == "__main__":
    unittest.main()
