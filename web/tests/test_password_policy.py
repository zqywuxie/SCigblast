"""Password boundaries across registration, login, account changes and the CLI."""
from contextlib import redirect_stderr, redirect_stdout
import io
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import test_workflow as workflow
import manage_users

web = workflow.web
HEADERS = {'X-SCIGBLAST-Request': '1'}


class PasswordPolicyTests(unittest.TestCase):
    setUp = workflow.WorkflowTests.setUp
    tearDown = workflow.WorkflowTests.tearDown

    def test_six_character_registration_login_and_password_change(self):
        invitation = self.client.post('/api/admin/invitations', json={'hours': 24}).json()['code']
        body = {'username': 'alice', 'display_name': 'Alice', 'password': 'abc123', 'invitation': invitation}
        for invalid in ('12345', 'x' * 129):
            self.assertEqual(self.client.post('/api/auth/register', json={**body, 'password': invalid}).status_code, 422)
        self.assertEqual(self.client.post('/api/auth/register', json=body).status_code, 200)
        with TestClient(web.app, headers=HEADERS) as client:
            self.assertEqual(client.post('/api/auth/login', json={'username': 'alice', 'password': 'abc123'}).status_code, 200)
            for invalid in ('12345', 'x' * 129):
                self.assertEqual(client.post('/api/auth/password', json={'current_password': 'abc123', 'new_password': invalid}).status_code, 422)
            self.assertEqual(client.post('/api/auth/password', json={'current_password': 'abc123', 'new_password': 'xyz789'}).status_code, 200)
            self.assertEqual(client.get('/api/auth/me').status_code, 401)
            self.assertEqual(client.post('/api/auth/login', json={'username': 'alice', 'password': 'abc123'}).status_code, 401)
            self.assertEqual(client.post('/api/auth/login', json={'username': 'alice', 'password': 'xyz789'}).status_code, 200)

    def run_cli(self, action, password):
        with patch('sys.argv', ['manage_users.py', action, 'cliadmin']), \
                patch.object(manage_users.getpass, 'getpass', side_effect=[password, password]), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            manage_users.main()

    def test_cli_create_and_reset_accept_six_characters(self):
        with self.assertRaises(SystemExit):
            self.run_cli('create-admin', '12345')
        self.run_cli('create-admin', 'abc123')
        with TestClient(web.app, headers=HEADERS) as client:
            self.assertEqual(client.post('/api/auth/login', json={'username': 'cliadmin', 'password': 'abc123'}).status_code, 200)
            self.assertEqual(client.get('/api/auth/me').json()['user']['role'], 'admin')
            self.run_cli('reset-password', 'xyz789')
            self.assertEqual(client.get('/api/auth/me').status_code, 401)
            with self.assertRaises(SystemExit):
                self.run_cli('reset-password', '12345')
            self.assertEqual(client.post('/api/auth/login', json={'username': 'cliadmin', 'password': 'xyz789'}).status_code, 200)


if __name__ == '__main__':
    unittest.main()
