# Website Guide — how the Turbulence Monitor dashboard works

Developer onboarding for the `turbulence-agent/` web app. Read this and you should be able
to navigate, modify, and debug the dashboard immediately.

> **First, a reconciliation.** The original *blueprint* described a Streamlit app and modules
> named `causal_hmm.py`, `conformal_anomaly.py`, `improved_models.py`, `event_granularity.py`,
> `analog_engine.py`, `app/streamlit_app.py`, `tasks.py`. **None of those files exist in this
> repo.** The working implementation consolidated them. Here is the real map:

| Blueprint module | Where it actually lives |
|---|---|
| `src/data/loaders.py` (FRED/Yahoo/CBOE) | `src/data.py` → `build_live()` (CBOE term structure comes from FRED `VXVCLS`) |
| `src/features/build.py` (8 fingerprints) | `src/features.py` → `build_features()` |
| `src/causal_hmm.py` (HMM regime) | `src/signals.py` → `regime()` — **rule-based** (Calm / FlightToQuality / ElevatedStress), not a trained HMM |
| `src/conformal_anomaly.py` | `src/signals.py` → `anomaly()` (conformal p-value) |
| `src/improved_models.py` (HAR) | `src/signals.py` → `har_turbulence()` |
| `src/event_granularity.py` (flare) | `src/signals.py` → `flare_prob()` |
| `src/signals.py` (bundle) | `src/signals.py` → `compute_all()` + `web/engine_api.py` → `build_snapshot()` |
| `src/papertrader.py` | `src/simulator.py` (legacy `papertrader.py` still sits in the repo root, unused by the site) |
| `src/analog_engine.py` / `crisis_types.py` (precedents) | **Not implemented** — the "nearest historical precedents" feature does not exist yet |
| `app/streamlit_app.py` | Replaced by a **FastAPI + vanilla-JS** app: `web/server.py` + `web/static/index.html` |
| `tasks.py` | `run_daily.py`, `serve.py`, `run.ps1` |
| real-time news layer | `src/live_news.py` (producer) + `src/entities.py` (tagging) + SSE in `web/server.py` |

So the dashboard is **not Streamlit**. It is one FastAPI process that serves a JSON API and a
single self-contained HTML page (no React, no build step; Chart.js loaded from a CDN).

---

## 1. The stack in one paragraph

`python serve.py` starts **uvicorn** running `web/server.py` (FastAPI). On startup a background
thread builds a "snapshot" of all daily signals and refreshes it every ~20 minutes
(`web/cache.py`), and an asyncio task starts the **live news producer** (`src/live_news.py`).
The browser loads `web/static/index.html`, which fetches `/api/snapshot` once to render the
seven daily sections, opens a **Server-Sent Events** stream at `/api/stream` for the always-on
Live Pulse, and calls `/api/papertrade` on demand for the simulator. Every number on the page
comes from the Python engine — the frontend computes nothing itself except chart layout.

---

## 2. User flow & sections (top to bottom on one scrolling page)

There are no tabs; it's a single scrolling page. Order:

1. **Live Pulse** (real-time) — heartbeat chart + scrolling news ticker. Always animating.
2. **Today hero** — the headline turbulence band + best-guess flare probability.
3. **Market vitals** — sparklines for S&P 500, VIX, gold, oil, yield curve, credit spread.
4. **Live news (tagged)** — today's narrative, detection→prediction tie, headline list.
5. **Why this call** — which fingerprint features are most unusual today.
6. **Anomaly timeline** — clickable history of every alarm + what followed.
7. **Paper-trading playground** — demo-money strategy simulator.
8. **Track record** — calibration chart + AUC vs VIX (the honest report card).

A persistent ribbon at the top states the honest disclaimer (turbulence not direction, demo
money, not advice). The brand dot and a "(live)" / "(demo data)" badge reflect whether the
engine used real or synthetic data.

---

## 3. The "Today" panel (hero)

Source: `web/engine_api.build_outlook()` → served in `/api/snapshot` under `outlook`.

