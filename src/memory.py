"""Stage 5 — Compound. A knowledge base that grows every run.

  * history.csv      : one row per daily run (the outlook + key signals) for self-scoring
  * failure_log.md   : when a prior 'Calm' call was followed by a real flare, log the miss
This is the 'learn from every result' loop, adapted to a monitor: we score yesterday's
best-guess against what actually happened and remember the misses."""
from __future__ import annotations
import csv, datetime as dt
import numpy as np
import pandas as pd
from .util import KNOW, log

HIST = KNOW / "history.csv"
FAIL = KNOW / "failure_log.md"


def record(outlook: dict, news: dict) -> None:
    new = not HIST.exists()
    with open(HIST, "a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["run_date", "as_of", "band", "flare_prob_pct", "regime",
                        "anom_flag", "vix", "news_narrative", "news_agreement", "confidence"])
        w.writerow([dt.date.today().isoformat(), outlook["as_of"], outlook["turbulence_band"],
                    outlook["flare_prob_pct"], outlook["regime"], outlook["anomaly"]["flagged"],
                    outlook["vix"], news.get("narrative"), outlook["news_agreement"],
                    outlook["confidence"]])


def self_score(features: pd.DataFrame) -> dict:
    """Did past calls hold up? Compare each past run's band to realized next-5d vol."""
    if not HIST.exists():
        return {"scored": 0}
    h = pd.read_csv(HIST, parse_dates=["as_of"]).dropna(subset=["as_of"])
    r = features["ret"]
    fwd = (r.rolling(5).std().shift(-5) * np.sqrt(252))
    thr = fwd.rolling(252, min_periods=120).quantile(0.75)
    hits = miss = 0; misses = []
    for _, row in h.iterrows():
        d = row["as_of"]
        if d not in fwd.index or pd.isna(fwd.loc[d]) or pd.isna(thr.loc[d]):
            continue
        realized_flare = fwd.loc[d] >= thr.loc[d]
        called_quiet = row["band"] in ("Calm", "Watch")
        if realized_flare and called_quiet:
            miss += 1
            misses.append((str(d.date()), row["band"], round(float(fwd.loc[d]) * 100, 1)))
        elif (not realized_flare) and called_quiet:
            hits += 1
    if misses:
        with open(FAIL, "a") as fh:
            fh.write(f"\n## Self-score {dt.date.today().isoformat()}\n")
            for d, band, vol in misses[-10:]:
                fh.write(f"- MISS {d}: called '{band}' but realized 5d vol hit {vol}% "
                         f"(a flare). Lesson: widen caution when news diverges.\n")
    return {"scored": hits + miss, "quiet_correct": hits, "quiet_missed_flares": miss}


if __name__ == "__main__":
    print("memory module ok; files ->", HIST, FAIL)
