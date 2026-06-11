# Deploy to Render (free) — step by step

This app needs a persistent process (the live-news producer, the SSE heartbeat, and the
20-minute background refresh). Render's free **Web Service** runs exactly that, supports SSE,
and can reach FRED/Yahoo — so it serves **live** data, not the demo panel. The repo is already
configured (`Dockerfile`, `render.yaml`, `.dockerignore`).

> I can't run the deploy for you — it needs your own GitHub + Render login. These steps take
> about 10 minutes the first time.

## Step 1 — Put the project on GitHub
From inside the `turbulence-agent/` folder (PowerShell):
```powershell
git init
git add .
git commit -m "Turbulence Monitor — initial deploy"
# create an empty repo at https://github.com/new  (name it e.g. turbulence-monitor), then:
git remote add origin https://github.com/<your-username>/turbulence-monitor.git
git branch -M main
git push -u origin main
```
(`.gitignore` already keeps caches, logs, the synthetic parquet, and runtime outputs out of the repo.)

## Step 2 — Deploy on Render
1. Go to **https://render.com** and sign in (free; "Sign in with GitHub" is easiest).
2. Click **New +** → **Blueprint**.
3. **Connect** your GitHub and pick the `turbulence-monitor` repo.
4. Render reads `render.yaml` automatically — it'll show a service named **turbulence-monitor**,
   runtime **Docker**, plan **Free**. Click **Apply** / **Create**.
5. Watch the build log (a few minutes the first time — it installs pandas, scikit-learn, etc.).
6. When it says **Live**, click the URL (looks like `https://turbulence-monitor.onrender.com`).
   That's your public link — share it with anyone.

## Step 3 — First load
On first open it builds the snapshot from live data (a few seconds). The badge should read
**(live)**. The Live Pulse heartbeat starts immediately; real news shocks appear as headlines
arrive (genuine market-moving events are rare — every week or two).

## Good to know about the free tier
- **Sleeps when idle:** after ~15 minutes with no visitors the service spins down; the next
  visit cold-starts (~30–60 s while it boots and builds the first snapshot). Fine for a demo/
  portfolio link. To keep it always-on, upgrade to Render's paid Starter plan.
- **512 MB RAM:** enough for this engine. If a build/run ever OOMs, it's the sklearn/pandas
  footprint — upgrading the plan fixes it.
- **Redeploy:** every `git push` to `main` auto-redeploys.
- **Env vars** (already set in `render.yaml`): `TURB_MODE=auto`, `TURB_REFRESH_MIN=20`,
  `LIVE_NEWS_MODE=live`. Change them in the Render dashboard → Environment if needed.

## Alternative hosts (same Dockerfile)
Railway (railway.app) and Fly.io (fly.io) work identically — connect the repo, they build the
Dockerfile, you get a URL. Render is the simplest free option, so start there.
