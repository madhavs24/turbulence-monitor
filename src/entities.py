"""Phase 2 — entity / sector / theme tagging (offline, free).

Given a headline, extract:
  * tickers  — known company names / ticker symbols (gazetteer)
  * sectors  — GICS-style sector for matched tickers + keyword-implied sectors
  * themes   — event themes for ANY topic (bank stress, semis, energy, rates, geopolitics,
               crypto, disaster, ...), so non-corporate shocks are still tagged.

Design: a curated built-in gazetteer covers the common large caps + key ETFs and is fully
offline. For broader coverage, drop the SEC's free `company_tickers.json` at
`data/company_tickers.json` and it will be merged in automatically (names -> tickers; sector
defaults to 'Unknown' unless in the built-in sector map). No spaCy model download required.
"""
from __future__ import annotations
import json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ticker -> (display name, sector). Small but representative across sectors + ETFs.
GAZETTEER = {
    # Financials
    "JPM": ("JPMorgan", "Financials"), "BAC": ("Bank of America", "Financials"),
    "WFC": ("Wells Fargo", "Financials"), "C": ("Citigroup", "Financials"),
    "GS": ("Goldman Sachs", "Financials"), "MS": ("Morgan Stanley", "Financials"),
    "SIVB": ("SVB Financial", "Financials"), "SCHW": ("Charles Schwab", "Financials"),
    "KRE": ("Regional Banks ETF", "Financials"), "XLF": ("Financials ETF", "Financials"),
    # Technology / semis
    "AAPL": ("Apple", "Technology"), "MSFT": ("Microsoft", "Technology"),
    "NVDA": ("Nvidia", "Technology"), "AMD": ("AMD", "Technology"),
    "INTC": ("Intel", "Technology"), "TSM": ("TSMC", "Technology"),
    "AVGO": ("Broadcom", "Technology"), "MU": ("Micron", "Technology"),
    "SMH": ("Semiconductor ETF", "Technology"), "XLK": ("Technology ETF", "Technology"),
    # Communication / consumer
    "GOOGL": ("Alphabet", "Communication"), "META": ("Meta", "Communication"),
    "AMZN": ("Amazon", "Consumer Discretionary"), "TSLA": ("Tesla", "Consumer Discretionary"),
    "NFLX": ("Netflix", "Communication"), "DIS": ("Disney", "Communication"),
    # Energy
    "XOM": ("Exxon", "Energy"), "CVX": ("Chevron", "Energy"),
    "COP": ("ConocoPhillips", "Energy"), "XLE": ("Energy ETF", "Energy"),
    "USO": ("Oil ETF", "Energy"),
    # Health
    "JNJ": ("Johnson & Johnson", "Health Care"), "PFE": ("Pfizer", "Health Care"),
    "UNH": ("UnitedHealth", "Health Care"), "XLV": ("Health Care ETF", "Health Care"),
    # Broad / macro
    "SPY": ("S&P 500 ETF", "Index"), "QQQ": ("Nasdaq 100 ETF", "Index"),
    "VIX": ("Volatility Index", "Index"), "TLT": ("Long Treasuries ETF", "Rates"),
    "HYG": ("High-Yield Bond ETF", "Credit"), "GLD": ("Gold ETF", "Materials"),
    # Crypto
    "COIN": ("Coinbase", "Crypto"), "MSTR": ("MicroStrategy", "Crypto"),
    "BTC": ("Bitcoin", "Crypto"), "ETH": ("Ethereum", "Crypto"),
}

# extra name aliases -> ticker (lowercase contains-match)
ALIASES = {
    "jpmorgan": "JPM", "jp morgan": "JPM", "bank of america": "BAC", "wells fargo": "WFC",
    "citigroup": "C", "goldman": "GS", "morgan stanley": "MS",
    "svb financial": "SIVB", "silicon valley bank": "SIVB", "svb": "SIVB",
    "schwab": "SCHW", "apple": "AAPL", "microsoft": "MSFT", "nvidia": "NVDA",
    "intel": "INTC", "tsmc": "TSM", "broadcom": "AVGO", "micron": "MU",
    "alphabet": "GOOGL", "google": "GOOGL", "meta": "META", "facebook": "META",
    "amazon": "AMZN", "tesla": "TSLA", "netflix": "NFLX", "disney": "DIS",
    "exxon": "XOM", "chevron": "CVX", "conocophillips": "COP",
    "johnson & johnson": "JNJ", "pfizer": "PFE", "unitedhealth": "UNH",
    "coinbase": "COIN", "microstrategy": "MSTR", "bitcoin": "BTC", "ethereum": "ETH",
}

