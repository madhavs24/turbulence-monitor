"""Historical analog engine — 'today most resembles ___, and here's what happened next.'

Given today's 8-feature market fingerprint, find the most similar PAST days (causal: never
compares to the future) and report the DISTRIBUTION of what the S&P did over the following
month. Honest uncertainty: a fan of outcomes, not a point guess. Inspired by Verdad Capital's
'analogous market moments' (macro similarity via credit, vol, stock-bond corr, yield curve).
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from .features import FINGERPRINT

# require some separation so "analogs" aren't just yesterday/last week
MIN_GAP_DAYS = 21


def analogs(features: pd.DataFrame, k: int = 20):
    F = features[FINGERPRINT].copy()
    valid = F.dropna()
    if len(valid) < 400:
        return {"n": 0, "analogs": [], "outcome": None}
    today_idx = valid.index[-1]
    today = valid.iloc[-1]
    past = valid.iloc[:-1]
    # standardize by the PAST distribution (so 'unusual' is measured fairly)
    mu, sd = past.mean(), past.std().replace(0, np.nan)
    zt = (today - mu) / sd
    zp = (past - mu) / sd
    dist = np.sqrt(((zp - zt) ** 2).mean(axis=1))          # mean sq z-distance
    dist = dist.dropna().sort_values()
    # forward outcome series (next 21 trading days) from SPY returns
    r = features["ret"]
    spy = (1 + r.fillna(0)).cumprod()
    fwd_ret = (spy.shift(-21) / spy - 1) * 100             # +21d return %
    fwd_vol = (r.rolling(21).std().shift(-21) * np.sqrt(252)) * 100  # realized vol %
    rows, outs, picked = [], [], []
    for d in dist.index:
        if d >= today_idx:
            continue
        if any(abs((d - p).days) < MIN_GAP_DAYS for p in picked):
            continue                                       # de-cluster nearby days
        picked.append(d)
        sim = round(float(100 * np.exp(-dist[d])), 1)      # 0..100 similarity
        fr = float(fwd_ret.loc[d]) if d in fwd_ret.index and pd.notna(fwd_ret.loc[d]) else None
        fv = float(fwd_vol.loc[d]) if d in fwd_vol.index and pd.notna(fwd_vol.loc[d]) else None
        rows.append({"date": d.strftime("%Y-%m-%d"), "similarity": sim,
                     "fwd_ret_pct": round(fr, 1) if fr is not None else None,
                     "fwd_vol_pct": round(fv, 1) if fv is not None else None})
        if fr is not None:
            outs.append(fr)
        if len(rows) >= k:
            break
    outcome = None
    if len(outs) >= 5:
        a = np.array(outs)
        outcome = {"n": int(len(a)),
                   "median": round(float(np.median(a)), 1),
                   "p25": round(float(np.percentile(a, 25)), 1),
                   "p75": round(float(np.percentile(a, 75)), 1),
                   "worst": round(float(a.min()), 1),
                   "best": round(float(a.max()), 1),
                   "share_down": round(float((a < 0).mean()), 2),
                   "share_down5": round(float((a < -5).mean()), 2)}
    return {"n": len(rows), "as_of": today_idx.strftime("%Y-%m-%d"),
            "analogs": rows[:6], "outcome": outcome}


if __name__ == "__main__":
    from .data import get_panel
    from .features import build_features
    import json
    f = build_features(get_panel("synthetic"))
    print(json.dumps(analogs(f), indent=2)[:900])
