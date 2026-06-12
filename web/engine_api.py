"""Rich snapshot builder for the website. Runs the engine once and assembles
everything the 7 site sections need. Pure functions on top of src/*; no Flask/FastAPI here."""
from __future__ import annotations
import numpy as np
import pandas as pd
from src.util import load_config
from src.data import get_panel, LAST_SOURCE
from src.features import build_features, FINGERPRINT
from src.signals import compute_all
from src.news import fetch_news, synthetic_news, news_summary
from src.outlook import build_outlook
from src.simulator import run as run_sim, STRATEGIES

PRETTY = {"rv5": "Realized vol (1w)", "rv21": "Realized vol (1m)", "vix": "VIX (fear gauge)",
          "vix_term": "VIX term structure", "stock_bond_corr": "Stock–bond correlation",
          "credit": "Credit spread", "curve": "Yield-curve slope", "dollar_ret": "Dollar (5d)"}

VITALS = [
    ("SPY", "S&P 500 (SPY)", "price"),
    ("vix", "VIX — fear gauge", "level"),
    ("GLD", "Gold (GLD)", "price"),
    ("oil", "Oil (WTI)", "price"),
    ("curve", "Yield curve 10y–2y", "level"),
    ("credit", "Credit spread (HY OAS)", "level"),
]


def _series(s: pd.Series, n: int = 180):
    s = s.dropna().tail(n)
    return [{"d": d.strftime("%Y-%m-%d"), "v": round(float(v), 3)} for d, v in s.items()]


def _vitals(panel, feats):
    src = {}
    src["SPY"] = panel["SPY"].ffill()
    src["vix"] = feats["vix"]
    src["GLD"] = panel["GLD"].ffill() if "GLD" in panel else pd.Series(dtype=float)
    src["oil"] = panel["oil"].ffill() if "oil" in panel else pd.Series(dtype=float)
    src["curve"] = feats["curve"]
    src["credit"] = feats["credit"]
    out = []
    for key, label, kind in VITALS:
        s = src.get(key, pd.Series(dtype=float)).dropna()
        if len(s) < 2:
            continue
        last = float(s.iloc[-1]); prev = float(s.iloc[-2])
        chg = last - prev
        pct = (chg / prev * 100) if (kind == "price" and prev) else None
        out.append({"key": key, "label": label, "kind": kind,
                    "last": round(last, 2), "change": round(chg, 3),
                    "pct": round(pct, 2) if pct is not None else None,
                    "spark": _series(s, 120)})
    return out


def _why(feats):
    """Which fingerprint features are most unusual today (causal z-scores)."""
    X = feats[FINGERPRINT]
    mu = X.rolling(252, min_periods=120).mean()
    sd = X.rolling(252, min_periods=120).std()
    z = ((X - mu) / sd).iloc[-1]
    rows = []
    for k in FINGERPRINT:
        if pd.isna(z[k]):
            continue
        rows.append({"feature": PRETTY.get(k, k), "z": round(float(z[k]), 2),
                     "dir": "high" if z[k] > 0 else "low",
                     "value": round(float(feats[k].iloc[-1]), 3)})
    rows.sort(key=lambda r: abs(r["z"]), reverse=True)
    return rows[:5]


def _anomaly_timeline(feats, signals):
    """Every flagged anomaly + aftermath (what the next 21d looked like)."""
    r = feats["ret"]
    fwd_vol = (r.rolling(21).std().shift(-21) * np.sqrt(252))
    flagged = signals[signals["anom_flag"] == True]
    items = []
    X = feats[FINGERPRINT]
    mu = X.rolling(252, min_periods=120).mean(); sd = X.rolling(252, min_periods=120).std()
    Z = (X - mu) / sd
    for d in flagged.index:
        z = Z.loc[d].dropna()
        top = z.reindex(z.abs().sort_values(ascending=False).index)[:3]
        aft = float(fwd_vol.loc[d]) if d in fwd_vol.index and pd.notna(fwd_vol.loc[d]) else None
        items.append({"date": d.strftime("%Y-%m-%d"),
                      "p": round(float(signals.loc[d, "anom_p"]), 4),
                      "vix": round(float(feats.loc[d, "vix"]), 1) if pd.notna(feats.loc[d, "vix"]) else None,
                      "regime": str(signals.loc[d, "regime"]),
                      "drivers": [{"f": PRETTY.get(k, k), "z": round(float(v), 2)} for k, v in top.items()],
                      "next21d_vol_pct": round(aft * 100, 1) if aft is not None else None})
    return items[-120:]   # cap payload


