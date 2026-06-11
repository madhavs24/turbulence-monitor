# Deploy — static-first (instant loads, free)

The dashboard now loads from a precomputed `web/static/snapshot.json` instead of computing on
the request path. Loads go from ~10 minutes to ~1 second. Three pieces:

```
GitHub Action (daily)  ->  commits web/static/snapshot.json   (the "brain")
Static frontend (CDN)  ->  reads snapshot.json instantly       (the "face", always fast)
Render service         ->  /api/stream SSE for the Live Pulse  (the "live extra", progressive)
```

## 0. Push the latest code
```powershell
cd "turbulence-agent"
git add -A
git commit -m "static-first: precomputed snapshot, client-side paper-trade, layered anomaly"
git push
```

## 1. Generate the first snapshot (GitHub Action)
The workflow `.github/workflows/refresh.yml` runs daily and commits `snapshot.json` built on
**live** FRED/Yahoo data (GitHub's runners reach them fast).
- In GitHub → **Actions** tab → enable workflows if prompted.
- Open **refresh-snapshot** → **Run workflow** (manual first run). It builds and commits
  `web/static/snapshot.json` with real data. After this, it refreshes every weekday.
- This is also where the flare model finally trains on REAL data — check the run log for the
  printed AUC vs VIX.

## 2. Static frontend on a CDN (instant, never "warming")
**Netlify (simplest, no build):**
1. netlify.com → Add new site → Import from GitHub → pick the repo.
2. Build command: **(leave blank)**. Publish directory: **`web/static`**.
3. Deploy. You get a URL like `https://your-site.netlify.app` that serves `index.html` +
   `snapshot.json` from the CDN — loads in ~1s, globally, and can't cold-start.
4. It auto-redeploys whenever the daily Action commits a fresh `snapshot.json`.

(GitHub Pages also works but needs an Actions-based Pages publish of `web/static`; Netlify's
"publish a subfolder" is easier.)

## 3. Render service for the Live Pulse (SSE)
Already configured as `madhav-turbulence-monitor` (unique name — avoids the old "Turbulence v3"
collision). Deploy it via **Render → New → Blueprint → pick the repo → Apply**. Note its URL,
e.g. `https://madhav-turbulence-monitor.onrender.com`. Verify the RIGHT app is live:
```
curl https://madhav-turbulence-monitor.onrender.com/api/version
# must return {"app":"market-turbulence-monitor","version":"1.0.0"}
```

## 4. Point the frontend at the Render SSE
In `web/static/index.html` near the top, set:
```html
<script>window.LIVE_BASE = "https://madhav-turbulence-monitor.onrender.com";</script>
```
Commit + push. Now the CDN frontend loads instantly from `snapshot.json` AND connects to the
Render SSE for the Live Pulse. If Render is asleep, the core dashboard still works fully; the
heartbeat just connects when it wakes.

## 5. Keep Render warm (free)
Render's free tier sleeps after ~15 min idle (30–60s cold start). Keep it warm:
- **UptimeRobot** (uptimerobot.com, free): add an HTTP monitor on
  `https://madhav-turbulence-monitor.onrender.com/api/health`, interval 5 min. Done.

## What loads when
- **Core dashboard** (Today, vitals, anomaly explorer with the 3 layers, track record, and the
  client-side paper-trade): instant, from `snapshot.json` on the CDN.
- **Live Pulse**: connects to Render in the background; shows "live feed offline" briefly if
  Render is cold, then streams.
- The page shows an **"as of <date>"** stamp so stale data is never disguised.

## Verify
```powershell
python -m tests.verify          # 29/29 engine/calibration checks (synthetic)
$env:TURB_MODE="live"; python -m tests.verify   # the real-data check (AUC vs VIX)
python serve.py                 # local: serves snapshot.json + live SSE at :8000
```
