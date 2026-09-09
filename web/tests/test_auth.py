"""Authentication, ownership and invitation regression tests (no pipelines started)."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import sqlite3
import time
import unittest

from fastapi.testclient import TestClient
import test_workflow as workflow

web = workflow.web

PASSWORD = 'testing-password-123'
HEADERS = {'X-SCIGBLAST-Request': '1'}


class AuthTests(unittest.TestCase):
    setUp = workflow.WorkflowTests.setUp
    tearDown = workflow.WorkflowTests.tearDown
    create = workflow.WorkflowTests.create

    def invite(self):
        result = self.client.post('/api/admin/invitations', json={'hours': 24})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def register(self, username='alice', code=None):
        with TestClient(web.app, headers=HEADERS) as client:
            return client.post('/api/auth/register', json={'username': username, 'display_name': '测试用户',
                              'password': PASSWORD, 'invitation': code or self.invite()['code']})

    def login(self, username='alice', password=PASSWORD):
        client = TestClient(web.app, headers=HEADERS)
        result = client.post('/api/auth/login', json={'username': username, 'password': password})
        self.assertEqual(result.status_code, 200, result.text)
        self.addCleanup(client.close)
        return client

    def test_login_required_csrf_and_cookie_flags(self):
        with TestClient(web.app) as public:
            self.assertEqual(public.get('/api/jobs').status_code, 401)
            self.assertEqual(public.get('/', follow_redirects=False).status_code, 303)
            self.assertEqual(public.get('/health').status_code, 200)
            self.assertEqual(public.get('/login').status_code, 200)
            body = {'username': 'admin', 'password': 'test-password-123'}
            self.assertEqual(public.post('/api/auth/login', json=body).status_code, 403)
            self.assertEqual(public.post('/api/auth/login', headers={**HEADERS, 'Origin': 'https://evil.example'}, json=body).status_code, 403)
            large = public.post('/api/auth/login', headers=HEADERS, content=b'x'*4097)
            self.assertEqual(large.status_code, 413)
            response = public.post('/api/auth/login', headers=HEADERS, json=body)
            self.assertEqual(response.status_code, 200, response.text)
            cookie = response.headers['set-cookie'].lower()
            self.assertIn('httponly', cookie)
            self.assertIn('samesite=strict', cookie)
            self.assertNotIn('password', response.text)

    def test_invitation_is_atomic_single_use_and_not_retrievable(self):
        invitation = self.invite()
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(self.register, name, invitation['code']) for name in ['alice', 'bobby']]
            statuses = sorted(f.result().status_code for f in futures)
        self.assertEqual(statuses, [200, 400])
        listing = self.client.get('/api/admin/invitations')
        self.assertNotIn(invitation['code'], listing.text)
        self.assertEqual(listing.json()['invitations'][0]['status'], '已使用')
        with web.db() as connection:
            row = connection.execute('SELECT * FROM invitations').fetchone()
            self.assertNotEqual(row['code_hash'], invitation['code'])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM users WHERE role='user'").fetchone()[0], 1)

    def test_expired_revoked_invites_and_duplicate_username(self):
        expired = self.invite()
        with web.db() as connection:
            connection.execute('UPDATE invitations SET expires_at=0 WHERE id=?', (expired['id'],))
        self.assertEqual(self.register(code=expired['code']).status_code, 400)
        revoked = self.invite()
        self.assertEqual(self.client.post(f"/api/admin/invitations/{revoked['id']}/revoke", json={}).status_code, 200)
        self.assertEqual(self.register(code=revoked['code']).status_code, 400)
        self.assertEqual(self.register().status_code, 200)
        fresh = self.invite()
        self.assertEqual(self.register(code=fresh['code']).status_code, 409)
        self.assertEqual(self.register('bobby', fresh['code']).status_code, 200)

    def test_invitation_registration_provenance_survives_disable_and_restart(self):
        invitation = self.invite()
        self.assertEqual(self.register('alice', invitation['code']).status_code, 200)
        user = next(u for u in self.client.get('/api/admin/users').json()['users'] if u['username'] == 'alice')
        self.assertEqual(user['invitation_id'], invitation['id'])
        response = self.client.get('/api/admin/invitations', params={'q': invitation['id']})
        record, = response.json()['invitations']
        self.assertEqual((record['created_by'], record['created_username']), ('test-admin', 'admin'))
        self.assertEqual((record['used_by'], record['used_username'], record['used_display_name']),
                         (user['id'], 'alice', '测试用户'))
        self.assertEqual(record['used_at'], user['created_at'])
        self.assertEqual(record['used_active'], 1)
        self.assertNotIn(invitation['code'], response.text)
        self.assertNotIn('code_hash', response.text)
        self.assertEqual(self.client.post(f"/api/admin/invitations/{invitation['id']}/revoke", json={}).status_code, 409)
        self.assertEqual(self.client.post(f"/api/admin/users/{user['id']}/active", json={'active': False}).status_code, 200)
        web.startup()
        record, = self.client.get('/api/admin/invitations', params={'q': 'ALICE'}).json()['invitations']
        self.assertEqual((record['id'], record['status'], record['used_active'], record['used_at']),
                         (invitation['id'], '已使用', 0, user['created_at']))
        self.assertEqual(record['used_by'], user['id'])

    def test_invitation_filters_and_pagination(self):
        available, used, revoked, expired = [self.invite() for _ in range(4)]
        self.assertEqual(self.register('alice', used['code']).status_code, 200)
        self.assertEqual(self.client.post(f"/api/admin/invitations/{revoked['id']}/revoke", json={}).status_code, 200)
        self.assertEqual(self.client.post(f"/api/admin/invitations/{revoked['id']}/revoke", json={}).status_code, 409)
        with web.db() as connection:
            connection.execute('UPDATE invitations SET expires_at=0 WHERE id=?', (expired['id'],))
        for status, invitation in [('available', available), ('used', used), ('revoked', revoked), ('expired', expired)]:
            response = self.client.get('/api/admin/invitations', params={'status': status})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['total'], 1)
            self.assertEqual(response.json()['invitations'][0]['id'], invitation['id'])
        for query in ('alice', '测试用户', used['id']):
            result = self.client.get('/api/admin/invitations', params={'q': query}).json()
            self.assertEqual(result['total'], 1)
            self.assertEqual(result['invitations'][0]['id'], used['id'])
        self.assertEqual(self.client.get('/api/admin/invitations', params={'q': 'admin'}).json()['total'], 4)
        self.assertEqual(self.client.get('/api/admin/invitations', params={'q': "' OR 1=1 --"}).json()['total'], 0)
        page1 = self.client.get('/api/admin/invitations', params={'limit': 2}).json()
        page2 = self.client.get('/api/admin/invitations', params={'limit': 2, 'offset': 2}).json()
        self.assertEqual(page1['total'], 4)
        self.assertEqual(len({r['id'] for p in [page1, page2] for r in p['invitations']}), 4)
        self.assertEqual(self.client.get('/api/admin/invitations', params={'status': 'bad'}).status_code, 400)
        for params in ({'limit': 0}, {'limit': 201}, {'offset': -1}):
            self.assertEqual(self.client.get('/api/admin/invitations', params=params).status_code, 422)
        ordinary_user = self.login('alice')
        self.assertEqual(ordinary_user.get('/api/admin/invitations', params={'q': 'admin'}).status_code, 403)
        self.assertEqual(ordinary_user.get('/api/admin/users').status_code, 403)

    def test_invitation_history_beyond_first_two_hundred_records(self):
        with web.db() as connection:
            connection.executemany('INSERT INTO invitations(id,code_hash,created_by,created_at,expires_at) '
                                   'VALUES(?,?,?, ?,0)',
                                   [(f'old-{i:03}', f'digest-{i}', 'test-admin', i) for i in range(205)])
        last_page = self.client.get('/api/admin/invitations', params={'status': 'expired', 'offset': 200}).json()
        self.assertEqual(last_page['total'], 205)
        self.assertEqual(len(last_page['invitations']), 5)
        self.assertEqual(last_page['invitations'][-1]['id'], 'old-000')

    def test_delete_unused_invitation_invalidates_code_and_preserves_audit(self):
        invitation = self.invite()
        endpoint = f"/api/admin/invitations/{invitation['id']}/delete"
        with TestClient(web.app) as public:
            self.assertEqual(public.post(endpoint, headers=HEADERS, json={}).status_code, 401)
        self.assertEqual(self.client.post(endpoint, json={}).status_code, 200)
        self.assertEqual(self.register('alice', invitation['code']).status_code, 400)
        for status in ('', 'available', 'revoked'):
            result = self.client.get('/api/admin/invitations', params={'status': status}).json()
            self.assertEqual(result['total'], 0)
        deleted, = self.client.get('/api/admin/invitations', params={'status': 'deleted'}).json()['invitations']
        self.assertEqual((deleted['id'], deleted['status'], deleted['created_username']),
                         (invitation['id'], '已删除', 'admin'))
        self.assertIsNotNone(deleted['deleted_at'])
        self.assertEqual(self.client.post(endpoint, json={}).status_code, 404)
        self.assertEqual(self.client.post(f"/api/admin/invitations/{invitation['id']}/revoke", json={}).status_code, 409)
        with web.db() as connection:
            rows = connection.execute("SELECT actor_id,target_id FROM auth_events WHERE action='delete-invitation'").fetchall()
            self.assertEqual([tuple(r) for r in rows], [('test-admin', invitation['id'])])

    def test_delete_used_invitation_keeps_user_session_and_registration_source(self):
        invitation = self.invite()
        self.assertEqual(self.register('alice', invitation['code']).status_code, 200)
        user_client = self.login('alice')
        user = user_client.get('/api/auth/me').json()['user']
        endpoint = f"/api/admin/invitations/{invitation['id']}/delete"
        self.assertEqual(user_client.post(endpoint, json={}).status_code, 403)
        self.assertEqual(self.client.post(endpoint, json={}).status_code, 200)
        web.startup()
        self.assertEqual(user_client.get('/api/auth/me').json()['user'], user)
        registered = next(u for u in self.client.get('/api/admin/users').json()['users'] if u['id'] == user['id'])
        self.assertEqual(registered['invitation_id'], invitation['id'])
        self.assertIsNotNone(registered['invitation_deleted_at'])
        history = self.client.get('/api/admin/invitations', params={'status': 'deleted', 'q': 'alice'}).json()
        self.assertEqual(history['total'], 1)
        self.assertEqual(history['invitations'][0]['used_by'], user['id'])
        self.assertEqual(history['invitations'][0]['used_at'], user['created_at'])

    def test_deleted_invitation_schema_migration_preserves_existing_records(self):
        with closing(sqlite3.connect(':memory:')) as connection:
            connection.executescript('CREATE TABLE jobs(id TEXT PRIMARY KEY); '
                'CREATE TABLE invitations(id TEXT PRIMARY KEY,code_hash TEXT NOT NULL UNIQUE, '
                'created_by TEXT NOT NULL,created_at INTEGER NOT NULL,expires_at INTEGER NOT NULL, '
                'used_by TEXT,revoked INTEGER NOT NULL DEFAULT 0); '
                "INSERT INTO invitations VALUES('old','hash','creator',1,2,'registered',0);")
            web.auth.init_schema(connection)
            web.auth.init_schema(connection)
            self.assertEqual(connection.execute('SELECT id,created_by,used_by,deleted_at FROM invitations').fetchone(),
                             ('old', 'creator', 'registered', None))

    def test_account_role_and_revocation(self):
        self.assertEqual(self.register().status_code, 200)
        user = self.login()
        me = user.get('/api/auth/me').json()['user']
        self.assertEqual(me['role'], 'user')
        self.assertEqual(user.get('/admin').status_code, 403)
        self.assertEqual(user.post('/api/admin/invitations', json={'hours': 1}).status_code, 403)
        self.assertEqual(self.client.post(f"/api/admin/users/{me['id']}/active", json={'active': False}).status_code, 200)
        self.assertEqual(user.get('/api/auth/me').status_code, 401)
        self.assertEqual(user.post('/api/auth/login', json={'username': 'alice', 'password': PASSWORD}).status_code, 401)
        self.assertEqual(self.client.post('/api/admin/users/test-admin/active', json={'active': False}).status_code, 400)

    def test_logout_and_password_change_revoke_sessions(self):
        self.register()
        first, second = self.login(), self.login()
        self.assertEqual(first.post('/api/auth/password', json={'current_password': PASSWORD, 'new_password': 'new-password-123'}).status_code, 200)
        self.assertEqual(first.get('/api/auth/me').status_code, 401)
        self.assertEqual(second.get('/api/auth/me').status_code, 401)
        third = self.login(password='new-password-123')
        self.assertEqual(third.post('/api/auth/logout', json={}).status_code, 200)
        self.assertEqual(third.get('/api/auth/me').status_code, 401)

    def test_task_ownership_cannot_be_forged_and_admin_audit(self):
        self.register()
        user = self.login()
        body = {'pipeline': 'igblast_base', 'operator': '伪造姓名', 'input_path': str(self.raw), 'submission_path': str(self.source)}
        result = user.post('/api/jobs', json=body)
        self.assertEqual(result.status_code, 200, result.text)
        job = result.json()
        self.assertEqual(job['operator'], '测试用户')
        self.assertEqual(workflow.Path(job['output_root']).parent, self.out/'alice'/'igblast_base')
        own = user.get('/api/jobs').json()
        self.assertEqual(own['total'], 1)
        legacy_response = self.client.post('/api/jobs', json={**body, 'output_root': str(self.out/'legacy')})
        self.assertEqual(legacy_response.status_code, 200, legacy_response.text)
        legacy = legacy_response.json()['id']
        web.update_job(legacy, owner_id=None)
        for path in [f'/jobs/{legacy}', f'/api/jobs/{legacy}', f'/api/jobs/{legacy}/log', f'/api/jobs/{legacy}/match-preview', f'/api/jobs/{legacy}/artifacts', f'/api/jobs/{legacy}/download?kind=match']:
            self.assertEqual(user.get(path).status_code, 404, path)
        for action in ['stop', 'resume', 'delete', 'review', 'confirm-match', 'rematch']:
            self.assertEqual(user.post(f'/api/jobs/{legacy}/{action}', json={}).status_code, 404, action)
        self.assertEqual(user.get('/api/jobs').json()['counts']['total'], 1)
        self.assertEqual(self.client.get('/api/jobs').json()['total'], 2)
        self.assertEqual(self.client.post(f"/api/jobs/{job['id']}/stop", json={}).status_code, 200)
        actions = user.get(f"/api/jobs/{job['id']}").json()['actions']
        self.assertEqual(actions[-1]['operator'], '郑钦云 (admin)')
        self.assertEqual(actions[0]['operator'], '测试用户 (alice)')

    def test_submission_ownership_and_output_overlap(self):
        self.register()
        self.register('bobby')
        alice, bob = self.login(), self.login('bobby')
        created = alice.post('/api/submissions/import', json={'path': str(self.source)}).json()
        token = created['revision']
        self.assertEqual(alice.get('/api/submissions', params={'revision': token}).status_code, 200)
        self.assertEqual(bob.get('/api/submissions', params={'revision': token}).status_code, 404)
        self.assertEqual(bob.post('/api/submissions/revise', json={'revision': token, 'changes': []}).status_code, 404)
        body = {'pipeline': 'igblast_base', 'input_path': str(self.raw), 'submission_revision': token, 'output_root': str(self.out/'alice')}
        self.assertEqual(bob.post('/api/jobs', json=body).status_code, 404)
        self.assertEqual(alice.post('/api/jobs', json=body).status_code, 200)
        body.update(submission_revision='', submission_path=str(self.source), dataset_label='different')
        self.assertEqual(bob.post('/api/jobs', json=body).status_code, 409)

    def test_rate_limit_is_persistent(self):
        with web.db() as connection:
            connection.execute('INSERT INTO auth_limits VALUES(?,?,?)', (web.auth.digest('login-ip:testclient'), int(time.time()), 30))
        with TestClient(web.app, headers=HEADERS) as client:
            result = client.post('/api/auth/login', json={'username': 'admin', 'password': 'test-password-123'})
            self.assertEqual(result.status_code, 429)

    def test_admin_edited_submission_remains_accessible_to_job_owner(self):
        self.register()
        user = self.login()
        body = {'pipeline': 'igblast_base', 'input_path': str(self.raw), 'submission_path': str(self.source)}
        result = user.post('/api/jobs', json=body)
        jid = result.json()['id']
        web.update_job(jid, status='FAILED')
        admin_copy = self.client.post('/api/submissions/import', json={'path': str(self.source)}).json()['revision']
        self.assertEqual(user.get('/api/submissions', params={'revision': admin_copy}).status_code, 404)
        self.assertEqual(self.client.post(f'/api/jobs/{jid}/rematch', json={'revision': admin_copy}).status_code, 200)
        self.assertEqual(user.get('/api/submissions', params={'revision': admin_copy}).status_code, 200)

    def test_migration_preserves_history(self):
        jid = self.create()
        web.update_job(jid, owner_id=None)
        web.init_db()
        self.assertIsNone(web.get_job_row(jid)['owner_id'])
        self.assertEqual(self.client.get(f'/api/jobs/{jid}').status_code, 200)


if __name__ == '__main__':
    unittest.main()
