# Real-Time News-Shock Layer — Architecture Plan

A developer-ready plan for adding a live, any-topic news-shock layer to the Crisis
Archetype Taxonomy / Turbulence Monitor. Free tools only. Honest risk awareness, not
trading advice. It detects *turbulence and stress reacting to news* — it does not predict
price direction.

---

## 0. Reality check (read this first)

Two requirements are in tension and one is partly a myth:

- **"Sub-5-second from publish to dashboard" is achievable, but only as fast as the
  publisher.** The bottleneck is when a source actually emits the story, not our code.
  Wire-service RSS/Atom feeds (Reuters, AP, CNBC, Yahoo Finance, Google News RSS) often
  appear within seconds; our job is to poll them aggressively and process in milliseconds.
- **GDELT cannot do sub-5s.** GDELT 2.0 updates in **15-minute batches**. It's superb for
  global breadth and automatic entity/theme tagging, but it is an *enrichment* layer, not
  the low-latency trigger. Use it to widen coverage and back-fill tags, not to be first.
- **NewsAPI's free tier is not real-time** (delayed results + ~100 requests/day). Don't
  build the live path on it. (Verify current limits before committing — vendor terms change.)
- **"Graphs always moving" needs honesty about what moves.** Free intraday *price/vol* data
  isn't clean in real time. What genuinely updates second-by-second is the **news-derived**
  layer: sentiment, shock score, breadth, and a *news-adjusted overlay* on the existing
  anomaly/flare signals. The daily macro fingerprint (VIX, credit, yields) still updates
  daily. Label the live gauges as news-driven so the dashboard stays trustworthy.

**Design consequence:** a two-tier detector — an instant *tentative* pulse the moment a
strong headline appears, upgraded to a *confirmed shock* once breadth/velocity confirm it.
This squares "be fast" with "don't cry wolf."

---

## 1. Recommended architecture

```
                      ┌──────────────────────────────────────────────┐
                      │  LIVE NEWS SERVICE  (async, its own process)  │
   RSS/Atom feeds ───▶│  poll → dedup → sentiment → entity/sector →   │
   (1–3s poll)        │  shock score → emit NewsEvent                 │
   GDELT 2.0 ────────▶│  (15-min enrichment: tags, global coverage)   │
                      └───────────────┬──────────────────────────────┘
                                      │ in-process pub/sub (asyncio broadcast)
                 ┌────────────────────┼─────────────────────────────┐
                 ▼                    ▼                             ▼
        rolling news-feature   live signal overlay           SSE endpoint
        (per-minute series)  (anomaly/flare nudge)        /api/stream  ──▶  browser
                 │                    │                          (EventSource)
                 ▼                    ▼                             │
        appended to the         feeds existing                 Chart.js live
        daily pipeline as     signals.py WITHOUT             update + news ticker
        an extra feature      changing batch logic              + pulse animation
```

Key principle: the **live service is decoupled** from the daily batch. It writes a small,
well-defined "news feature" and an event stream. The batch pipeline and the website both
*read* those; neither is rewritten. If the live service is down, the daily system still
works exactly as today.

---

## 2. Data sources & libraries (all free / open-source)

**Ingestion**
- **RSS/Atom (low-latency tier):** `feedparser` (already a dependency) polled on a short
  interval with `asyncio` + `aiohttp` for concurrent fetches. Use HTTP conditional GET
  (`ETag` / `If-Modified-Since`) so you only pay for changed feeds. Start with a curated
  list of fast, broad feeds (wires + markets + world + a Google News RSS query per theme).
- **GDELT (breadth/enrichment tier):** `gdeltdoc` (the GDELT DOC 2.0 Python client) on a
  15-min cron. Gives global coverage and built-in themes/locations to widen tagging.

**NLP**
- **Sentiment:** `vaderSentiment` — lexicon-based, no model download, microsecond-fast,
  great for headlines. This is the live-tier scorer.
- **Optional richer sentiment:** `FinBERT` (via `transformers` + `torch`) run *async / off
  the hot path* for confirmed shocks only — more accurate on financial tone but heavy. Keep
  VADER as the instant score; let FinBERT refine within a few seconds if you want.
- **Entity / ticker / sector extraction:** `spaCy` (`en_core_web_sm`) for ORG/GPE named
  entities, matched against a **gazetteer**:
  - company name → ticker from the free **SEC `company_tickers.json`** (and/or a static
    S&P 500 / Russell 1000 CSV);
  - ticker → **GICS sector** from a free static map;
  - non-corporate events (war, central banks, disasters, rates) → a hand-curated **keyword
    taxonomy** so "any topic" is covered, not just companies.
  Use `rapidfuzz` for fuzzy name matching ("JPMorgan" ≈ "JP Morgan Chase").

