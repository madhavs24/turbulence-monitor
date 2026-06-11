"""Stage 3 — Signals (the brains). Each is small, causal, independently sensible.

  3a regime          : Calm / Flight-to-Quality / Elevated-Stress  (rule-based, leakage-free)
  3b anomaly alarm   : conformal p-value with a CALIBRATED false-alarm rate
  3c turbulence (HAR): forecast next-month realized vol from recent daily/weekly/monthly vol
  3d flare-up prob   : walk-forward logistic 'best guess' that next week is unusually choppy
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression, LinearRegression
from sklearn.preprocessing import StandardScaler
from .features import FINGERPRINT

FEATS = ["rv5", "rv21", "vix", "vix_term", "credit_mom"]


# ---- 3a regime -------------------------------------------------------------
def regime(f: pd.DataFrame) -> pd.Series:
    """Leakage-free market 'mood'. Thresholds are on causal rolling medians."""
    vix = f["vix"]; vixmed = f["vix_med"]
    corr = f["stock_bond_corr"]
    out = pd.Series("Calm", index=f.index)
    elevated = vix > (vixmed * 1.5)
    ftq = (~elevated) & (corr < -0.2) & (vix > vixmed)
    out[ftq] = "FlightToQuality"
    out[elevated] = "ElevatedStress"
    return out


# ---- 3b conformal anomaly --------------------------------------------------
def anomaly(f: pd.DataFrame, alpha: float = 0.01) -> pd.DataFrame:
    """Flag days whose fingerprint is unusually far from recent history.
    Score = scaled L2 distance from a trailing-window mean. p-value is conformal:
        p_t = (1 + #{past scores >= today}) / (cal_size + 1);  flag if p_t <= alpha.
    """
    X = f[FINGERPRINT].copy()
    X = (X - X.rolling(252, min_periods=120).mean()) / X.rolling(252, min_periods=120).std()
    score = np.sqrt((X ** 2).mean(axis=1))   # RMS standardized distance
    score = score.replace([np.inf, -np.inf], np.nan)
    p = pd.Series(np.nan, index=f.index)
    s = score.dropna()
    vals = s.values
    for i in range(252, len(s)):
        cal = vals[max(0, i-504):i]          # ~2y trailing calibration window
        ge = np.sum(cal >= vals[i])
        p.loc[s.index[i]] = (1 + ge) / (len(cal) + 1)
    out = pd.DataFrame({"anom_score": score, "anom_p": p})
    out["anom_flag"] = (out["anom_p"] <= alpha)
    return out


# ---- 3b' fast spike detector (always-available, reactive) --------------------
def spike_anomaly(f: pd.DataFrame, window: int = 63, z: float = 3.0) -> pd.DataFrame:
    """Instant, cheap anomaly layer: flag a day whose S&P return or VIX jump is an
    N-sigma outlier vs its trailing ~3-month window. Reactive (confirms a spike as it
    happens), needs no training, and is always available the moment data loads."""
    r = f["ret"]
    rz = (r - r.rolling(window).mean()) / r.rolling(window).std()
    vchg = f["vix"].diff()
    vz = (vchg - vchg.rolling(window).mean()) / vchg.rolling(window).std()
    flag = (rz.abs() >= z) | (vz >= z)        # big move (either way) OR a VIX jump up
    return pd.DataFrame({"spike_ret_z": rz.round(2), "spike_vix_z": vz.round(2),
                         "spike_flag": flag.fillna(False)})


# ---- 3c HAR turbulence forecast --------------------------------------------
def har_turbulence(f: pd.DataFrame) -> pd.Series:
    """Predict next-21d realized vol from daily(1)/weekly(5)/monthly(22) vol + VIX.
    Walk-forward (expanding window) so it never peeks ahead. Returns predicted annualized vol."""
    r = f["ret"]
    rv1 = r.abs() * np.sqrt(252)            # daily component (rolling(1).std is NaN)
    rv5 = r.rolling(5).std() * np.sqrt(252)
    rv22 = r.rolling(22).std() * np.sqrt(252)
    fwd = (r.rolling(21).std() * np.sqrt(252)).shift(-21)   # target: next-month vol
    X = pd.concat([rv1, rv5, rv22, f["vix"]], axis=1)
    X.columns = ["rv1", "rv5", "rv22", "vix"]
    d = pd.concat([X, fwd.rename("y")], axis=1).dropna()
    pred = pd.Series(np.nan, index=f.index)
    if len(d) < 400:
        return pred
    cols = ["rv1","rv5","rv22","vix"]
    step = 21
    for i in range(380, len(d), step):
        tr = d.iloc[:i]; te = d.iloc[i:i+step]
        m = LinearRegression().fit(tr[cols], tr["y"])
        pred.loc[te.index] = m.predict(te[cols])
    # live tail: rows with features but no known target yet -> use full-history model
    m = LinearRegression().fit(d[cols], d["y"])
    live = X.dropna()
    live = live[live.index > d.index[-1]] if len(d) else live
    if len(live):
        pred.loc[live.index] = m.predict(live[cols])
    return pred


# ---- 3d flare-up 'best guess' probability ----------------------------------
def flare_prob(f: pd.DataFrame, horizon: int = 5, q: float = 0.75) -> pd.DataFrame:
    """Walk-forward logistic estimate that the next `horizon` days are unusually choppy.
    This is the project's one real edge (beats a VIX-only rule in backtests)."""
    r = f["ret"]
    fwd = r.rolling(horizon).std().shift(-horizon) * np.sqrt(252)
    thr = fwd.rolling(252, min_periods=120).quantile(q).shift(1)
    y = (fwd >= thr).astype(float).where(fwd.notna() & thr.notna())
    df = f.copy(); df["flare_ev"] = y
    out = pd.Series(np.nan, index=f.index)
    feats_use = [c for c in FEATS if c in df.columns and df[c].notna().any()]
    if len(feats_use) < 2:
        return pd.DataFrame({"flare_prob": out, "flare_ev": y})
    dd = df.dropna(subset=feats_use + ["flare_ev"])
    last_model = None
    yrs = sorted(set(dd.index.year))
    for yr in yrs:
        tr = dd[dd.index <= pd.Timestamp(f"{yr-1}-12-31")].iloc[:-25]
        te = dd[(dd.index >= pd.Timestamp(f"{yr}-01-01")) & (dd.index <= pd.Timestamp(f"{yr}-12-31"))]
        if len(tr) < 300 or tr["flare_ev"].nunique() < 2:
            continue
        sc = StandardScaler().fit(tr[feats_use].values)
        m = LogisticRegression(max_iter=500).fit(   # calibrated (no class_weight -> probs match base rate)
            sc.transform(tr[feats_use].values), tr["flare_ev"].values)
        last_model = (sc, m)
        if len(te):
            out.loc[te.index] = m.predict_proba(sc.transform(te[feats_use].values))[:, 1]
    # live tail: recent days whose target isn't known yet still get a best-guess
    if last_model is not None:
        sc, m = last_model
        tail = df.dropna(subset=feats_use)
        tail = tail[out.reindex(tail.index).isna()]
        if len(tail):
            out.loc[tail.index] = m.predict_proba(sc.transform(tail[feats_use].values))[:, 1]
    return pd.DataFrame({"flare_prob": out, "flare_ev": y})


def compute_all(f: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    s = cfg["signals"]
    out = pd.DataFrame(index=f.index)
    out["regime"] = regime(f)
    out = out.join(anomaly(f, s["anomaly_alpha"]))
    out["har_vol"] = har_turbulence(f)
    out = out.join(spike_anomaly(f))
    out = out.join(flare_prob(f, s["flare_horizon_days"], s["flare_quantile"]))
    return out


if __name__ == "__main__":
    from .data import get_panel
    from .features import build_features
    from .util import load_config
    f = build_features(get_panel("synthetic"))
    sig = compute_all(f, load_config())
    print(sig.tail(10)[["regime","anom_p","anom_flag","har_vol","flare_prob"]])
    print("anomaly days flagged:", int(sig["anom_flag"].sum()))