Shows: the **turbulence band** (Calm / Watch / Elevated / High, colored), the **flare-up
probability** ("best-guess chance the next ~5 trading days are unusually choppy"), **expected
annualized volatility** (HAR forecast), the **regime** label, the latest **VIX** with its move
vs the 1-year median, the **anomaly** status (quiet / TRIGGERED, with the conformal p-value),
and a **confidence** score (how many independent signals agree).

Refresh cadence: the page renders this from the cached snapshot on load; the cache recomputes
every ~20 minutes (`TURB_REFRESH_MIN`), and the page silently re-renders only when the snapshot
timestamp changes (so it won't reset your playground every poll). It is **not** recomputed per
visitor — that protects the free data APIs.

Honesty note baked in: the flare probability is now **calibrated** (no `class_weight="balanced"`),
so the % means what it says. The hero never shows a direction (up/down) — by design.

---

## 4. The Anomaly Explorer (timeline)

Source: `web/engine_api._anomaly_timeline()` → `/api/snapshot` under `anomalies`.

How the dots/bars are generated: `signals.anomaly()` computes, for every day in history, a
conformal p-value — the standardized distance of that day's 8-feature fingerprint from its own
trailing 2-year normal, turned into `p_t = (1 + #{past scores ≥ today}) / (cal_size + 1)`. A day
is **flagged** when `p_t ≤ alpha` (default 1%). The timeline renders one bar per flagged day; bar
height encodes the realized volatility over the following 21 trading days.

Clicking a bar opens the detail for that date: the **drivers** (which fingerprint features were
most unusual, as z-scores), the **regime** and **VIX** that day, the conformal **p-value**, and
**what followed** (realized vol over the next 21 days — i.e., was the alarm justified). This is
the trust-builder: you can audit every alarm against what actually happened.

(Note: the blueprint's "news headlines from that day" inside the anomaly detail is **not yet
wired** — that requires a historical news store. Today's live news is shown in section 4, not in
the historical anomaly detail.)

---

## 5. The Paper-Trading Playground

Backend: `src/simulator.py` → `/api/papertrade?strategies=...&start=...&cash=...`.

Configuration UI: choose any subset of strategies (`buy_hold`, `vix_rule`, `regime`,
`turbulence`, `coinflip`), set the demo **bankroll**, and a **start date**. Press Run.

Execution: the endpoint pulls the already-computed `features` + `signals` from the cache (no
refetch), then for each strategy computes a daily **fraction invested (0–1)** and applies it to
the next day's S&P return. **Causality is enforced by `pos.shift(1)`** — every position acts on
*yesterday's* signal, and cash earns ~0. The strategies:
- `buy_hold` — always 100% invested (the benchmark).
- `vix_rule` — invested when VIX is below its 1-year median, else cash.
- `regime` — invested unless the model says ElevatedStress.
- `turbulence` — scales down (1.0 → 0.5 → 0.0) as the flare probability rises.
- `coinflip` — random in/out (the "luck-only" control).

Stats computed (`_stats`): final equity = `start_cash × cumprod(1 + fraction·return)`; total &
annualized return; **max drawdown** = `min(equity / running_max − 1)`; **Sharpe** =
`mean(daily) / std(daily) × √252`; % time invested. The frontend draws the equity curves
(down-sampled to ~250 points) and a stats table. The honest punchline is printed under the
chart: signal strategies usually cut drawdown but do **not** beat buy-and-hold on return.

---

## 6. Real-time News Pulse (the live layer)

Producer: `src/live_news.py` (`LiveNews`), tagging: `src/entities.py`, transport: SSE.

What it does each cycle: polls RSS feeds (`config.yaml → live_news.feeds`) every few seconds
with `aiohttp`, de-duplicates story echoes, scores **sentiment** (VADER) reinforced by the
project's finance **stress lexicon** (word-boundary matched), extracts **tickers/sectors/themes**
(`entities.extract`), and computes a **shock score** from sentiment magnitude + **breadth**
(distinct outlets carrying the stress in a 10-min window) + **velocity** (stressed items in 5
min). It emits two event types over an in-process broadcaster:
- `pulse` (every ~1.5 s) — rolling gauges (sentiment, shock) so the heartbeat is always alive.
- `news` — one deduped headline with `tier` ∈ {low, tentative, confirmed}, shock, tickers,
  `lead_sector`, themes, and a link.

Two-tier detection: a strongly stressed single headline reaches **tentative** instantly; a
**confirmed** shock requires a burst across ≥3 outlets. This is the noise filter — real
market-movers get corroborated, one-off headlines don't cry wolf.

Transport: FastAPI exposes `GET /api/stream` (via `sse-starlette`). On connect it sends a small
backlog, then streams events. The browser uses a native `EventSource` (auto-reconnecting). On a
`pulse` it appends a point to the live Chart.js datasets (rolling window) and updates the shock
readout; on a `news` it prepends a ticker row (tier badge + tags + clickable link) and, if the
tier isn't `low`, flashes the card. The Live Pulse panel lives **outside** the snapshot-rendered
`#app`, so it persists and keeps streaming even when the daily sections re-render.

Causal guard: `_emit_news()` clamps `ts_publish = min(ts_publish, now)` — a future-stamped item
can never affect the current signal.

Integration with the daily signals (status): the live layer currently publishes a Phase-4
*overlay stub* (`overlay_flare`) but does **not yet** feed the anomaly alarm or flare model as a
trained feature — that's the planned Phase 3/4 work. Today the live pulse is its own honest
read; the daily flare/anomaly come from market data.

Modes: `LIVE_NEWS_MODE=live` (real RSS) or `LIVE_NEWS_MODE=replay` (an offline SVB-style burst,
for demos and tests). `LIVE_SPEED` time-compresses replay.

---

## 7. Data-flow architecture

```
 data/processed/panel.parquet          (23→16 daily series; built by src/data.py)
        │   src/features.build_features()
        ▼
   8 fingerprint features  ── src/signals.compute_all() ──▶ regime · anomaly(p) · HAR vol · flare prob
        │                                                          │
        │   web/engine_api.build_snapshot()  (adds vitals, why, anomaly timeline, calibration, outlook)
        ▼                                                          ▼
   web/cache.STORE  (background refresh every ~20 min, in-memory) ──▶  /api/snapshot ─┐
                                                                                       ├─▶ browser (index.html)
   src/live_news.LiveNews  (asyncio, real-time)  ──▶ broadcaster ──▶ /api/stream (SSE)─┘
                                                                       /api/papertrade (on demand)
```

### API endpoints (`web/server.py`)
- `GET /` — serves `web/static/index.html` (with `Cache-Control: no-cache`).
- `GET /api/snapshot` — the entire daily payload: `outlook`, `vitals`, `news`, `why`,
  `anomalies`, `calibration`, `sim_default`, `mode`, `data_source`, `updated`. Returns 503 with
  `ready:false` until the first snapshot is built.
- `GET /api/papertrade?strategies&start&cash` — runs the simulator on cached engine state,
  returns per-strategy stats + down-sampled equity curves.
- `GET /api/stream` — SSE stream of `pulse` + `news` events (the Live Pulse).
- `GET /api/live/recent` — last ~30 news events + current gauges (REST fallback / debugging).
- `GET /api/health` — readiness probe.

The frontend (`web/static/index.html`) is one file: a `render(d)` function builds the seven
daily sections from `/api/snapshot`; `initLive()` opens the SSE stream and drives the heartbeat;
`runPlay()` calls `/api/papertrade`. Charts are destroyed before re-creation to avoid leaks, and
every canvas sits in a fixed-height wrapper so the page scrolls normally.

---

## 8. How to run & verify
```bash
pip install -r requirements.txt
python serve.py                 # http://localhost:8000  (live data + live news)
LIVE_NEWS_MODE=replay python serve.py   # instant offline demo of the Live Pulse
python -m tests.verify          # 29-check correctness/calibration suite (synthetic)
TURB_MODE=live python -m tests.verify   # the same suite on REAL market data
```
