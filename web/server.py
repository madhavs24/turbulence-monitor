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
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse
from .cache import STORE
from src.simulator import run as run_sim, STRATEGIES
from src.live_news import LiveNews

LIVE = LiveNews()
LIVE_MODE = os.environ.get("LIVE_NEWS_MODE", "live")   # 'live' RSS, or 'replay' (offline)
SSE_ONLY = os.environ.get("SSE_ONLY", "0") == "1"
STATIC_FIRST_MSG = "static-first: use the committed snapshot.json"
VERSION = "1.0.0"
APP_ID = "market-turbulence-monitor"
SNAPSHOT_FILE = Path(__file__).resolve().parent / "static" / "snapshot.json"


def _static_snapshot_meta():
    if not SNAPSHOT_FILE.exists():
        return None, None
    try:
        data = json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))
        return data.get("data_source"), data.get("as_of") or data.get("generated_at")
    except Exception:
        return None, None


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not SSE_ONLY:
        threading.Thread(target=STORE.refresh, daemon=True).start()
        STORE.start_background()
    asyncio.create_task(LIVE.run(LIVE_MODE, speed=float(os.environ.get("LIVE_SPEED", "1"))))
    yield


app = FastAPI(title="Market Turbulence Monitor", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])   # lets a CDN-hosted frontend reach the SSE stream
STATIC = Path(__file__).resolve().parent / "static"


@app.get("/api/snapshot")
def snapshot():
    if SSE_ONLY:
        return JSONResponse({"ready": False, "error": STATIC_FIRST_MSG}, status_code=503)
    snap, updated, err = STORE.get()
    if snap is None:
        return JSONResponse({"ready": False, "error": err}, status_code=503)
    return {"ready": True, "updated": updated, "error": err, **snap}


@app.get("/api/papertrade")
def papertrade(strategies: str = Query(",".join(STRATEGIES)),
               start: str = "2012-01-01", cash: float = 10000.0):
    if SSE_ONLY:
        return JSONResponse({"error": STATIC_FIRST_MSG}, status_code=503)
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
    if SSE_ONLY:
        data_source, as_of = _static_snapshot_meta()
        return {
            "ok": True,
            "app": APP_ID,
            "version": VERSION,
            "data_source": data_source,
            "as_of": as_of,
            "turb_mode": os.environ.get("TURB_MODE", "auto"),
            "live_news_mode": LIVE_MODE,
            "sse_only": True,
            "updated": as_of,
            "error": None,
        }
    snap, updated, err = STORE.get()
    return {"ok": snap is not None, "app": APP_ID, "version": VERSION,
            "data_source": (snap or {}).get("data_source"),
            "turb_mode": os.environ.get("TURB_MODE", "auto"),
            "live_news_mode": LIVE_MODE, "sse_only": False,
            "updated": updated, "error": err}


@app.get("/api/version")
def version():
    return {"app": APP_ID, "version": VERSION}


@app.get("/snapshot.json")
def snapshot_file():
    f = STATIC / "snapshot.json"
    if f.exists():
        return FileResponse(f, headers={"Cache-Control": "no-cache"})
    return JSONResponse({"error": "snapshot not built yet"}, status_code=404)


@app.get("/api/live/recent")
def live_recent():
    shock, sent = LIVE._display_gauges()
    return {"items": list(LIVE.recent)[-30:],
            "gauges": {"sentiment": sent, "shock": shock}}


@app.get("/api/stream")
async def stream(request: Request):
    q = await LIVE.bc.subscribe()
    async def gen():
        # pulse history first so the chart isn't flat on connect; then headline backlog
        for ev in list(LIVE.pulse_history)[-40:]:
            yield {"data": json.dumps(ev)}
        for ev in list(LIVE.recent)[-15:]:
            yield {"data": json.dumps(ev)}
        if not LIVE.pulse_history:
            yield {"data": json.dumps(LIVE._publish_pulse())}
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
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
