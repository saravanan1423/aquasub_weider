import unittest

import test_usb_backup
from app import app
from core.database import database_connection


class SettingsUITests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_usb_backup.UsbBackupTests()
        self.fixture.setUp()
        self.client = self.fixture.client

    def tearDown(self):
        self.fixture.tearDown()

    def test_admin_has_settings_tiles_and_no_configuration_links_in_header(self):
        response = self.client.get('/settings')
        self.assertEqual(response.status_code, 200)
        header, content = response.data.split(b'</aside>', 1)
        for path in ('/communication', '/shifts', '/melt-settings', '/device-settings', '/images', '/users'):
            link = f'href="{path}"'.encode()
            self.assertNotIn(link, header)
            self.assertIn(link, content)
        self.assertIn(b'data-rustdesk-launch', content)
        self.assertIn(b'/static/theme.css', response.data)

    def test_all_existing_screens_share_compact_header(self):
        for path in ('/', '/logs', '/communication', '/shifts', '/melt-settings', '/device-settings', '/images', '/users'):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                header = response.data.split(b'</aside>', 1)[0]
                self.assertIn(b'href="/settings"', header)
                self.assertNotIn(b'href="/communication"', header)
                self.assertNotIn(b'href="/device-settings"', header)
                self.assertIn(b'/static/theme.css', response.data)

    def test_restricted_user_sees_only_permitted_settings(self):
        self.fixture.login_operator(logs_access=False)
        with self.client.session_transaction() as session:
            user_id = session['user_id']
        with app.app_context():
            with database_connection() as connection:
                connection.execute("INSERT INTO user_permissions (user_id,screen) VALUES (?, 'shifts')", (user_id,))
        response = self.client.get('/settings')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'href="/shifts"', response.data)
        for path in ('/communication', '/melt-settings', '/device-settings', '/images', '/users'):
            self.assertNotIn(f'href="{path}"'.encode(), response.data)
        self.assertNotIn(b'data-rustdesk-launch', response.data)
        self.assertEqual(self.client.get('/device-settings').status_code, 403)

    def test_logs_only_user_still_has_backup_but_no_settings_access(self):
        self.fixture.login_operator()
        self.assertEqual(self.client.get('/settings').status_code, 403)
        response = self.client.get('/logs')
        self.assertNotIn(b'href="/settings"', response.data)
        self.assertIn(b'id="report-output"', response.data)

    def test_settings_requires_login(self):
        with self.client.session_transaction() as session:
            session.clear()
        self.assertEqual(self.client.get('/settings').status_code, 302)


if __name__ == '__main__':
    unittest.main()
