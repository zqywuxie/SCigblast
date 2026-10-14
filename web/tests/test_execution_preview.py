"""Attempt isolation, bounded previews and dependency gates without analysis tools."""
import csv
import json
from pathlib import Path
import signal
import unittest
from unittest.mock import patch

import test_workflow
import table_preview

web = test_workflow.web


class ExecutionPreviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_workflow.WorkflowTests()
        self.fixture.setUp()
        self.client = self.fixture.client
        table_preview._TOTALS.clear()

    def tearDown(self):
        self.fixture.tearDown()

    def test_plain_airr_is_bounded_and_has_no_match_errors(self):
        path = self.fixture.root / 'airr.tsv'
        path.write_text('sequence_id\tv_call\n' + ''.join(f'{i}\tIGHV1\n' for i in range(1000)), encoding='utf-8')
        result = web.read_table(path, limit=5)
        self.assertEqual(len(result['rows']), 5)
        self.assertEqual(result['scanned_rows'], 5)
        self.assertIsNone(result['total'])
        self.assertNotIn('error', result['counts'])
        self.assertFalse(result['has_status'])
        next_page = web.read_table(path, limit=5, cursor=result['next_cursor'])
        self.assertEqual(next_page['rows'][0]['sequence_id'], '5')
        self.assertEqual(next_page['scanned_rows'], 10)

    def test_search_scans_bounded_chunks_and_preserves_multiline_cells(self):
        path = self.fixture.root / 'preview.csv'
        with path.open('w', newline='', encoding='utf-8') as handle:
            writer = csv.writer(handle)
            writer.writerow(['sequence_id', 'note'])
            for index in range(12):
                writer.writerow([index, 'target\nquoted,内容' if index == 10 else 'no'])
        with patch.object(table_preview, 'SCAN_ROWS', 4):
            first = web.read_table(path, query='target', limit=5)
            self.assertEqual(first['rows'], [])
            self.assertTrue(first['has_more'])
            second = web.read_table(path, query='target', cursor=first['next_cursor'])
            final = web.read_table(path, query='target', cursor=second['next_cursor'])
        self.assertEqual(final['total'], 1)
        self.assertEqual(final['rows'][0]['note'], 'target\nquoted,内容')
        self.assertTrue(final['scan_complete'])
        with patch.object(table_preview, 'SCAN_ROWS', 4):
            cached = web.read_table(path, query='target')
        self.assertEqual(cached['total'], 1)
        self.assertEqual(cached['scanned_rows'], 4)

    def test_cursor_invalidates_on_file_or_filter_change(self):
        path = self.fixture.root / 'preview.csv'
        path.write_text('sample,status\nA,OK\nB,ERROR\n', encoding='utf-8')
        first = web.read_table(path, limit=1)
        for query in ('new',):
            with self.assertRaises(web.HTTPException) as raised:
                web.read_table(path, query=query, cursor=first['next_cursor'])
            self.assertEqual(raised.exception.status_code, 409)
        path.write_text('sample,status\nA,OK\nC,ERROR\nD,OK\n', encoding='utf-8')
        with self.assertRaises(web.HTTPException):
            web.read_table(path, cursor=first['next_cursor'])

    def test_only_explicit_error_status_counts_as_error(self):
        path = self.fixture.root / 'preview.csv'
        path.write_text('sample,status\nA,OK\nB,ERROR\nC,SKIPPED\nD,\n', encoding='utf-8')
        result = web.read_table(path)
        self.assertEqual(result['counts'], {'total': 4, 'ok': 1, 'error': 1})
        self.assertEqual([r['sample'] for r in web.read_table(path, errors_only=True)['rows']], ['B'])

    def test_retry_waiting_for_lock_does_not_show_previous_log(self):
        jid = self.fixture.create()
        row = web.get_job_row(jid)
        old = web.log_path(row)
        old.parent.mkdir(parents=True)
        old.write_text('[BASE 5/5] old result\npercent=100\n', encoding='utf-8')
        launcher = web.STATE_ROOT / 'jobs' / jid / 'launcher-2.log'
        launcher.parent.mkdir(parents=True)
        launcher.write_text('[SCigblast] waiting for shared pipeline resource lock: /tmp/lock\n', encoding='utf-8')
        web.update_job(jid, status='RUNNING', attempt_no=2, current_stage='', progress=0)
        row = web.get_job_row(jid)
        self.assertEqual(web.parse_progress(row), ('', 0))
        self.assertEqual(web.snapshot(row)['execution_phase'], 'waiting_resource')
        response = self.client.get(f'/api/jobs/{jid}/log').json()
        self.assertIn('waiting for', response['content'])
        self.assertNotIn('old result', response['content'])

    def test_progress_reuses_one_attempt_log_read(self):
        jid = self.fixture.create()
        web.update_job(jid, status='RUNNING')
        with patch.object(web, 'attempt_tail', return_value='[BASE 2/5] fastp\npercent=30\n') as read:
            self.assertEqual(web.parse_progress(web.get_job_row(jid)), ('02.fastp', 30))
        read.assert_called_once()

    def test_list_snapshot_reuses_owner_name_from_list_query(self):
        jid = self.fixture.create()
        with web.db() as connection:
            row = connection.execute("SELECT jobs.*, '显示姓名' AS operator_display_name FROM jobs WHERE id=?", (jid,)).fetchone()
        with patch.object(web, 'db', side_effect=AssertionError('snapshot must not reopen the database')):
            self.assertEqual(web.snapshot(row)['operator_display_name'], '显示姓名')

    def test_mapping_log_starts_at_current_attempt_boundary(self):
        jid = self.fixture.create()
        row = web.get_job_row(jid)
        path = web.log_path(row)
        path.parent.mkdir(parents=True)
        path.write_text('[MAPPING 3/3] old\npercent=100\n', encoding='utf-8')
        stat = path.stat()
        web.update_job(jid, pipeline='mapping', attempt_no=2, status='RUNNING',
                       execution_json=json.dumps({'runtime_log': {'inode': stat.st_ino, 'size': stat.st_size}}))
        with path.open('a', encoding='utf-8') as handle:
            handle.write('[MAPPING 1/3] new\npercent=20\n')
        response = self.client.get(f'/api/jobs/{jid}/log').json()
        self.assertNotIn('old', response['content'])
        self.assertIn('new', response['content'])
        self.assertEqual(web.parse_progress(web.get_job_row(jid)), ('01.fasta', 20))

    def test_failed_spawn_records_attempt_and_visible_error(self):
        jid = self.fixture.create()
        with patch.object(web.subprocess, 'Popen', side_effect=OSError('fixture failure')):
            self.assertFalse(web.launch_job(jid))
        row = web.get_job_row(jid)
        self.assertEqual(row['attempt_no'], 1)
        self.assertIsNotNone(row['started_at'])
        self.assertIn('fixture failure', self.client.get(f'/api/jobs/{jid}/log').json()['content'])

    def test_archiving_is_visible_as_active_without_using_a_runner_slot(self):
        jid = self.fixture.create()
        web.update_job(jid, status='ARCHIVING')
        result = self.client.get('/api/jobs').json()
        self.assertEqual(result['counts']['active'], 1)
        self.assertEqual(result['active_jobs'], 0)

    def test_create_confirm_resume_and_launch_check_the_next_stage(self):
        calls = []
        def check(pipeline, env, chains=None, **kwargs):
            calls.append((pipeline, kwargs['stage'], set(chains or [])))
            result = test_workflow.available_dependencies(pipeline, env, chains, **kwargs)
            if kwargs['stage'] == 'analysis':
                result.update(valid=False, errors=[{'field': 'environment', 'code': 'missing_database', 'message': 'missing reference', 'blocking': True}])
            return result
        with patch.object(web.runtime_preflight, 'check_dependencies', side_effect=check):
            jid = self.fixture.create()
            self.assertEqual(calls[-1][1], 'match')
            self.fixture.match(jid, 1)
            self.fixture.mark_all(jid)
            match = self.client.get(f'/api/jobs/{jid}/match-preview').json()
            response = self.client.post(f'/api/jobs/{jid}/confirm-match', json={'revision': match['revision']})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(calls[-1], ('igblast_base', 'analysis', {'TRA', 'TRB'}))
            self.assertEqual(web.get_job_row(jid)['status'], 'WAITING_REVIEW')
            env = json.loads(web.get_job_row(jid)['env_json'])
            env['SCIGBLAST_WEB_MATCH_ONLY'] = '0'
            web.update_job(jid, status='FAILED', env_json=json.dumps(env))
            self.assertEqual(self.client.post(f'/api/jobs/{jid}/resume').status_code, 400)
            web.update_job(jid, status='QUEUED')
            with patch.object(web.subprocess, 'Popen') as spawn:
                self.assertFalse(web.launch_job(jid))
                spawn.assert_not_called()
            self.assertIn('missing reference', self.client.get(f'/api/jobs/{jid}/log').json()['content'])
            (self.fixture.raw / 'A__IGH.csv').write_text('CDR3(pep),copy,joinedSeq\nC,1,ACGT\n')
            denied = self.client.post('/api/jobs', json={'pipeline':'mapping','input_path':str(self.fixture.raw),'mapping_chains':['IGH']})
            self.assertEqual(denied.status_code, 400)
            self.assertEqual(calls[-1], ('mapping', 'analysis', {'IGH'}))

    def test_live_recovered_group_blocks_resume_and_unverified_group_is_not_killed(self):
        jid = self.fixture.create()
        web.update_job(jid, status='RUNNING', pgid=999, pid=999,
                       execution_json=json.dumps({'process_token': 'fixture-token'}))
        web.mark_interrupted_jobs()
        with patch.object(web, 'group_state', return_value='verified'):
            self.assertEqual(self.client.post(f'/api/jobs/{jid}/resume').status_code, 409)
            self.assertEqual(web.get_job_row(jid)['attempt_no'], 0)
            self.assertTrue(web.snapshot(web.get_job_row(jid))['can_stop_recovered'])
        with patch.object(web, 'signal_group', return_value='unverified') as send:
            self.assertEqual(self.client.post(f'/api/jobs/{jid}/stop').status_code, 409)
            send.assert_called_once_with(999, 'fixture-token', signal.SIGTERM)
        with patch.object(web, 'group_state', return_value='gone'):
            self.assertEqual(self.client.post(f'/api/jobs/{jid}/resume').status_code, 200)

    def test_verified_recovered_stop_finishes_before_releasing_its_slot(self):
        jid = self.fixture.create()
        web.update_job(jid, status='INTERRUPTED', pgid=999, pid=999,
                       execution_json=json.dumps({'process_token': 'fixture-token'}))
        with patch.object(web, 'group_state', return_value='verified'), \
                patch.object(web, 'signal_group', return_value='verified'), \
                patch.object(web.threading, 'Thread'):
            response = self.client.post(f'/api/jobs/{jid}/stop')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['status'], 'STOPPING')
            self.assertEqual(web.active_count(), 1)
        with patch.object(web, 'group_state', return_value='gone'):
            web.finish_recovered_stop(jid, 999, 'fixture-token')
        self.assertEqual(web.get_job_row(jid)['status'], 'STOPPED')
        self.assertIsNone(web.get_job_row(jid)['pgid'])


if __name__ == '__main__':
    unittest.main()
