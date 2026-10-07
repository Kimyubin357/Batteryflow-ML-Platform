import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from itertools import count
from unittest.mock import patch, Mock

from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.main import create_api
from backend.portfolio_service import PortfolioService, VISITOR_COOKIE
from backend.tests.fakes import FakeCopilot


class PortfolioTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.now = 1791340000.0
        self.path = Path(self.folder.name)/'visitors.sqlite3'

    def service(self, **kwargs):
        return PortfolioService(self.path, clock=lambda: self.now, **kwargs)

    def enter(self, client):
        self.assertIn('포트폴리오 둘러보기', client.get('/login').text)
        client.headers.update({'Origin':'http://testserver', 'X-CSRF-Token':client.cookies['manufacturing_csrf']})
        response = client.post('/auth/portfolio')
        self.assertEqual(response.status_code, 200, response.text)
        client.headers['X-CSRF-Token'] = client.cookies['manufacturing_csrf']
        return response.json()['uid']

    def test_budget_survives_restart_reentry_logout_and_midnight(self):
        service = self.service(visitor_limit=1)
        token, user, visitor = service.start(None, 'client')
        service.budget(user['uid'], reserve=True)
        service.logout(token)
        other = self.service(visitor_limit=1)
        _, again, _ = other.start(visitor, 'client')
        self.assertEqual(again['uid'], user['uid'])
        self.assertEqual(other.budget(user['uid'])['remaining'], 0)
        with self.assertRaises(HTTPException) as exc:
            other.budget(user['uid'], reserve=True)
        self.assertEqual(exc.exception.status_code, 429)
        self.now += 86400
        other.start(visitor, 'client')
        self.assertEqual(other.budget(user['uid'])['remaining'], 1)

    def test_cookie_rotation_ip_cap_and_atomic_global_cap(self):
        service = self.service(ip_limit=1, global_limit=2)
        _, a, _ = service.start(None, 'same-ip')
        service.budget(a['uid'], reserve=True)
        _, b, _ = service.start('forged.cookie', 'same-ip')
        self.assertEqual(service.budget(b['uid'])['remaining'], 0)
        visitors = [service.start(None, f'ip-{n}')[1]['uid'] for n in range(8)]
        def spend(uid):
            try:
                self.service(ip_limit=1, global_limit=2).budget(uid, reserve=True)
                return 1
            except HTTPException as exc:
                self.assertEqual(exc.status_code, 429)
                return 0
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(spend, visitors)), 1)

    def test_endpoints_quota_isolation_csrf_and_read_only(self):
        service = self.service(visitor_limit=1)
        copilot = FakeCopilot()
        with patch('backend.main.FirebaseService', side_effect=AssertionError('No Firebase')):
            app = create_api(False, service=service, copilot_service=copilot)
        a, b = TestClient(app), TestClient(app)
        self.assertEqual(a.get('/api/copilot/quota').status_code, 401)
        self.assertEqual(a.post('/auth/portfolio').status_code, 403)
        uid = self.enter(a)
        self.enter(b)
        self.assertNotEqual(a.cookies[VISITOR_COOKIE], b.cookies[VISITOR_COOKIE])
        self.assertEqual(a.get('/api/health').json()['firebase'], 'disabled')
        self.assertEqual(a.get('/api/demand').status_code, 200)
        self.assertEqual(a.get('/api/records/state').status_code, 403)
        self.assertEqual(a.get('/api/admin/auth-logs').status_code, 403)
        ticks = count(100, 4)
        with patch('backend.main.time', SimpleNamespace(monotonic=lambda: next(ticks))):
            answer = a.post('/api/copilot/ask', json={'question':'계획과 차이가 큰 부품은?'})
            self.assertEqual(answer.status_code, 200, answer.text)
            row = answer.json()
            self.assertEqual(len(row['messages']), 2)
            blocked = a.post('/api/copilot/ask', json={'question':'새 대화도 제한되나요?'})
            self.assertEqual(blocked.status_code, 429)
        self.assertEqual(a.get('/api/copilot/quota').json()['remaining'], 0)
        self.assertEqual(b.get('/api/copilot/conversations').json()['threads'], [])
        self.assertEqual(b.get('/api/copilot/conversations/'+row['id']).status_code, 404)
        self.assertEqual(a.post('/api/copilot/draft', json={'threadId':row['id']}).status_code, 403)
        self.assertEqual(service.budget(uid)['used'], 1)

    def test_missing_personal_key_does_not_spend_or_use_team_key(self):
        service = self.service()
        with patch.dict(os.environ, {'PORTFOLIO_GEMINI_API_KEY':'', 'GEMINI_API_KEY':'team-must-not-be-used'}):
            client = TestClient(create_api(False, service=service))
        uid = self.enter(client)
        self.assertFalse(client.get('/api/copilot/quota').json()['available'])
        with patch('backend.copilot.httpx.post', side_effect=AssertionError('No external request')):
            self.assertEqual(client.post('/api/copilot/ask', json={'question':'검증 근거는?'}).status_code, 503)
        self.assertEqual(service.budget(uid)['used'], 0)

    def test_portfolio_factory_and_pack_snapshot_never_construct_firebase(self):
        with patch.dict(os.environ, {'MANUFACTURING_MODE':'portfolio', 'PORTFOLIO_DATA_SOURCE':'local', 'PORTFOLIO_DB':str(self.path)}), \
                patch('backend.main.FirebaseService', side_effect=AssertionError('No Firebase')):
            client = TestClient(create_api(False))
            self.enter(client)
            self.assertEqual(client.get('/api/health').json()['mode'], 'portfolio')
            self.assertEqual(client.get('/api/battery-packs').status_code, 200)
            self.assertEqual(client.get('/api/copilot/sources/demand-validation').status_code, 200)
            body = {'track':'quality','target':'Test07_NG_dchg','decision':'retest',
                    'requestId':'portfolio-test-0001','expectedRevision':0}
            self.assertEqual(client.post('/api/records', json=body).status_code, 403)

    def test_failed_generation_counts_and_expired_chats_are_unavailable(self):
        service = self.service()
        copilot = FakeCopilot()
        client = TestClient(create_api(False, service=service, copilot_service=copilot))
        uid = self.enter(client)
        with patch.object(copilot, 'answer', side_effect=HTTPException(503, 'upstream timeout')):
            self.assertEqual(client.post('/api/copilot/ask', json={'question':'질문'}).status_code, 503)
        self.assertEqual(service.budget(uid)['used'], 1)
        service.save_conversation(uid, 'a'*32, 0, '질문', {'text':'답변'})
        self.now += 86401
        self.assertEqual(service.list_conversations(uid), [])
        with self.assertRaises(HTTPException):
            service.conversation(uid, 'a'*32)

    def test_personal_firestore_analysis_keeps_local_visitors_and_blocks_records(self):
        from backend.analysis_store import FirestoreAnalysis
        from backend.data import Repository
        repo = Repository()
        cloud = SimpleNamespace(config={'project_id':'personal-project'})
        store = FirestoreAnalysis(cloud, repo)
        store._read_root = Mock(return_value=SimpleNamespace(update_time='v4', to_dict=lambda:repo.docs[repo.base]))
        store._read_documents = Mock(return_value=repo.docs)
        with patch.dict(os.environ, {'MANUFACTURING_MODE':'portfolio', 'PORTFOLIO_DATA_SOURCE':'firestore', 'PORTFOLIO_DB':str(self.path)}), \
                patch('backend.main.FirebaseService', return_value=cloud) as factory, \
                patch('backend.analysis_store.FirestoreAnalysis', return_value=store), \
                patch('backend.battery_packs.BatteryPacks') as packs:
            client = TestClient(create_api(False, copilot_service=FakeCopilot()))
            self.assertEqual(client.get('/api/meta').status_code,401)
            store._read_root.assert_not_called()
            self.enter(client)
            response = client.get('/api/meta')
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['analysisSource']['projectId'],'personal-project')
            self.assertEqual(response.headers['X-Analysis-Source'],'firestore')
            self.assertEqual(client.get('/api/me').json()['role'],'visitor')
            self.assertEqual(client.get('/api/records/state').status_code,403)
            self.assertEqual(client.get('/api/health').json()['firebase'],'read-only')
            factory.assert_called_once()
            packs.assert_called_once_with(cloud,demo=False)


if __name__ == '__main__':
    unittest.main()
