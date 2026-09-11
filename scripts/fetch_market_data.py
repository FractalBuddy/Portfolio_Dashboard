#!/usr/bin/env python3
"""
Fetches market data (indices, stocks, commodities, forex, US rates, and a
country-level heatmap) and writes it to data.json at the repo root.

Run automatically by the GitHub Actions workflow on a schedule. Can also be
run locally for testing:

    pip install -r requirements.txt
    export FRED_API_KEY=your_key_here   # optional, only needed for the rates section
    python scripts/fetch_market_data.py

This script never touches anything about your personal portfolio -- it only
fetches generic public market data (index/ETF/commodity/forex prices).
"""
import json
import os
import sys
from datetime import datetime, timezone

import requests
import yfinance as yf

FRED_API_KEY = os.environ.get("FRED_API_KEY", "")

# ---------------------------------------------------------------------------
# Watchlists -- edit these lists to change what shows up on the dashboard.
# ---------------------------------------------------------------------------
INDICES = [
    ("^GSPC", "S&P 500"), ("^IXIC", "Nasdaq Composite"), ("^DJI", "Dow Jones"),
    ("^RUT", "Russell 2000"), ("^FTSE", "UK 100"), ("^GDAXI", "DAX 40"),
    ("^FCHI", "CAC 40"), ("^N225", "Nikkei 225"), ("^HSI", "Hang Seng"),
    ("^STOXX50E", "Euro Stoxx 50"),
]

STOCKS = [
    ("AAPL", "Apple"), ("MSFT", "Microsoft"), ("NVDA", "NVIDIA"), ("GOOGL", "Alphabet"),
    ("AMZN", "Amazon"), ("META", "Meta"), ("TSLA", "Tesla"), ("AVGO", "Broadcom"),
    ("JPM", "JPMorgan Chase"), ("LLY", "Eli Lilly"),
]

COMMODITIES = [
    ("GC=F", "Gold"), ("SI=F", "Silver"), ("CL=F", "WTI Crude Oil"), ("BZ=F", "Brent Crude Oil"),
    ("NG=F", "Natural Gas"), ("HG=F", "Copper"), ("ZW=F", "Wheat"), ("ZC=F", "Corn"),
]

FOREX = [
    ("EURUSD=X", "EUR/USD"), ("USDJPY=X", "USD/JPY"), ("GBPUSD=X", "GBP/USD"),
    ("DX-Y.NYB", "Dollar Index"), ("AUDUSD=X", "AUD/USD"), ("USDCHF=X", "USD/CHF"),
]

# Country ETFs used as proxies for a per-country heatmap (mirrors the
# gmdmarkets.com-style mosaic: symbol, display name, region).
COUNTRY_HEATMAP = [
    ("SPY", "United States", "Americas"), ("EWC", "Canada", "Americas"),
    ("EWZ", "Brazil", "Americas"), ("EWW", "Mexico", "Americas"),
    ("EWU", "United Kingdom", "Europe"), ("EWQ", "France", "Europe"),
    ("EWG", "Germany", "Europe"), ("EWL", "Switzerland", "Europe"),
    ("EWI", "Italy", "Europe"), ("EWP", "Spain", "Europe"),
    ("MCHI", "China", "Asia-Pacific"), ("EWJ", "Japan", "Asia-Pacific"),
    ("INDA", "India", "Asia-Pacific"), ("EWH", "Hong Kong", "Asia-Pacific"),
    ("EWT", "Taiwan", "Asia-Pacific"), ("EWY", "South Korea", "Asia-Pacific"),
    ("EWA", "Australia", "Asia-Pacific"),
]

# FRED series id -> display name (rates/bonds). Only fetched if FRED_API_KEY is set.
RATES = [
    ("DGS10", "US 10-Year Treasury"), ("DGS2", "US 2-Year Treasury"),
    ("DGS30", "US 30-Year Treasury"), ("FEDFUNDS", "Fed Funds Rate (monthly)"),
    ("DFF", "Fed Funds Rate (daily)"),
]


def fetch_symbol(symbol):
    """Returns price / 1-day change / 7-day change / volume ratio for a yfinance symbol."""
    try:
        t = yf.Ticker(symbol)
        hist = t.history(period="8d", interval="1d")
        if hist.empty or len(hist) < 2:
            print(f"  ! no data for {symbol}", file=sys.stderr)
            return None
        last = float(hist["Close"].iloc[-1])
        prev = float(hist["Close"].iloc[-2])
        week_ago = float(hist["Close"].iloc[0])
        change_pct = (last / prev - 1) * 100 if prev else 0.0
        change_pct_7d = (last / week_ago - 1) * 100 if week_ago else 0.0
        vol = float(hist["Volume"].iloc[-1]) if "Volume" in hist else None
        avg_vol = float(hist["Volume"].mean()) if "Volume" in hist else None
        vol_ratio = (vol / avg_vol) if (vol and avg_vol) else None
        return {
            "price": round(last, 4),
            "change_pct": round(change_pct, 2),
            "change_pct_7d": round(change_pct_7d, 2),
            "volume_ratio": round(vol_ratio, 2) if vol_ratio else None,
        }
    except Exception as e:
        print(f"  ! failed {symbol}: {e}", file=sys.stderr)
        return None


def build_list(items):
    out = []
    for entry in items:
        symbol, name = entry[0], entry[1]
        extra = entry[2:]
        data = fetch_symbol(symbol)
        if data:
            row = {"symbol": symbol, "name": name, **data}
            if extra:
                row["region"] = extra[0]
            out.append(row)
    return out


def fetch_fred_series(series_id):
    if not FRED_API_KEY:
        return None
    try:
        url = (
            "https://api.stlouisfed.org/fred/series/observations"
            f"?series_id={series_id}&api_key={FRED_API_KEY}&file_type=json"
            "&sort_order=desc&limit=2"
        )
        res = requests.get(url, timeout=15)
        res.raise_for_status()
        obs = [o for o in res.json().get("observations", []) if o["value"] not in (".", "")]
        if not obs:
            return None
        latest = float(obs[0]["value"])
        prev = float(obs[1]["value"]) if len(obs) > 1 else latest
        return {"value": latest, "prev": prev, "date": obs[0]["date"]}
    except Exception as e:
        print(f"  ! FRED {series_id} failed: {e}", file=sys.stderr)
        return None


def build_rates():
    if not FRED_API_KEY:
        print("  (FRED_API_KEY not set -- skipping rates section)", file=sys.stderr)
        return []
    out = []
    for series_id, name in RATES:
        d = fetch_fred_series(series_id)
        if d:
            out.append({"series": series_id, "name": name, **d})
    return out


def main():
    print("Fetching indices...")
    indices = build_list(INDICES)
    print("Fetching stocks...")
    stocks = build_list(STOCKS)
    print("Fetching commodities...")
    commodities = build_list(COMMODITIES)
    print("Fetching forex...")
    forex = build_list(FOREX)
    print("Fetching country heatmap...")
    heatmap = build_list(COUNTRY_HEATMAP)
    print("Fetching FRED rates...")
    rates = build_rates()

    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "indices": indices,
        "stocks": stocks,
        "commodities": commodities,
        "forex": forex,
        "rates": rates,
        "heatmap": heatmap,
    }

    with open("data.json", "w") as f:
        json.dump(data, f, indent=2)

    print(
        f"Wrote data.json: {len(indices)} indices, {len(stocks)} stocks, "
        f"{len(commodities)} commodities, {len(forex)} forex, {len(rates)} rates, "
        f"{len(heatmap)} heatmap entries."
    )


if __name__ == "__main__":
    main()
