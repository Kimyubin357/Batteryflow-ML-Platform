"""Current evidence is served and old cached evidence cannot survive replacement."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from backend.copilot import Copilot, SOURCES
from backend.data import Repository
from backend.main import create_api
from backend.settings import ROOT
from backend.tests.fakes import FakeService


class CopilotSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = Repository()

    def test_source_api_serves_current_documents_and_rejects_retired_sources(self):
        current = {
            'service-policy', 'demand-validation', 'demand-contract',
            'maintenance-current', 'quality-current',
        }
        retired = {
            'seed-v3', 'seed-v4', 'maintenance-model',
            'maintenance-dictionary', 'maintenance-validation',
            'quality-validation', 'quality-details',
        }
        self.assertEqual(set(SOURCES), current)
        copilot = Copilot(self.repo)
        client = TestClient(create_api(False, FakeService(), copilot))
        client.cookies.set('manufacturing_session', 'a')
        with patch.object(copilot, 'prepare') as prepare:
            for source_id, (_, relative) in SOURCES.items():
                with self.subTest(source=source_id):
                    source_path = ROOT / relative
                    self.assertEqual(source_path.parent, ROOT / 'docs/copilot')
                    response = client.get('/api/copilot/sources/' + source_id)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.text, source_path.read_text(encoding='utf-8'))
                    self.assertTrue(response.text.strip())
            for source_id in retired:
                with self.subTest(retired=source_id):
                    self.assertEqual(client.get('/api/copilot/sources/' + source_id).status_code, 404)
            prepare.assert_not_called()

    def fake_retrieval(self):
        """Exercise disk-cache decisions without model downloads or native FAISS."""
        embedding = MagicMock()
        embedding.embed.side_effect = lambda texts: [[1.0, 0.0] for _ in texts]
        faiss = MagicMock()
        faiss.write_index.side_effect = lambda index, path: Path(path).write_bytes(b'test-index')
        return embedding, faiss

    def copilot_at(self, directory):
        copilot = Copilot(self.repo)
        copilot.config['rag_dir'] = Path(directory) / 'rag'
        return copilot

    def test_retired_disk_cache_is_replaced_with_current_corpus(self):
        with TemporaryDirectory() as directory:
            old_root = Path(directory)
            (old_root / 'old.md').write_text('Retired policy: moving average primary.', encoding='utf-8')
            embedding, faiss = self.fake_retrieval()
            with patch('backend.copilot.TextEmbedding', return_value=embedding), patch('backend.copilot.faiss', faiss):
                with patch('backend.copilot.ROOT', old_root), patch('backend.copilot.SOURCES', {
                    'seed-v3': ('Retired seed', 'old.md'),
                }):
                    old = self.copilot_at(directory)
                    old.prepare()
                manifest = Path(directory) / 'rag/chunks.json'
                old_signature = json.loads(manifest.read_text(encoding='utf-8'))['signature']
                embedding.embed.reset_mock()
                faiss.read_index.reset_mock()
                current = self.copilot_at(directory)
                current.prepare()
                saved = json.loads(manifest.read_text(encoding='utf-8'))
                self.assertNotEqual(saved['signature'], old_signature)
                embedding.embed.assert_called_once()
                faiss.read_index.assert_not_called()
                self.assertEqual({chunk['source'] for chunk in saved['chunks']}, set(SOURCES))
                self.assertNotIn('Retired policy', json.dumps(saved['chunks']))

    def test_source_revision_rebuilds_cache_but_unchanged_sources_reuse_it(self):
        with TemporaryDirectory() as directory:
            embedding, faiss = self.fake_retrieval()
            with patch('backend.copilot.TextEmbedding', return_value=embedding), patch('backend.copilot.faiss', faiss):
                first = self.copilot_at(directory)
                first.prepare()
                manifest = Path(directory) / 'rag/chunks.json'
                first_signature = json.loads(manifest.read_text(encoding='utf-8'))['signature']
                embedding.embed.reset_mock()
                cached = self.copilot_at(directory)
                cached.prepare()
                embedding.embed.assert_not_called()
                faiss.read_index.assert_called_once()

                revised = self.copilot_at(directory)
                original_source = revised.source

                def changed_source(source_id):
                    item = original_source(source_id)
                    if source_id == 'demand-contract':
                        item['text'] += '\nRevised input contract regression marker.\n'
                    return item

                faiss.read_index.reset_mock()
                with patch.object(revised, 'source', side_effect=changed_source):
                    revised.prepare()
                saved = json.loads(manifest.read_text(encoding='utf-8'))
                self.assertNotEqual(saved['signature'], first_signature)
                embedding.embed.assert_called_once()
                faiss.read_index.assert_not_called()
                self.assertTrue(any('regression marker' in chunk['text'] for chunk in saved['chunks']))


if __name__ == '__main__':
    unittest.main()
