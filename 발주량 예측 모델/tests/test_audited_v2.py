import hashlib
import json
import subprocess
import sys
import unittest
from contextlib import contextmanager
from unittest.mock import patch
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
import torch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from demand_contract import SOURCE, MODEL_NAMES, ML_NAMES, build_samples, evaluation_plan, feature_record, metrics
from retrain_audited import predict_artifact, relations
import inference
from inference import predict_records, build_feature_row, load_metadata

OUT=ROOT/'outputs/audited_v2'


@contextmanager
def model_dir(path):
    """Run inference against a given deployment folder (the service default is the current version)."""
    inference.load_metadata.cache_clear();inference.load_artifact.cache_clear()
    with patch.object(inference,'MODEL_DIR',path):
        yield
    inference.load_metadata.cache_clear();inference.load_artifact.cache_clear()


def committed_sha256(path):
    """SHA-256 of every committed version of a file (stored -text, so bytes match the run)."""
    def git(*args):
        return subprocess.run(['git',*args],cwd=path.parent,capture_output=True,check=True).stdout
    # Paths stay relative to the file's folder: non-ASCII folder names never reach git arguments.
    try:
        commits=git('log','--format=%H','--',path.name).decode().split()
    except (OSError,subprocess.CalledProcessError):
        raise unittest.SkipTest('git history unavailable')
    return {hashlib.sha256(git('show',f'{c}:./{path.name}')).hexdigest() for c in commits}

class AuditedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.daily=pd.read_csv(OUT/'daily_history.csv',parse_dates=['date'])
        cls.frame=build_samples(cls.daily)
        cls.meta=json.loads((OUT/'metadata.json').read_text(encoding='utf-8'))

    def test_source_provenance_and_quarantine(self):
        run=json.loads((OUT/'run_manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(),run['raw_sha256'])
        # The shared code has since gained audited_v3 options (v2 defaults kept), so provenance means
        # the exact producing code is preserved in git history; behaviour is checked by the
        # prediction-reproduction tests below.
        for name,digest in run['code_sha256'].items():
            self.assertIn(digest,committed_sha256(ROOT/'src'/name),f'{name} that produced audited_v2 is not in git history')
        self.assertEqual(self.meta['quarantined_parts'],['Part 21','Part 26'])
        self.assertFalse(self.daily.part_number.isin(self.meta['quarantined_parts']).any())
        self.assertEqual(len(self.daily),5380)
        self.assertFalse(self.daily.duplicated(['part_number','date']).any())
        self.assertTrue(self.daily.actual_d.eq(0).any())
        self.assertIn('Part 115',set(self.daily.part_number))

    def test_splits_are_purged_and_retrospective_is_honest(self):
        for name,(tr,va,te,rf) in evaluation_plan(self.frame):
            self.assertLess(self.frame.loc[tr,'target_date'].max(),self.frame.loc[va,'origin_date'].min())
            self.assertLess(self.frame.loc[rf,'target_date'].max(),self.frame.loc[te,'origin_date'].min())
            self.assertLess(self.frame.loc[va,'target_date'].max(),self.frame.loc[te,'origin_date'].min())
            self.assertEqual(self.frame.loc[va,'target_date'].nunique(),5)
        self.assertFalse(self.meta['selection']['new_unseen_validation_available'])
        self.assertFalse(self.meta['selection']['final_holdout_is_selection_independent'])

    def test_metrics_and_selection_are_reproducible(self):
        for scenario in ['source_total','slot_sum']:
            for kind,filename in [('cv','cv_metrics_pooled.csv'),('final_holdout','final_holdout_metrics.csv')]:
                p=pd.read_csv(OUT/scenario/f'{kind}_predictions.csv')
                scores=pd.read_csv(OUT/scenario/filename).set_index('Model')
                for model in MODEL_NAMES:
                    for key,value in metrics(p.actual,p[model]).items():
                        np.testing.assert_allclose(value,scores.loc[model,key],rtol=1e-7,atol=1e-6)
            cv=pd.read_csv(OUT/scenario/'cv_metrics_pooled.csv').sort_values(['MAE','RMSE'])
            choice=json.loads((OUT/scenario/'selection_frozen_before_retrospective.json').read_text())
            self.assertEqual(choice['overall_method'],cv.iloc[0].Model)
            self.assertEqual(choice['ml_model'],cv[cv.Model.isin(ML_NAMES)].iloc[0].Model)

    def test_all_saved_models_reproduce_every_outer_prediction(self):
        cv=pd.read_csv(OUT/'source_total/cv_predictions.csv')
        retro=pd.read_csv(OUT/'source_total/final_holdout_predictions.csv')
        for name,(_,_,te,_) in evaluation_plan(self.frame):
            f=self.frame.loc[te]
            saved=(retro if name=='retrospective' else cv[cv.Fold.eq(name)])
            self.assertEqual(f.part_number.tolist(),saved.part_number.tolist())
            for family in ML_NAMES:
                folder=ROOT/'models/audited_v2/source_total'/name
                a=torch.load(folder/'lstm.pt',weights_only=False) if family=='LSTM' else joblib.load(folder/(family.lower().replace(' ','_')+'.joblib'))
                values=predict_artifact(a,f,self.daily)
                np.testing.assert_allclose(values,saved[family],rtol=1e-6,atol=1e-5)

    def test_future_values_cannot_change_features_or_relations(self):
        origin=pd.Timestamp('2021-10-14')
        part=self.daily[self.daily.part_number.eq('Part 94')].copy()
        before=feature_record(part,origin)
        part.loc[part.date>origin,['actual_d','plan_d3','plan_d4','plan_d5']]=999999
        self.assertEqual(before,feature_record(part,origin))
        changed=self.daily.copy();changed.loc[changed.date>origin,'actual_d']=999999
        self.assertEqual(relations(self.daily,origin),relations(changed,origin))

    def test_inference_contract_and_runtime_models_match_training(self):
        saved=pd.read_csv(OUT/'source_total/final_holdout_predictions.csv')
        with model_dir(ROOT/'models/audited_v2/source_total/deployment'):
            self._check_inference(saved)

    def _check_inference(self,saved):
        for _,row in saved[saved.part_number.eq('Part 94')].iterrows():
            origin=pd.Timestamp(row.origin_date)
            history=self.daily[self.daily.part_number.eq(row.part_number)&self.daily.date.between(origin-pd.Timedelta(days=7),origin)]
            result=predict_records(history)
            self.assertAlmostEqual(result['recommended_forecast'],row[self.meta['selection']['overall_method']],places=5)
            self.assertAlmostEqual(result['auxiliary_prediction'],row[self.meta['selection']['ml_model']],places=4)
            shared,_=build_feature_row(history)
            training=self.frame[self.frame.part_number.eq(row.part_number)&self.frame.origin_date.eq(origin)].iloc[0]
            for col in self.meta['feature_contract']['features_basic']+self.meta['feature_contract']['features_extra']:
                np.testing.assert_allclose(shared.iloc[0][col],training[col],equal_nan=True)

    def test_invalid_inputs_and_new_part_fallback(self):
        template=pd.read_csv(ROOT/'outputs/dashboard_data/inference_input_template.csv')
        for kind in ['duplicate','infinite','negative','quarantine']:
            data=template.copy()
            if kind=='duplicate':data.loc[1,'date']=data.loc[0,'date']
            elif kind=='infinite':data['actual_d']=float('inf')
            elif kind=='negative':data['actual_d']=-1
            else:data['part_number']='Part 21'
            with self.assertRaises(ValueError):predict_records(data)
        template['part_number']='New Part'
        result=predict_records(template)
        self.assertEqual(result['recommended_model'],'Last Value')
        self.assertEqual(result['recommended_forecast'],template.sort_values('date').iloc[-1].actual_d)
        self.assertIsNone(result['auxiliary_prediction'])

if __name__=='__main__':unittest.main()
