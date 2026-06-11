"""Background-refresh cache. The engine recomputes on a timer; pages read the cached
snapshot instantly. Keeps features+signals around so the paper-trade playground is fast."""
from __future__ import annotations
import os, threading, time, datetime as dt
from .engine_api import build_snapshot

MODE = os.environ.get("TURB_MODE", "auto")          # 'auto' (live) or 'synthetic'
REFRESH_MIN = int(os.environ.get("TURB_REFRESH_MIN", "20"))


class Store:
    def __init__(self):
        self._lock = threading.Lock()
        self.snapshot = None
        self.feats = None
        self.signals = None
        self.updated = None
        self.error = None
        self.refreshing = False

    def refresh(self):
        with self._lock:
            if self.refreshing:
                return
            self.refreshing = True
        try:
            snap, feats, signals = build_snapshot(MODE)
            with self._lock:
                self.snapshot, self.feats, self.signals = snap, feats, signals
                self.updated = dt.datetime.utcnow().isoformat() + "Z"
                self.error = None
        except Exception as e:
            with self._lock:
                self.error = f"{type(e).__name__}: {e}"
        finally:
            with self._lock:
                self.refreshing = False

    def get(self):
        with self._lock:
            return self.snapshot, self.updated, self.error

    def engine(self):
        with self._lock:
            return self.feats, self.signals

    def start_background(self):
        def loop():
            time.sleep(REFRESH_MIN * 60)   # initial load handled by startup thread
            while True:
                self.refresh()
                time.sleep(REFRESH_MIN * 60)
        threading.Thread(target=loop, daemon=True).start()


STORE = Store()
