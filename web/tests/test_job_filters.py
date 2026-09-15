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
