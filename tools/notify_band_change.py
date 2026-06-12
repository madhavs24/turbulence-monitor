"""After build_snapshot: compare turbulence band to alert_state.json; email subscribers on change.

Uses Resend (free tier). Set GitHub secrets RESEND_API_KEY and RESEND_FROM.
Subscribers: one email per line in data/alert_subscribers.txt (add from Netlify form submissions).
"""
from __future__ import annotations
import json, os, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAP = ROOT / "web" / "static" / "snapshot.json"
STATE = ROOT / "web" / "static" / "alert_state.json"
SUBS = ROOT / "data" / "alert_subscribers.txt"
SITE = os.environ.get("SITE_URL", "https://shimmering-moonbeam-f3463d.netlify.app")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _subs() -> list[str]:
    if not SUBS.exists():
        return []
    out = []
    for ln in SUBS.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#") and "@" in ln:
            out.append(ln)
    return out


def _send(to: str, subject: str, body: str) -> bool:
    key, frm = os.environ.get("RESEND_API_KEY"), os.environ.get("RESEND_FROM")
    if not key or not frm:
        print("RESEND_API_KEY or RESEND_FROM missing — skipping email send")
        return False
    payload = json.dumps({"from": frm, "to": [to], "subject": subject, "text": body}).encode()
    req = urllib.request.Request(
        "https://api.resend.com/emails", data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return 200 <= resp.status < 300


def main():
    snap = _load(SNAP)
    new_band = snap["outlook"]["turbulence_band"]
    as_of = snap.get("as_of", "")
    old_band = _load(STATE).get("band") if STATE.exists() else None

    if old_band == new_band:
        print(f"band unchanged ({new_band})")
    else:
        print(f"band change: {old_band!r} -> {new_band!r}")
        flare = snap["outlook"].get("flare_prob_pct")
        subs = _subs()
        if old_band and subs:
            subject = f"Turbulence band: {old_band} → {new_band} (as of {as_of})"
            body = (
                f"Market Turbulence Monitor — turbulence band shift\n\n"
                f"Band changed: {old_band} → {new_band} (as of {as_of})\n"
                f"Flare probability: {flare}%\n\n"
                f"This measures risk and turbulence — not price direction. "
                f"Not investment advice.\n"
                f"Historical analogs rhyme; they do not repeat.\n\n"
                f"Dashboard: {SITE}\n"
            )
            for email in subs:
                try:
                    _send(email, subject, body)
                    print(f"alert sent to {email}")
                except Exception as e:
                    print(f"alert failed for {email}: {e}")
        elif not subs:
            print("no subscribers in data/alert_subscribers.txt")

    STATE.write_text(json.dumps({"band": new_band, "as_of": as_of}, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
