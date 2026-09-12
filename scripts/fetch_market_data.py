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
import re
import sys
from datetime import datetime, timezone

import requests
import yfinance as yf
import feedparser

FRED_API_KEY = os.environ.get("FRED_API_KEY", "")

# ---------------------------------------------------------------------------
# News sources -- all free RSS feeds, no API key needed.
# ---------------------------------------------------------------------------
GENERAL_NEWS_FEEDS = [
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC&region=US&lang=en-US",
    "https://www.marketwatch.com/rss/topstories",
    "https://www.cnbc.com/id/20910258/device/rss/rss.html",  # CNBC Markets
    "https://www.investing.com/rss/news_25.rss",  # Investing.com economic news
]
CRYPTO_NEWS_FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
]
# Watched crypto keywords, used to filter CRYPTO_NEWS_FEEDS into position-relevant items.
CRYPTO_WATCHLIST = ["bitcoin", "btc", "ethereum", "eth", "chainlink", "link",
                     "polkadot", "dot", "mina", "rocket pool", "rpl", "tezos", "xtz", "unibright"]

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
    # Explore tab universe -- keep in sync with EXPLORE_UNIVERSE in the dashboard HTML
    ("COHR", "Coherent Corp."), ("LITE", "Lumentum Holdings"), ("IPGP", "IPG Photonics"),
    ("NOVT", "Novanta Inc."), ("AMS", "ams OSRAM AG"),
    ("ASML", "ASML Holding"), ("TSM", "Taiwan Semiconductor"), ("AMD", "Advanced Micro Devices"),
    ("LMT", "Lockheed Martin"), ("RTX", "RTX Corporation"), ("NOC", "Northrop Grumman"), ("BA", "Boeing Co."),
    ("PLTR", "Palantir Technologies"), ("CRWD", "CrowdStrike Holdings"),
    ("NEE", "NextEra Energy"), ("ENPH", "Enphase Energy"), ("ORSTED.CO", "Ørsted A/S"),
    ("NVO", "Novo Nordisk"),
]

# UCITS index funds -- Yahoo Finance tickers (not ISINs; yfinance needs an exchange ticker).
FUNDS = [
    ("SWDA.L", "iShares Core MSCI World UCITS ETF"),
    ("EIMI.L", "iShares Core MSCI EM IMI UCITS ETF"),
    ("IMEU.L", "iShares Core MSCI Europe UCITS ETF"),
    ("SGLN.L", "iShares Physical Gold ETC"),
]

# Your two actual MyInvestor holdings (Class S index mutual funds -- not exchange-traded,
# so yfinance has no ticker for them). We scrape the exact Class S NAV directly off their
# ishares.com product page (public, no login, no API -- just the same page a human would
# read). If the page layout ever changes and scraping fails, we fall back automatically to
# a proxy ETF that tracks the same index, applying its % change to your last known NAV.
YOUR_FUNDS = [
    {
        "isin": "IE000ZYRH0Q7",
        "name": "iShares Developed World Index (IE) Acc EUR — Class S",
        "product_url": "https://www.ishares.com/ch/individual/en/products/229050/ishares-developed-world-index-fund-ie",
        "proxy_symbol": "SWDA.L",
    },
    {
        "isin": "IE000QAZP7L2",
        "name": "iShares Emerging Markets Index Fund (IE) Acc EUR — Class S",
        "product_url": "https://www.ishares.com/ch/individual/en/products/345276/ishares-emerging-markets-index-fund-ie",
        "proxy_symbol": "EIMI.L",
    },
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
            "sparkline": [round(float(c), 4) for c in hist["Close"].tolist()],
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


def parse_feed(url, limit=8):
    try:
        parsed = feedparser.parse(url)
        items = []
        for entry in parsed.entries[:limit]:
            items.append({
                "title": entry.get("title", "").strip(),
                "link": entry.get("link", ""),
                "source": parsed.feed.get("title", url),
                "published": entry.get("published", entry.get("updated", "")),
            })
        return items
    except Exception as e:
        print(f"  ! failed feed {url}: {e}", file=sys.stderr)
        return []


def build_general_news():
    """Top general market/finance headlines from a few free RSS feeds."""
    items = []
    for url in GENERAL_NEWS_FEEDS:
        items.extend(parse_feed(url, limit=8))
    seen = set()
    deduped = []
    for it in items:
        key = it["title"].lower()
        if key and key not in seen:
            seen.add(key)
            deduped.append(it)
    return deduped[:20]


def build_position_news():
    """
    Headlines relevant to the tracked stocks/funds (via per-ticker Yahoo Finance RSS)
    and tracked cryptocurrencies (via keyword-filtering general crypto news feeds).
    """
    items = []
    watched_symbols = STOCKS + FUNDS
    for symbol, name in watched_symbols:
        url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
        entries = parse_feed(url, limit=2)
        for e in entries:
            e["symbol"] = symbol
            e["asset_name"] = name
        items.extend(entries)

    crypto_items = []
    for url in CRYPTO_NEWS_FEEDS:
        crypto_items.extend(parse_feed(url, limit=15))
    for it in crypto_items:
        title_lower = it["title"].lower()
        matched = [kw for kw in CRYPTO_WATCHLIST if re.search(r'\b' + re.escape(kw) + r'\b', title_lower)]
        if matched:
            it["symbol"] = matched[0].upper()
            it["asset_name"] = matched[0].title()
            items.append(it)

    seen = set()
    deduped = []
    for it in items:
        key = it["title"].lower()
        if key and key not in seen:
            seen.add(key)
            deduped.append(it)
    return deduped[:30]


def fetch_ishares_nav(product_url, isin):
    """
    Scrapes the exact Class S NAV straight off the fund's public ishares.com product
    page. Returns None (never raises) if the page layout doesn't match what we expect,
    so callers can fall back to the proxy-ETF approximation instead of crashing.
    """
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; personal-portfolio-dashboard/1.0)"}
        res = requests.get(product_url, headers=headers, timeout=20)
        res.raise_for_status()
        text = res.text
        plain = re.sub(r'<[^>]+>', ' ', text)
        plain = re.sub(r'\s+', ' ', plain)

        nav_match = re.search(r'NAV as of\s+([\d/A-Za-z]+)\s+EUR\s*([\d.,]+)', plain)
        change_match = re.search(r'1 Day NAV Change.*?EUR\s*(-?[\d.,]+)\s*\(?\s*(-?[\d.,]+)%\s*\)?', plain)
        wk_match = re.search(r'52\s*WK:\s*([\d.,]+)\s*-\s*([\d.,]+)', plain)
        isin_present = isin in plain

        if not (nav_match and isin_present):
            print(f"  ! could not find expected NAV pattern for {isin} -- falling back to proxy", file=sys.stderr)
            return None

        price = float(nav_match.group(2).replace(',', ''))
        result = {"price": round(price, 4), "nav_date": nav_match.group(1)}
        if change_match:
            result["change_pct"] = float(change_match.group(2).replace(',', ''))
        if wk_match:
            result["wk52_low"] = float(wk_match.group(1).replace(',', ''))
            result["wk52_high"] = float(wk_match.group(2).replace(',', ''))
        return result
    except Exception as e:
        print(f"  ! ishares scrape failed for {isin}: {e} -- falling back to proxy", file=sys.stderr)
        return None


