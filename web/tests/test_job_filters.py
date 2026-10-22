import unittest
import test_auth


class JobFilterTests(unittest.TestCase):
    setUp = test_auth.AuthTests.setUp
    tearDown = test_auth.AuthTests.tearDown
    invite = test_auth.AuthTests.invite
    register = test_auth.AuthTests.register
    login = test_auth.AuthTests.login

    def test_operator_filter_pagination_and_ownership(self):
        self.assertEqual(self.register().status_code, 200)
        user = self.login()
        body = {'pipeline': 'igblast_base', 'input_path': str(self.raw), 'submission_path': str(self.source)}
        own = user.post('/api/jobs', json=body)
        self.assertEqual(own.status_code, 200, own.text)
        other = self.client.post('/api/jobs', json=body)
        self.assertEqual(other.status_code, 200, other.text)
        admin_name = other.json()['operator_display_name']
        result = self.client.get('/api/jobs', params={'operator': 'Test User', 'limit': 1}).json()
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['jobs'][0]['id'], own.json()['id'])
        self.assertEqual(set(result['operators']), {'Test User', admin_name})
        self.assertEqual(self.client.get('/api/jobs', params={'operator': 'Test User', 'offset': 1}).json()['jobs'], [])
        self.assertEqual(self.client.get('/api/jobs', params={'operator': 'Test'}).json()['total'], 0)
        private = user.get('/api/jobs', params={'operator': admin_name}).json()
        self.assertEqual(private['total'], 0)
        self.assertEqual(private['operators'], ['Test User'])
        self.assertIn('operator-filter', self.client.get('/').text)
        self.assertEqual(self.client.get('/api/pipelines').json()['mapping']['label'], 'reMapping')

    def test_equal_creation_times_have_stable_pages_and_renamed_operators(self):
        body = {'pipeline': 'igblast_base', 'input_path': str(self.raw), 'submission_path': str(self.source)}
        ids = [self.client.post('/api/jobs', json=body).json()['id'] for _ in range(3)]
        with test_auth.web.db() as connection:
            connection.execute("UPDATE jobs SET created_at='2026-10-22T00:00:00+00:00'")
            connection.execute("UPDATE users SET display_name='Renamed Admin' WHERE role='admin'")
        actual = [self.client.get('/api/jobs', params={'operator': 'Renamed Admin', 'offset': offset, 'limit': 1}).json()['jobs'][0]['id'] for offset in range(3)]
        self.assertEqual(actual, sorted(ids, reverse=True))
        result = self.client.get('/api/jobs', params={'operator': 'Renamed Admin', 'query': 'no-match'}).json()
        self.assertEqual(result['total'], 0)
        self.assertEqual(result['counts']['total'], 3)
