# Run & deploy the website

## Run locally (in Cursor)
```bash
pip install -r requirements.txt
python serve.py
```
Open **http://localhost:8000**. It fetches free market data on first load, then refreshes
every ~20 minutes in the background. For a no-network demo: `TURB_MODE=synthetic python serve.py`.

## How it's wired
- `web/server.py` — FastAPI: `/api/snapshot` (everything for the 7 sections),
  `/api/papertrade` (on-demand sim), serves the static frontend.
- `web/cache.py` — recomputes the engine on a timer and caches it, so pages load instantly
  and visitors never hammer the free APIs. Tune with `TURB_REFRESH_MIN`.
- `web/static/` — the single-page UI (no build step, Chart.js via CDN).

## Deploy free (so friends & strangers can reach it)
**Render (easiest):** push this folder to a GitHub repo → Render → New → Blueprint → select
the repo. `render.yaml` + `Dockerfile` do the rest. You get a public URL.

**Any Docker host (Railway/Fly/Cloud Run):** `docker build -t turb . && docker run -p 8000:8000 turb`.

Note: free tiers sleep when idle and cold-start slowly; the first visit after a nap waits for
one engine build. That's fine for a demo. Keep `TURB_MODE=auto` in production for live data.
