"""Phase 1 — real-time news-shock producer.

Polls RSS/Atom feeds every few seconds, de-duplicates story echoes, scores sentiment
(VADER), and computes a transparent SHOCK score from sentiment magnitude + breadth (how
many distinct outlets carry the stress) + velocity (how fast it's spreading). Emits two
kinds of events over an in-process broadcaster:

  * "news"  — a single deduped headline with sentiment, shock, tier (tentative/confirmed)
  * "pulse" — a fixed-cadence heartbeat carrying rolling gauges (sentiment, shock, overlay)

Honest framing: this measures the market NARRATIVE reacting to news (stress/turbulence),
NOT price direction. Every headline is treated as DATA, never an instruction (injection
quarantine, reused from news.py).

Causal: events are stamped with ts_publish; live features only ever use news already
published. A REPLAY mode streams canned historical-style events (offline-testable) in
publish-time order so the pipeline can be validated without network access.

Run standalone (replay):  python -m src.live_news replay
"""
from __future__ import annotations
import asyncio, time, re, datetime as dt
from collections import deque, defaultdict
from urllib.parse import urlparse

import feedparser
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

try:
    import aiohttp
except Exception:
    aiohttp = None

from .util import load_config, log
from .news import _clean, INJECTION_PAT, STRESS, stress_score
from .entities import extract

VADER = SentimentIntensityAnalyzer()
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TurbulenceMonitor/1.0"}
STOP = set("the a an of to in on for and or but with as at by from is are was were be this "
           "that it its has have will would could after over amid into new said says".split())

# outlets we trust a bit more get a higher source weight
SOURCE_WEIGHT = {"reuters": 1.3, "associated press": 1.3, "ap": 1.3, "bloomberg": 1.25,
                 "wall street journal": 1.2, "cnbc": 1.1, "financial times": 1.2}


def _sig(title: str) -> frozenset:
    """A cheap story 'signature': the set of significant words, used only for de-dup."""
    words = [w for w in re.findall(r"[a-z]{4,}", title.lower()) if w not in STOP]
    return frozenset(words[:8])


def _source_weight(src: str) -> float:
    s = (src or "").lower()
    for k, w in SOURCE_WEIGHT.items():
        if k in s:
            return w
    return 1.0


def _outlet_key(source: str, url: str) -> str:
    """Distinct outlet for breadth — prefer article host, not the RSS feed label."""
    if url:
        try:
            host = urlparse(url).netloc.lower().replace("www.", "")
            if host and "google.com" not in host and "news.google" not in host and "example.com" not in host:
                return host
        except Exception:
            pass
    return (source or "unknown").lower()


class Broadcaster:
    """Minimal in-process pub/sub: each SSE client gets its own asyncio.Queue."""
    def __init__(self):
        self.subs: set[asyncio.Queue] = set()

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self.subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        self.subs.discard(q)

    def publish(self, event: dict):
        dead = []
        for q in self.subs:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self.subs.discard(q)


