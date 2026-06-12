"""Build the STATIC daily artifact: web/static/snapshot.json.

This is the heart of the static-first architecture. Run it on a schedule (GitHub Action)
where FRED/Yahoo are reachable; it does all the heavy work ONCE and writes a JSON the website
loads instantly. The web server / CDN never computes on the request path.

Usage:
    python -m tools.build_snapshot            # live data (use in CI)
    python -m tools.build_snapshot synthetic  # offline demo artifact
"""
from __future__ import annotations
import sys, json, datetime as dt
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from web.engine_api import build_snapshot
from src.simulator import sim_payload
from src.util import log

OUT = ROOT / "web" / "static" / "snapshot.json"


def main(mode: str = "auto"):
    snap, feats, signals = build_snapshot(mode)
    if mode != "synthetic":
        c = snap.get("calibration", {})
        flare = snap.get("outlook", {}).get("flare_prob_pct")
        if c.get("n", 0) == 0 or flare is None:
            log(f"REFUSED degraded snapshot: data_source={snap.get('data_source')} "
                f"flare={flare} calibration_n={c.get('n')}")
            sys.exit(1)
    snap["sim"] = sim_payload(feats, signals,
                              start=snap.get("sim_default") and "2012-01-01" or "2012-01-01")
    snap["generated_at"] = dt.datetime.utcnow().isoformat() + "Z"
    snap["as_of"] = snap.get("as_of")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(snap, separators=(",", ":")))
    kb = OUT.stat().st_size / 1024
    c = snap.get("calibration", {})
    log(f"snapshot.json written: {kb:.0f} KB | as_of {snap['as_of']} | "
        f"source {snap.get('data_source')} | flare {snap['outlook']['flare_prob_pct']}% | "
        f"anomaly_layers any={snap['anomaly_layers']['any_active']}")
    log(f"calibration: flare_auc={c.get('auc')} vix_auc={c.get('vix_auc')} brier={c.get('brier')} n={c.get('n')}")
    return snap


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "auto")
