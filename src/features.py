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
    f["rv5"]  = r.rolling(5).std()  * np.sqrt(252)     # realized vol, 1 week
    f["rv21"] = r.rolling(21).std() * np.sqrt(252)     # realized vol, 1 month
    f["vix"]  = panel.get("vix").astype(float) if "vix" in panel else np.nan
    if "VIX3M" in panel and "vix" in panel:
        f["vix_term"] = (panel["VIX3M"] - panel["vix"]).astype(float)   # term-structure slope
    else:
        f["vix_term"] = np.nan
    # stock-bond correlation (21d) — flips negative in flights to quality
    if "TLT" in panel:
        br = panel["TLT"].ffill().pct_change()
        f["stock_bond_corr"] = r.rolling(21).corr(br)
    else:
        f["stock_bond_corr"] = np.nan
    f["credit"] = panel["hy_oas"].astype(float) if "hy_oas" in panel else np.nan
    f["credit_mom"] = panel["hy_oas"].diff(21).astype(float) if "hy_oas" in panel else np.nan
    f["curve"] = (panel["y10"] - panel["y2"]).astype(float) if "y10" in panel and "y2" in panel else np.nan
    f["dollar_ret"] = panel["dollar"].ffill().pct_change(5) if "dollar" in panel else np.nan
    f["oil_ret"] = panel["oil"].ffill().pct_change(5) if "oil" in panel else np.nan
    f["vix_med"] = f["vix"].rolling(252, min_periods=120).median()
    return f


# the 8 headline "fingerprint" features used for anomaly detection
FINGERPRINT = ["rv5", "rv21", "vix", "vix_term", "stock_bond_corr",
               "credit", "curve", "dollar_ret"]

if __name__ == "__main__":
    from .data import get_panel
    f = build_features(get_panel("synthetic"))
    print(f[FINGERPRINT].tail()); print("rows:", len(f))
