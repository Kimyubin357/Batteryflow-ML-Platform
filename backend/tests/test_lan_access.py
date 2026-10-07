import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.main import create_api
from backend.settings import _hosts
from backend.tests.fakes import FakeCopilot, FakeService
from run_local import configure_lan


class AllowedHostsTests(unittest.TestCase):
    def test_launcher_allows_detected_ip_and_preserves_explicit_hosts(self):
        with patch.dict(os.environ, {'ALLOWED_HOSTS': 'pc-01.local,192.168.0.5'}), patch('run_local.lan_address', return_value='192.168.0.5'):
            self.assertEqual(configure_lan('0.0.0.0'), '192.168.0.5')
            self.assertEqual(os.environ['ALLOWED_HOSTS'], 'pc-01.local,192.168.0.5')
        with patch.dict(os.environ, {'ALLOWED_HOSTS': ''}), patch('run_local.lan_address', return_value='192.168.0.6'):
            configure_lan('0.0.0.0')
            self.assertEqual(os.environ['ALLOWED_HOSTS'], '192.168.0.6')

    def test_local_mode_leaves_host_policy_unchanged(self):
        with patch.dict(os.environ, {'ALLOWED_HOSTS': 'pc-01.local'}):
            self.assertIsNone(configure_lan('127.0.0.1'))
            self.assertEqual(os.environ['ALLOWED_HOSTS'], 'pc-01.local')

    def test_missing_network_address_stops_lan_startup(self):
        with patch('run_local.lan_address', return_value=None):
            with self.assertRaises(SystemExit):
                configure_lan('0.0.0.0')

    def test_parsing_accepts_ips_and_names_only(self):
        self.assertEqual(_hosts(''), [])
        self.assertEqual(_hosts(' 10.101.134.171, pc-01.local ;192.168.0.5'), ['10.101.134.171', 'pc-01.local', '192.168.0.5'])
        for bad in ('*', 'http://10.0.0.1', '10.0.0.1:8070', '*.example.com'):
            with self.assertRaises(ValueError):
                _hosts(bad)

    def client(self, hosts):
        with patch.dict(os.environ, {'ALLOWED_HOSTS': hosts}):
            return TestClient(create_api(False, FakeService(), FakeCopilot()))

    def test_lan_ip_is_rejected_until_listed(self):
        closed = self.client('')
        self.assertEqual(closed.get('/api/health', headers={'Host': '10.101.134.171:8070'}).status_code, 400)
        opened = self.client('10.101.134.171')
        self.assertEqual(opened.get('/api/health', headers={'Host': '10.101.134.171:8070'}).status_code, 200)
        self.assertEqual(opened.get('/api/health', headers={'Host': 'evil.example:8070'}).status_code, 400)
        self.assertEqual(opened.get('/api/health').status_code, 200)

    def test_login_works_from_lan_origin(self):
        client = self.client('10.101.134.171')
        base = {'Host': '10.101.134.171:8070'}
        client.get('/login', headers=base)
        token = client.cookies.get('manufacturing_csrf')
        response = client.post('/auth/login', json={'email': 'a@example.com', 'password': 'x'},
                               headers={**base, 'Origin': 'http://10.101.134.171:8070', 'X-CSRF-Token': token})
        self.assertEqual(response.status_code, 200)


if __name__ == '__main__':
    unittest.main()
