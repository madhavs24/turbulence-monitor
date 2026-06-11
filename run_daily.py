"""run_daily.py — the autonomous job. Run this on a schedule and it does everything
by itself: refresh free data -> features -> signals -> live news -> best-guess outlook
-> demo paper-trade -> self-score past calls -> write the morning digest.

Usage:
    python run_daily.py            # live data + live news (use on your own machine)
    python run_daily.py --demo     # synthetic data + synthetic news (offline test)
    python run_daily.py --no-news  # skip news

A kill switch: create a file named STOP next to this script to make the job exit
immediately without doing anything (mirrors the doc's kill-switch idea)."""
from __future__ import annotations
import sys, json, datetime as dt
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from src.util import load_config, log, RESULTS
from src.data import get_panel
from src.features import build_features
from src.signals import compute_all
from src.news import fetch_news, synthetic_news, news_summary
from src.outlook import build_outlook
from src.simulator import run as run_sim
from src.memory import record, self_score
from src.digest import write_digest


def main(argv):
    if (ROOT / "STOP").exists():
        log("STOP file present — kill switch active, exiting."); return
    demo = "--demo" in argv
    no_news = "--no-news" in argv
    cfg = load_config()
    t0 = dt.datetime.now()
    log(f"=== Turbulence Monitor run {dt.date.today()} (demo={demo}) ===")

    panel = get_panel("synthetic" if demo else "auto")
    feats = build_features(panel)
    log(f"features: {feats.shape[0]} days")
    signals = compute_all(feats, cfg)
    log(f"signals computed; anomalies flagged all-time: {int(signals['anom_flag'].sum())}")

    if no_news or not cfg["news"].get("enabled", True):
        news_df = synthetic_news("calm") if demo else fetch_news(cfg).iloc[:0]
    else:
        news_df = fetch_news(cfg)
        if len(news_df) == 0:
            log("no live news (offline?) — using synthetic placeholder")
            news_df = synthetic_news("cautious")
    news = news_summary(news_df)
    log(f"news: {news['n']} items, narrative={news['narrative']}")

    outlook = build_outlook(feats, signals, news)
    log(f"OUTLOOK: {outlook['turbulence_band']} band, "
        f"flare {outlook['flare_prob_pct']}%, agreement={outlook['news_agreement']}")

    sim_stats, curves = run_sim(feats, signals,
                                start=cfg["project"]["sim_start"],
                                start_cash=cfg["project"]["start_cash"])
    curves.to_csv(RESULTS / "papertrader_curves.csv")

    record(outlook, news)
    score = self_score(feats)

    paths = write_digest(outlook, news, sim_stats, score)
    json.dump({"outlook": outlook, "news": news, "sim": sim_stats, "score": score},
              open(RESULTS / "latest_run.json", "w", encoding="utf-8"), indent=2, default=str)
    log(f"digest -> {paths['md']}")
    log(f"done in {(dt.datetime.now()-t0).total_seconds():.1f}s")
    print("\n" + "="*60)
    print((RESULTS / "digest_latest.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main(sys.argv[1:])
