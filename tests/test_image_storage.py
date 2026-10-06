import gc
import io
import tempfile
import unittest
from pathlib import Path

from app import app
from core.database import database_connection, initialize_database
from master_image.routes import migrate_legacy_images


class ImageStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.original = {key: app.config[key] for key in ("DATABASE_PATH", "PRODUCT_IMAGE_DIR", "MASTER_IMAGE_DIR")}
        root = Path(self.temporary.name)
        app.config.update(DATABASE_PATH=root / "test.db", PRODUCT_IMAGE_DIR=root / "legacy", MASTER_IMAGE_DIR=root / "master_images")
        initialize_database(app)
        self.client = app.test_client()
        with app.app_context():
            connection = database_connection()
            admin_id = connection.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
            connection.close()
        with self.client.session_transaction() as session:
            session.update(user_id=admin_id, username="admin", role="admin")

    def tearDown(self):
        self.client = None
        app.config.update(self.original)
        gc.collect()
        self.temporary.cleanup()

    def upload(self, name, image_type, furnace_id=None):
        data = {"image_name": name, "image_type": image_type, "image": (io.BytesIO(b"image"), "sample.png", "image/png")}
        if furnace_id is not None:
            data["furnace_id"] = str(furnace_id)
        response = self.client.post("/api/product-images", data=data, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 201, response.json)
        return response.json

    def test_images_are_grouped_under_their_furnace(self):
        furnace = self.upload("Furnace A", "furnace")
        product = self.upload("Aluminium", "product", furnace["id"])
        furnace_path = Path(furnace["image_url"].removeprefix("/storage/master-images/"))
        product_path = Path(product["image_url"].removeprefix("/storage/master-images/"))
        self.assertEqual(product_path.parent, furnace_path.parent / "product_images")
        self.assertIn("Furnace_A", furnace_path.parent.name)
        self.assertTrue(product_path.name.startswith("Aluminium_"))
        self.assertEqual(self.client.get(furnace["image_url"]).data, b"image")
        self.assertEqual(self.client.get(product["image_url"]).data, b"image")

    def test_moving_product_preserves_captured_image(self):
        first = self.upload("A", "furnace")
        second = self.upload("B", "furnace")
        product = self.upload("Brass", "product", first["id"])
        with app.app_context():
            connection = database_connection()
            connection.execute("INSERT INTO weight_captures (product_image_id, image_name, image_url, weight, captured_at) VALUES (?, ?, ?, ?, ?)", (product["id"], "Brass", product["image_url"], "2", "2026-10-06"))
            connection.commit()
            connection.close()
        response = self.client.put(f"/api/product-images/{product['id']}", data={"image_name": "Brass", "image_type": "product", "furnace_id": str(second["id"])})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertNotEqual(response.json["image_url"], product["image_url"])
        self.assertEqual(self.client.get(product["image_url"]).data, b"image")
        self.assertEqual(self.client.get(response.json["image_url"]).data, b"image")
        second_path = Path(second["image_url"].removeprefix("/storage/master-images/"))
        self.assertIn((second_path.parent / "product_images").as_posix(), response.json["image_url"])

    def test_replacing_furnace_preserves_historical_furnace_image(self):
        furnace = self.upload("A", "furnace")
        product = self.upload("Copper", "product", furnace["id"])
        with app.app_context():
            connection = database_connection()
            connection.execute("INSERT INTO weight_captures (product_image_id,image_name,image_url,weight,captured_at,furnace_image_url) VALUES (?, 'Copper', ?, '5', '2026-10-06', ?)", (product["id"], product["image_url"], furnace["image_url"]))
            connection.commit()
            connection.close()
        response = self.client.put(f"/api/product-images/{furnace['id']}", data={"image_name": "A", "image_type": "furnace", "image": (io.BytesIO(b"replacement"), "new.png", "image/png")}, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(self.client.get(furnace["image_url"]).data, b"image")
        self.assertEqual(self.client.get(response.json["image_url"]).data, b"replacement")

    def test_legacy_image_urls_still_work(self):
        legacy = app.config["PRODUCT_IMAGE_DIR"]
        legacy.mkdir(parents=True)
        (legacy / "old.png").write_bytes(b"old image")
        self.assertEqual(self.client.get("/storage/product-images/old.png").data, b"old image")

    def test_legacy_records_are_migrated_without_breaking_capture_urls(self):
        legacy = app.config["PRODUCT_IMAGE_DIR"]
        legacy.mkdir(parents=True)
        (legacy / "furnace.png").write_bytes(b"furnace")
        (legacy / "product.png").write_bytes(b"product")
        with app.app_context():
            connection = database_connection()
            values = ("2026-10-06", "admin", "2026-10-06")
            furnace_id = connection.execute("INSERT INTO product_images (image_name,image_url,stored_filename,captured_at,created_by,created_at,image_type) VALUES ('Old Furnace','/storage/product-images/furnace.png','furnace.png',?,?,?,'furnace')", values).lastrowid
            product_id = connection.execute("INSERT INTO product_images (image_name,image_url,stored_filename,captured_at,created_by,created_at,image_type,furnace_id) VALUES ('Old Product','/storage/product-images/product.png','product.png',?,?,?,'product',?)", (*values, furnace_id)).lastrowid
            connection.execute("INSERT INTO weight_captures (product_image_id,image_name,image_url,weight,captured_at,furnace_image_url) VALUES (?, 'Old Product', '/storage/product-images/product.png', '2', '2026-10-06', '/storage/product-images/furnace.png')", (product_id,))
            connection.commit()
            connection.close()
        migrate_legacy_images(app)
        with app.app_context():
            connection = database_connection()
            rows = connection.execute("SELECT image_url FROM product_images ORDER BY id").fetchall()
            connection.close()
        self.assertIn("/Old_Furnace_", rows[0]["image_url"])
        self.assertIn("/product_images/Old_Product", rows[1]["image_url"])
        self.assertEqual(self.client.get(rows[0]["image_url"]).data, b"furnace")
        self.assertEqual(self.client.get(rows[1]["image_url"]).data, b"product")
        self.assertEqual(self.client.get("/storage/product-images/product.png").data, b"product")


if __name__ == "__main__":
    unittest.main()
