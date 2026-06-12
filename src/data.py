"""Stage 1 — Data. Pull the market's daily vital signs from FREE sources and align
them into one table (data/processed/panel.parquet).

Sources (all public, no paid feeds):
  - FRED   : VIX, Treasury yields, credit spread, stress index, dollar, oil (CSV endpoint, no key)
  - Yahoo  : SPY/QQQ/TLT/HYG/LQD/GLD/USO (chart API with a browser User-Agent — NOT yfinance)

Design notes:
  * Incremental: if panel.parquet exists we only refresh; otherwise full history since 2000.
  * Robust: any single series failing does not kill the run (logged, forward-filled).
  * Offline/demo: build_synthetic() fabricates a plausible panel so the whole pipeline
    is runnable and testable without network access.
"""
from __future__ import annotations
import io, os, time
import numpy as np
import pandas as pd
import requests
from .util import PROC, load_config, log

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
PANEL = PROC / "panel.parquet"
LAST_SOURCE = {"value": None}


import concurrent.futures as _cf

FETCH_TIMEOUT = int(os.environ.get("DATA_FETCH_TIMEOUT", "10"))


def _fred(series_id: str, timeout: int = FETCH_TIMEOUT) -> pd.Series:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = ["date", "value"]
    df["date"] = pd.to_datetime(df["date"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df.set_index("date")["value"].dropna()


def _yahoo(ticker: str, timeout: int = FETCH_TIMEOUT) -> pd.Series:
    sym = ticker.replace("^", "%5E")
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
           f"?range=max&interval=1d")
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    js = r.json()["chart"]["result"][0]
    ts = pd.to_datetime(js["timestamp"], unit="s").normalize()
    close = js["indicators"]["quote"][0]["close"]
    return pd.Series(close, index=ts, name=ticker).dropna()


def _fetch_one(kind: str, key: str) -> pd.Series:
    """One series with a single retry. Short timeout so a dead source can't block the boot."""
    fn = _fred if kind == "fred" else _yahoo
    try:
        return fn(key)
    except Exception:
        return fn(key)   # one retry; raises if it fails again


def build_live(max_workers: int = 12) -> pd.DataFrame:
    """Fetch every series CONCURRENTLY with short timeouts. Worst case ~2x the per-request
    timeout, not (series x timeout x retries). A failed source is logged and skipped, not waited on."""
    cfg = load_config()["data"]
    jobs = {name: ("fred", sid) for sid, name in cfg["fred"].items()}
    jobs.update({tk: ("yahoo", tk) for tk in cfg["yahoo"]})
    cols = {}
    with _cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(_fetch_one, kind, key): name for name, (kind, key) in jobs.items()}
        try:
            for fut in _cf.as_completed(futs, timeout=FETCH_TIMEOUT * 4):
                name = futs[fut]
                try:
                    cols[name] = fut.result(); log(f"{name} ok ({len(cols[name])})")
                except Exception as e:
                    log(f"{name} FAILED: {e}")
        except _cf.TimeoutError:
            log("some sources exceeded the global fetch window — proceeding with what arrived")
    # resilience: if FRED VIX is missing, fall back to Yahoo ^VIX so a FRED outage isn't fatal
    if "vix" not in cols or cols["vix"].dropna().empty:
        try:
            cols["vix"] = _yahoo("^VIX"); log("vix via Yahoo ^VIX fallback")
        except Exception as e:
            log(f"VIX fallback failed: {e}")
    if not cols:
        raise RuntimeError("All data sources failed — check network/firewall.")
    panel = pd.DataFrame(cols).sort_index()
    panel = panel[panel.index >= "2000-01-01"]
    panel = panel.ffill(limit=5)
    return panel


def build_synthetic(n_days: int = 3600, seed: int = 7) -> pd.DataFrame:
    """A plausible fake panel for offline testing: volatility clusters, occasional
    crises, correlated stock/credit moves. NOT real data — for pipeline validation only."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n_days)
    # latent volatility regime (GARCH-ish clustering)
    vol = np.zeros(n_days); vol[0] = 0.01
    shock = rng.normal(0, 1, n_days)
    for t in range(1, n_days):
        vol[t] = np.sqrt(1e-6 + 0.92 * vol[t-1]**2 + 0.07 * (vol[t-1]*shock[t-1])**2)
    # inject a few crises
    for c in rng.choice(range(252, n_days-60), size=4, replace=False):
        vol[c:c+40] *= rng.uniform(3, 6)
    ret = rng.normal(0.0003, 1, n_days) * vol
    spy = 100 * np.cumprod(1 + ret)
    vix = np.clip(vol * np.sqrt(252) * 100 * rng.uniform(0.8, 1.2, n_days), 9, 85)
    df = pd.DataFrame(index=idx)
    df["SPY"] = spy
    df["QQQ"] = 100 * np.cumprod(1 + ret * 1.15 + rng.normal(0, 0.002, n_days))
    df["vix"] = vix
    df["VIX3M"] = vix * rng.uniform(0.9, 1.05, n_days) + 2
    df["VIX9D"] = vix * rng.uniform(0.95, 1.1, n_days)
    df["hy_oas"] = np.clip(3 + (vix - 18) * 0.12 + rng.normal(0, 0.2, n_days), 2.5, 12)
    df["y10"] = np.clip(2.5 + np.cumsum(rng.normal(0, 0.01, n_days)), 0.5, 6)
    df["y2"] = np.clip(df["y10"] - rng.uniform(-0.5, 1.0, n_days), 0.1, 6)
    df["dollar"] = 100 + np.cumsum(rng.normal(0, 0.05, n_days))
    df["oil"] = np.clip(70 + np.cumsum(rng.normal(0, 0.4, n_days)), 20, 140)
    df["GLD"] = 150 + np.cumsum(rng.normal(0.005, 0.5, n_days))
    df["TLT"] = 120 - (df["y10"] - 2.5) * 15 + rng.normal(0, 0.5, n_days)
    df["HYG"] = 90 - (df["hy_oas"] - 3) * 2 + rng.normal(0, 0.2, n_days)
    df["LQD"] = 115 - (df["y10"] - 2.5) * 8 + rng.normal(0, 0.3, n_days)
    df["USO"] = df["oil"] * 0.9
    df["stlfsi"] = (vix - 18) * 0.08 + rng.normal(0, 0.1, n_days)
    return df


def get_panel(mode: str = "auto") -> pd.DataFrame:
    """mode: 'live' (force fetch), 'synthetic' (force fake), 'auto' (live, fall back to synthetic)."""
    if mode == "synthetic":
        LAST_SOURCE["value"]="synthetic"
        panel = build_synthetic(); panel.to_parquet(PANEL); return panel
    try:
        panel = build_live()
        LAST_SOURCE["value"]="live"
        panel.to_parquet(PANEL)
        log(f"panel saved: {panel.shape[0]} rows x {panel.shape[1]} cols -> {PANEL}")
        return panel
    except Exception as e:
        if mode == "live":
            raise
        log(f"LIVE fetch failed ({e}); using synthetic panel for this run.")
        LAST_SOURCE["value"]="synthetic"
        panel = build_synthetic(); panel.to_parquet(PANEL); return panel


if __name__ == "__main__":
    import sys
    m = sys.argv[1] if len(sys.argv) > 1 else "auto"
    p = get_panel(m); print(p.tail()); print(p.columns.tolist())
