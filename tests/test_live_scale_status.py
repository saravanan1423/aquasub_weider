import unittest
from unittest.mock import patch

from app import app
from communication_setting.routes import start_saved_scale
from communication_setting.serial_monitor import SerialMonitor
import test_usb_backup


class LiveScaleStatusTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_usb_backup.UsbBackupTests()
        self.fixture.setUp()
        self.monitor=SerialMonitor()
        self.monitor.config={'start_marker':'$','end_marker':']','start_address':1,'end_address':5,
                             'frame_timeout':1,'frame_gap':.25,'refresh_interval_ms':200}
        self.monitor.connected=True
        self.monitor_patch=patch('communication_setting.routes.monitor',self.monitor)
        self.monitor_patch.start()

    def tearDown(self):
        self.monitor_patch.stop()
        self.fixture.tearDown()

    def test_lightweight_status_updates_without_reconnect_or_frames(self):
        with patch.object(self.monitor,'start') as start:
            self.monitor._append(b'$00020]')
            first=self.fixture.client.get('/api/scale-status')
            self.assertEqual(first.status_code,200)
            self.assertEqual(first.json['weight'],'00020')
            self.assertNotIn('frames',first.json)
            self.assertNotIn('config',first.json)
            self.assertEqual(first.headers['Cache-Control'],'no-store')
            self.monitor._append(b'$00025]')
            self.assertEqual(self.fixture.client.get('/api/scale-status').json['weight'],'00025')
            start.assert_not_called()

    def test_live_display_is_only_on_main_screen(self):
        main=self.fixture.client.get('/')
        self.assertIn(b'id="main-live-weight">----</strong>',main.data)
        self.assertIn(b'id="main-status"',main.data)
        self.assertIn(b'scale_status.js',main.data)
        for path in ('/settings','/logs','/backup-settings','/users','/shifts','/melt-settings','/device-settings','/material-allocation'):
            with self.subTest(path=path):
                response=self.fixture.client.get(path)
                self.assertEqual(response.status_code,200)
                self.assertNotIn(b'data-scale-indicator',response.data)
                self.assertNotIn(b'scale_status.js',response.data)

    def test_login_required_but_read_only_status_available_to_logs_user(self):
        self.assertEqual(app.test_client().get('/api/scale-status').status_code,401)
        result=self.fixture.client.post('/api/users',json={'username':'logviewer','password':'password123','permissions':['logs']})
        self.assertEqual(result.status_code,201,result.json)
        viewer=app.test_client()
        viewer.post('/login',data={'username':'logviewer','password':'password123'})
        self.assertEqual(viewer.get('/api/scale-status').status_code,200)
        self.assertEqual(viewer.get('/api/data').status_code,403)
        self.assertEqual(viewer.post('/api/connect',json={}).status_code,403)

    def test_saved_refresh_interval_changes_live_without_restart(self):
        payload={'port':'TEST','baud_rate':9600,'data_bits':8,'parity':'none','stop_bits':'1',
                 'start_marker':'$','end_marker':']','start_address':1,'end_address':5,
                 'refresh_interval_ms':100,'frame_gap_ms':250,'frame_timeout':1}
        response=self.fixture.client.post('/api/settings',json=payload)
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(self.fixture.client.get('/api/scale-status').json['refresh_interval_ms'],100)
        self.monitor.connected=False
        with patch.object(self.monitor,'start') as start:
            start_saved_scale(app)
            self.assertEqual(start.call_args.args[0]['port'],'TEST')
            self.assertEqual(start.call_args.args[0]['refresh_interval_ms'],100)
    def test_saved_data_persists_and_exposes_in_scale_status(self):
        payload={'port':'TEST','baud_rate':9600,'data_bits':8,'parity':'none','stop_bits':'1',
                 'start_marker':'$','end_marker':']','start_address':1,'end_address':5,
                 'refresh_interval_ms':200,'frame_gap_ms':250,'frame_timeout':1,
                 'saved_data':'0014.21'}
        response=self.fixture.client.post('/api/settings',json=payload)
        self.assertEqual(response.status_code,200,response.json)
        saved=self.fixture.client.get('/api/settings').json
        self.assertEqual(saved.get('saved_data'),'0014.21')
        self.monitor.connected=False
        status=self.fixture.client.get('/api/scale-status').json
        self.assertEqual(status.get('saved_data'),'0014.21')
        self.assertEqual(status.get('weight'),'')
        self.assertFalse(status.get('scale_connected'))
