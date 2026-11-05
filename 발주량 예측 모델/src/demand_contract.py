"""Versioned, training/inference shared demand contract. No training dependencies."""
from pathlib import Path
import numpy as np
import pandas as pd

VERSION = 'audited_v2'
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'Dataset_공급망 최적화 AI 데이터셋/data/data.xls'
QUANTITIES = ['actual_d', 'plan_d1', 'plan_d2', 'plan_d3', 'plan_d4', 'plan_d5']
SEQUENCE = ['actual_d', 'plan_d3', 'plan_d4', 'plan_d5']
CALENDAR = ['dow_sin', 'dow_cos', 'is_weekend', 'month']
BASIC = [f'{c}_{s}' for s in ['lag2', 'lag1', 'origin'] for c in SEQUENCE] + CALENDAR
EXTRA = ['actual_lag7', 'actual_mean7', 'actual_std7', 'history_count7',
         'plan_revision1', 'plan_revision2', 'plan_revision_rate', 'actual_change1', 'actual_change2']
BASELINES = ['Last Value', '3-day Moving Average', '7-day Moving Average', 'D+3 Plan Reference']
ML_NAMES = ['XGBoost', 'LightGBM', 'CatBoost', 'LSTM', 'Related LightGBM']
MODEL_NAMES = BASELINES + ML_NAMES
# Raw column holding the quantity planned k days after the snapshot's own base date D.
HORIZON_COLUMNS = {0: 11, 1: 22, 2: 33, 3: 44, 4: 55, **{k: 51 + k for k in range(5, 31)}}
SLOT_RANGES = {0: (1, 11), 1: (12, 22), 2: (23, 33), 3: (34, 44), 4: (45, 55)}
CARRYOVER_SHARE = 0.9


def _horizon_values(raw, quantity_source):
    """Quantity for every horizon k=0..30 of each raw row, relative to that row's own base date."""
    values = {}
    for k, col in HORIZON_COLUMNS.items():
        if quantity_source == 'slot_sum' and k in SLOT_RANGES:
            start, stop = SLOT_RANGES[k]
            values[k] = raw.iloc[:, start:stop].sum(axis=1, min_count=stop - start)
        else:
            values[k] = pd.to_numeric(raw.iloc[:, col], errors='raise')
    return pd.DataFrame(values)


def realign_carryover(daily, raw, quantity_source='source_total', share=CARRYOVER_SHARE):
    """Re-read carried-over snapshots against the date they were actually written.

    On some dates (holidays, most weekends) the log repeats the previous snapshot unchanged
    under a new CRET_TIME, so its D/D+k columns still refer to the earlier base date. A date is
    marked carried over when at least ``share`` of the parts it shares with the previous observed
    date have all 82 quantity columns identical; detection only uses that date and the one before.
    Calendar dates with no log at all are filled from the previous date as carried over.
    The final quantity of a carried-over date is unknown: ``actual_known`` is False and
    ``actual_d`` holds the latest plan for that date, usable as an input but never as a label.
    """
    numeric = raw.iloc[:, 1:83].to_numpy()
    horizons = _horizon_values(raw, quantity_source).to_numpy()
    d = daily.sort_values(['date', 'part_number']).reset_index(drop=True)
    dates = sorted(d.date.unique())
    base_of, flags, prev = {}, [], None
    for date in dates:
        today = d[d.date.eq(date)].set_index('part_number').row_id
        carried, ratio = False, np.nan
        if prev is not None:
            before = d[d.date.eq(prev)].set_index('part_number').row_id
            common = today.index.intersection(before.index)
            if len(common):
                same = (numeric[today[common].to_numpy()] == numeric[before[common].to_numpy()]).all(axis=1)
                ratio = float(same.mean()); carried = ratio >= share
        base_of[date] = base_of[prev] if carried else date
        flags.append({'date': date, 'carried_over': carried, 'identical_share': ratio, 'calendar_gap': False})
        prev = date
    rows = d.assign(calendar_gap_filled=False)
    filled = []
    for date in pd.date_range(dates[0], dates[-1]):
        if date in base_of:
            continue
        # Copy the last date that has logs; earlier filled dates have no rows of their own,
        # so consecutive missing days all repeat the same observed snapshot.
        last = max(x for x in dates if x < date)
        copy = rows[rows.date.eq(last)].copy()
        copy['date'] = date; copy['calendar_gap_filled'] = True
        filled.append(copy); base_of[date] = base_of[last]
        flags.append({'date': date, 'carried_over': True, 'identical_share': np.nan, 'calendar_gap': True})
    if filled:
        rows = pd.concat([rows] + filled, ignore_index=True)
    rows['snapshot_base_date'] = rows.date.map(base_of)
    rows['carryover_days'] = (rows.date - rows.snapshot_base_date).dt.days.astype(int)
    rows['actual_known'] = rows.carryover_days.eq(0)
    lag = rows.carryover_days.to_numpy()
    if (lag + 5 > max(HORIZON_COLUMNS)).any():
        raise ValueError('Carry-over longer than the snapshot horizon; cannot realign.')
    h = horizons[rows.row_id.to_numpy()]
    for c, k in zip(QUANTITIES, range(6)):
        rows[c] = h[np.arange(len(rows)), lag + k]
    rows = rows.sort_values(['part_number', 'date']).reset_index(drop=True)
    return rows, pd.DataFrame(flags).sort_values('date').reset_index(drop=True)


