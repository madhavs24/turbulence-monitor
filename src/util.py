"""Shared helpers: paths, config, logging."""
from __future__ import annotations
from pathlib import Path
import yaml, datetime as dt

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
RESULTS = ROOT / "results"
KNOW = ROOT / "knowledge"
for _p in (PROC, RESULTS, KNOW):
    _p.mkdir(parents=True, exist_ok=True)

def load_config() -> dict:
    with open(ROOT / "config.yaml") as fh:
        return yaml.safe_load(fh)

def today_str() -> str:
    return dt.date.today().isoformat()

def log(msg: str) -> None:
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)
