"""Actual shared-lock acquisition triggers mail; queue/spawn alone never does."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
import unittest
from unittest.mock import Mock, patch

import test_notifications

web, mail = test_notifications.web, test_notifications.mail
ACQUIRED = '[SCigblast] acquired shared pipeline resource lock\n'
WAITING = '[SCigblast] waiting for shared pipeline resource lock: /synthetic/shared.lock\n'


class StartNotificationTests(unittest.TestCase):
    setUp = test_notifications.NotificationTests.setUp
    tearDown = test_notifications.NotificationTests.tearDown
    enable = test_notifications.NotificationTests.enable
    outbox = test_notifications.NotificationTests.outbox
    another_user = test_notifications.NotificationTests.another_user

    def started(self, jid, attempt=1, *, match=True, text=ACQUIRED):
        row = web.get_job_row(jid)
        env = json.loads(row['env_json'])
        env['SCIGBLAST_WEB_MATCH_ONLY'] = '1' if match else '0'
        metadata = dict(process_token=f'attempt-{attempt}', runtime_log={'inode': 123, 'size': 456})
        web.update_job(jid,status='RUNNING',attempt_no=attempt,pid=100+attempt,pgid=100+attempt,
                       started_at=web.now(),env_json=json.dumps(env),execution_json=json.dumps(metadata))
        launcher=web.STATE_ROOT/'jobs'/jid/f'launcher-{attempt}.log'
        launcher.parent.mkdir(parents=True,exist_ok=True)
        launcher.write_text(text,encoding='utf-8')
        return web.get_job_row(jid),launcher

    def test_spawn_waiting_and_old_logs_do_not_send_then_acquisition_sends_once(self):
        self.enable()
        jid=self.fixture.create()
        with patch.object(web.subprocess,'Popen') as spawn,patch.object(web.threading,'Thread'):
            spawn.return_value.pid=100
            self.assertTrue(web.launch_job(jid))
        self.assertEqual(self.outbox(),[])
        row,launcher=self.started(jid,attempt=2,text=WAITING)
        (launcher.parent/'launcher-1.log').write_text(ACQUIRED,encoding='utf-8')
        runtime=web.log_path(row)
        runtime.parent.mkdir(parents=True,exist_ok=True)
        runtime.write_text(ACQUIRED+'[BASE 5/5] old output\n',encoding='utf-8')
        self.assertFalse(web.notify_execution_started(row))
        with web.db() as connection:
            mail.enqueue(connection,jid,web.REGISTRY,event='STARTED')
        self.assertEqual(self.outbox(),[])
        with launcher.open('a',encoding='utf-8') as handle:
            handle.write(ACQUIRED+'x'*300000)  # Receipt outside the progress tail still counts.
        with patch.object(mail,'send') as send:
            self.assertTrue(web.notify_execution_started(row))
            self.assertFalse(web.notify_execution_started(row))
            send.assert_not_called()
            self.assertEqual(len(self.outbox()),1)
            self.assertEqual(self.outbox()[0]['event'],'STARTED')
            self.assertTrue(self.worker.deliver_one())
            message=send.call_args.args[1]
            self.assertIn('开始执行',str(message['Subject']))
            self.assertIn('执行内容：样本匹配（Match）',message.get_content())
            self.assertIn('北京时间',message.get_content())
            self.assertNotIn('结束：',message.get_content())
            self.assertIn('/jobs/'+jid,message.get_content())
        self.assertFalse(web.notify_execution_started(web.get_job_row(jid)))
        metadata=web.execution_metadata(web.get_job_row(jid))
        self.assertEqual(metadata['runtime_log'],{'inode':123,'size':456})
        self.assertEqual(metadata['process_token'],'attempt-2')

    def test_match_analysis_and_resume_each_notify_for_their_own_attempt(self):
        self.enable()
        jid=self.fixture.create()
        for attempt in (1,2,3):
            row,_=self.started(jid,attempt=attempt,match=attempt==1)
            self.assertTrue(web.notify_execution_started(row))
            with patch.object(mail,'send') as send:
                self.assertTrue(self.worker.deliver_one())
                content=send.call_args.args[1].get_content()
                self.assertIn('执行次数：'+str(attempt),content)
                self.assertIn('样本匹配（Match）' if attempt==1 else '分析 / 续跑',content)
        self.assertCountEqual([item['attempt_no'] for item in self.outbox()],[1,2,3])

    def test_start_is_skipped_if_finished_stopped_or_superseded_before_delivery(self):
        self.enable()
        for number,status in enumerate(('SUCCEEDED','WAITING_REVIEW','FAILED','STOPPED','STOPPING','INTERRUPTED'),1):
            jid=self.fixture.create(dataset=f'late-{number}')
            row,_=self.started(jid)
            self.assertTrue(web.notify_execution_started(row))
            web.update_job(jid,status=status)
        jid=self.fixture.create(dataset='new-attempt')
        row,_=self.started(jid)
        self.assertTrue(web.notify_execution_started(row))
        self.started(jid,attempt=2)
        with patch.object(mail,'send') as send:
            while self.worker.deliver_one():
                pass
            self.assertTrue(all('开始执行' not in str(call.args[1]['Subject']) for call in send.call_args_list))
        self.assertTrue(all(item['status']=='skipped' for item in self.outbox() if item['event']=='STARTED'))

    def test_stale_observation_cannot_update_new_attempt_token_or_stopped_job(self):
        self.enable()
        jid=self.fixture.create()
        row,_=self.started(jid)
        for updates in (dict(status='STOPPING'),dict(status='RUNNING',execution_json=json.dumps({'process_token':'replaced'})),dict(attempt_no=2)):
            web.update_job(jid,**updates)
            self.assertFalse(web.notify_execution_started(row))
        self.assertEqual(self.outbox(),[])

    def test_disabled_email_does_not_send_a_retrospective_start(self):
        jid=self.fixture.create()
        row,_=self.started(jid)
        self.assertTrue(web.notify_execution_started(row))
        self.assertEqual(self.outbox(),[])
        self.enable()
        self.assertFalse(web.notify_execution_started(web.get_job_row(jid)))
        self.assertEqual(self.outbox(),[])

    def test_start_recipient_is_owner_and_changed_email_is_rechecked(self):
        self.enable()
        other=self.another_user()
        other.post('/api/notifications/preferences',json={'email':'another@example.test'})
        jid=self.fixture.create()
        with web.db() as connection:
            connection.execute('UPDATE jobs SET owner_id=? WHERE id=?',('another',jid))
        row,_=self.started(jid)
        self.assertTrue(web.notify_execution_started(row))
        self.assertEqual(self.outbox()[0]['recipient'],'another@example.test')
        other.post('/api/notifications/preferences',json={'email':''})
        with patch.object(mail,'send') as send:
            self.assertTrue(self.worker.deliver_one())
            send.assert_not_called()
        self.assertEqual(self.outbox()[0]['status'],'skipped')

    def test_parallel_observations_deduplicate_without_mutating_execution_identity(self):
        self.enable()
        jid=self.fixture.create()
        row,_=self.started(jid)
        with ThreadPoolExecutor(max_workers=4) as pool:
            observed=list(pool.map(web.notify_execution_started,[row]*4))
        self.assertEqual(sum(observed),1)
        self.assertEqual(len(self.outbox()),1)
        self.assertEqual(web.execution_metadata(web.get_job_row(jid))['process_token'],'attempt-1')

    def test_notification_storage_failure_cannot_terminate_watcher(self):
        self.enable()
        jid=self.fixture.create()
        row,_=self.started(jid)
        with patch.object(web,'db',side_effect=sqlite3.OperationalError('private detail')),self.assertLogs('notifications',level='WARNING') as logs:
            self.assertFalse(web.notify_execution_started(row))
        self.assertNotIn('private detail',''.join(logs.output))
        with patch.object(mail,'enqueue',side_effect=sqlite3.OperationalError('private detail')),self.assertLogs('notifications',level='WARNING'):
            self.assertTrue(web.notify_execution_started(row))
        self.assertEqual(web.get_job_row(jid)['status'],'RUNNING')

    def test_fast_runner_records_start_but_only_completion_is_delivered_after_exit(self):
        self.enable()
        jid=self.fixture.create()
        row,_=self.started(jid)
        process=Mock(pid=row['pid'],returncode=0)
        process.poll.return_value=0
        web.PROCESSES[jid]=process
        with patch.object(web,'review_ready',return_value=False),patch.object(web,'pipeline_done',return_value=True):
            web.watch_job(jid,process)
        self.assertNotIn(jid,web.PROCESSES)
        self.assertEqual(web.get_job_row(jid)['status'],'SUCCEEDED')
        self.assertEqual({item['event'] for item in self.outbox()},{'STARTED','SUCCEEDED'})
        with patch.object(mail,'send') as send:
            while self.worker.deliver_one():
                pass
            send.assert_called_once()
            self.assertIn('分析完成',str(send.call_args.args[1]['Subject']))
        web.schedule.assert_called()

    def test_scheduler_shares_two_fifo_slots_across_users_and_pipelines(self):
        self.enable()
        self.another_user()
        ids=[self.fixture.create(dataset=f'fifo-{i}') for i in range(5)]
        with web.db() as connection:
            for i,jid in enumerate(ids):
                connection.execute('UPDATE jobs SET owner_id=?,pipeline=?,created_at=? WHERE id=?',
                    ('test-admin' if i%2==0 else 'another',list(web.REGISTRY)[i],f'2026-10-22T00:00:0{i}+00:00',jid))
        self.fixture.patches[-1].stop()
        calls=[]
        def launch(jid):
            calls.append(jid)
            web.PROCESSES[jid]=object()
            web.update_job(jid,status='RUNNING')
            return True
        try:
            with patch.object(web,'launch_job',side_effect=launch):
                with ThreadPoolExecutor(max_workers=8) as pool:
                    list(pool.map(lambda _:web.schedule(),range(8)))
                self.assertEqual(calls,ids[:web.MAX_ACTIVE_JOBS])
                self.assertTrue(all(web.get_job_row(jid)['status']=='QUEUED' for jid in ids[web.MAX_ACTIVE_JOBS:]))
                web.PROCESSES.pop(ids[0])
                web.update_job(ids[0],status='WAITING_REVIEW')
                web.schedule()
                self.assertEqual(calls,ids[:web.MAX_ACTIVE_JOBS+1])
                self.assertEqual(web.active_count(),web.MAX_ACTIVE_JOBS)
        finally:
            self.fixture.patches[-1].start()

    @unittest.skipUnless(os.name=='posix' and shutil.which('bash') and shutil.which('flock'),'requires Linux shared flock')
    def test_real_lock_waiter_only_notifies_when_one_of_two_slots_is_released(self):
        self.enable()
        jid=self.fixture.create()
        row,launcher=self.started(jid,text='')
        helper=Path(__file__).resolve().parents[2]/'pipeline_resource_lock.sh'
        env={**os.environ,'SCIGBLAST_RESOURCE_LOCK_FILE':str(self.fixture.root/'shared.lock'),
             'SCIGBLAST_RESOURCE_LOCK_WAIT_SECONDS':'10'}
        command=f'source "{helper}"; scigblast_acquire_resource_lock || exit $?; read -r release'
        first=subprocess.Popen(['bash','-c',command],env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        second=occupant=None
        try:
            self.assertIn('waiting for',first.stdout.readline())
            self.assertIn('acquired shared',first.stdout.readline())
            occupant=subprocess.Popen(['bash','-c',command],env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            self.assertIn('waiting for',occupant.stdout.readline())
            self.assertIn('acquired shared',occupant.stdout.readline())
            with launcher.open('wb') as output:
                second=subprocess.Popen(['bash','-c',command],env=env,stdin=subprocess.PIPE,stdout=output,stderr=output,text=True)
                web.update_job(jid,pid=second.pid,pgid=second.pid)
                row=web.get_job_row(jid)
                deadline=time.monotonic()+5
                while 'waiting for' not in launcher.read_text() and time.monotonic()<deadline:
                    time.sleep(.02)
                self.assertIn('waiting for',launcher.read_text())
                self.assertNotIn('acquired shared',launcher.read_text())
                self.assertFalse(web.notify_execution_started(row))
                self.assertEqual(self.outbox(),[])
                first.stdin.write('\n');first.stdin.flush()
                first.wait(timeout=5)
                deadline=time.monotonic()+5
                while 'acquired shared' not in launcher.read_text() and time.monotonic()<deadline:
                    time.sleep(.02)
                self.assertIn('acquired shared',launcher.read_text())
                self.assertIsNone(second.poll())
                self.assertTrue(web.notify_execution_started(row))
                with patch.object(mail,'send') as send:
                    self.assertTrue(self.worker.deliver_one())
                    send.assert_called_once()
                self.assertEqual(self.outbox()[0]['event'],'STARTED')
        finally:
            for process in (second,occupant,first):
                if process is None:
                    continue
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=5)
                for stream in (process.stdin,process.stdout,process.stderr):
                    if stream:
                        stream.close()


if __name__=='__main__':
    unittest.main()