def load_daily(quantity_source='source_total', conflict_policy='quarantine', carryover='keep'):
    """carryover='keep' reproduces audited_v2; 'realign' applies realign_carryover."""
    raw = pd.read_excel(SOURCE)
    # Validate labels as well as positions before interpreting the legacy XLS.
    for i, label in {0:'Part Number', 11:'D일 투입예정 수량(D일계획)',
                     44:'D+3일 투입예정 수량(Total)', 55:'D+4일 투입예정 수량(Total)',
                     56:'D+5일 투입예정 수량', 83:'CRET_TIME'}.items():
        if raw.columns[i] != label:
            raise ValueError(f'Unexpected source schema at column {i}: {raw.columns[i]}')
    selected = pd.DataFrame({'part_number':raw.iloc[:,0], 'row_id':np.arange(len(raw)),
        'timestamp':pd.to_datetime(raw.iloc[:,83].astype(str),format='%Y%m%d%H%M',errors='raise')})
    mismatch = {}
    for c, start, stop, total in [('actual_d',1,11,11),('plan_d1',12,22,22),
        ('plan_d2',23,33,33),('plan_d3',34,44,44),('plan_d4',45,55,55)]:
        source = pd.to_numeric(raw.iloc[:,total], errors='raise')
        slots = raw.iloc[:,start:stop].sum(axis=1, min_count=stop-start)
        mismatch[c] = int(source.ne(slots).sum())
        selected[c] = source if quantity_source == 'source_total' else slots
    selected['plan_d5'] = pd.to_numeric(raw.iloc[:,56], errors='raise')
    if selected.isna().any().any() or (selected[QUANTITIES] < 0).any().any():
        raise ValueError('Missing, invalid or negative source values require explicit review.')
    selected['date'] = selected.timestamp.dt.normalize()
    # Conflict detection uses all original numeric columns, independent of quantity scenario.
    groups = raw.groupby([raw.iloc[:,0],raw.iloc[:,83]],sort=False)
    conflicts = groups.nunique().max(axis=1).gt(1)
    bad_keys = set(conflicts.index[conflicts])
    bad_parts = sorted({str(p) for p,t in bad_keys})
    conflict_rows = raw[[ (p,t) in bad_keys for p,t in zip(raw.iloc[:,0],raw.iloc[:,83]) ]].copy()
    conflict_rows.insert(0,'excel_row',conflict_rows.index+2)
    ordered = selected.sort_values(['part_number','timestamp','row_id'])
    if conflict_policy == 'quarantine':
        ordered = ordered[~ordered.part_number.isin(bad_parts)]
    elif conflict_policy == 'first':
        ordered = ordered.drop_duplicates(['part_number','timestamp'],keep='first')
    elif conflict_policy != 'last':
        raise ValueError('Unknown conflict policy')
    daily = ordered.drop_duplicates(['part_number','date'],keep='last').reset_index(drop=True)
    quality = {'source_rows':len(raw),'source_columns':len(raw.columns),'source_parts':raw.iloc[:,0].nunique(),
        'source_missing_cells':int(raw.isna().sum().sum()),'quantity_source':quantity_source,
        'conflict_policy':conflict_policy,'quarantined_parts':bad_parts,'conflicting_timestamp_groups':len(bad_keys),
        'conflict_rows':len(conflict_rows),'daily_rows':len(daily),'daily_parts':daily.part_number.nunique(),
        'observed_dates':daily.date.nunique(),'total_slot_mismatch_rows':mismatch,
        'forecast_as_of':'after the final daily source snapshot; not an intraday forecast',
        'target_definition':'final daily ERP order-plan quantity, not independently measured consumption'}
    if carryover == 'realign':
        daily, flags = realign_carryover(daily, raw, quantity_source)
        quality.update({'carryover_policy': 'realign', 'carryover_identical_share_threshold': CARRYOVER_SHARE,
            'carryover_dates': [str(x.date()) for x in flags.loc[flags.carried_over, 'date']],
            'calendar_gap_dates': [str(x.date()) for x in flags.loc[flags.calendar_gap, 'date']],
            'label_days': int(daily.loc[daily.actual_known, 'date'].nunique()), 'daily_rows_with_fill': len(daily)})
        quality['carryover_flags'] = flags.assign(date=flags.date.dt.strftime('%Y-%m-%d')).to_dict('records')
    elif carryover != 'keep':
        raise ValueError('Unknown carryover policy')
    return daily, quality, conflict_rows


