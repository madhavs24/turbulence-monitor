"""Verify a deployed Turbulence Monitor instance matches the local engine.

Usage:
  python -m tests.verify_deploy
  python -m tests.verify_deploy --base-url http://127.0.0.1:8765
  DEPLOY_URL=https://turbulence-monitor-madhav.onrender.com python -m tests.verify_deploy
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

from web.engine_api import build_snapshot

DEFAULT_URL = "https://turbulence-monitor-madhav.onrender.com"
APP_ID = "market-turbulence-monitor"
PASS: list[str] = []
FAIL: list[str] = []
DEPLOY_URL = DEFAULT_URL


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    tag = "PASS" if ok else "FAIL"
    extra = f" — {detail}" if detail else ""
    print(f"  [{tag}] {name}{extra}")


def _last_val(series):
    s = series.dropna()
    return float(s.iloc[-1]) if len(s) else None


def fetch(path: str, timeout: int = 30) -> tuple[int, dict | str]:
    req = urllib.request.Request(
        f"{DEPLOY_URL}{path}", headers={"User-Agent": "turbulence-verify-deploy"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError:
                return resp.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, body


def wait_for_snapshot(max_wait: int = 120) -> dict | None:
    deadline = time.time() + max_wait
    last_err = ""
    while time.time() < deadline:
        code, data = fetch("/api/snapshot")
        if code == 404:
            check("deployed app exposes /api/snapshot", False, "404 — wrong app or failed deploy")
            return None
        if code == 200 and isinstance(data, dict) and data.get("ready"):
            return data
        if isinstance(data, dict):
            last_err = data.get("error") or f"HTTP {code}"
        else:
            last_err = f"HTTP {code}"
        print(f"    waiting for snapshot... ({last_err})")
        time.sleep(10)
    check("snapshot becomes ready", False, last_err or "timeout")
    return None


def check_sse_stream(timeout: int = 15) -> bool:
    url = f"{DEPLOY_URL}/api/stream"
    req = urllib.request.Request(url, headers={"User-Agent": "turbulence-verify-deploy", "Accept": "text/event-stream"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            chunk = resp.read(4096).decode("utf-8", errors="replace")
            return "data:" in chunk
    except Exception as e:
        check("/api/stream SSE", False, str(e))
        return False


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Verify deployed Turbulence Monitor")
    p.add_argument(
        "--base-url",
        default=os.environ.get("DEPLOY_URL", DEFAULT_URL),
        help="Base URL of deployed service (default: turbulence-monitor-madhav.onrender.com)",
    )
    return p.parse_args()


def main() -> int:
    global DEPLOY_URL
    args = parse_args()
    DEPLOY_URL = args.base_url.rstrip("/")

    print("=" * 70)
    print(f"DEPLOY VERIFICATION: {DEPLOY_URL}")
    print("=" * 70)

    print("\n1. Health + app identity")
    code, health = fetch("/api/health")
    check("health returns HTTP 200", code == 200, f"got {code}")
    if isinstance(health, dict):
        check("health has app id", health.get("app") == APP_ID, f"app={health.get('app')}")
        check("health uses ok key (not status)", "ok" in health, f"keys={list(health.keys())}")
        check("health has updated field", "updated" in health)
        if health.get("error"):
            print(f"    note: server error field = {health['error']}")
    else:
        check("health returns JSON", False, str(health)[:120])

    print("\n2. Version endpoint")
    code, ver = fetch("/api/version")
    check("/api/version returns 200", code == 200, f"got {code}")
    if isinstance(ver, dict):
        check("version app id matches", ver.get("app") == APP_ID, f"app={ver.get('app')}")

    print("\n3. Homepage identity")
    code, home = fetch("/")
    check("homepage returns 200", code == 200, f"got {code}")
    if isinstance(home, str):
        check("homepage title is Market Turbulence Monitor", "Market Turbulence Monitor" in home)
        check("homepage is NOT Turbulence v3", "Turbulence v3" not in home)

    print("\n4. Snapshot warmup")
    snap = wait_for_snapshot()
    if snap:
        check("snapshot ready=true", snap.get("ready") is True)
        for key in ("outlook", "vitals", "calibration", "anomalies", "news"):
            check(f"snapshot has {key}", key in snap)

    print("\n5. Other API endpoints")
    code, live = fetch("/api/live/recent")
    check("/api/live/recent returns 200", code == 200, f"got {code}")
    if isinstance(live, dict):
        check("live recent has gauges", "gauges" in live)

    code, pt = fetch("/api/papertrade?strategies=buy_hold&cash=10000")
    check("/api/papertrade returns 200", code == 200, f"got {code}")
    if isinstance(pt, dict):
        check("papertrade has stats", "stats" in pt)
        check("papertrade has curves", "curves" in pt)

    print("\n6. SSE stream")
    check("/api/stream delivers events", check_sse_stream())

    print("\n7. Engine parity (local vs deployed)")
    if snap and "outlook" in snap:
        try:
            parity_mode = "synthetic" if snap.get("data_source") == "synthetic" else "auto"
            local, _, signals = build_snapshot(parity_mode)
            lo, so = local["outlook"], snap["outlook"]
            check(
                "flare_prob_pct matches engine",
                lo.get("flare_prob_pct") == so.get("flare_prob_pct"),
                f"local={lo.get('flare_prob_pct')} deploy={so.get('flare_prob_pct')}",
            )
            check(
                "anomaly p_value matches engine",
                lo.get("anomaly", {}).get("p_value") == so.get("anomaly", {}).get("p_value"),
                f"local={lo.get('anomaly', {}).get('p_value')} deploy={so.get('anomaly', {}).get('p_value')}",
            )
            check(
                "VIX matches engine",
                lo.get("vix") == so.get("vix"),
                f"local={lo.get('vix')} deploy={so.get('vix')}",
            )
            check(
                "regime matches engine",
                lo.get("regime") == so.get("regime"),
                f"local={lo.get('regime')} deploy={so.get('regime')}",
            )
            check(
                "turbulence_band matches engine",
                lo.get("turbulence_band") == so.get("turbulence_band"),
                f"local={lo.get('turbulence_band')} deploy={so.get('turbulence_band')}",
            )
            flare_raw = _last_val(signals["flare_prob"])
            if flare_raw is None:
                check("deployed flare% == raw engine flare_prob", False, "no flare_prob")
            else:
                eng_flare = round(flare_raw * 100, 1)
                check(
                    "deployed flare% == raw engine flare_prob",
                    so.get("flare_prob_pct") == eng_flare,
                    f"site={so.get('flare_prob_pct')} engine={eng_flare}",
                )
        except Exception as e:
            check("local engine parity run", False, f"{type(e).__name__}: {e}")
    else:
        check("engine parity", False, "no deployed snapshot to compare")

    print("\n" + "=" * 70)
    print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED:", FAIL)
        print("\nIf health shows app!=market-turbulence-monitor or /api/snapshot is 404,")
        print("Render is serving the wrong app. Use Blueprint -> turbulence-monitor-madhav")
        print("or confirm repo = madhavs24/turbulence-monitor, branch = main, runtime = Docker")
    print("=" * 70)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