def build_your_funds():
    """
    Your actual fund holdings. Tries the exact scraped Class S NAV first; if that
    fails for any reason, falls back to applying the proxy ETF's daily % change to
    the last scraped/known NAV, so the dashboard never just breaks silently.
    """
    out = []
    for fund in YOUR_FUNDS:
        scraped = fetch_ishares_nav(fund["product_url"], fund["isin"])
        if scraped:
            out.append({
                "isin": fund["isin"],
                "name": fund["name"],
                "price": scraped["price"],
                "change_pct": scraped.get("change_pct"),
                "nav_date": scraped.get("nav_date"),
                "wk52_low": scraped.get("wk52_low"),
                "wk52_high": scraped.get("wk52_high"),
                "source": "ishares_nav_scrape",
            })
        else:
            proxy_data = fetch_symbol(fund["proxy_symbol"])
            out.append({
                "isin": fund["isin"],
                "name": fund["name"],
                "price": None,
                "change_pct": proxy_data["change_pct"] if proxy_data else None,
                "proxy_symbol": fund["proxy_symbol"],
                "source": "proxy_approx",
            })
    return out


def build_benchmarks():
    """90-day daily closes for S&P 500 and an MSCI World proxy, for portfolio comparison charts."""
    benchmarks = {}
    for key, symbol in [("sp500", "^GSPC"), ("msci_world", "URTH")]:
        try:
            hist = yf.Ticker(symbol).history(period="90d", interval="1d")
            benchmarks[key] = [
                {"date": idx.strftime("%Y-%m-%d"), "close": round(float(row["Close"]), 4)}
                for idx, row in hist.iterrows()
            ]
        except Exception as e:
            print(f"  ! benchmark {symbol} failed: {e}", file=sys.stderr)
            benchmarks[key] = []
    return benchmarks


def main():
    print("Fetching indices...")
    indices = build_list(INDICES)
    print("Fetching stocks...")
    stocks = build_list(STOCKS)
    print("Fetching funds...")
    funds = build_list(FUNDS)
    print("Fetching your actual fund holdings (NAV scrape + fallback)...")
    your_funds = build_your_funds()
    print("Fetching commodities...")
    commodities = build_list(COMMODITIES)
    print("Fetching forex...")
    forex = build_list(FOREX)
    print("Fetching country heatmap...")
    heatmap = build_list(COUNTRY_HEATMAP)
    print("Fetching FRED rates...")
    rates = build_rates()
    print("Fetching general news...")
    general_news = build_general_news()
    print("Fetching position news...")
    position_news = build_position_news()
    print("Fetching benchmark history...")
    benchmarks = build_benchmarks()

    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "indices": indices,
        "stocks": stocks,
        "funds": funds,
        "your_funds": your_funds,
        "commodities": commodities,
        "forex": forex,
        "rates": rates,
        "heatmap": heatmap,
        "news": {
            "general": general_news,
            "positions": position_news,
        },
        "benchmarks": benchmarks,
    }

    with open("data.json", "w") as f:
        json.dump(data, f, indent=2)

    print(
        f"Wrote data.json: {len(indices)} indices, {len(stocks)} stocks, {len(funds)} funds, "
        f"{len(your_funds)} of your own funds ({sum(1 for f in your_funds if f['source']=='ishares_nav_scrape')} scraped exact, "
        f"{sum(1 for f in your_funds if f['source']=='proxy_approx')} fell back to proxy approx), "
        f"{len(commodities)} commodities, {len(forex)} forex, {len(rates)} rates, "
        f"{len(heatmap)} heatmap entries, {len(general_news)} general news, "
        f"{len(position_news)} position news, "
        f"{len(benchmarks.get('sp500',[]))} sp500 / {len(benchmarks.get('msci_world',[]))} msci_world benchmark points."
    )


if __name__ == "__main__":
    main()
