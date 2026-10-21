import tempfile
import unittest
from pathlib import Path

from app import app
from core.database import database_connection, initialize_database


class ContactUsPageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.original_path = app.config["DATABASE_PATH"]
        app.config["DATABASE_PATH"] = Path(self.temporary.name) / "test.db"
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
        app.config["DATABASE_PATH"] = self.original_path
        self.temporary.cleanup()

    def test_settings_page_contains_contact_us_tile(self):
        res = self.client.get("/settings")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn('href="/contact-us"', html)
        self.assertIn('Contact Us', html)

    def test_contact_us_page_renders_successfully(self):
        res = self.client.get("/contact-us")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn('<h1>Contact Us</h1>', html)
        self.assertIn('href="/settings"', html)
        self.assertIn('weider-logo.png', html)
        self.assertIn('WEIDER', html)
        self.assertIn('WEIDER WEIGHING &amp; AUTOMATIONS', html)
        self.assertIn('Precision Weighing Solutions for a', html)
        self.assertIn('Smarter Tomorrow', html)
        self.assertIn('Reliable', html)
        self.assertIn('Industrial', html)
        self.assertIn('Better', html)
        self.assertIn('Trusted', html)
        self.assertIn('Door No.5/162/A, Site No.304', html)
        self.assertIn('Sri Sakthi Avenue, Nanjundapuram', html)
        self.assertIn('Kallipalayam Post', html)
        self.assertIn('Coimbatore - 641107', html)
        self.assertIn('Tamil Nadu, India', html)
        self.assertIn('service@weiderweighing.com', html)

    def test_contact_us_requires_login(self):
        with self.client.session_transaction() as session:
            session.clear()
        res = self.client.get("/contact-us")
        self.assertEqual(res.status_code, 302)

    def test_contact_us_css_is_served(self):
        res = self.client.get("/static/contact_us.css")
        self.assertEqual(res.status_code, 200)
        css = res.data.decode("utf-8")
        self.assertIn('.contact-us-card', css)
        self.assertIn('.contact-brand-name', css)
        self.assertIn('.contact-teal-divider', css)


if __name__ == "__main__":
    unittest.main()
