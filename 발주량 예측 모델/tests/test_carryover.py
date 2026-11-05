"""Carried-over snapshot handling (audited_v3). Independent of saved model outputs."""
import sys
import unittest
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from demand_contract import HORIZON_COLUMNS, QUANTITIES, build_samples, load_daily, realign_carryover


def synthetic_raw(snapshots):
    """snapshots: list of (part, 'YYYYmmddHHMM', {horizon: qty}). Other quantity cells are 0."""
    rows = []
    for part, stamp, plan in snapshots:
        r = [part] + [0] * 82 + [int(stamp)]
        for k, q in plan.items():
            r[HORIZON_COLUMNS[k]] = q
        rows.append(r)
    return pd.DataFrame(rows, columns=['Part Number'] + [f'c{i}' for i in range(1, 83)] + ['CRET_TIME'])


def daily_from(raw):
    d = pd.DataFrame({'part_number': raw.iloc[:, 0], 'row_id': np.arange(len(raw)),
                      'date': pd.to_datetime(raw.iloc[:, 83].astype(str), format='%Y%m%d%H%M').dt.normalize()})
    for c, k in zip(QUANTITIES, range(6)):
        d[c] = raw.iloc[:, HORIZON_COLUMNS[k]].to_numpy()
    return d


def week(part, base_values, carried_days=(), skip_days=()):
    """Mon 2021-10-04 .. Wed 10-13; plan for calendar day t written on day s sits in horizon t-s."""
    snaps, last = [], None
    for i in range(10):
        day = pd.Timestamp('2021-10-04') + pd.Timedelta(days=i)
        if i in skip_days:
            continue
        if i in carried_days:
            plan = last
        else:
            plan = {k: base_values(i + k) for k in range(0, 15)}
            last = plan
        snaps.append((part, day.strftime('%Y%m%d') + '0700', plan))
    return snaps


class CarryoverTests(unittest.TestCase):
    def setUp(self):
        value = lambda t: 100 + 10 * t  # distinct quantity for every calendar day
        self.value = value
        # Sat(5) and Sun(6) repeat Friday(4); Wednesday(2) has no log at all.
        self.raw = synthetic_raw(week('P1', value, carried_days=(5, 6), skip_days=(2,)) +
                                 week('P2', lambda t: 7 * t, carried_days=(5, 6), skip_days=(2,)))
        self.daily, self.flags = realign_carryover(daily_from(self.raw), self.raw)
        self.p1 = self.daily[self.daily.part_number.eq('P1')].set_index('date')

    def test_repeated_snapshot_is_read_from_its_base_date(self):
        sat, sun = pd.Timestamp('2021-10-09'), pd.Timestamp('2021-10-10')
        self.assertEqual(self.p1.loc[sat, 'snapshot_base_date'], pd.Timestamp('2021-10-08'))
        self.assertEqual(self.p1.loc[sun, 'carryover_days'], 2)
        # Saturday's D+3 is Tuesday 10-12, i.e. calendar day index 8 counted from Monday.
        self.assertEqual(self.p1.loc[sat, 'plan_d3'], self.value(8))
        self.assertEqual(self.p1.loc[sun, 'plan_d3'], self.value(9))
        self.assertEqual(self.p1.loc[sat, 'actual_d'], self.value(5))
        self.assertFalse(self.p1.loc[sat, 'actual_known'])
        self.assertTrue(self.p1.loc[pd.Timestamp('2021-10-08'), 'actual_known'])

    def test_calendar_gap_is_filled_as_carried_over(self):
        wed = pd.Timestamp('2021-10-06')
        self.assertTrue(self.p1.loc[wed, 'calendar_gap_filled'])
        self.assertEqual(self.p1.loc[wed, 'carryover_days'], 1)
        self.assertEqual(self.p1.loc[wed, 'plan_d3'], self.value(5))
        self.assertFalse(self.p1.loc[wed, 'actual_known'])

    def test_carried_dates_are_never_labels_and_baselines_use_known_values(self):
        frame = build_samples(self.daily)
        known = set(self.daily.loc[self.daily.actual_known, 'date'])
        self.assertTrue(set(frame.target_date) <= known)
        self.assertNotIn(pd.Timestamp('2021-10-09'), set(frame.target_date))
        p1 = frame[frame.part_number.eq('P1')].set_index('origin_date')
        fri = p1.loc[pd.Timestamp('2021-10-08')]
        self.assertEqual(fri.target_date, pd.Timestamp('2021-10-11'))
        self.assertEqual(fri['D+3 Plan Reference'], self.value(7))
        # Window Wed(gap)-Thu-Fri: only Thu and Fri final quantities are known.
        self.assertAlmostEqual(fri['3-day Moving Average'], (self.value(3) + self.value(4)) / 2)
        # Saturday origin: plan for Tuesday comes from Friday's D+4; last known final is Friday's.
        sat = p1.loc[pd.Timestamp('2021-10-09')]
        self.assertEqual(sat.target_date, pd.Timestamp('2021-10-12'))
        self.assertEqual(sat['D+3 Plan Reference'], self.value(8))
        self.assertEqual(sat['Last Value'], self.value(4))
        self.assertEqual(sat.target, self.value(8))

    def test_detection_does_not_look_ahead(self):
        cut = self.raw[self.raw.CRET_TIME.astype(str) < '202110090000']
        part, _ = realign_carryover(daily_from(cut), cut)
        full = self.daily[self.daily.date < pd.Timestamp('2021-10-09')]
        cols = ['part_number', 'date', 'carryover_days', 'actual_known'] + QUANTITIES
        pd.testing.assert_frame_equal(part[cols].reset_index(drop=True), full[cols].reset_index(drop=True))


class SourceCarryoverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.daily, cls.quality, _ = load_daily(carryover='realign')

    def test_detected_dates_match_audit(self):
        expected = [f'2021-09-{d}' for d in range(18, 27)] + ['2021-10-03', '2021-10-09', '2021-10-10',
                    '2021-10-11', '2021-10-17', '2021-10-23', '2021-10-24', '2021-10-30', '2021-10-31']
        self.assertEqual(self.quality['carryover_dates'], expected)
        self.assertEqual(self.quality['calendar_gap_dates'], ['2021-10-31'])
        self.assertEqual(self.quality['label_days'], 32)
        # Saturdays with a genuine update stay label days.
        known = set(self.daily.loc[self.daily.actual_known, 'date'].dt.strftime('%Y-%m-%d'))
        self.assertTrue({'2021-10-02', '2021-10-16'} <= known)

    def test_saturday_plan_points_at_the_right_monday(self):
        # 10-23 repeats Friday 10-22; its D+2 must be Monday 10-25 = Friday's D+3.
        d = self.daily.set_index(['part_number', 'date'])
        sat = d.xs(pd.Timestamp('2021-10-23'), level=1)
        fri = d.xs(pd.Timestamp('2021-10-22'), level=1)
        self.assertGreater(sat.plan_d2.sum(), 5000)
        common = sat.index.intersection(fri.index)
        self.assertGreater(len(common), 100)
        np.testing.assert_array_equal(sat.plan_d2.loc[common], fri.plan_d3.loc[common])

    def test_default_load_is_unchanged(self):
        keep, quality, _ = load_daily()
        self.assertNotIn('actual_known', keep.columns)
        self.assertEqual(len(keep), 5380)
        self.assertNotIn('carryover_policy', quality)


class AuditedV3RunTests(unittest.TestCase):
    OUT = ROOT / 'outputs/audited_v3'

    def setUp(self):
        if not (self.OUT / 'run_manifest.json').exists():
            self.skipTest('audited_v3 has not been generated')

    def test_run_matches_current_code_and_compares_all_methods(self):
        import hashlib, json
        run = json.loads((self.OUT / 'run_manifest.json').read_text(encoding='utf-8'))
        for name, digest in run['code_sha256'].items():
            self.assertEqual(hashlib.sha256((ROOT / 'src' / name).read_bytes()).hexdigest(), digest)
        plan = json.loads((self.OUT / 'training_plan_v2.json').read_text(encoding='utf-8'))
        self.assertEqual(plan['carryover'], 'realign')
        scores = pd.read_csv(self.OUT / 'source_total/cv_metrics_pooled.csv')
        self.assertEqual(len(scores), 9)
        cv = pd.read_csv(self.OUT / 'source_total/cv_predictions.csv')
        carried = set(json.loads((self.OUT / 'data_quality.json').read_text(encoding='utf-8'))['carryover_dates'])
        self.assertFalse(carried & set(cv.target_date))


if __name__ == '__main__':
    unittest.main()
