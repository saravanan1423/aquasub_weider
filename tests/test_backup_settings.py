import json
import unittest
from datetime import datetime, timedelta

from app import app
from core.database import database_connection, initialize_database
from core.shift_backup import scheduled_windows
import test_shift_backup


class BackupSettingsTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_shift_backup.ShiftBackupTests()
        self.fixture.setUp()
        self.client=self.fixture.fixture.client
        self.day=self.fixture.day

    def tearDown(self):
        self.fixture.tearDown()

    def settings(self, **changes):
        return {"enabled":True,"frequency":"daily","backup_time":"16:00","month_day":1,**changes}

    def configure(self, **changes):
        settings=self.settings(**changes)
        with app.app_context(), database_connection() as connection:
            connection.execute("UPDATE backup_settings SET enabled=?,frequency=?,backup_time=?,month_day=? WHERE id=1",
                               (settings['enabled'],settings['frequency'],settings['backup_time'],settings['month_day']))
        return settings

    def test_defaults_save_validation_and_persistence(self):
        defaults=self.client.get('/api/backup-settings').json['settings']
        self.assertEqual(defaults,dict(enabled=1,frequency='shift',backup_time='00:00',month_day=1))
        response=self.client.put('/api/backup-settings',json=self.settings(frequency='monthly',month_day=31))
        self.assertEqual(response.status_code,200,response.json)
        initialize_database(app)
        self.assertEqual(self.client.get('/api/backup-settings').json['settings']['month_day'],31)
        for payload in (None,[],self.settings(frequency='weekly'),self.settings(backup_time='24:00'),
                        self.settings(month_day=0),self.settings(month_day=True),self.settings(enabled=1)):
            self.assertEqual(self.client.put('/api/backup-settings',json=payload).status_code,400)

    def test_daily_jobs_wait_retry_and_export_both_reports(self):
        self.configure()
        self.fixture.tick(self.day+timedelta(hours=7))
        self.fixture.tick(self.day+timedelta(hours=16))
        jobs=self.fixture.jobs()
        self.assertEqual(len(jobs),1)
        self.assertEqual(jobs[0]['schedule_type'],'daily')
        reports=json.loads(jobs[0]['reports_json'])
        self.assertEqual(reports['melt']['total_net_weight'],7)
        self.assertEqual(reports['individual']['total_products'],2)
        self.assertEqual(reports['individual']['shift'],'All shifts')
        self.assertEqual({row['shift_type'] for row in reports['individual']['items']},{'Day'})
        self.fixture.tick(self.day+timedelta(hours=17),plugged=True)
        self.assertEqual(self.fixture.jobs()[0]['status'],'completed')
        self.assertIn('meltreport_2026-10-11',self.fixture.jobs()[0]['folder'])
        self.fixture.tick(self.day+timedelta(hours=18),plugged=True)
        self.assertEqual(len(self.fixture.jobs()),1)

    def test_monthly_clamps_short_months_and_handles_year_rollover(self):
        settings=self.settings(frequency='monthly',month_day=31,backup_time='02:30')
        zone=self.day.tzinfo
        windows=list(scheduled_windows(settings,[],datetime(2026,1,1,tzinfo=zone),datetime(2026,3,31,2,30,tzinfo=zone)))
        self.assertEqual([end.day for _,_,end in windows],[31,28,31])
        self.assertEqual(windows[0][1],datetime(2025,12,31,2,30,tzinfo=zone))
        self.assertEqual(windows[1][1],windows[0][2])
        self.assertEqual(windows[2][1],windows[1][2])
        leap=list(scheduled_windows(settings,[],datetime(2028,2,1,tzinfo=zone),datetime(2028,2,29,3,tzinfo=zone)))
        self.assertEqual(leap[0][2].day,29)

    def test_monthly_worker_queues_missed_periods_after_restart(self):
        self.configure(frequency='monthly',month_day=1,backup_time='00:00')
        self.fixture.tick(self.day)
        self.fixture.tick(self.day.replace(month=12,day=1))
        jobs=self.fixture.jobs()
        self.assertEqual(len(jobs),2)
        self.assertEqual({job['schedule_type'] for job in jobs},{'monthly'})
        self.assertEqual([datetime.fromisoformat(job['ends_at']).month for job in jobs],[11,12])
        self.fixture.tick(self.day.replace(month=12,day=1,hour=1),plugged=True)
        self.assertTrue(all(job['status']=='completed' for job in self.fixture.jobs()))

    def test_disabled_schedule_keeps_pending_jobs_and_history(self):
        self.fixture.enqueue()
        self.configure(enabled=False)
        self.fixture.tick(self.day+timedelta(days=2,hours=17))
        self.assertEqual(len(self.fixture.jobs()),1)
        history=self.client.get('/api/backup-history').json
        self.assertEqual(history['items'][0]['status'],'pending')
        self.fixture.tick(self.day+timedelta(days=2,hours=18),plugged=True)
        self.assertEqual(self.client.get('/api/backup-history').json['items'][0]['status'],'completed')

    def test_schedule_changes_preserve_jobs_and_reset_cursor_only_on_change(self):
        self.fixture.enqueue()
        self.assertEqual(self.client.put('/api/backup-settings',json=self.settings()).status_code,200)
        with app.app_context(), database_connection() as connection:
            first=connection.execute('SELECT last_checked_at FROM automatic_backup_state').fetchone()[0]
        self.client.put('/api/backup-settings',json=self.settings())
        with app.app_context(), database_connection() as connection:
            second=connection.execute('SELECT last_checked_at FROM automatic_backup_state').fetchone()[0]
        self.assertEqual(first,second)
        self.assertEqual(len(self.fixture.jobs()),1)

    def test_logs_operator_can_view_history_but_not_change_schedules(self):
        with app.app_context(), database_connection() as connection:
            operator=connection.execute("INSERT INTO users (username,password_hash,role,created_at) VALUES ('operator','unused','user',?)",(self.day.isoformat(),)).lastrowid
            connection.execute("INSERT INTO user_permissions (user_id,screen) VALUES (?,'logs')",(operator,))
        with self.client.session_transaction() as session:
            session.update(user_id=operator,username='operator',role='user')
        self.assertEqual(self.client.get('/backup-history').status_code,200)
        self.assertEqual(self.client.get('/api/backup-history').status_code,200)
        self.assertEqual(self.client.get('/backup-settings').status_code,403)
        self.assertEqual(self.client.put('/api/backup-settings',json=self.settings()).status_code,403)

    def test_history_is_paginated_and_excludes_report_payload(self):
        self.fixture.tick(self.day+timedelta(hours=7))
        self.fixture.tick(self.day+timedelta(days=7,hours=17))
        response=self.client.get('/api/backup-history?page=2').json
        self.assertEqual(response['total'],8)
        self.assertEqual(response['pages'],2)
        self.assertEqual(len(response['items']),3)
        self.assertNotIn('reports_json',response['items'][0])


if __name__=='__main__':
    unittest.main()
