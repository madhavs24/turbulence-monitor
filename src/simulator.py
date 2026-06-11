"""Stage 4 — Paper-trading simulator (DEMO money only, ever).
Each strategy decides what fraction of the demo bankroll to hold in SPY vs cash each
day, using ONLY yesterday's info (causal). Ported from the project's papertrader.py.

Strategies: buy_hold, vix_rule, regime, turbulence, coinflip.
Honest result: buy_hold wins on RETURN; signal strategies roughly HALVE drawdown (pain)."""
from __future__ import annotations
import numpy as np
import pandas as pd

STRATEGIES = ["buy_hold", "vix_rule", "regime", "turbulence", "coinflip"]


def _positions(d: pd.DataFrame, strat: str, seed: int = 42) -> pd.Series:
    if strat == "buy_hold":
        pos = pd.Series(1.0, index=d.index)
    elif strat == "vix_rule":
        pos = (d["vix"] < d["vix_med"]).astype(float)
    elif strat == "regime":
        pos = (~d["regime"].isin(["ElevatedStress"])).astype(float)
    elif strat == "turbulence":
        x = d["flare_prob"].fillna(0.2)
        pos = pd.Series(np.where(x >= 0.6, 0.0, np.where(x >= 0.45, 0.5, 1.0)), index=d.index)
    elif strat == "coinflip":
        rng = np.random.default_rng(seed)
        pos = pd.Series(rng.integers(0, 2, len(d)).astype(float), index=d.index)
    else:
        raise ValueError(strat)
    return pos.shift(1).fillna(1.0).clip(0, 1)     # act on yesterday's signal


def _stats(equity, sr, pos, start_cash):
    total = float(equity.iloc[-1] / start_cash - 1)
    yrs = (equity.index[-1] - equity.index[0]).days / 365.25
    cagr = float((equity.iloc[-1] / start_cash) ** (1 / yrs) - 1) if yrs > 0 else np.nan
    dd = float((equity / equity.cummax() - 1).min())
    sharpe = float(sr.mean() / sr.std() * np.sqrt(252)) if sr.std() > 0 else np.nan
    return {"final_value": round(float(equity.iloc[-1])),
            "total_return_pct": round(total * 100, 1),
            "annual_return_pct": round(cagr * 100, 1),
            "max_drawdown_pct": round(dd * 100, 1),
            "sharpe": round(sharpe, 2),
            "pct_time_invested": round(float(pos.mean()) * 100)}


def run(features: pd.DataFrame, signals: pd.DataFrame,
        start="2012-01-01", start_cash=10000.0):
    d = pd.DataFrame(index=features.index)
    d["ret"] = features["ret"]
    d["vix"] = features["vix"]; d["vix_med"] = features["vix_med"]
    d["regime"] = signals["regime"]; d["flare_prob"] = signals["flare_prob"]
    if d["vix"].isna().all():
        d["vix"] = 20.0; d["vix_med"] = 20.0
    d = d.dropna(subset=["ret", "vix"])
    d = d[d.index >= pd.Timestamp(start)]
    stats, curves = {}, {}
    if len(d) == 0:
        for s in STRATEGIES:
            stats[s] = {"final_value": start_cash, "total_return_pct": 0.0, "annual_return_pct": 0.0, "max_drawdown_pct": 0.0, "sharpe": 0.0, "pct_time_invested": 0}
            curves[s] = pd.Series(start_cash, index=[pd.Timestamp(start)])
        return stats, pd.DataFrame(curves)
    for s in STRATEGIES:
        pos = _positions(d, s)
        sr = pos * d["ret"]
        eq = start_cash * (1 + sr).cumprod()
        stats[s] = _stats(eq, sr, pos, start_cash)
        curves[s] = eq
    return stats, pd.DataFrame(curves)


if __name__ == "__main__":
    from .data import get_panel
    from .features import build_features
    from .signals import compute_all
    from .util import load_config
    cfg = load_config()
    f = build_features(get_panel("synthetic"))
    sig = compute_all(f, cfg)
    stats, curves = run(f, sig)
    print(pd.DataFrame(stats).T.to_string())
