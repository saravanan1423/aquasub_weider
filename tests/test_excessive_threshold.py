import gc
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app
from core.database import database_connection, initialize_database
try:
    from scale_stub import stable_scale_snapshot
except ImportError:
    from tests.scale_stub import stable_scale_snapshot


class ExcessiveThresholdTests(unittest.TestCase):
    def setUp(self):
        scale_patch = patch("main_screen.routes.monitor.snapshot", side_effect=stable_scale_snapshot)
        scale_patch.start()
        self.addCleanup(scale_patch.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.original_path = app.config["DATABASE_PATH"]
        app.config["DATABASE_PATH"] = Path(self.temporary.name) / "test.db"
        initialize_database(app)
        with app.app_context():
            connection = database_connection()
            self.admin_id = connection.execute("SELECT id FROM users WHERE role='admin'").fetchone()[0]
            captured_at = "2026-10-21T10:00:00"
            self.furnace_id = connection.execute("""
                INSERT INTO product_images (
                    image_name, image_url, stored_filename, captured_at,
                    created_by, created_at, image_type
                ) VALUES ('Furnace 1', '/f', 'f1', ?, 'admin', ?, 'furnace')
            """, (captured_at, captured_at)).lastrowid
            self.product_id = connection.execute("""
                INSERT INTO product_images (
                    image_name, image_url, stored_filename, captured_at,
                    created_by, created_at, image_type, furnace_id
                ) VALUES ('Copper', '/p', 'p1', ?, 'admin', ?, 'product', ?)
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
            "weight": str(weight),
        }
        if melt:
            payload.update(melt_number=melt["melt_number"], melt_serial=melt["melt_serial"])
        return self.client.post("/api/weight-captures", json=payload)

    def test_threshold_percent_settings_crud(self):
        # 1. Update threshold settings including threshold_percent
        response = self.client.put("/api/melt-threshold-settings", json={
            "individual_threshold_kg": "500",
            "melt_threshold_kg": "2000",
            "threshold_percent": "30",
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["ok"])
        self.assertEqual(response.json["threshold_percent"], "30")

        # 2. GET returns threshold_percent
        get_res = self.client.get("/api/melt-threshold-settings")
        self.assertEqual(get_res.status_code, 200)
        self.assertEqual(get_res.json["threshold_percent"], "30")

        # 3. Invalid percentage formats or ranges
        bad_res = self.client.put("/api/melt-threshold-settings", json={
            "threshold_percent": "150",
        })
        self.assertEqual(bad_res.status_code, 400)

        bad_res2 = self.client.put("/api/melt-threshold-settings", json={
            "threshold_percent": "-5",
        })
        self.assertEqual(bad_res2.status_code, 400)

        bad_res3 = self.client.put("/api/melt-threshold-settings", json={
            "threshold_percent": "abc",
        })
        self.assertEqual(bad_res3.status_code, 400)

    def test_allocation_context_includes_threshold_percent(self):
        # When not set
        res = self.client.get(f"/api/material-allocations/{self.furnace_id}")
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.json.get("threshold_percent"))

        # When set to 30%
        self.client.put("/api/melt-threshold-settings", json={"threshold_percent": "30"})
        res2 = self.client.get(f"/api/material-allocations/{self.furnace_id}")
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.json.get("threshold_percent"), "30")

    def test_weight_capture_respects_excessive_threshold_percent(self):
        # Clear individual limits so they don't block
        self.client.put("/api/melt-threshold-settings", json={
            "individual_threshold_kg": "",
            "melt_threshold_kg": "",
            "threshold_percent": "30",
        })

        ctx = self.client.get(f"/api/material-allocations/{self.furnace_id}").json
        melt_number = ctx["melt_number"]

        # Allocate 100 kg for Copper
        alloc_res = self.client.put(f"/api/material-allocations/{self.furnace_id}", json={
            "melt_number": melt_number,
            "materials": [{"product_image_id": self.product_id, "allocated_kg": "100"}],
        })
        self.assertEqual(alloc_res.status_code, 200)

        # Capture 100 kg -> OK (100 <= 100 * 1.30 = 130)
        res1 = self.capture("100")
        self.assertEqual(res1.status_code, 201)

        # Capture another 25 kg -> Total 125 kg <= 130 kg -> OK
        res2 = self.capture("25", res1.json)
        self.assertEqual(res2.status_code, 201)

        # Capture another 10 kg -> Total 135 kg > 130 kg -> Should fail with allocation_exceeded
        res3 = self.capture("10", res1.json)
        self.assertEqual(res3.status_code, 409)
        self.assertEqual(res3.json["error"], "Copper in melt " + melt_number + " would total 135 kg, above the 130 kg limit (100 kg allocated + 30% threshold). Contact admin.")

    def test_main_screen_allows_1kg_plus_threshold_and_blocks_beyond(self):
        # Scenario from user:
        # Material allocation allocates 1 kg.
        # Threshold is 10%.
        # Main screen allows up to 1kg + 10% (1.1kg).
        # Beyond 1.1kg shows alert (409 error).
        # When 1.2kg with 20% threshold, it shows 1.2kg captured against 1kg allocated.
        self.client.put("/api/melt-threshold-settings", json={
            "individual_threshold_kg": "",
            "melt_threshold_kg": "",
            "threshold_percent": "10",
        })

        ctx = self.client.get(f"/api/material-allocations/{self.furnace_id}").json
        melt_number = ctx["melt_number"]

        # 1. In Melt Material Allocation, allocate 1 kg
        alloc_res = self.client.put(f"/api/material-allocations/{self.furnace_id}", json={
            "melt_number": melt_number,
            "materials": [{"product_image_id": self.product_id, "allocated_kg": "1"}],
        })
        self.assertEqual(alloc_res.status_code, 200)

        # 2. Main screen captures 1.0 kg -> Success
        res1 = self.capture("1", melt=None)
        self.assertEqual(res1.status_code, 201)

        # 3. Main screen captures another 0.1 kg -> Total 1.1 kg <= 1.1 kg -> Success
        res2 = self.capture("0.1", res1.json)
        self.assertEqual(res2.status_code, 201)

        # Verify context shows 1.1 captured and 1 allocated
        ctx_after = self.client.get(f"/api/material-allocations/{self.furnace_id}").json
        copper = next(m for m in ctx_after["materials"] if m["id"] == self.product_id)
        self.assertEqual(copper["allocated_kg"], "1")
        self.assertEqual(copper["captured_kg"], "1.1")

        # 4. Attempting to capture another 0.1 kg -> Total 1.2 kg > 1.1 kg limit -> Fails with alert
        res3 = self.capture("0.1", res1.json)
        self.assertEqual(res3.status_code, 409)
        self.assertEqual(res3.json["code"], "allocation_exceeded")
        self.assertEqual(
            res3.json["error"],
            f"Copper in melt {melt_number} would total 1.2 kg, above the 1.1 kg limit (1 kg allocated + 10% threshold). Contact admin."
        )

    def test_weight_capture_without_threshold_percent_blocks_excess(self):
        # Clear limits and threshold_percent
        self.client.put("/api/melt-threshold-settings", json={
            "individual_threshold_kg": "",
            "melt_threshold_kg": "",
            "threshold_percent": "",
        })

        ctx = self.client.get(f"/api/material-allocations/{self.furnace_id}").json
        melt_number = ctx["melt_number"]

        # Allocate 100 kg for Copper
        alloc_res = self.client.put(f"/api/material-allocations/{self.furnace_id}", json={
            "melt_number": melt_number,
            "materials": [{"product_image_id": self.product_id, "allocated_kg": "100"}],
        })
        self.assertEqual(alloc_res.status_code, 200)

        # Capture 100 kg -> OK
        res1 = self.capture("100")
        self.assertEqual(res1.status_code, 201)

        # Capture another 1 kg -> Total 101 kg > 100 kg -> Should fail
        res2 = self.capture("1", res1.json)
        self.assertEqual(res2.status_code, 409)
        self.assertEqual(res2.json["error"], "Copper in melt " + melt_number + " would total 101 kg, above its allocated 100 kg. Contact admin.")

    def test_ui_templates_render_threshold_percent_fields(self):
        settings_page = self.client.get("/melt-settings")
        self.assertIn(b'id="threshold-percent"', settings_page.data)
        self.assertIn(b'name="threshold_percent"', settings_page.data)
        self.assertIn(b"Excessive threshold (%)", settings_page.data)

        allocation_page = self.client.get("/material-allocation")
        # Threshold percent controls are NOT in material allocation (they apply on the main screen)
        self.assertNotIn(b'id="allocation-add-percent"', allocation_page.data)
        self.assertNotIn(b'id="allocation-quick-percents"', allocation_page.data)
        self.assertNotIn(b'id="allocation-threshold-wrap"', allocation_page.data)
        self.assertIn(b'id="allocation-add-kg"', allocation_page.data)


if __name__ == "__main__":
    unittest.main()
