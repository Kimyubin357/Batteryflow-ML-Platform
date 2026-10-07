import unittest
from firestore.sync_official_seed import diff


class SafeSyncTests(unittest.TestCase):
    def test_prunes_only_archived_paths_and_preserves_unknown_records(self):
        plan=diff({'root/current':{'x':2},'root/new':{}},
                  {'root/current':{'x':1},'root/old':{},'root/operator':{}},{'root/old'})
        self.assertEqual(plan['delete'],['root/old'])
        self.assertEqual(plan['preserved'],['root/operator'])
        self.assertEqual(plan['update'],['root/current'])
        self.assertEqual(plan['create'],['root/new'])

    def test_matching_seed_is_noop(self):
        docs={'root/current':{'nested':{'value':[1,2,3]}}}
        plan=diff(docs,docs,{'root/old'})
        self.assertEqual(plan,{'create':[],'update':[],'delete':[],'preserved':[],'unchanged':1})
