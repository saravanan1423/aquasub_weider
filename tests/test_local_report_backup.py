import json
import unittest
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from app import app
from core.shift_backup import ShiftBackupWorker
from core.usb_backup import REPORT_FILENAMES, save_local_reports, scheduled_report_name
import test_shift_backup


class LocalReportBackupTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_shift_backup.ShiftBackupTests()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def test_no_usb_saves_both_csv_and_pdf_locally_and_reports_status(self):
        self.fixture.enqueue()
        job=self.fixture.jobs()[0]
        self.assertEqual(job['status'],'pending')
        self.assertEqual(job['attempts'],0)
        self.assertIsNotNone(job['local_saved_at'])
        folder=Path(job['local_folder'])
        self.assertEqual(folder.parent,app.config['REPORT_STORAGE_DIR'])
        self.assertEqual(folder.name,'meltreport_2026-10-12_Day')
        self.assertEqual({file.name for file in folder.iterdir()},set(REPORT_FILENAMES)|{'.backup.json'})
        for name in REPORT_FILENAMES:
            self.assertGreater((folder/name).stat().st_size,0)
        client=self.fixture.fixture.client
        history=client.get('/api/backup-history').json['items'][0]
        self.assertEqual(history['local_folder'],str(folder))
        self.assertIsNone(history['completed_at'])
        self.assertEqual(client.get('/api/automatic-backups').json['local_pending'],1)

    def test_later_usb_copies_local_bytes_without_regenerating_and_keeps_local_files(self):
        self.fixture.enqueue()
        initial=self.fixture.jobs()[0]
        local=Path(initial['local_folder'])
        expected={name:(local/name).read_bytes() for name in REPORT_FILENAMES}
        with patch('core.usb_backup.report_pdf',side_effect=AssertionError('Must copy saved reports')):
            self.fixture.tick(self.fixture.day+timedelta(hours=17),plugged=True,worker=ShiftBackupWorker(app))
        completed=self.fixture.jobs()[0]
        self.assertEqual(completed['status'],'completed')
        self.assertEqual(completed['local_saved_at'],initial['local_saved_at'])
        usb=Path(completed['folder'])
        self.assertEqual(usb.name,local.name)
        for name,content in expected.items():
            self.assertEqual((usb/name).read_bytes(),content)
            self.assertEqual((local/name).read_bytes(),content)
        self.fixture.tick(self.fixture.day+timedelta(hours=18),plugged=True)
        self.assertEqual(completed['attempts'],1)
        self.assertEqual(len(list(usb.parent.iterdir())),1)

    def test_no_usb_ticks_do_not_rewrite_saved_files(self):
        self.fixture.enqueue()
        with patch('core.shift_backup.save_local_reports',side_effect=AssertionError('Already saved')):
            self.fixture.tick(self.fixture.day+timedelta(hours=17))
        self.assertEqual(self.fixture.jobs()[0]['attempts'],0)

    def test_local_failure_retries_before_any_usb_copy(self):
        self.fixture.tick(self.fixture.day+timedelta(hours=7))
        with patch('core.shift_backup.save_local_reports',side_effect=OSError('Storage is full')), patch('core.shift_backup.backup_shift_reports') as writer, self.assertLogs(app.logger,level='WARNING'):
            self.fixture.tick(self.fixture.day+timedelta(hours=16),plugged=True)
        writer.assert_not_called()
        failed=self.fixture.jobs()[0]
        self.assertEqual(failed['last_error'],'Storage is full')
        self.assertIsNone(failed['local_saved_at'])
        self.fixture.tick(self.fixture.day+timedelta(hours=17),plugged=True)
        self.assertEqual(self.fixture.jobs()[0]['status'],'completed')

    def test_crash_after_local_save_reuses_files_after_lease_expiry(self):
        self.fixture.tick(self.fixture.day+timedelta(hours=7))
        def save_then_crash(reports,key):
            save_local_reports(reports,key)
            raise KeyboardInterrupt()
        with patch('core.shift_backup.save_local_reports',side_effect=save_then_crash), self.assertRaises(KeyboardInterrupt):
            self.fixture.tick(self.fixture.day+timedelta(hours=16))
        self.assertEqual(self.fixture.jobs()[0]['status'],'processing')
        with patch('core.usb_backup.report_pdf',side_effect=AssertionError('Already rendered')):
            self.fixture.tick(self.fixture.day+timedelta(hours=16,minutes=6),plugged=True,worker=ShiftBackupWorker(app))
        self.assertEqual(self.fixture.jobs()[0]['status'],'completed')
        self.assertEqual(len(list(app.config['REPORT_STORAGE_DIR'].iterdir())),1)

    def test_names_and_collisions_are_safe_and_do_not_overwrite(self):
        self.fixture.enqueue()
        job=self.fixture.jobs()[0]
        reports=json.loads(job['reports_json'])
        with app.app_context():
            first=Path(job['local_folder'])
            second=Path(save_local_reports(reports,uuid.uuid4().hex))
        self.assertNotEqual(first,second)
        self.assertTrue(first.exists())
        reports['melt']['shift']='../../Day / A'
        self.assertEqual(scheduled_report_name(reports),'meltreport_2026-10-12_Day_A')
        reports['melt']['backup_schedule']='daily'
        self.assertEqual(scheduled_report_name(reports),'meltreport_2026-10-12')
        reports['melt']['backup_schedule']='monthly'
        self.assertEqual(scheduled_report_name(reports),'meltreport_October_2026')

    def test_restart_drains_multiple_unsynced_local_backups_only_once(self):
        self.fixture.tick(self.fixture.day+timedelta(hours=7))
        self.fixture.tick(self.fixture.day+timedelta(days=4,hours=16))
        self.fixture.tick(self.fixture.day+timedelta(days=4,hours=16,minutes=1))
        self.assertEqual(len(self.fixture.jobs()),5)
        self.assertTrue(all(job['local_saved_at'] for job in self.fixture.jobs()))
        restarted=ShiftBackupWorker(app)
        for minute in (2,3,4):
            self.fixture.tick(self.fixture.day+timedelta(days=4,hours=16,minutes=minute),plugged=True,worker=restarted)
        self.assertTrue(all(job['status']=='completed' and job['attempts']==1 for job in self.fixture.jobs()))
        self.assertEqual(len(list((self.fixture.fixture.drive_root/'Weider Reports').iterdir())),5)


if __name__=='__main__':
    unittest.main()
