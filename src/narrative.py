"""Plain-language daily narrative — turns the model's signals into the story a real person
needs: a verdict, what changed since yesterday, a reality-check (is the news real or noise),
and a behavioral nudge grounded in the historical analog outcomes. Honest, never advice."""
from __future__ import annotations
import pandas as pd

VERDICT = {
    "Calm":     "Markets look calm. Nothing here suggests unusual risk this week.",
    "Watch":    "Slightly unsettled — worth a glance, but not alarming.",
    "Elevated": "Stress is building. Conditions are bumpier than usual.",
    "High":     "High turbulence. Expect outsized swings this week.",
    "Unknown":  "Not enough data for a confident read right now.",
}


def build_narrative(features, signals, outlook, news, analog) -> dict:
    band = outlook["turbulence_band"]

    # --- what changed since ~a week ago (the daily diff most dashboards lack) ---
    fp = signals["flare_prob"].dropna()
    changed = None
    if len(fp) > 6:
        now = float(fp.iloc[-1]) * 100; prev = float(fp.iloc[-6]) * 100
        d = now - prev
        changed = {"delta_pts": round(d, 1), "prev_pct": round(prev, 1),
                   "dir": "rising" if d > 4 else "easing" if d < -4 else "steady"}

    # --- reality check: does the news narrative agree with the market signals? ---
    narr = news.get("narrative")
    sig_stress = band in ("Elevated", "High") or outlook["anomaly"]["flagged"]
    news_stress = narr in ("stressed", "cautious")
    if news.get("n", 0) == 0:
        reality = {"state": "no_news", "line": "No notable market news right now."}
    elif news_stress and sig_stress:
        reality = {"state": "real", "line": "News and the market are both showing stress — this looks real, not just noise."}
    elif news_stress and not sig_stress:
        reality = {"state": "noise", "line": "Headlines sound worried, but the market signals are calm — likely media noise so far."}
    elif (not news_stress) and sig_stress:
        reality = {"state": "quiet_stress", "line": "The market is showing stress even though headlines are quiet — worth watching."}
    else:
        reality = {"state": "calm", "line": "News and market are both calm."}

    # --- behavioral nudge from the analog OUTCOME distribution (anti-panic, honest) ---
    nudge = None
    if analog and analog.get("outcome"):
        o = analog["outcome"]
        up_share = round((1 - o["share_down"]) * 100)
        if o["share_down5"] >= 0.25:
            nudge = (f"In the {o['n']} most similar past setups, the market fell sharply "
                     f"(>5%) about {round(o['share_down5']*100)}% of the time — but it was still "
                     f"higher a month later in {up_share}% of cases. History says: stay alert, don't panic.")
        else:
            nudge = (f"In the {o['n']} most similar past setups, sharp drops were uncommon and "
                     f"the market was higher a month later {up_share}% of the time. Reacting "
                     f"impulsively to weeks like this has usually cost more than it saved.")

    # --- one-line "what this means for you" ---
    means = {
        "Calm": "A normal week — a good time to ignore the noise and stick to your plan.",
        "Watch": "Keep an eye out, but no need to act on this alone.",
        "Elevated": "A bumpier stretch; if market swings stress you, this is a reminder to check your risk — not to react impulsively.",
        "High": "Expect big moves. The biggest mistake in weeks like this is panic-selling.",
        "Unknown": "We'll have a clearer read once more data is in.",
    }[band]

    return {"verdict": VERDICT[band], "means_for_you": means,
            "changed": changed, "reality_check": reality, "nudge": nudge}


if __name__ == "__main__":
    from .data import get_panel
    from .features import build_features
    from .signals import compute_all
    from .outlook import build_outlook
    from .news import synthetic_news, news_summary
    from .analogs import analogs
    from .util import load_config
    cfg = load_config()
    f = build_features(get_panel("synthetic")); s = compute_all(f, cfg)
    o = build_outlook(f, s, news_summary(synthetic_news("cautious")))
    import json
    print(json.dumps(build_narrative(f, s, o, news_summary(synthetic_news("cautious")), analogs(f)), indent=2))
