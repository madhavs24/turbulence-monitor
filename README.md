# Market Turbulence Monitor — autonomous edition

An **AI-assisted market turbulence monitor + demo-money paper-trader that runs by itself.**
Built in the spirit of the prediction-market trading-bot guide, but adapted to the **stock
market**, on **free data only**, with **demo money only (no real trades, ever)**.

It wakes up on a schedule, pulls the market's daily vital signs, reads real-time news,
forms a **best-guess outlook** for the week, paper-trades signal strategies, scores its own
past calls, and leaves you a **morning digest**. No babysitting.

> **Educational only. It detects turbulence / stress, NOT price direction. It never trades
> real money. Not investment advice.**

## The pipeline (five stages, fully automatic)

```
(1) DATA  ->  (2) FEATURES  ->  (3) SIGNALS  ->  (4) NEWS + OUTLOOK  ->  (5) DIGEST + MEMORY
 FRED/Yahoo    8 fingerprint   regime / anomaly /   real-time RSS +        morning report +
 free feeds    numbers          turbulence / flare    best-guess fusion      self-scoring KB
```

1. **Data** (`src/data.py`) — free FRED + Yahoo feeds into `data/processed/panel.parquet`. Synthetic fallback for offline testing.
2. **Features** (`src/features.py`) — 8 causal "fingerprint" numbers per day.
3. **Signals** (`src/signals.py`) — leakage-free regime, conformal **anomaly alarm** with a calibrated false-alarm rate, HAR turbulence forecast, and the walk-forward **flare-up probability** (the one real edge, beats VIX-only in backtests).
4. **News + Outlook** (`src/news.py`, `src/outlook.py`) — pulls real-time headlines, scores stress sentiment (treating all text as **data, never instructions** — injection-safe), and fuses signals + news into a **best-guess situational outlook**: turbulence band, flare-up %, regime, anomaly status, confidence, and whether news *confirms* or *diverges* from the signals.
5. **Digest + Memory** (`src/digest.py`, `src/memory.py`) — writes a Markdown + HTML digest and appends to a knowledge base that **scores its own past calls** and logs misses to `knowledge/failure_log.md`.

## Run it

```bash
pip install -r requirements.txt

python run_daily.py            # live free data + live news (on your own machine)
python run_daily.py --demo     # synthetic data + news, fully offline (test the wiring)
python run_daily.py --no-news  # signals only
```

Outputs land in `results/` (`digest_latest.md`, `digest_latest.html`, `latest_run.json`)
and the knowledge base grows in `knowledge/`.

**Kill switch:** create an empty file named `STOP` next to `run_daily.py` and the job
exits immediately without doing anything.

## Make it run by itself

See **`SCHEDULING.md`** — one command sets up a Windows Task Scheduler job that runs the
digest every morning automatically.

## Why this design is honest
- **Predicts only what's predictable:** turbulence/stress clusters and is forecastable; direction is near-random, so we refuse to fake it.
- **Everything causal:** every signal and every paper-trade uses only past information (`shift(1)` before acting).
- **Free + local:** no paid feeds, no cloud bills, no real money.
- The paper-trader's honest lesson: signal strategies **cut drawdown (pain)**, they do **not** beat buy-and-hold on return. It's a risk monitor, not a money machine.

---

## The website (live engine)

A polished public-facing site with all seven sections, served by one Python command.

```bash
pip install -r requirements.txt
python serve.py          # open http://localhost:8000
# offline demo (no network): TURB_MODE=synthetic python serve.py
```

**Sections:** (1) turbulence hero gauge · (2) market vitals with sparklines (S&P 500, VIX,
gold, oil, yield curve, credit) · (3) live news tagged with stress scores + anomaly banner ·
(4) "why this call" feature attribution · (5) clickable anomaly timeline with aftermath ·
(6) paper-trading playground · (7) track record (calibration + AUC vs VIX).

It uses background-refresh caching (the engine recomputes on a timer; pages load instantly)
and is deploy-ready — see `web/DEPLOY.md` for free hosting on Render or any Docker host.
