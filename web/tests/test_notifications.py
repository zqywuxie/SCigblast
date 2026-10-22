"""Mail tests use fake SMTP only. No external mail is sent."""
import json
import smtplib
import sqlite3
import ssl
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import test_workflow
import notifications as mail

web = test_workflow.web


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_workflow.WorkflowTests()
        self.fixture.setUp()
        self.client = self.fixture.client
        self.worker = mail.Dispatcher(web.db)
        self.config = dict(enabled=True, host='smtp.example.test', port=587, encryption='starttls',
                           sender_email='sender@example.test', username='smtp-user', password='fake-authorization-code',
                           public_base_url='https://platform.example.test')

    def tearDown(self):
        self.worker.stop()
        self.fixture.tearDown()

    def enable(self):
        response = self.client.post('/api/admin/mail-settings', json=self.config)
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.post('/api/notifications/preferences', json={'email':'owner@example.test'})
        self.assertEqual(response.status_code, 200, response.text)

    def outbox(self):
        with web.db() as connection:
            return [dict(row) for row in connection.execute('SELECT * FROM mail_outbox ORDER BY created_at,id')]

    def another_user(self, role='user'):
        with web.db() as connection:
            connection.execute('INSERT INTO users(id,username,display_name,password_hash,role,created_at) VALUES(?,?,?,?,?,?)',
                               ('another', 'another', 'Another User', 'unused-fixture-hash', role, int(time.time())))
            connection.execute('INSERT INTO sessions VALUES(?,?,?)',
                               (web.auth.digest('another-fixture-session'), 'another', int(time.time())+3600))
        client = TestClient(web.app, headers={'X-SCIGBLAST-Request':'1'})
        client.cookies.set(web.auth.COOKIE, 'another-fixture-session')
        self.addCleanup(client.close)
        return client

    def test_schema_migration_and_preferences_are_self_only(self):
        web.init_db(); web.init_db()
        initial=self.client.get('/api/admin/mail-settings').json()
        self.assertEqual((initial['host'],initial['port'],initial['encryption'],initial['enabled']),('smtp.qq.com',465,'ssl',False))
        self.assertEqual(self.client.get('/api/notifications/preferences').json(), {'email':'','service_enabled':False})
        other = self.another_user()
        response = other.post('/api/notifications/preferences', json={'email':'Someone@Example.TEST','owner_id':'test-admin'})
        self.assertEqual(response.json()['email'], 'Someone@example.test')
        self.assertEqual(self.client.get('/api/notifications/preferences').json()['email'], '')
        self.assertEqual(other.get('/api/notifications/preferences').json()['email'], 'Someone@example.test')
        self.assertEqual(other.get('/api/admin/mail-settings').status_code, 403)
        self.assertEqual(other.post('/api/admin/mail-settings', json=self.config).status_code, 403)
        self.assertEqual(other.post('/api/admin/mail-settings/test', json={}).status_code, 403)
        self.assertEqual(other.post('/api/notifications/preferences',json={'email':''}).status_code,200)
        self.assertNotIn('notification_email', self.client.get('/api/auth/me').json()['user'])

    def test_invalid_addresses_and_smtp_configuration(self):
        for email in ['one@example.test,two@example.test','Display <one@example.test>','a\nb@example.test',
                      'one@example.test\r\nBcc:other@example.test','a..b@example.test','a@-example.test','a@localhost']:
            with self.subTest(email=email):
                self.assertEqual(self.client.post('/api/notifications/preferences',json={'email':email}).status_code,400)
        for override in [dict(host='smtp://example.test'),dict(sender_email='bad'),dict(host=''),
                         dict(public_base_url='file:///etc/passwd'),dict(public_base_url='http://['),
                         dict(public_base_url='https://host:wrong'),dict(public_base_url='https://user:pass@host'),
                         dict(public_base_url='https://host/jobs/x'),dict(public_base_url='https://host/?secret=1')]:
            with self.subTest(override=override):
                self.assertEqual(self.client.post('/api/admin/mail-settings',json={**self.config,**override}).status_code,400)
        self.assertEqual(self.client.post('/api/admin/mail-settings',json={**self.config,'port':0}).status_code,422)

    def test_sender_secret_is_write_only_and_blank_preserves_it(self):
        self.enable()
        visible = self.client.get('/api/admin/mail-settings').json()
        self.assertNotIn('password', visible)
        self.assertNotIn(self.config['password'], json.dumps(visible))
        self.assertTrue(visible['password_configured'])
        response = self.client.post('/api/admin/mail-settings',json={**self.config,'password':'','port':465,'encryption':'ssl'})
        self.assertTrue(response.json()['password_configured'])
        with web.db() as connection:
            self.assertEqual(mail.settings(connection,secret=True)['password'],self.config['password'])
        self.assertEqual(self.client.post('/api/admin/mail-settings',json={**self.config,'clear_password':True}).status_code,400)
        response = self.client.post('/api/admin/mail-settings',json={**self.config,'enabled':False,'clear_password':True})
        self.assertEqual(response.status_code,200)
        self.assertFalse(response.json()['password_configured'])

    def test_status_hook_deduplicates_and_never_sends_inline(self):
        self.enable(); jid = self.fixture.create()
        with patch.object(mail,'send') as send:
            web.update_job(jid,attempt_no=1,status='RUNNING')
            for _ in range(2):web.update_job(jid,status='SUCCEEDED')
            send.assert_not_called()
            self.assertEqual(len(self.outbox()),1)
            self.assertEqual(self.outbox()[0]['recipient'],'owner@example.test')
            self.assertTrue(self.worker.deliver_one())
            message = send.call_args.args[1]
            self.assertIn('分析完成',str(message['Subject']))
            self.assertIn('/jobs/'+jid,message.get_content())
            self.assertNotIn(self.config['password'],message.get_content())
            self.assertEqual(self.outbox()[0]['status'],'sent')
            self.assertFalse(self.worker.deliver_one())
        web.update_job(jid,attempt_no=2,status='FAILED')
        self.assertEqual(len(self.outbox()),2)

    def test_only_completion_failure_and_abnormal_events_send(self):
        self.enable(); jid = self.fixture.create()
        cases = [
            ('QUEUED', None), ('RUNNING', None), ('MATCHING', None),
            ('WAITING_REVIEW', None), ('STOPPING', None), ('STOPPED', None),
            ('SUCCEEDED', '分析完成'), ('FAILED', '分析失败'),
            ('INTERRUPTED', '执行中断'), ('COMPLETED_WITHOUT_MARKER', '执行异常'),
            ('ARCHIVING', None), ('ARCHIVED', None),
        ]
        queued = 0
        for attempt, (status, label) in enumerate(cases, 1):
            with self.subTest(status=status), patch.object(mail, 'send') as send:
                web.update_job(jid, status=status, attempt_no=attempt)
                queued += int(label is not None)
                self.assertEqual(len(self.outbox()), queued)
                self.assertEqual(self.worker.deliver_one(), label is not None)
                if label is None:
                    send.assert_not_called()
                else:
                    send.assert_called_once()
                    self.assertIn(label, str(send.call_args.args[1]['Subject']))
                    self.assertIn('/jobs/' + jid, send.call_args.args[1].get_content())

    def test_legacy_review_and_stop_notifications_are_skipped(self):
        self.enable(); jid = self.fixture.create()
        for attempt, event in enumerate(['WAITING_REVIEW', 'STOPPED'], 1):
            with self.subTest(event=event):
                web.update_job(jid, status='FAILED', attempt_no=attempt)
                # Reproduce an unsent event persisted by the previous version.
                with web.db() as connection:
                    connection.execute('UPDATE mail_outbox SET event=? WHERE job_id=? AND attempt_no=?',
                                       (event, jid, attempt))
                web.update_job(jid, status=event)
                with patch.object(mail, 'send') as send:
                    self.assertTrue(self.worker.deliver_one())
                    send.assert_not_called()
        self.assertEqual([row['status'] for row in self.outbox()], ['skipped', 'skipped'])
        self.assertTrue(all(row['attempts'] == 0 for row in self.outbox()))

    def test_mail_only_goes_to_the_task_owner_not_the_acting_admin(self):
        self.enable(); other = self.another_user()
        other.post('/api/notifications/preferences',json={'email':'another@example.test'})
        jid = self.fixture.create()
        with web.db() as connection:connection.execute('UPDATE jobs SET owner_id=? WHERE id=?',('another',jid))
        web.update_job(jid,status='FAILED')
        self.assertEqual(self.outbox()[0]['recipient'],'another@example.test')
        with patch.object(mail,'send') as send:
            self.worker.deliver_one()
            self.assertEqual(str(send.call_args.args[1]['To']),'another@example.test')

    def test_disabled_service_and_empty_preference_do_not_queue(self):
        jid = self.fixture.create()
        web.update_job(jid,status='FAILED')
        self.assertEqual(self.outbox(),[])
        self.client.post('/api/admin/mail-settings',json=self.config)
        web.update_job(jid,status='SUCCEEDED')
        self.assertEqual(self.outbox(),[])

    def test_stale_event_and_changed_email_are_skipped(self):
        self.enable(); jid = self.fixture.create()
        web.update_job(jid,status='FAILED',attempt_no=1)
        web.update_job(jid,status='RUNNING',attempt_no=2)
        with patch.object(mail,'send') as send:
            self.worker.deliver_one(); send.assert_not_called()
        self.assertEqual(self.outbox()[0]['status'],'skipped')
        web.update_job(jid,status='FAILED')
        self.client.post('/api/notifications/preferences',json={'email':'new@example.test'})
        with patch.object(mail,'send') as send:
            self.worker.deliver_one(); send.assert_not_called()
        self.assertEqual([row['status'] for row in self.outbox()],['skipped','skipped'])

    def test_deleted_jobs_and_disabled_accounts_are_skipped(self):
        self.enable(); jid = self.fixture.create()
        web.update_job(jid,status='SUCCEEDED')
        with web.db() as connection:connection.execute('DELETE FROM jobs WHERE id=?',(jid,))
        with patch.object(mail,'send') as send:
            self.worker.deliver_one(); send.assert_not_called()
        jid = self.fixture.create(dataset='second'); web.update_job(jid,status='FAILED')
        with web.db() as connection:connection.execute("UPDATE users SET active=0 WHERE id='test-admin'")
        with patch.object(mail,'send') as send:
            self.worker.deliver_one(); send.assert_not_called()
        self.assertTrue(all(row['status']=='skipped' for row in self.outbox()))

    def test_delivery_failure_retries_without_affecting_the_task_or_leaking_secrets(self):
        self.enable(); jid = self.fixture.create(); web.update_job(jid,status='FAILED')
        with patch.object(mail,'send',side_effect=smtplib.SMTPAuthenticationError(535,b'fake-authorization-code')):
            for attempt in range(1,4):
                self.worker.deliver_one()
                row=self.outbox()[0]
                self.assertEqual(row['attempts'],attempt)
                self.assertEqual(row['status'],'failed' if attempt==3 else 'retry')
                self.assertNotIn('fake-authorization-code',row['last_error'])
                self.assertEqual(web.get_job_row(jid)['status'],'FAILED')
                with web.db() as connection:connection.execute('UPDATE mail_outbox SET next_attempt_at=0')
        self.assertFalse(self.worker.deliver_one())

    def test_interrupt_hook_and_queue_failure_isolation(self):
        self.enable(); jid=self.fixture.create(); web.update_job(jid,status='RUNNING',attempt_no=1)
        web.mark_interrupted_jobs()
        self.assertEqual(self.outbox()[0]['event'],'INTERRUPTED')
        with patch.object(mail,'enqueue',side_effect=sqlite3.OperationalError('sensitive error')),self.assertLogs('notifications',level='WARNING') as logs:
            web.update_job(jid,status='FAILED')
        self.assertEqual(web.get_job_row(jid)['status'],'FAILED')
        self.assertNotIn('sensitive error',''.join(logs.output))

    def test_admin_test_email_and_status_are_scoped(self):
        self.assertEqual(self.client.post('/api/admin/mail-settings/test',json={}).status_code,400)
        self.client.post('/api/admin/mail-settings',json=self.config)
        self.assertEqual(self.client.post('/api/admin/mail-settings/test',json={}).status_code,400)
        self.client.post('/api/notifications/preferences',json={'email':'owner@example.test'})
        result=self.client.post('/api/admin/mail-settings/test',json={}).json(); endpoint='/api/admin/mail-settings/test/'+result['id']
        self.assertEqual(self.client.get(endpoint).json()['status'],'queued')
        other=self.another_user(role='admin')
        self.assertEqual(other.get(endpoint).status_code,404)
        with patch.object(mail,'send') as send:self.worker.deliver_one()
        self.assertEqual(self.client.get(endpoint).json()['status'],'sent')
        self.assertIn('测试',str(send.call_args.args[1]['Subject']))

    def test_sender_tls_auth_and_envelope(self):
        self.enable(); jid=self.fixture.create(); web.update_job(jid,status='SUCCEEDED')
        with web.db() as connection:config=mail.settings(connection,secret=True)
        item=self.outbox()[0]; message=mail.message(config,item)
        for encryption in ['starttls','ssl']:
            with patch.object(mail.smtplib,'SMTP') as plain,patch.object(mail.smtplib,'SMTP_SSL') as secure:
                mail.send({**config,'encryption':encryption},message)
                factory=secure if encryption=='ssl' else plain
                actual=factory.return_value
                actual.login.assert_called_once_with('smtp-user','fake-authorization-code')
                actual.send_message.assert_called_once_with(message,from_addr='sender@example.test',to_addrs=['owner@example.test'])
                self.assertEqual(factory.call_args.kwargs['timeout'],10)
                if encryption=='starttls':
                    context=actual.starttls.call_args.kwargs['context']; self.assertTrue(context.check_hostname)
                    self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED)
                else:actual.starttls.assert_not_called()

    def test_quit_error_does_not_retry_an_accepted_message_or_mask_a_send_error(self):
        self.enable(); jid=self.fixture.create(); web.update_job(jid,status='SUCCEEDED')
        with web.db() as connection:config=mail.settings(connection,secret=True)
        config['encryption']='ssl'
        message=mail.message(config,self.outbox()[0])
        with patch.object(mail.smtplib,'SMTP_SSL') as factory:
            client=factory.return_value
            client.quit.side_effect=smtplib.SMTPResponseException(421,b'session ended')
            mail.send(config,message)
            client.send_message.assert_called_once()
            client.close.assert_called_once()
            client.send_message.side_effect=smtplib.SMTPDataError(550,b'refused')
            with self.assertRaises(smtplib.SMTPDataError):mail.send(config,message)

    def test_restart_recovers_claimed_mail(self):
        self.enable(); jid=self.fixture.create(); web.update_job(jid,status='SUCCEEDED')
        with web.db() as connection:connection.execute("UPDATE mail_outbox SET status='sending'")
        with patch.object(self.worker,'deliver_one',return_value=False):
            self.worker.start(); self.worker.stop()
        self.assertFalse(self.worker.thread.is_alive())
        self.assertEqual(self.outbox()[0]['status'],'queued')


if __name__=='__main__':unittest.main()
