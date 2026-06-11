"""Best-guess outlook — the 'predict' step, done honestly.

We do NOT predict price direction (up/down) — the blueprint proved that's a coin flip.
Instead we output a calibrated best-guess of the SITUATION for the coming week:
  * turbulence band  : Calm / Watch / Elevated / High   (from the flare-up probability)
  * anomaly status   : whether today's fingerprint is unusual
  * regime           : the market's current mood
  * confidence       : how strong/agreeing the signals are
  * news agreement    : does the live news narrative confirm or diverge from the signals

The flare-up probability is the headline number: 'best guess that the next ~5 trading
days are unusually choppy is X%'. It beats a VIX-only rule in backtests (AUC ~0.76 vs ~0.71).
"""
from __future__ import annotations
import pandas as pd


def _band(p: float) -> str:
    if p is None or pd.isna(p): return "Unknown"
    return ("High" if p >= 0.6 else "Elevated" if p >= 0.45 else
            "Watch" if p >= 0.3 else "Calm")


def build_outlook(features: pd.DataFrame, signals: pd.DataFrame, news: dict) -> dict:
    last = signals.index[-1]
    f = features.loc[last]
    s = signals.loc[last]
    p = float(s.get("flare_prob")) if pd.notna(s.get("flare_prob")) else None
    band = _band(p)
    vix = float(f.get("vix")) if pd.notna(f.get("vix")) else None
    vixmed = float(f.get("vix_med")) if pd.notna(f.get("vix_med")) else None
    har = float(s.get("har_vol")) if pd.notna(s.get("har_vol")) else None
    regime = str(s.get("regime"))
    anom_flag = bool(s.get("anom_flag"))
    anom_p = float(s.get("anom_p")) if pd.notna(s.get("anom_p")) else None

    # signal-vs-news agreement: do they tell the same story?
    sig_stress = (band in ("Elevated", "High")) or anom_flag or regime == "ElevatedStress"
    news_stress = news.get("narrative") in ("stressed", "cautious")
    if news.get("n", 0) == 0:
        agreement = "no_news"
    elif sig_stress == news_stress:
        agreement = "confirms"
    else:
        agreement = "diverges"

    # confidence: agreement across independent signals (0..1)
    votes = []
    if p is not None: votes.append(1 if p >= 0.45 else 0)
    if vix is not None and vixmed is not None: votes.append(1 if vix > vixmed * 1.2 else 0)
    votes.append(1 if anom_flag else 0)
    votes.append(1 if regime == "ElevatedStress" else 0)
    agree = max(sum(votes), len(votes) - sum(votes))
    confidence = round(agree / len(votes), 2) if votes else 0.0

    copy = {
        "Calm":     ("Conditions look calm.", "Best guess: a normal, low-turbulence week ahead."),
        "Watch":    ("Slightly unsettled.", "Best guess: keep an eye out — mild chop is possible."),
        "Elevated": ("Turbulence risk is elevated.", "Best guess: a bumpier-than-usual week ahead."),
        "High":     ("High turbulence risk.", "Best guess: expect outsized swings this week."),
        "Unknown":  ("Not enough data for a confident read.", "Waiting on more market history."),
    }[band]
    title, subtitle = copy
    headline = f"{title} {subtitle}"

    return {
        "as_of": str(last.date()),
        "headline": headline,
        "title": title,
        "subtitle": subtitle,
        "turbulence_band": band,
        "flare_prob_pct": round(p * 100, 1) if p is not None else None,
        "expected_vol_annual_pct": round(har * 100, 1) if har is not None else None,
        "regime": regime,
        "anomaly": {"flagged": anom_flag,
                    "p_value": round(anom_p, 4) if anom_p is not None else None},
        "vix": round(vix, 1) if vix is not None else None,
        "vix_vs_1y_median": (round(vix - vixmed, 1) if (vix and vixmed) else None),
        "confidence": confidence,
        "news_agreement": agreement,
        "note": "Situational best-guess (turbulence/stress), NOT a direction call. Demo only.",
    }


if __name__ == "__main__":
    from .data import get_panel
    from .features import build_features
    from .signals import compute_all
    from .news import synthetic_news, news_summary
    from .util import load_config
    cfg = load_config()
    f = build_features(get_panel("synthetic"))
    sig = compute_all(f, cfg)
    nws = news_summary(synthetic_news("cautious"))
    import json; print(json.dumps(build_outlook(f, sig, nws), indent=2))
