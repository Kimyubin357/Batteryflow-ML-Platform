"""Deterministic ordering of demand response rows at their displayed precision."""


def demand_rank_key(row):
    # Repository rows expose gap rounded to two decimals. Equal magnitudes use
    # the canonical numeric Part ID, including ties at a requested top-N cutoff.
    return -abs(row['gap']), int(row['part'].split()[-1])
