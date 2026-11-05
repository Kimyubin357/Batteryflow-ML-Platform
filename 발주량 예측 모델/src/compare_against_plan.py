"""Day-level comparison of every method against the D+3 plan reference for a completed version.

Rows from correlated parts on one target date are not independent, so whole target dates are
resampled. Reads only saved predictions; writes <version>/plan_comparison.json.
"""
import argparse, json
import numpy as np
import pandas as pd
from demand_contract import ROOT, MODEL_NAMES

REFERENCE = 'D+3 Plan Reference'


def compare(predictions, resamples=4000, seed=42):
    result = {}
    for model in [m for m in MODEL_NAMES if m != REFERENCE]:
        rng = np.random.default_rng(seed)
        delta = (predictions.assign(d=(predictions[model] - predictions.actual).abs()
                                    - (predictions[REFERENCE] - predictions.actual).abs())
                 .groupby('target_date').d.mean())
        boot = np.array([rng.choice(delta.to_numpy(), len(delta)).mean() for _ in range(resamples)])
        result[model] = {'mean_daily_mae_minus_plan': float(delta.mean()),
                         'bootstrap95': np.quantile(boot, [.025, .975]).round(4).tolist(),
                         'days_better_than_plan': int((delta < 0).sum()), 'target_days': int(len(delta))}
    return result


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--version', default='audited_v3')
    out = ROOT / 'outputs' / parser.parse_args().version
    folder = out / 'source_total'
    cv = pd.read_csv(folder / 'cv_predictions.csv'); retro = pd.read_csv(folder / 'final_holdout_predictions.csv')
    report = {'reference': REFERENCE, 'unit': 'daily mean absolute error difference (negative = better than plan)',
              'resampling': 'target-date bootstrap, 4000 resamples, seed 42',
              'cv': compare(cv), 'retrospective': compare(retro), 'all_test_dates': compare(pd.concat([cv, retro]))}
    (out / 'plan_comparison.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    for model, r in report['all_test_dates'].items():
        print(f"{model:<22} {r['mean_daily_mae_minus_plan']:+7.2f}  {r['bootstrap95']}  better {r['days_better_than_plan']}/{r['target_days']}")


if __name__ == '__main__':
    main()
