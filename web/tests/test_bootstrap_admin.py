"""Deployment administrator initialization with an isolated database."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as web
from fastapi.testclient import TestClient


class BootstrapAdminTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        settings = {'SCIGBLAST_ADMIN_USERNAME': 'DeployAdmin',
                    'SCIGBLAST_ADMIN_PASSWORD': 'Ab1$#x',
                    'SCIGBLAST_ADMIN_DISPLAY_NAME': 'Deployment Admin'}
        for mock in (patch.object(web, 'STATE_ROOT', root),
                     patch.object(web, 'DB_PATH', root / 'test.sqlite3'),
                     patch.dict(os.environ, settings), patch.object(web, 'schedule')):
            mock.start()
            self.addCleanup(mock.stop)
        web.init_db()

    def users(self):
        with web.db() as connection:
            return [dict(row) for row in connection.execute('SELECT * FROM users')]

    def test_startup_creates_admin_who_can_login(self):
        with TestClient(web.app, headers={'X-SCIGBLAST-Request': '1'}) as client:
            response = client.post('/api/auth/login', json={
                'username': 'DeployAdmin', 'password': os.environ['SCIGBLAST_ADMIN_PASSWORD']})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(client.get('/api/admin/users').status_code, 200)
        user, = self.users()
        self.assertEqual((user['username'], user['display_name'], user['role'], user['active']),
                         ('deployadmin', 'Deployment Admin', 'admin', 1))
        self.assertNotEqual(user['password_hash'], os.environ['SCIGBLAST_ADMIN_PASSWORD'])
        self.assertTrue(web.auth.password_matches(os.environ['SCIGBLAST_ADMIN_PASSWORD'], user['password_hash']))

    def test_restarts_preserve_existing_admin_even_if_disabled(self):
        web.startup()
        with web.db() as connection:
            connection.execute('UPDATE users SET active=0')
        before = self.users()
        with patch.dict(os.environ, {'SCIGBLAST_ADMIN_USERNAME': 'another',
                                    'SCIGBLAST_ADMIN_PASSWORD': '',
                                    'SCIGBLAST_ADMIN_DISPLAY_NAME': 'Changed'}):
            web.startup()
        self.assertEqual(self.users(), before)
        with web.db() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM auth_events WHERE action='bootstrap-admin'").fetchone()[0], 1)

    def test_no_configuration_keeps_manual_setup(self):
        with patch.dict(os.environ, {key: '' for key in (
                'SCIGBLAST_ADMIN_USERNAME', 'SCIGBLAST_ADMIN_PASSWORD', 'SCIGBLAST_ADMIN_DISPLAY_NAME')}):
            web.startup()
        self.assertEqual(self.users(), [])

    def test_display_name_defaults_to_username(self):
        with patch.dict(os.environ, {'SCIGBLAST_ADMIN_DISPLAY_NAME': ''}):
            web.startup()
        self.assertEqual(self.users()[0]['display_name'], 'deployadmin')

    def test_invalid_configuration_fails_without_creating_accounts(self):
        cases = [('SCIGBLAST_ADMIN_USERNAME', ''), ('SCIGBLAST_ADMIN_USERNAME', 'bad name'),
                 ('SCIGBLAST_ADMIN_PASSWORD', ''), ('SCIGBLAST_ADMIN_PASSWORD', 'short'),
                 ('SCIGBLAST_ADMIN_PASSWORD', 'x' * 129),
                 ('SCIGBLAST_ADMIN_DISPLAY_NAME', '123 Invalid')]
        for key, value in cases:
            with self.subTest(key=key, value=value), patch.dict(os.environ, {key: value}):
                with self.assertRaisesRegex(RuntimeError, key):
                    web.startup()
                self.assertEqual(self.users(), [])

    def test_existing_regular_user_is_not_promoted(self):
        with web.db() as connection:
            connection.execute("INSERT INTO users(id,username,display_name,password_hash,role,created_at) "
                               "VALUES('regular','deployadmin','Regular','unchanged','user',0)")
        before = self.users()
        with self.assertRaisesRegex(RuntimeError, 'non-admin'):
            web.startup()
        self.assertEqual(self.users(), before)

    def test_concurrent_initialization_creates_one_admin(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: web.auth.bootstrap_admin(web.db), range(2)))
        self.assertEqual(len(self.users()), 1)


if __name__ == '__main__':
    unittest.main()
