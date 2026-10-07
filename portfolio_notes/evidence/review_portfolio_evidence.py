"""Reproduce portfolio follow-up evidence without training or cloud access.

Run from the project root with .venv/Scripts/python.exe -B and this file path.
Bootstrap units are paired target-date clusters, retaining every part in a day.
The estimator is pooled row-weighted MAE reduction, not the mean daily ratio.
"""
from pathlib import Path
import hashlib
import json
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.data import Repository
from backend.copilot_search import search, ranked_answer

OUT = Path(__file__).resolve().parent
SOURCE = ROOT / '발주량 예측 모델/outputs/audited_v2'
CV = SOURCE / 'source_total/cv_predictions.csv'
REPETITIONS = 20_000
SEED = 42


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def summary(frame):
    m7, plan = float(frame.error_ma7.mean()), float(frame.error_plan.mean())
    return {'rows': len(frame), 'target_days': int(frame.target_date.nunique()),
            'mae_ma7': m7, 'mae_plan': plan, 'mae_reduction_units': plan-m7,
            'relative_mae_reduction_pct': 100*(1-m7/plan)}


def main():
    cv = pd.read_csv(CV)
    assert not cv.duplicated(['part_number', 'target_date']).any()
    assert (cv.groupby('target_date').Fold.nunique() == 1).all()
    assert np.isfinite(cv[['actual', '7-day Moving Average', 'D+3 Plan Reference']]).all().all()
    cv = cv.assign(error_ma7=abs(cv.actual-cv['7-day Moving Average']),
                   error_plan=abs(cv.actual-cv['D+3 Plan Reference']))
    days = cv.groupby('target_date', sort=True).agg(
        n=('actual', 'size'), sum_error_ma7=('error_ma7', 'sum'),
        sum_error_plan=('error_plan', 'sum'))
    rng = np.random.default_rng(SEED)
    draws = rng.integers(0, len(days), size=(REPETITIONS, len(days)))
    # Sum each selected day's errors AND row count; repeated days repeat all rows.
    sums = days.to_numpy()[draws].sum(axis=1)
    assert (sums[:, 2] > 0).all()
    ratios = 100*(1-sums[:, 1]/sums[:, 2])
    differences = (sums[:, 2]-sums[:, 1])/sums[:, 0]
    assert np.isclose(summary(cv)['mae_ma7'], days.sum_error_ma7.sum()/days.n.sum())
    history = pd.read_csv(SOURCE / 'daily_history.csv')
    missing = []
    for part in sorted(set(history.part_number)-set(cv.part_number), key=lambda p:int(p.split()[-1])):
        observed = history[history.part_number == part]
        overlap = sorted(set(observed.date) & set(cv.target_date))
        assert not overlap
        missing.append({'part': part, 'observed_rows': len(observed),
                        'first_observation': observed.date.min(),
                        'last_observation': observed.date.max(),
                        'observed_dates': observed.date.tolist(),
                        'cv_target_observations': len(overlap)})
    result = {
        'checked_at': '2026-10-06', 'input': CV.relative_to(ROOT).as_posix(),
        'input_sha256': checksum(CV), 'script_sha256': checksum(Path(__file__)),
        'versions': {'numpy': np.__version__, 'pandas': pd.__version__},
        'method': 'paired target-day cluster percentile bootstrap; pooled MAE',
        'seed': SEED, 'repetitions': REPETITIONS,
        'ci_level': .95, 'quantile_method': 'linear',
        'all_folds': summary(cv),
        'ci95_relative_mae_reduction_pct': np.quantile(ratios, [.025, .975]).tolist(),
        'ci95_mae_reduction_units': np.quantile(differences, [.025, .975]).tolist(),
        'by_fold': {fold: summary(g) for fold,g in cv.groupby('Fold', sort=True)},
        'leave_one_fold_out': {fold: summary(cv[cv.Fold != fold]) for fold in sorted(cv.Fold.unique())},
        'parts_absent_from_cv': missing,
        'scope': 'Post-hoc uncertainty of saved, CV-selected forecasts. Twelve resampled days; '
                 'does not adjust for serial dependence across days or model-selection optimism. '
                 'Leave-one-fold-out is a sensitivity check without refitting.',
        'training_performed': False,
    }
    (OUT / 'baseline_comparison.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    days.to_csv(OUT / 'daily_error_sums.csv')

    repo = Repository()
    raw = []
    for part in repo.parts.values():
        for forecast in part['forecasts']:
            if forecast['target_date'] == '2021-11-01':
                pred, plan = float(forecast['7-day Moving Average']), float(forecast['D+3 Plan Reference'])
                raw.append({'part': part['part_number'], 'forecast': pred, 'plan': plan, 'gap': pred-plan})
    raw.sort(key=lambda row: (-abs(row['gap']), int(row['part'].split()[-1])))
    tie = next(row['gap'] for row in raw if row['part']=='Part 60')
    facts, _, error = search(repo, {'track': 'demand', 'part': 'ALL', 'date': '2021-11-01'}, '계획과 차이가 큰 부품 3개')
    assert error is None
    ranking = {'checked_at': '2026-10-06', 'data_version': repo.manifest['dataVersion'],
               'forecast_rows': len(raw),
               'precision_rule': 'API gap rounded to 2 decimals; absolute descending, numeric Part ascending',
               'raw_top_seven': raw[:7],
               'exact_raw_ties_with_part60': [r['part'] for r in raw if abs(r['gap'])==abs(tie)],
               'display_ties_with_part60': [r['part'] for r in raw if abs(round(r['gap'],2))==abs(round(tie,2))],
               'api_ranked_results': facts['rankedResults'], 'server_answer': ranked_answer(facts),
               'source': 'official local seed; no remote read and no Gemini call'}
    (OUT / 'ranking_audit.json').write_text(json.dumps(ranking, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'reduction_pct': result['all_folds']['relative_mae_reduction_pct'],
                      'ci95_pct': result['ci95_relative_mae_reduction_pct'],
                      'without_fold1_pct': result['leave_one_fold_out']['fold_1']['relative_mae_reduction_pct'],
                      'exact_ties': ranking['exact_raw_ties_with_part60']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
