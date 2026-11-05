"""Check emitted training-plan metadata without loading data or training models."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import retrain_audited as training
from demand_contract import evaluation_plan


class StopBeforeTraining(Exception):
    pass


class TrainingPlanTests(unittest.TestCase):
    def test_cli_plan_matches_default_v3_and_partial_split_settings(self):
        cases=[
            ('default',{},
             {'holdout_days':7,'cv_days':12,'folds':3,'validation_days':5,'min_train_days':7},[4,4,4]),
            ('v3',{'holdout_days':6,'cv_days':7,'folds':2,'validation_days':4,'min_train_days':5},
             {'holdout_days':6,'cv_days':7,'folds':2,'validation_days':4,'min_train_days':5},[4,3]),
            ('partial',{'folds':4,'holdout_days':5},
             {'holdout_days':5,'cv_days':12,'folds':4,'validation_days':5,'min_train_days':7},[3,3,3,3])]
        dates=pd.date_range('2021-01-01',periods=80)
        frame=pd.DataFrame({'target_date':dates,'origin_date':dates-pd.Timedelta(days=3)})
        for name,overrides,expected,fold_days in cases:
            with self.subTest(name=name),tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                argv=['retrain_audited.py','--version','metadata_test','--skip-lstm']
                if name=='v3':argv+=['--carryover','realign']
                for key,value in overrides.items():argv+=['--'+key.replace('_','-'),str(value)]
                saved={key:getattr(training,key) for key in
                       ['OUT','MODELS','VERSION','CARRYOVER','ML_NAMES','MODEL_NAMES','SPLIT']}
                with patch.multiple(training,ROOT=root,**saved),patch.object(sys,'argv',argv), \
                     patch.object(training,'load_daily',side_effect=StopBeforeTraining):
                    with self.assertRaises(StopBeforeTraining):training.main()
                output=root/'outputs/metadata_test/training_plan_v2.json'
                doc=json.loads(output.read_text(encoding='utf-8'))
                self.assertEqual(doc['split_sizes'],expected)
                self.assertIn(f"{expected['folds']} purged outer folds across {expected['cv_days']} target dates",doc['selection'])
                self.assertIn(f"{expected['validation_days']}-date inner validation",doc['selection'])
                self.assertIn(f"minimum {expected['min_train_days']} training target dates",doc['selection'])
                self.assertIn(f"last {expected['holdout_days']} target dates",doc['holdout'])
                self.assertIn('not new independent evidence',doc['holdout'])
                self.assertEqual([p for p in root.rglob('*') if p.is_file()],[output])
                plan=evaluation_plan(frame,**overrides)
                self.assertEqual([frame.loc[masks[2],'target_date'].nunique() for _,masks in plan[:-1]],fold_days)
                self.assertEqual(frame.loc[plan[-1][1][2],'target_date'].nunique(),expected['holdout_days'])
                for _,masks in plan:
                    self.assertEqual(frame.loc[masks[1],'target_date'].nunique(),expected['validation_days'])
                    self.assertGreaterEqual(frame.loc[masks[0],'target_date'].nunique(),expected['min_train_days'])


if __name__=='__main__':unittest.main()