**Streaming & web**
- **`sse-starlette`** for Server-Sent Events on top of your existing **FastAPI** app
  (`web/server.py`). SSE is the right tool here: one-way server→client push, auto-reconnect,
  no extra infrastructure, trivial on the browser via `EventSource`. Reach for WebSockets
  only if you later need client→server messaging.
- **`asyncio`** for the producer and an in-process broadcaster (a set of per-client
  `asyncio.Queue`s, or `broadcaster`/`aiopubsub` if you want a library).
- Frontend stays your **no-build Chart.js** page; optionally add
  `chartjs-plugin-streaming` for the heartbeat look (or animate manually).

---

## 3. The live pipeline (the producer)

New module: `src/live_news.py` (sibling to the existing `src/news.py`, reusing its lexicon,
cleaning, and the **injection-quarantine** rule — every headline is data, never an
instruction).

Per poll cycle (every 1–3s):
1. **Fetch** all feeds concurrently (`aiohttp`), parse with `feedparser`.
2. **Dedup** against a rolling window (last ~30 min) using a normalized-title hash +
   near-duplicate check (token set / MinHash) so the same story across 10 outlets counts
   once — but *count the outlets* as a breadth signal.
3. **Score** each new item:
   - `sentiment` = VADER compound (−1…+1);
   - `entities` = spaCy ORG/GPE → tickers + sectors via gazetteer; theme tags via taxonomy;
   - `novelty` = is this a new story cluster or an echo of an existing one;
   - `source_weight` = trust/priority of the outlet.
4. **Shock score** = f(|sentiment|, breadth = #distinct outlets in a short window, velocity
   = stories/min on this cluster, source_weight, novelty). One transparent weighted formula,
   tuned on historical events (Section 7). Emit a `NewsEvent`:
   ```json
   {"ts_publish": "...", "ts_ingest": "...", "headline": "...", "url": "...",
    "sentiment": -0.74, "tickers": ["SIVB"], "sectors": ["Financials"],
    "themes": ["bank_stress"], "shock": 0.83, "tier": "tentative|confirmed"}
   ```
5. **Two-tier gating:** emit `tentative` immediately on a strong single headline (low
   latency, clearly labeled); upgrade to `confirmed` when breadth/velocity cross a threshold
   within the confirmation window (seconds–minutes). The frontend shows tentative as a faint
   pulse, confirmed as a full alert.

The producer maintains a **rolling news-feature series** in memory: per-minute aggregate
sentiment, shock count, max shock, and breadth — bucketed by sector and overall. This is the
clean hand-off to the rest of the system.

---

## 4. Integrating with existing signals (without breaking the batch)

The existing `signals.py` (`anomaly`, `flare_prob`, `regime`, `har`) and the blueprint's
`conformal_anomaly.py` stay **batch and causal** as they are. Add news as an *optional extra
feature* and a *live overlay*:

1. **As a daily feature (batch).** When building the daily panel, add columns
   `news_sent_daily`, `news_shock_daily` (the day's aggregate from the live store, or from
   GDELT history for backfill). Append them to `FINGERPRINT` / `FEATS` *guarded* so a missing
   value defaults to neutral (you already made features tolerant of missing columns — reuse
   that pattern). The flare model and anomaly score then *learn* whether news adds signal,
   measured honestly out-of-sample (does AUC/Brier improve vs. without it?).

2. **As a live overlay (real time).** Add a pure function, e.g.
   `live_overlay(latest_fingerprint, live_news_feature) -> {anom_p_live, flare_live,
   regime_shift}`. It takes the *most recent daily fingerprint* (fixed) and the *current*
   news feature and returns a lightweight, clearly-labeled "live" adjustment — e.g. shrink
   the conformal p-value when a confirmed negative shock lands, nudge the flare probability
   up. This never mutates the batch outputs; it's an additive view the stream publishes.
   Keep it simple and monotonic (more negative shock → more stress), not a second black box.

3. **Historical analog engine.** Optionally add news-sentiment as one more dimension when
   finding "most similar past days," so analogs match on *narrative* as well as macro.

Because integration is "extra feature + overlay function," the daily Streamlit/FastAPI
outputs are unchanged when the live layer is absent.

---

## 5. Causal integrity (no lookahead)

- Every `NewsEvent` carries **both** `ts_publish` and `ts_ingest`. All live features at wall-
  clock time *t* use only events with `ts_publish <= t`. A monotonic clock guards the stream.
- The rolling feature is a **trailing** aggregate (e.g., last 5/15/60 min). Never center a
  window on "now," never use a future bucket.
- For training the daily model on news, align news to a trading day using a **cutoff** (e.g.
  news up to the prior close), and `shift(1)` before it informs any simulated decision —
  same discipline you already enforce.
- The backtest harness (Section 7) replays news by `ts_publish` only and physically cannot
  see ahead, which is how you *prove* causal integrity rather than assert it.