# theme taxonomy: keyword -> (theme, implied sector). Covers ANY topic, not just companies.
THEMES = {
    "bank": ("bank_stress", "Financials"), "banks": ("bank_stress", "Financials"),
    "deposit": ("bank_stress", "Financials"), "lender": ("bank_stress", "Financials"),
    "regional bank": ("bank_stress", "Financials"), "default": ("credit_stress", "Credit"),
    "credit": ("credit_stress", "Credit"), "downgrade": ("credit_stress", "Credit"),
    "chip": ("semiconductors", "Technology"), "chips": ("semiconductors", "Technology"),
    "semiconductor": ("semiconductors", "Technology"), "export controls": ("semiconductors", "Technology"),
    "oil": ("energy_shock", "Energy"), "crude": ("energy_shock", "Energy"),
    "opec": ("energy_shock", "Energy"), "gas prices": ("energy_shock", "Energy"),
    "fed": ("rates", "Rates"), "rate hike": ("rates", "Rates"), "fomc": ("rates", "Rates"),
    "inflation": ("rates", "Rates"), "cpi": ("rates", "Rates"), "yields": ("rates", "Rates"),
    "war": ("geopolitics", "Geopolitics"), "invasion": ("geopolitics", "Geopolitics"),
    "sanctions": ("geopolitics", "Geopolitics"), "missile": ("geopolitics", "Geopolitics"),
    "geopolitic": ("geopolitics", "Geopolitics"), "tariff": ("trade", "Geopolitics"),
    "hurricane": ("disaster", "Disaster"), "earthquake": ("disaster", "Disaster"),
    "flood": ("disaster", "Disaster"), "wildfire": ("disaster", "Disaster"),
    "crypto": ("crypto", "Crypto"), "bitcoin": ("crypto", "Crypto"),
    "recession": ("macro", "Macro"), "jobs report": ("macro", "Macro"), "gdp": ("macro", "Macro"),
}

# words that LOOK like tickers but aren't (avoid false positives on ALL-CAPS words)
_NOT_TICKERS = {"CEO", "CFO", "USA", "GDP", "CPI", "FED", "ETF", "IPO", "AI", "EU", "UK",
                "OPEC", "SEC", "FOMC", "Q1", "Q2", "Q3", "Q4"}


def _load_sec():
    p = ROOT / "data" / "company_tickers.json"
    if not p.exists():
        return
    try:
        data = json.loads(p.read_text())
        for row in data.values():
            t = str(row.get("ticker", "")).upper()
            name = str(row.get("title", "")).lower()
            if t and t not in GAZETTEER:
                GAZETTEER[t] = (row.get("title", t), "Unknown")
            if name:
                ALIASES.setdefault(name, t)
    except Exception:
        pass


_load_sec()


def extract(text: str) -> dict:
    low = (text or "").lower()
    tickers, sectors, themes = set(), set(), set()
    # 1) explicit ticker symbols ($AAPL or standalone AAPL)
    for m in re.findall(r"\$?\b([A-Z]{2,5})\b", text or ""):
        if m in GAZETTEER and m not in _NOT_TICKERS:
            tickers.add(m); sectors.add(GAZETTEER[m][1])
    # 2) company names / aliases
    for alias, t in ALIASES.items():
        if alias in low:
            tickers.add(t)
            if t in GAZETTEER:
                sectors.add(GAZETTEER[t][1])
    # 3) themes (works for non-corporate events too)
    for kw, (theme, sector) in THEMES.items():
        if kw in low:
            themes.add(theme); sectors.add(sector)
    sectors.discard("Index")  # not an interesting "affected sector" label
    return {"tickers": sorted(tickers)[:6],
            "sectors": sorted(sectors)[:4],
            "themes": sorted(themes)[:4]}


if __name__ == "__main__":
    for h in ["SVB stock craters as bank stress fears spread across financials",
              "Nvidia and AMD slump as new chip export controls hit semiconductors",
              "Oil surges as OPEC signals output cuts amid Middle East war fears",
              "Fed signals another rate hike as CPI inflation runs hot"]:
        print(f"{h[:55]:55} -> {extract(h)}")