def feature_record(group, origin):
    lookup = group.set_index('date')
    dates = [origin-pd.Timedelta(days=k) for k in [2,1,0]]
    if not all(d in lookup.index for d in dates):
        return None
    rows = lookup.loc[dates]
    result = {'part_number':str(rows.iloc[-1].part_number),'origin_date':origin,
              'target_date':origin+pd.Timedelta(days=3)}
    for suffix, (_, row) in zip(['lag2','lag1','origin'],rows.iterrows()):
        for c in SEQUENCE:result[f'{c}_{suffix}'] = float(row[c])
    result.update(dow_sin=float(np.sin(2*np.pi*origin.dayofweek/7)),
        dow_cos=float(np.cos(2*np.pi*origin.dayofweek/7)),is_weekend=int(origin.dayofweek>=5),month=origin.month)
    realigned='actual_known' in lookup.columns
    # Realigned data: history statistics and baselines use only final quantities actually observed.
    actual=lookup.actual_d.where(lookup.actual_known.astype(bool)) if realigned else lookup.actual_d
    hist=actual.reindex(pd.date_range(origin-pd.Timedelta(days=6),origin))
    result.update(actual_lag7=float(actual.get(origin-pd.Timedelta(days=7),np.nan)),
        actual_mean7=float(hist.mean()),actual_std7=float(hist.std(ddof=0)),history_count7=int(hist.notna().sum()),
        plan_revision1=result['plan_d3_origin']-result['plan_d4_lag1'],
        plan_revision2=result['plan_d4_lag1']-result['plan_d5_lag2'],
        plan_revision_rate=(result['plan_d3_origin']-result['plan_d4_lag1'])/(1+abs(result['plan_d4_lag1'])),
        actual_change1=result['actual_d_origin']-result['actual_d_lag1'],
        actual_change2=result['actual_d_lag1']-result['actual_d_lag2'])
    if realigned:
        known=actual.loc[:origin].dropna()
        last=float(known.iloc[-1]) if len(known) else result['actual_d_origin']
        recent=actual.reindex(dates)
        result['Last Value']=last
        result['3-day Moving Average']=float(recent.mean()) if recent.notna().any() else last
        result['7-day Moving Average']=float(hist.mean()) if hist.notna().any() else last
        result['origin_carryover_days']=int(lookup.loc[origin,'carryover_days'])
    else:
        result['Last Value']=result['actual_d_origin']
        result['3-day Moving Average']=float(rows.actual_d.mean())
        result['7-day Moving Average']=float(hist.mean())
    result['D+3 Plan Reference']=result['plan_d3_origin']
    return result


