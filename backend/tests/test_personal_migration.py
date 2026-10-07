import unittest
from datetime import datetime, timezone
from google.cloud.firestore_v1 import _helpers
from google.cloud.firestore_v1.types import Document
import json

from scripts.migrate_personal_firestore import fields, remap, permitted, digest, create_batch
from google.api_core.exceptions import InvalidArgument, DeadlineExceeded


class MigrationTests(unittest.TestCase):
    def test_large_atomic_batch_splits_but_ambiguous_timeout_does_not_retry(self):
        class Database:
            committed=[]
            attempts=0
            timeout=False
            def document(self,path): return path
            def batch(self):
                owner=self
                class Batch:
                    def __init__(self): self.rows=[]
                    def create(self,path,value): self.rows.append((path,value))
                    def commit(self,**kwargs):
                        owner.attempts+=1
                        if owner.timeout: raise DeadlineExceeded('timeout')
                        if len(self.rows)>2: raise InvalidArgument('Transaction too big. Decrease transaction size.')
                        owner.committed.extend(self.rows)
                return Batch()
        db=Database()
        entries=[('battery_meta/'+str(n),{'n':n}) for n in range(7)]
        create_batch(db,entries)
        self.assertEqual(db.committed,entries)
        db.timeout=True; before=db.attempts
        with self.assertRaises(DeadlineExceeded): create_batch(db,entries)
        self.assertEqual(db.attempts,before+1)

    def test_typed_backup_round_trip(self):
        data={'integer':2**60, 'float':1.125, 'flag':False, 'empty':None,
              'blob':b'\x00\xff', 'stamp':datetime(2026,10,7,tzinfo=timezone.utc),
              'nested':{'rows':[1,'한글',None]}}
        encoded=fields(data)
        restored=_helpers.decode_dict(Document.from_json(json.dumps({'fields':encoded})).fields,None)
        self.assertEqual(data,restored)
        self.assertEqual(digest(encoded),digest(fields(restored)))

    def test_scope_excludes_staff_and_other_official_roots(self):
        for path in ['manufacturingEmployees/a','manufacturingConversations/a/threads/b',
                     'manufacturingRecords/a','manufacturingAi/other','battery_packs/a/series']:
            self.assertFalse(permitted(path),path)
        self.assertTrue(permitted('manufacturingAi/manufacturing-ai/demandParts/a'))
        self.assertTrue(permitted('battery_packs/1000_chg/series/score'))

    def test_references_retarget_only_migrated_documents(self):
        value={'referenceValue':'projects/source/databases/(default)/documents/battery_packs/1000_chg'}
        self.assertEqual(remap(value,'source','target')['referenceValue'],value['referenceValue'].replace('/source/','/target/'))
        with self.assertRaises(ValueError):
            remap({'referenceValue':'projects/source/databases/(default)/documents/manufacturingEmployees/a'},'source','target')


if __name__=='__main__':
    unittest.main()
