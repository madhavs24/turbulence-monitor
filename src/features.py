"""Stage 2 — Features. Turn raw prices into 8 daily 'fingerprint' numbers.
All causal: every value uses only data up to and including that day."""
from __future__ import annotations
import numpy as np
import pandas as pd


def build_features(panel: pd.DataFrame) -> pd.DataFrame:
    spy = panel["SPY"].ffill()
    r = spy.pct_change()
    f = pd.DataFrame(index=panel.index)
    f["ret"] = r
    f["rv5"]  = r.rolling(5).std()  * np.sqrt(252)
    f["rv21"] = r.rolling(21).std() * np.sqrt(252)
    f["vix"]  = panel["vix"].astype(float) if "vix" in panel else np.nan
    if "VIX3M" in panel and "vix" in panel:
        f["vix_term"] = (panel["VIX3M"] - panel["vix"]).astype(float)
    else:
        f["vix_term"] = np.nan
    if "TLT" in panel:
        br = panel["TLT"].ffill().pct_change()
        f["stock_bond_corr"] = r.rolling(21).corr(br)
    else:
        f["stock_bond_corr"] = np.nan
    if "hy_oas" in panel:
        f["credit"] = panel["hy_oas"].astype(float)
        f["credit_mom"] = panel["hy_oas"].diff(21).astype(float)
    elif "HYG" in panel:
        # FRED credit spread unavailable -> derive from HYG (high-yield bond ETF falls when
        # credit spreads widen). A defensible Yahoo-only proxy, labeled as such.
        hyg = panel["HYG"].ffill()
        f["credit"] = (hyg.rolling(252, min_periods=60).mean() / hyg)
        f["credit_mom"] = -(hyg.pct_change(21)) * 100
    else:
        f["credit"] = np.nan
        f["credit_mom"] = np.nan
    if "y10" in panel and "y2" in panel:
        f["curve"] = (panel["y10"] - panel["y2"]).astype(float)
    elif "y10" in panel:
        # Yahoo-only: no 2y yield — proxy curve from 10y momentum (still causal)
        f["curve"] = panel["y10"].astype(float).diff(21)
    else:
        f["curve"] = np.nan
    if "dollar" in panel:
        f["dollar_ret"] = panel["dollar"].ffill().pct_change(5)
    elif "USO" in panel:
        # No dollar index — crude as a rough risk-proxy placeholder (0-centered)
        f["dollar_ret"] = panel["USO"].ffill().pct_change(5)
    else:
        f["dollar_ret"] = 0.0
    f["oil_ret"] = panel["oil"].ffill().pct_change(5) if "oil" in panel else np.nan
    f["vix_med"] = f["vix"].rolling(252, min_periods=120).median()
    return f


FINGERPRINT = ["rv5", "rv21", "vix", "vix_term", "stock_bond_corr",
               "credit", "curve", "dollar_ret"]
