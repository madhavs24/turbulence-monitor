"""Real-time news ingestion + sentiment (the doc's 'Research' step, done honestly).

What it does: pulls recent headlines from free RSS feeds, keeps the market-relevant
ones, scores a simple stress/sentiment lexicon, and reports whether the NEWS NARRATIVE
agrees with the MARKET SIGNALS or diverges from them.

SECURITY: every headline/summary is treated as DATA, never as instructions. We never
execute, follow, or interpret text from a feed as a command. This blocks prompt
injection from a malicious article. (See the doc's warning.)
"""
from __future__ import annotations
import html, re, datetime as dt
import pandas as pd
from .util import load_config, log

try:
    import feedparser
except Exception:
    feedparser = None

# small, transparent stress lexicon (negative = market stress / fear)
STRESS = {
    "crash": -3, "selloff": -3, "plunge": -3, "plummet": -3, "panic": -3, "crisis": -3,
    "recession": -2, "default": -3, "bankruptcy": -3, "collapse": -3, "turmoil": -2,
    "fear": -2, "slump": -2, "tumble": -2, "rout": -3, "contagion": -3, "downgrade": -2,
    "volatility": -1, "uncertainty": -1, "inflation": -1, "rate hike": -1, "layoffs": -2,
    "warning": -1, "weak": -1, "miss": -1, "slowdown": -1, "fell": -1, "drop": -1,
    "rally": 2, "surge": 2, "soar": 2, "rebound": 2, "record high": 2, "gains": 1,
    "optimism": 1, "calm": 1, "stabilize": 1, "recovery": 1, "beat": 1, "rose": 1,
}
INJECTION_PAT = re.compile(r"(ignore previous|disregard|system prompt|you are now|"
                           r"act as|new instructions)", re.I)


_STRESS_PATS = [(re.compile(r"\b" + re.escape(k) + r"(?:s|ed|ing|d)?\b"), w)
                for k, w in STRESS.items()]

def stress_score(text: str) -> int:
    """Sum the stress lexicon with WORD-BOUNDARY matching (+ common inflections), so
    'routine' no longer matches 'rout', 'warm' no longer matches 'war', etc."""
    t = (text or "").lower()
    return sum(w for pat, w in _STRESS_PATS if pat.search(t))


def _clean(t: str) -> str:
    t = html.unescape(t or "")
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def fetch_news(cfg: dict | None = None) -> pd.DataFrame:
    cfg = cfg or load_config()
    nc = cfg["news"]
    if not nc.get("enabled", True) or feedparser is None:
        return pd.DataFrame(columns=["time", "title", "url", "source", "score", "flagged"])
    cutoff = dt.datetime.utcnow() - dt.timedelta(hours=nc["lookback_hours"])
    rows = []
    for url in nc["feeds"]:
        try:
            d = feedparser.parse(url)
        except Exception as e:
            log(f"news feed failed {url[:40]}: {e}"); continue
        src = _clean(getattr(d.feed, "title", url.split("/")[2]))
        for e in d.entries[: nc["max_items"]]:
            title = _clean(getattr(e, "title", ""))
            summ = _clean(getattr(e, "summary", ""))
            text = f"{title}. {summ}"
            t = None
            if getattr(e, "published_parsed", None):
                t = dt.datetime(*e.published_parsed[:6])
            if t and t < cutoff:
                continue
            flagged = bool(INJECTION_PAT.search(text))   # quarantine, never obey
            score = stress_score(text)
            link = _clean(getattr(e, "link", "")) or ""
            rows.append({"time": t, "title": title[:160], "url": link, "source": src,
                         "score": score, "flagged": flagged})
    df = pd.DataFrame(rows)
    if len(df):
        df = df.drop_duplicates("title").sort_values("time", na_position="last")
    return df


def news_summary(df: pd.DataFrame) -> dict:
    """Aggregate the headlines into a narrative read. All numbers, no model trust."""
    if df is None or len(df) == 0:
        return {"n": 0, "narrative": "no_data", "mean_score": 0.0,
                "stress_share": 0.0, "top_negative": [], "injection_flags": 0}
    neg = df[df["score"] < 0]
    mean = float(df["score"].mean())
    narrative = ("stressed" if mean <= -1.0 else
                 "cautious" if mean < -0.2 else
                 "calm" if mean > 0.5 else "neutral")
    top = (df.sort_values("score").head(3)["title"].tolist())
    return {"n": int(len(df)), "narrative": narrative, "mean_score": round(mean, 2),
            "stress_share": round(float(len(neg) / len(df)), 2),
            "top_negative": top, "injection_flags": int(df["flagged"].sum())}


def synthetic_news(narrative: str = "cautious") -> pd.DataFrame:
    """Offline placeholder so the pipeline is testable without network."""
    samples = {
        "stressed": [("Markets plunge as recession fears grip Wall Street", -5),
                     ("VIX surges; selloff deepens on credit warning", -4),
                     ("Investors panic amid contagion concerns", -4)],
        "cautious": [("Stocks slip as inflation data raises uncertainty", -2),
                     ("Fed signals caution; volatility ticks up", -2),
                     ("Mixed earnings keep markets on edge", -1)],
        "calm":     [("Markets rally to record high on optimism", 4),
                     ("Stocks rebound; volatility calm", 3),
                     ("Recovery gains as earnings beat", 2)],
    }[narrative]
    now = dt.datetime.utcnow()
    rows = [{"time": now, "title": t, "url": "", "source": "synthetic (offline)",
             "score": s, "flagged": False} for t, s in samples]
    return pd.DataFrame(rows)


if __name__ == "__main__":
    df = fetch_news()
    if len(df) == 0:
        log("no live news (offline) — using synthetic"); df = synthetic_news("cautious")
    import json; print(json.dumps(news_summary(df), indent=2))