---

## 6. Streaming to the frontend + "Live Pulse" behavior

**Server (FastAPI, your `web/server.py`):**
- Add `GET /api/stream` (SSE via `sse-starlette`). On connect, send a small backlog
  (last N events + current gauges), then stream new `NewsEvent`s and gauge updates as they're
  published by the producer's broadcaster.
- Keep the existing `/api/snapshot` for the daily/static sections; the stream only carries
  the *live* deltas. This keeps payloads tiny and the heartbeat smooth.

**Browser (your no-build page):**
- `const es = new EventSource('/api/stream'); es.onmessage = ...` — on each event:
  - **append** a point to the live sentiment / shock / overlay Chart.js datasets and shift
    off the oldest (rolling window) → the smooth "heartbeat" look (or use
    `chartjs-plugin-streaming`);
  - **flash a pulse**: a CSS keyframe glow on the affected gauge + a spike marker when
    `shock` is high;
  - **news ticker**: a scrolling strip showing the latest headline, its tickers/sectors, and
    estimated impact; confirmed shocks pinned/colored, tentative ones faint.
- Reconnect is automatic with SSE; show a small "live ● / reconnecting…" indicator.

**Streamlit note.** Streamlit reruns top-to-bottom and is poor at true push/animation. You
can fake it with `st_autorefresh` every 1–2s for a prototype, but the real heartbeat monitor
belongs in the **FastAPI + JS (SSE)** app you already have. Recommendation: prototype the
gauge logic in Streamlit if convenient, ship "Live Pulse" in the FastAPI app.

---

## 7. Testing with historical events (the part that earns trust)

Build a **replay harness** (`tools/replay_news.py`):
- Pull historical news for a known event window from **GDELT's archive** (it's queryable by
  date) — e.g. **SVB collapse (Mar 8–13, 2023)**, a semiconductor sell-off week, a CPI
  surprise, a geopolitical flare.
- Stream those articles into the *same* producer pipeline **in `ts_publish` order**, optionally
  time-compressed (e.g. 60×), with the clock hard-bounded so nothing reads ahead.
- Measure:
  - **time-to-detection**: `ts_publish` of the first triggering article → time the shock
    flag fires (and tentative vs confirmed);
  - **did the signals react**: anomaly p-value drop / flare-up rise / regime shift around the
    event;
  - **precision/recall** of "shock" flags against a hand-labeled set of real events;
  - **false-alarm rate per day** on quiet control weeks (e.g. a calm stretch) — this is where
    you tune thresholds.
- Report these the way the project already reports everything: honest numbers, with a VIX-/
  no-news baseline so you can show news *adds* signal (or admit it doesn't on some events).

---

## 8. Trade-offs to expect

- **Latency vs. false alarms.** Instant single-headline triggers are noisy; confirmation
  windows are cleaner but slower. The two-tier design makes the trade-off explicit and
  visible to users rather than hidden.
- **Rate limits / politeness.** Poll RSS with conditional GET and backoff; don't hammer.
  GDELT 15-min cadence caps its freshness. NewsAPI free tier is unsuitable for live.
- **Noise & small news.** Most headlines move nothing. Lean on breadth + velocity + source
  weight, and a high confirmed-shock threshold, since real market-moving shocks are rare
  ("every week or two," as you said) — treat frequent triggers as a tuning failure.
- **Entity errors.** Fuzzy ticker matching mislabels ("Apple" the company vs. fruit;
  ambiguous tickers). Keep a confidence score and a denylist; show low-confidence tags faintly.
- **Dedup is hard.** Syndication and rewrites inflate breadth; near-duplicate clustering
  matters or you'll fake your own confirmation.
- **Honesty.** The live gauges are *news-reaction* signals, not price predictions. Label them
  as such; never let a pulse imply "buy/sell."

---

## 9. Suggested phased roadmap

1. **Producer MVP:** `src/live_news.py` — async RSS poll + dedup + VADER + a simple shock
   score; print `NewsEvent`s to console. Validate latency on live feeds.
2. **Entity/sector tagging:** add spaCy + gazetteer + sector map + keyword taxonomy.
3. **Stream to web:** `sse-starlette` `/api/stream`; add a Live Pulse panel (one live chart +
   ticker) to the FastAPI page.
4. **Signal integration:** rolling news feature + `live_overlay()`; wire into anomaly/flare as
   an optional feature; measure improvement out-of-sample.
5. **Replay harness + tuning:** SVB and friends; set thresholds; publish honest detection
   metrics.
6. **GDELT enrichment + FinBERT (optional):** breadth and richer tone for confirmed shocks.

Everything above is free and open-source, and it slots onto the FastAPI + Chart.js app you
already have without disturbing the daily, causal batch pipeline that makes the project
trustworthy.
