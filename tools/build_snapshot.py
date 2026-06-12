"""Build the STATIC daily artifact: web/static/snapshot.json.

Runs on a schedule (GitHub Action) where Yahoo/FRED are reachable. Yahoo-primary fetch makes
this resilient to FRED outages. It RETRIES the live build and REFUSES to write a degraded
snapshot (no flare / empty calibration / synthetic fallback), so the site never publishes
"not enough data" — a bad run simply fails and the next run retries.

Usage:
    python -m tools.build_snapshot auto        # live data (CI) — exits non-zero if degraded
    python -m tools.build_snapshot synthetic   # offline demo artifact
"""
from __future__ import annotations
import sys, json, time, datetime as dt
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from web.engine_api import build_snapshot
from src.simulator import sim_payload
from src.util import log

OUT = ROOT / "web" / "static" / "snapshot.json"


def _quality_ok(snap, mode):
    if mode == "synthetic":
        return True
    return (snap.get("data_source") == "live"
            and snap["outlook"].get("flare_prob_pct") is not None
            and snap["calibration"].get("n", 0) > 0)


def main(mode: str = "auto"):
    tries = 1 if mode == "synthetic" else 4
    snap = feats = signals = None
    for i in range(tries):
        snap, feats, signals = build_snapshot(mode)
        if _quality_ok(snap, mode):
            break
        log(f"attempt {i+1}/{tries} degraded (source={snap.get('data_source')}, "
            f"flare={snap['outlook'].get('flare_prob_pct')}, cal_n={snap['calibration'].get('n')}) "
            f"— retrying")
        time.sleep(5)
    else:
        if mode != "synthetic":
            log("REFUSING to write a degraded live snapshot — failing so the next run retries.")
            sys.exit(1)

    snap["sim"] = sim_payload(feats, signals, start="2012-01-01")
    snap["generated_at"] = dt.datetime.utcnow().isoformat() + "Z"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(snap, separators=(",", ":")))
    kb = OUT.stat().st_size / 1024
    cal = snap["calibration"]
    log(f"snapshot.json OK: {kb:.0f} KB | as_of {snap['as_of']} | source {snap.get('data_source')} "
        f"| flare {snap['outlook']['flare_prob_pct']}% | AUC {cal.get('auc')} vs VIX {cal.get('vix_auc')} "
        f"| Brier {cal.get('brier')}")
    return snap


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "auto")
