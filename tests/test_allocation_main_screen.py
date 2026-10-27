import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import unittest
from app import app
import test_usb_backup

class AllocationMainScreenTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_usb_backup.UsbBackupTests()
        self.fixture.setUp()
        self.admin = self.fixture.client

    def tearDown(self):
        self.fixture.tearDown()

    def test_allocation_user_login_and_root_redirect(self):
        # Create a user with only allocation permission
        res = self.admin.post('/api/users', json={
            'username': 'alloc_user',
            'password': 'password123',
            'permissions': ['allocation'],
            'default_screen': 'allocation'
        })
        self.assertEqual(res.status_code, 201)

        # Login as alloc_user
        user_client = app.test_client()
        login_res = user_client.post('/login', data={'username': 'alloc_user', 'password': 'password123'})
        self.assertEqual(login_res.status_code, 302)
        self.assertTrue(login_res.location.endswith('/material-allocation'), login_res.location)

        # Hitting root '/' should also redirect to /material-allocation (not 403 Forbidden)
        root_res = user_client.get('/')
        self.assertEqual(root_res.status_code, 302)
        self.assertTrue(root_res.location.endswith('/material-allocation'), root_res.location)

        # Material allocation page itself should be accessible
        page_res = user_client.get('/material-allocation')
        self.assertEqual(page_res.status_code, 200)
        self.assertIn(b'href="/material-allocation"', page_res.data)
        self.assertIn(b'Material Allocation', page_res.data)

    def test_user_management_displays_allocation_at_start_and_default_screen(self):
        page = self.admin.get('/users')
        self.assertEqual(page.status_code, 200)
        html = page.data.decode('utf-8')

        # Check screen access ordering: 'main' followed by 'allocation'
        main_pos = html.find('value="main"')
        alloc_pos = html.find('value="allocation"')
        settings_pos = html.find('value="settings"')
        self.assertNotEqual(main_pos, -1)
        self.assertNotEqual(alloc_pos, -1)
        self.assertNotEqual(settings_pos, -1)
        self.assertTrue(main_pos < alloc_pos < settings_pos, "Expected main < allocation < settings in screen access")

        # Check default screen selector exists in form
        self.assertIn('id="new-default-screen"', html)
        self.assertIn('value="allocation"', html)

    def test_api_users_persists_default_screen(self):
        res = self.admin.post('/api/users', json={
            'username': 'test_screen_user',
            'password': 'password123',
            'permissions': ['main', 'allocation'],
            'default_screen': 'allocation'
        })
        self.assertEqual(res.status_code, 201)
        user_id = res.json['id']

        # Verify GET /api/users returns default_screen
        users = self.admin.get('/api/users').json
        user = next(u for u in users if u['id'] == user_id)
        self.assertEqual(user['default_screen'], 'allocation')

        # Update to main
        put_res = self.admin.put(f'/api/users/{user_id}', json={
            'username': 'test_screen_user',
            'permissions': ['main', 'allocation'],
            'default_screen': 'main'
        })
        self.assertEqual(put_res.status_code, 200)

        users = self.admin.get('/api/users').json
        user = next(u for u in users if u['id'] == user_id)
        self.assertEqual(user['default_screen'], 'main')

    def test_navigation_sidebar_shows_material_allocation(self):
        # Admin has all permissions, should see both Main Screen and Material Allocation
        page = self.admin.get('/')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'href="/"', page.data)
        self.assertIn(b'href="/material-allocation"', page.data)
        self.assertIn(b'<b>Material Allocation</b>', page.data)

    def test_api_users_persists_keyboard_enabled(self):
        res = self.admin.post('/api/users', json={
            'username': 'keyboard_user',
            'password': 'password123',
            'permissions': ['main'],
            'keyboard_enabled': False
        })
        self.assertEqual(res.status_code, 201)
        user_id = res.json['id']

        # Verify GET /api/users returns keyboard_enabled=False
        users = self.admin.get('/api/users').json
        user = next(u for u in users if u['id'] == user_id)
        self.assertFalse(user['keyboard_enabled'])

        # Update to keyboard_enabled=True
        put_res = self.admin.put(f'/api/users/{user_id}', json={
            'username': 'keyboard_user',
            'permissions': ['main'],
            'keyboard_enabled': True
        })
        self.assertEqual(put_res.status_code, 200)

        users = self.admin.get('/api/users').json
        user = next(u for u in users if u['id'] == user_id)
        self.assertTrue(user['keyboard_enabled'])

    def test_page_renders_keyboard_dataset_based_on_user(self):
        # Admin has keyboard_enabled=True by default
        admin_page = self.admin.get('/')
        self.assertIn(b'dataset.keyboard = "true"', admin_page.data)

        # Create a user with keyboard_enabled=False
        self.admin.post('/api/users', json={
            'username': 'nokeyboard',
            'password': 'password123',
            'permissions': ['main'],
            'keyboard_enabled': False
        })

        client = app.test_client()
        login_res = client.post('/login', data={'username': 'nokeyboard', 'password': 'password123'})
        self.assertEqual(login_res.status_code, 302)

        page = client.get('/')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'dataset.keyboard = "false"', page.data)

        shift_res = client.get('/api/current-shift')
        self.assertEqual(shift_res.status_code, 200)
        self.assertFalse(shift_res.json.get('keyboard_enabled'))

if __name__ == '__main__':
    unittest.main()

