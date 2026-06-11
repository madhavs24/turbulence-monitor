"""FastAPI server: REST API for the 7 sections + on-demand paper-trade, serves the
polished static frontend. Run:  uvicorn web.server:app --host 0.0.0.0 --port 8000
Live data by default; set TURB_MODE=synthetic for an offline demo."""
from __future__ import annotations
import os, json, asyncio, threading
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse
from .cache import STORE
from src.simulator import run as run_sim, STRATEGIES
from src.live_news import LiveNews

LIVE = LiveNews()
LIVE_MODE = os.environ.get("LIVE_NEWS_MODE", "live")   # 'live' RSS, or 'replay' (offline)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # build the first snapshot in the background so the server starts instantly,
    # then keep refreshing on a timer.
    threading.Thread(target=STORE.refresh, daemon=True).start()
    STORE.start_background()
    # start the real-time news producer on the event loop
    asyncio.create_task(LIVE.run(LIVE_MODE, speed=float(os.environ.get("LIVE_SPEED", "1"))))
    yield


app = FastAPI(title="Market Turbulence Monitor", lifespan=lifespan)
STATIC = Path(__file__).resolve().parent / "static"


@app.get("/api/snapshot")
def snapshot():
    snap, updated, err = STORE.get()
    if snap is None:
        return JSONResponse({"ready": False, "error": err}, status_code=503)
    return {"ready": True, "updated": updated, "error": err, **snap}


@app.get("/api/papertrade")
def papertrade(strategies: str = Query(",".join(STRATEGIES)),
               start: str = "2012-01-01", cash: float = 10000.0):
    feats, signals = STORE.engine()
    if feats is None:
        return JSONResponse({"error": "engine not ready"}, status_code=503)
    want = [s for s in strategies.split(",") if s in STRATEGIES] or STRATEGIES
    stats, curves = run_sim(feats, signals, start=start, start_cash=cash)
    curves = curves[want]
    # downsample to ~250 points for the chart
    step = max(1, len(curves) // 250)
    cd = curves.iloc[::step]
    series = {s: [round(float(v), 1) for v in cd[s].values] for s in want}
    dates = [d.strftime("%Y-%m-%d") for d in cd.index]
    return {"stats": {s: stats[s] for s in want}, "dates": dates, "curves": series}


@app.get("/api/health")
def health():
    snap, updated, err = STORE.get()
    return {"ok": snap is not None, "updated": updated, "error": err}


@app.get("/api/live/recent")
def live_recent():
    return {"items": list(LIVE.recent)[-30:],
            "gauges": {"sentiment": round(LIVE.sent_ewma, 3), "shock": round(LIVE.shock_ewma, 3)}}


@app.get("/api/stream")
async def stream(request: Request):
    q = await LIVE.bc.subscribe()
    async def gen():
        for ev in list(LIVE.recent)[-15:]:          # small backlog so a fresh page isn't empty
            yield {"data": json.dumps(ev)}
        try:
            while True:
                if await request.is_disconnected():
                    break
                ev = await q.get()
                yield {"data": json.dumps(ev)}
        finally:
            LIVE.bc.unsubscribe(q)
    return EventSourceResponse(gen())


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
