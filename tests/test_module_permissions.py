import unittest

from app import app
import test_usb_backup


class ModulePermissionTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_usb_backup.UsbBackupTests()
        self.fixture.setUp()
        self.admin=self.fixture.client

    def tearDown(self):
        self.fixture.tearDown()

    def create_user(self, permissions):
        response=self.admin.post('/api/users',json={'username':'moduleuser','password':'password123','permissions':permissions})
        self.assertEqual(response.status_code,201,response.json)
        user=next(user for user in self.admin.get('/api/users').json if user['username']=='moduleuser')
        client=app.test_client()
        login=client.post('/login',data={'username':'moduleuser','password':'password123'})
        return user,client,login

    def test_create_and_update_accept_new_permissions_and_render_controls(self):
        page=self.admin.get('/users')
        self.assertIn(b'value="allocation"',page.data)
        self.assertIn(b'value="backup"',page.data)
        user,client,_=self.create_user(['allocation','backup'])
        self.assertEqual(set(user['permissions']),{'allocation','backup'})
        self.assertEqual(client.get('/material-allocation').status_code,200)
        self.assertEqual(client.get('/backup-settings').status_code,200)
        result=self.admin.put(f"/api/users/{user['id']}",json={'username':'moduleuser','permissions':[]})
        self.assertEqual(result.status_code,200,result.json)
        self.assertEqual(client.get('/material-allocation').status_code,403)
        self.assertEqual(client.get('/backup-settings').status_code,403)
        self.assertEqual(client.get('/api/backup-history').status_code,403)

    def test_allocation_only_login_navigation_and_isolation(self):
        _,client,login=self.create_user(['allocation'])
        self.assertTrue(login.location.endswith('/material-allocation'),login.location)
        page=client.get('/settings')
        self.assertEqual(page.status_code,200)
        self.assertIn(b'href="/material-allocation"',page.data)
        self.assertNotIn(b'href="/melt-settings"',page.data)
        self.assertNotIn(b'href="/backup-settings"',page.data)
        self.assertEqual(client.get('/api/product-images').status_code,200)
        self.assertEqual(client.post('/api/product-images').status_code,403)
        self.assertEqual(client.get('/api/melt-number-settings').status_code,403)
        self.assertEqual(client.get('/api/backup-settings').status_code,403)
        self.assertEqual(client.get('/users').status_code,403)

    def test_backup_only_can_save_schedules_and_view_history(self):
        _,client,login=self.create_user(['backup'])
        self.assertTrue(login.location.endswith('/backup-settings'),login.location)
        page=client.get('/settings')
        self.assertEqual(page.status_code,200)
        self.assertIn(b'href="/backup-settings"',page.data)
        self.assertNotIn(b'href="/material-allocation"',page.data)
        self.assertNotIn(b'href="/device-settings"',page.data)
        payload={'enabled':True,'frequency':'daily','backup_time':'16:00','month_day':1}
        self.assertEqual(client.put('/api/backup-settings',json=payload).status_code,200)
        self.assertEqual(client.get('/api/backup-settings').json['settings']['frequency'],'daily')
        self.assertEqual(client.get('/api/backup-history').status_code,200)
        self.assertEqual(client.get('/api/automatic-backups').status_code,200)
        self.assertIn(b'href="/backup-settings"',client.get('/backup-history').data)
        self.assertEqual(client.get('/logs').status_code,403)
        self.assertEqual(client.get('/api/device-settings').status_code,403)

    def test_melt_number_permission_does_not_grant_allocation_access(self):
        _,client,_=self.create_user(['melts'])
        self.assertEqual(client.get('/melt-settings').status_code,200)
        self.assertEqual(client.get('/material-allocation').status_code,403)
        self.assertEqual(client.get('/api/material-allocations/1').status_code,403)
        self.assertEqual(client.put('/api/material-allocations/1',json={}).status_code,403)
        self.assertEqual(client.post('/api/material-allocations/1/complete',json={}).status_code,403)


if __name__=='__main__':
    unittest.main()