def build_samples(daily):
    rows=[]
    for part,g in daily.groupby('part_number',sort=True):
        g=g.sort_values('date'); indexed=g.set_index('date'); target=indexed.actual_d
        # Carried-over dates have no observed final quantity and are never labels.
        labels=indexed.actual_known.astype(bool) if 'actual_known' in indexed.columns else None
        for origin in g.date:
            r=feature_record(g,origin)
            if r is not None and r['target_date'] in target.index and (labels is None or labels.loc[r['target_date']]):
                r['target']=float(target.loc[r['target_date']]);rows.append(r)
    return pd.DataFrame(rows).sort_values(['target_date','part_number']).reset_index(drop=True)


def split_for_dates(frame,test_dates,validation_days=5,min_train_days=7):
    dates=pd.DatetimeIndex(frame.target_date.unique()).sort_values().to_numpy(dtype='datetime64[ns]')
    test_dates=pd.to_datetime(test_dates)
    test_origin=test_dates.min()-pd.Timedelta(days=3)
    val_pool=dates[dates<test_origin.to_datetime64()]
    val_dates=val_pool[-validation_days:]
    val_origin=pd.Timestamp(val_dates[0])-pd.Timedelta(days=3)
    train=frame.target_date.lt(val_origin).to_numpy()
    val=frame.target_date.isin(val_dates).to_numpy()
    test=frame.target_date.isin(test_dates).to_numpy()
    # Refit after hyperparameters/epochs freeze; all labels strictly before test origin.
    refit=frame.target_date.lt(test_origin).to_numpy()
    if len(val_dates)!=validation_days or frame.loc[train,'target_date'].nunique()<min_train_days:
        raise ValueError('Insufficient history for this split')
    assert frame.loc[train,'target_date'].max()<frame.loc[val,'origin_date'].min()
    assert frame.loc[val,'target_date'].max()<frame.loc[test,'origin_date'].min()
    assert frame.loc[refit,'target_date'].max()<frame.loc[test,'origin_date'].min()
    return train,val,test,refit


def evaluation_plan(frame,holdout_days=7,cv_days=12,folds=3,validation_days=5,min_train_days=7):
    """Defaults are the audited_v2 design; smaller label sets (audited_v3) pass explicit sizes."""
    dates=pd.DatetimeIndex(frame.target_date.unique()).sort_values().to_numpy(dtype='datetime64[ns]')
    holdout=dates[-holdout_days:]
    available=dates[dates<(pd.Timestamp(holdout[0])-pd.Timedelta(days=3)).to_datetime64()]
    cv=np.array_split(available[-cv_days:],folds)
    split=lambda d:split_for_dates(frame,d,validation_days,min_train_days)
    return [(f'fold_{i+1}',split(d)) for i,d in enumerate(cv)]+[('retrospective',split(holdout))]


def metrics(actual,predicted):
    y=np.asarray(actual,float);p=np.asarray(predicted,float);e=p-y;den=np.abs(y).sum()
    variance=np.square(y-y.mean()).sum()
    return {'MAE':float(np.abs(e).mean()),'RMSE':float(np.sqrt(np.square(e).mean())),
        'WAPE_pct':float(np.abs(e).sum()/den*100) if den else np.nan,
        'Forecast_Bias':float(e.mean()),'R2':float(1-np.square(e).sum()/variance) if variance else np.nan}