class LiveNews:
    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or load_config()
        lc = self.cfg.get("live_news", {})
        self.feeds = lc.get("feeds") or self.cfg["news"]["feeds"]
        self.poll_secs = lc.get("poll_secs", 3)
        self.pulse_secs = lc.get("pulse_secs", 1.5)
        self.window_min = lc.get("window_min", 30)
        self.tentative_thr = lc.get("tentative_shock", 0.45)
        self.confirm_thr = lc.get("confirm_shock", 0.60)
        self.confirm_breadth = lc.get("confirm_breadth", 3)
        self.shock_decay = lc.get("shock_decay", 0.97)
        self.sent_decay = lc.get("sent_decay", 0.985)
        self.activity_secs = lc.get("activity_secs", 300)
        self.market_poll_secs = lc.get("market_poll_secs", 60)
        self.bc = Broadcaster()
        self.market: dict = {"vix": None, "vix_chg": None, "spy_chg": None, "ts": 0.0}
        self.seen: dict[frozenset, float] = {}    # signature -> first-seen ts (de-dup)
        self.stress_buf: deque = deque()          # (wall_ts, outlet) of recent STRESSED items
        self.sector_buf: dict = defaultdict(deque) # sector -> deque[(ts, source)]
        self.recent: deque = deque(maxlen=120)    # recent NewsEvents (for SSE backlog)
        self.activity_buf: deque = deque(maxlen=300)  # (wall_ts, shock, sentiment) all headlines
        self.pulse_history: deque = deque(maxlen=80)
        self.sent_ewma = 0.0
        self.shock_ewma = 0.0
        self.last_event_ts = 0.0
        self.running = False
        self._etag: dict[str, str] = {}

    # ---- scoring ----------------------------------------------------------
    def _score(self, title: str, summary: str, source: str, ts: float,
               url: str = "", clock_ts: float | None = None) -> dict:
        text = f"{title}. {summary}"
        sent = VADER.polarity_scores(text)["compound"]            # -1..+1
        lex = stress_score(text)
        # Live breadth/velocity use wall-clock ingest time so the pulse reflects what's
        # hitting the wire now (RSS backlog items still carry honest ts_publish for display).
        now = clock_ts if clock_ts is not None else ts
        outlet = _outlet_key(source, url)
        if sent <= -0.25 or lex <= -2:
            self.stress_buf.append((now, outlet))
        w10 = now - 600     # breadth window: 10 min
        w5 = now - 300      # velocity window: 5 min
        self.stress_buf = deque((t, s) for (t, s) in self.stress_buf if t >= w10)
        breadth = len({s for (t, s) in self.stress_buf})              # distinct outlets, 10m
        velocity = sum(1 for (t, s) in self.stress_buf if t >= w5)    # stressed items, 5m
        sw = _source_weight(source)
        # VADER is weak on finance ("bank collapses" barely registers), so fold in the
        # project's domain stress lexicon: stress magnitude = stronger of the two negatives.
        neg_lex = max(0, -lex)
        stress_mag = max((-sent if sent < 0 else 0.0), min(neg_lex / 4.0, 1.0))
        shock = min(1.0, (0.50 * stress_mag + 0.32 * min(breadth / 4, 1)
                          + 0.18 * min(velocity / 4, 1)) * sw)
        is_stress = (sent <= -0.2 or lex <= -2)   # the item ITSELF must be stressed to be a shock
        tags = extract(text)
        # per-sector stress: which sector is the burst concentrated in?
        lead_sector = None
        if is_stress:
            for sec in (tags["sectors"] or []):
                self.sector_buf[sec].append((now, outlet))
        for sec, dq in list(self.sector_buf.items()):
            self.sector_buf[sec] = deque((t, s) for (t, s) in dq if t >= w10)
        if self.sector_buf:
            lead_sector = max(self.sector_buf,
                              key=lambda k: len({s for _, s in self.sector_buf[k]}))
            if not self.sector_buf[lead_sector]:
                lead_sector = None
        if is_stress and breadth >= self.confirm_breadth and velocity >= 3 and shock >= self.confirm_thr:
            tier = "confirmed"
        elif is_stress and shock >= self.tentative_thr:
            tier = "tentative"
        else:
            tier = "low"
        return {"sentiment": round(sent, 3), "lex": int(lex), "shock": round(shock, 3),
                "tier": tier, "breadth": breadth, "velocity": velocity,
                "tickers": tags["tickers"], "sectors": tags["sectors"],
                "themes": tags["themes"], "lead_sector": lead_sector}

    def _emit_news(self, *, title, url, source, ts_publish):
        title = _clean(title)[:180]
        if not title:
            return
        sig = _sig(title)
        now = time.time()
        ts_publish = min(ts_publish, now)   # causal guard: never accept future-stamped news
        # de-dup: same signature seen recently -> still counts toward breadth, don't re-emit
        if sig in self.seen and now - self.seen[sig] < self.window_min * 60:
            self._score(title, "", source, ts_publish, url=url, clock_ts=now)
            return
        self.seen[sig] = now
        sc = self._score(title, "", source, ts_publish, url=url, clock_ts=now)
        flagged = bool(INJECTION_PAT.search(title))
        ev = {"type": "news",
              "ts_publish": dt.datetime.utcfromtimestamp(ts_publish).isoformat() + "Z",
              "ts_ingest": dt.datetime.utcnow().isoformat() + "Z",
              "headline": title, "url": url or "", "source": source or "",
              "flagged": flagged, **sc}
        self.sent_ewma = 0.6 * sc["sentiment"] + 0.4 * self.sent_ewma
        self.shock_ewma = max(self.shock_ewma, sc["shock"])
        self.activity_buf.append((now, sc["shock"], sc["sentiment"]))
        self.last_event_ts = now
        self.recent.append(ev)
        self.bc.publish(ev)
        self._publish_pulse()   # instant chart tick on each new headline
        return ev

    def _display_gauges(self, now: float | None = None) -> tuple[float, float]:
        """Blend decaying EWMA with rolling recent-headline activity (keeps pulse alive)."""
        now = now or time.time()
        cutoff = now - self.activity_secs
        recent = [(t, sh, sn) for (t, sh, sn) in self.activity_buf if t >= cutoff]
        if recent:
            roll_shock = max(sh for _, sh, _ in recent)
            roll_sent = sum(sn for _, _, sn in recent) / len(recent)
            # honest baseline motion from headline flow (tone + scored stress), not fake noise
            activity = min(0.4, 0.12 + 0.2 * sum(abs(sn) for _, _, sn in recent) / len(recent)
                           + 0.35 * sum(sh for _, sh, _ in recent) / len(recent))
            shock = max(self.shock_ewma, roll_shock, activity)
            sent = 0.5 * self.sent_ewma + 0.5 * roll_sent
        else:
            shock, sent = self.shock_ewma, self.sent_ewma
        return round(shock, 3), round(sent, 3)

    def _breadth_velocity(self, now: float | None = None) -> tuple[int, int]:
        now = now or time.time()
        w10, w5 = now - 600, now - 300
        buf = [(t, s) for (t, s) in self.stress_buf if t >= w10]
        breadth = len({s for (t, s) in buf})
        velocity = sum(1 for (t, _) in buf if t >= w5)
        return breadth, velocity

    def _market_stress(self) -> float:
        """0..1 from live VIX level + intraday moves (~15-min delayed Yahoo)."""
        m = self.market
        parts: list[float] = []
        if m.get("vix_chg") is not None:
            parts.append(min(1.0, abs(m["vix_chg"]) / 4.0))
        if m.get("spy_chg") is not None:
            parts.append(min(1.0, abs(m["spy_chg"]) / 1.2))
        if m.get("vix") is not None:
            parts.append(min(0.55, max(0.0, (m["vix"] - 14) / 30)))
        return max(parts) if parts else 0.0

    def _stress_index(self, shock: float, breadth: int, velocity: int) -> int:
        mkt = self._market_stress()
        b_n = min(1.0, breadth / 4)
        v_n = min(1.0, velocity / 5)
        if mkt > 0 or self.market.get("vix") is not None:
            raw = 0.38 * shock + 0.32 * mkt + 0.18 * b_n + 0.12 * v_n
        else:
            raw = 0.55 * shock + 0.28 * b_n + 0.17 * v_n
        return int(round(100 * min(1.0, raw)))

    def _confirm_verdict(self, shock: float, market_stress: float) -> str:
        news_up = shock >= 0.22
        mkt_up = market_stress >= 0.25
        if news_up and mkt_up:
            return "agree"
        if news_up and not mkt_up:
            return "diverge"
        if not news_up and mkt_up:
            return "market_only"
        return "calm"

    def _sector_heatmap(self, now: float | None = None) -> list[dict]:
        now = now or time.time()
        w10 = now - 600
        out = []
        for sec, dq in self.sector_buf.items():
            active = [(t, s) for (t, s) in dq if t >= w10]
            if not active:
                continue
            heat = min(1.0, len({s for _, s in active}) / max(1, self.confirm_breadth))
            out.append({"s": sec, "heat": round(heat, 2)})
        out.sort(key=lambda x: -x["heat"])
        return out[:5]

    def _publish_pulse(self):
        now = time.time()
        shock, sent = self._display_gauges(now)
        breadth, velocity = self._breadth_velocity(now)
        mkt = self._market_stress()
        stress_index = self._stress_index(shock, breadth, velocity)
        overlay_flare = round(min(1.0, 0.2 + 0.6 * shock), 3)
        pulse = {"type": "pulse", "ts": dt.datetime.utcnow().isoformat() + "Z",
                 "sentiment": sent, "shock": shock,
                 "stress_index": stress_index,
                 "confirm": self._confirm_verdict(shock, mkt),
                 "vix": self.market.get("vix"),
                 "vix_chg": self.market.get("vix_chg"),
                 "spy_chg": self.market.get("spy_chg"),
                 "breadth": breadth, "velocity": velocity,
                 "sectors": self._sector_heatmap(now),
                 "overlay_flare": overlay_flare, "n_recent": len(self.recent)}
        self.pulse_history.append(pulse)
        self.bc.publish(pulse)
        return pulse

    # ---- pulse heartbeat --------------------------------------------------
    async def pulse_loop(self):
        while True:
            self.shock_ewma *= self.shock_decay
            self.sent_ewma *= self.sent_decay
            self._publish_pulse()
            await asyncio.sleep(self.pulse_secs)

    # ---- live market (VIX / SPY, ~15-min delayed) ------------------------
    async def _fetch_quote(self, session, symbol: str) -> dict | None:
        sym = symbol.replace("^", "%5E")
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
               f"?range=1d&interval=5m")
        try:
            async with session.get(url, headers=UA,
                                   timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status != 200:
                    return None
                js = await r.json()
                result = (js.get("chart") or {}).get("result")
                if not result:
                    return None
                meta = result[0].get("meta") or {}
                price = meta.get("regularMarketPrice") or meta.get("previousClose")
                chg = meta.get("regularMarketChangePercent")
                if chg is None and price and meta.get("chartPreviousClose"):
                    prev = meta["chartPreviousClose"]
                    chg = (price - prev) / prev * 100 if prev else None
                return {"price": round(float(price), 2) if price is not None else None,
                        "chg_pct": round(float(chg), 2) if chg is not None else None}
        except Exception:
            return None

    async def market_loop(self):
        if aiohttp is None:
            return
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    vix_q, spy_q = await asyncio.gather(
                        self._fetch_quote(session, "^VIX"),
                        self._fetch_quote(session, "SPY"))
                    if vix_q:
                        self.market["vix"] = vix_q["price"]
                        self.market["vix_chg"] = vix_q["chg_pct"]
                    if spy_q:
                        self.market["spy_chg"] = spy_q["chg_pct"]
                    self.market["ts"] = time.time()
                except Exception as ex:
                    log(f"live market err: {ex}")
                await asyncio.sleep(self.market_poll_secs)

    # ---- live RSS ---------------------------------------------------------
    async def _fetch(self, session, url):
        headers = dict(UA)
        if url in self._etag:
            headers["If-None-Match"] = self._etag[url]
        async with session.get(url, headers=headers,
                               timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status == 304:
                return None
            if "ETag" in r.headers:
                self._etag[url] = r.headers["ETag"]
            return await r.read()

    async def poll_loop(self):
        if aiohttp is None:
            log("aiohttp missing — live RSS disabled"); return
        async with aiohttp.ClientSession() as session:
            while True:
                for url in self.feeds:
                    try:
                        raw = await self._fetch(session, url)
                        if not raw:
                            continue
                        d = feedparser.parse(raw)
                        src = _clean(getattr(d.feed, "title", url.split("/")[2]))
                        for e in d.entries[:25]:
                            tp = getattr(e, "published_parsed", None)
                            ts = time.mktime(tp) if tp else time.time()
                            ts = min(ts, time.time())           # causal guard: never future
                            self._emit_news(title=getattr(e, "title", ""),
                                            url=getattr(e, "link", ""), source=src, ts_publish=ts)
                    except Exception as ex:
                        log(f"live feed err {url[:40]}: {ex}")
                await asyncio.sleep(self.poll_secs)

    # ---- replay (offline test) -------------------------------------------
    async def replay_loop(self, speed: float = 1.0):
        """Stream a canned, historical-style sequence (SVB-style multi-outlet shock) in
        publish-time order so the two-tier detector can be validated offline. Loops."""
        script = [
            (0,  "Stocks edge higher as investors await inflation data", "Reuters"),
            (2,  "Tech shares mixed in quiet morning trade", "CNBC"),
            (5,  "SVB Financial shares halted after capital raise disclosure", "Reuters"),
            (6,  "Silicon Valley Bank scrambles to raise capital amid deposit outflows", "Bloomberg"),
            (7,  "SVB stock craters as bank stress fears spread across financials", "CNBC"),
            (8,  "Regional bank selloff deepens; KRE plunges on contagion worries", "Wall Street Journal"),
            (9,  "Investors flee bank stocks; volatility spikes on SVB collapse fears", "Financial Times"),
            (14, "Treasury yields tumble as traders seek safety", "Reuters"),
            (18, "Markets steady after regulators signal deposit backstop", "Associated Press"),
            (22, "Calm returns as bank stress eases into the close", "CNBC"),
        ]
        while True:
            base = time.time()
            for offset, title, source in script:
                await asyncio.sleep(max(0, (offset - (time.time() - base)) / max(speed, 0.01)))
                self._emit_news(title=title, url="https://example.com/replay",
                                source=source, ts_publish=time.time())
            self.stress_buf.clear(); self.seen.clear()   # reset between cycles
            await asyncio.sleep(15)                       # quiet gap, then replay again

    async def run(self, mode: str = "live", **kw):
        self.running = True
        tasks = [asyncio.create_task(self.pulse_loop()),
                 asyncio.create_task(self.market_loop())]
        if mode == "replay":
            tasks.append(asyncio.create_task(self.replay_loop(kw.get("speed", 1.0))))
        else:
            tasks.append(asyncio.create_task(self.poll_loop()))
        await asyncio.gather(*tasks)


async def _demo():
    ln = LiveNews()
    q = await ln.bc.subscribe()
    task = asyncio.create_task(ln.run("replay", speed=4.0))
    seen_news = seen_pulse = 0
    confirmed = []
    end = time.time() + 12
    while time.time() < end:
        try:
            ev = await asyncio.wait_for(q.get(), timeout=2)
        except asyncio.TimeoutError:
            continue
        if ev["type"] == "news":
            seen_news += 1
            if ev["tier"] == "confirmed":
                confirmed.append(ev["headline"][:50])
            print(f"  NEWS [{ev['tier']:9}] shock={ev['shock']:.2f} b={ev['breadth']} "
                  f"sent={ev['sentiment']:+.2f}  {ev['headline'][:60]}")
        else:
            seen_pulse += 1
    task.cancel()
    print(f"\n  pulses={seen_pulse} news={seen_news} confirmed_shocks={len(confirmed)}")


if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "replay"
    if mode == "replay":
        asyncio.run(_demo())
    else:
        asyncio.run(LiveNews().run("live"))