def _calibration(feats, signals):
    """Honest report card computed over ALL history: when the model said X% flare, how
    often was it actually choppy? Plus the headline AUC vs a VIX-only rule."""
    p = signals["flare_prob"]
    y = signals["flare_ev"]
    d = pd.concat([p, y], axis=1).dropna()
    d.columns = ["p", "y"]
    if len(d) < 50:
        return {"n": 0, "bins": [], "auc": None, "vix_auc": None, "brier": None}
    bins = []
    for lo in np.arange(0, 1.0, 0.2):
        hi = lo + 0.2
        m = d[(d["p"] >= lo) & (d["p"] < hi)]
        if len(m) == 0:
            continue
        bins.append({"bucket": f"{int(lo*100)}-{int(hi*100)}%",
                     "predicted": round(float(m["p"].mean()) * 100, 1),
                     "actual": round(float(m["y"].mean()) * 100, 1),
                     "n": int(len(m))})
    brier = float(((d["p"] - d["y"]) ** 2).mean())
    def auc(score, label):
        s = pd.concat([score, label], axis=1).dropna(); s.columns = ["s", "y"]
        pos = s[s["y"] == 1]["s"].values; neg = s[s["y"] == 0]["s"].values
        if len(pos) == 0 or len(neg) == 0: return None
        # Mann-Whitney U / (n*m)
        allv = np.concatenate([pos, neg]); order = allv.argsort()
        ranks = np.empty_like(order, dtype=float); ranks[order] = np.arange(1, len(allv) + 1)
        rpos = ranks[:len(pos)].sum()
        return round(float((rpos - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))), 3)
    model_auc = auc(d["p"], d["y"])
    vix_auc = auc(feats["vix"].reindex(d.index), d["y"])
    return {"n": int(len(d)), "bins": bins, "auc": model_auc,
            "vix_auc": vix_auc, "brier": round(brier, 3)}


def build_snapshot(mode: str = "auto"):
    cfg = load_config()
    panel = get_panel("synthetic" if mode == "synthetic" else "live")
    feats = build_features(panel)
    signals = compute_all(feats, cfg)
    # news
    if mode == "synthetic":
        news_df = synthetic_news("cautious")
    else:
        news_df = fetch_news(cfg)
        if len(news_df) == 0:
            news_df = synthetic_news("cautious")
    news = news_summary(news_df)
    outlook = build_outlook(feats, signals, news)
    sim_stats, curves = run_sim(feats, signals, start=cfg["project"]["sim_start"],
                                start_cash=cfg["project"]["start_cash"])
    why = _why(feats)
    news_items = [] if news_df is None else [
        {"time": (r["time"].strftime("%Y-%m-%d %H:%M") if pd.notna(r["time"]) else ""),
         "title": r["title"], "url": (r.get("url") or ""), "source": r["source"],
         "score": int(r["score"]), "flagged": bool(r["flagged"])}
        for _, r in news_df.sort_values("score").iterrows()]
    news_context = {
        "anomaly_active": bool(signals.iloc[-1]["anom_flag"]),
        "band": outlook["turbulence_band"],
        "flare_prob_pct": outlook["flare_prob_pct"],
        "top_driver": (why[0]["feature"] if why else None),
        "agreement": outlook["news_agreement"],
    }
    # Layered anomaly: spike (instant) + conformal (rigorous) + news (event). Always shows
    # SOMETHING correct on load, even before the heavy models finish.
    last = signals.iloc[-1]
    def _f(x):
        return None if pd.isna(x) else round(float(x), 2)
    anomaly_layers = {
        "spike": {"active": bool(last.get("spike_flag", False)),
                  "ret_z": _f(last.get("spike_ret_z")), "vix_z": _f(last.get("spike_vix_z"))},
        "conformal": {"active": bool(last["anom_flag"]),
                      "p": (None if pd.isna(last["anom_p"]) else round(float(last["anom_p"]), 4))},
        "news": {"active": bool(news_context["anomaly_active"]) or news.get("narrative") == "stressed"},
    }
    anomaly_layers["any_active"] = any(v["active"] for v in
        (anomaly_layers["spike"], anomaly_layers["conformal"], anomaly_layers["news"]))
    snap = {
        "as_of": outlook["as_of"],
        "outlook": outlook,
        "vitals": _vitals(panel, feats),
        "news": {"summary": news, "items": news_items,
                 "anomaly_active": news_context["anomaly_active"],
                 "band": outlook["turbulence_band"], "context": news_context},
        "why": why,
        "anomalies": _anomaly_timeline(feats, signals),
        "anomaly_layers": anomaly_layers,
        "calibration": _calibration(feats, signals),
        "sim_default": sim_stats,
        "mode": mode,
        "data_source": LAST_SOURCE["value"] or mode,
    }
    return snap, feats, signals


if __name__ == "__main__":
    import json, sys
    m = sys.argv[1] if len(sys.argv) > 1 else "synthetic"
    snap, f, s = build_snapshot(m)
    for k in ["as_of", "vitals", "why", "calibration"]:
        print(f"--- {k} ---")
        print(json.dumps(snap[k], indent=2)[:600])
    print("anomalies:", len(snap["anomalies"]), "news items:", len(snap["news"]["items"]))
