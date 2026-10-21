import unittest

from app import app
from core.report_export import report_pdf
from logs.routes import build_capture_report
import test_usb_backup


class BrandingTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_usb_backup.UsbBackupTests()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def test_all_pages_use_accessible_branding(self):
        for path in ('/','/settings','/logs','/communication','/shifts','/melt-settings','/device-settings',
                     '/images','/users','/backup-settings','/backup-history','/material-allocation'):
            with self.subTest(path=path):
                response=self.fixture.client.get(path)
                self.assertEqual(response.status_code,200)
                self.assertIn(b'weider-logo.png',response.data)
                self.assertIn(b'alt="Weider"',response.data)
                self.assertIn(b'aqua-group-logo.png',response.data)
                self.assertIn(b'alt="Aqua Group"',response.data)
                self.assertIn(b'brand-separator',response.data)
                self.assertIn(b'|',response.data)
                self.assertNotIn(b'bottom-aqua-logo',response.data)
                self.assertNotIn(b'WeightFlow',response.data)
                self.assertNotIn(b'Powered by <strong>Weider Weighing',response.data)
        login=app.test_client().get('/login')
        self.assertIn(b'weider-logo.png',login.data)
        self.assertIn(b'alt="Weider"',login.data)
        self.assertIn(b'aqua-group-logo.png',login.data)
        self.assertIn(b'alt="Aqua Group"',login.data)
        self.assertIn(b'brand-separator',login.data)
        self.assertNotIn(b'bottom-aqua-logo',login.data)
        self.assertNotIn(b'Powered by',login.data)
        for text in (b'WeightFlow',b'Operator console',b'SECURE ACCESS',b'Welcome back',b'Sign in to continue'):
            self.assertNotIn(text,login.data)
        self.assertEqual(login.data.count(b'/static/theme.css'),1)
        self.assertEqual(self.fixture.client.get('/static/weider-logo.png').status_code,200)
        self.assertEqual(self.fixture.client.get('/static/aqua-group-logo.png').status_code,200)

    def test_both_pdf_reports_embed_brand_image_without_changing_totals(self):
        with app.app_context():
            for report_type in ('melt','individual'):
                report=build_capture_report({**self.fixture.payload(),'report_type':report_type},'admin')
                total=report['total_net_weight']
                output=report_pdf(report)
                self.assertTrue(output.startswith(b'%PDF'))
                self.assertIn(b'/Subtype /Image',output)
                self.assertEqual(report['total_net_weight'],total)


if __name__=='__main__':
    unittest.main()
