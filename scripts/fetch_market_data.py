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
import math
import os
import re
import sys
from datetime import datetime, timedelta, timezone

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
    ("AMP.MC", "Amper, S.A."), ("NXT.MC", "Nueva Expresión Textil (Nextil)"),
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
        "product_url": "https://www.ishares.com/ch/individual/en/products/345277/ishares-developed-world-index-fund-ie",
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

# SPDR Select Sector ETFs -- the standard 11 GICS sectors (mirrors gmdmarkets.com's
# sector heatmap: symbol, display name, region left blank since it's not geographic).
SECTOR_HEATMAP = [
    ("XLK", "Technology"), ("XLI", "Industrials"), ("XLC", "Communication Services"),
    ("XLY", "Consumer Discretionary"), ("XLRE", "Real Estate"), ("XLF", "Financials"),
    ("XLB", "Materials"), ("XLP", "Consumer Staples"), ("XLE", "Energy"),
    ("XLV", "Health Care"), ("XLU", "Utilities"),
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


def downsample_history(history, daily_years=5):
    """
    Keeps true daily resolution for the most recent `daily_years` years (enough for
    every client-side range up to and including 5Y), and collapses anything older than
    that to one point per calendar month (the last trading day of that month) for the
    ALL/MAX range. A 60-year-old daily point is visually indistinguishable from a
    monthly one on a chart that wide, but costs 20x+ more bytes -- this is what keeps
    data.json under jsDelivr's 20MB hard serving limit as "max" period history
    accumulates decades of data for long-listed symbols (added 14/Sep/2026, see the
    commit message for the size numbers that prompted this).
    """
    if not history:
        return history
    hist = sorted(history, key=lambda h: h["date"])
    cutoff = (datetime.now() - timedelta(days=daily_years * 365)).strftime("%Y-%m-%d")
    older = [h for h in hist if h["date"] < cutoff]
    recent = [h for h in hist if h["date"] >= cutoff]
    monthly = {}
    for h in older:
        monthly[h["date"][:7]] = h  # last date seen per YYYY-MM wins (hist is date-sorted)
    return list(monthly.values()) + recent


def fetch_full_history(symbol, period="max"):
    """Full available daily history for the interactive stock-detail chart (1M/6M/YTD/1Y/
    5Y/ALL are sliced client-side from this single series). "max" -- the same span Yahoo
    Finance's own "Max" button shows -- not a fixed window, so "ALL" is genuinely all of
    it (was capped at 5y, then 10y, both of which could silently equal "5Y"/"10Y" for
    long-listed symbols; "max" has no such ceiling). The raw "max" series is downsampled
    via downsample_history() before being returned -- see that function's docstring."""
    try:
        hist = yf.Ticker(symbol).history(period=period, interval="1d")
        if hist.empty:
            return []
        points = [
            {"date": idx.strftime("%Y-%m-%d"), "close": round(float(row["Close"]), 4)}
            for idx, row in hist.iterrows()
        ]
        return downsample_history(points)
    except Exception as e:
        print(f"  ! full history failed for {symbol}: {e}", file=sys.stderr)
        return []


def fetch_intraday_history(symbol):
    """
    Real intraday bars for the 1D chart (5-minute bars, including pre/post market),
    the same kind of data Yahoo Finance's own 1D chart uses. Yahoo only keeps this
    granularity for a short window, so we ask for the last 2 days and keep whatever
    comes back (usually just the most recent session).
    """
    try:
        hist = yf.Ticker(symbol).history(period="2d", interval="5m", prepost=True)
        if hist.empty:
            return []
        return [
            {"time": idx.strftime("%Y-%m-%d %H:%M"), "close": round(float(row["Close"]), 4)}
            for idx, row in hist.iterrows()
        ]
    except Exception as e:
        print(f"  ! intraday history failed for {symbol}: {e}", file=sys.stderr)
        return []


def attach_histories(items):
    """Attaches full ('max') daily history + intraday 5-min bars to any list of items
    that have a 'symbol' key (stocks, indices, forex, commodities, funds)."""
    for s in items:
        s["history"] = fetch_full_history(s["symbol"])
        s["intraday"] = fetch_intraday_history(s["symbol"])
    return items


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
    # de-dupe by title, keep first 20
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
    # Stocks & funds: Yahoo Finance has a per-symbol RSS feed.
    watched_symbols = STOCKS + FUNDS
    for symbol, name in watched_symbols:
        url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
        entries = parse_feed(url, limit=2)
        for e in entries:
            e["symbol"] = symbol
            e["asset_name"] = name
        items.extend(entries)

    # Crypto: filter general crypto news feeds by keyword match against our watchlist.
    crypto_items = []
    for url in CRYPTO_NEWS_FEEDS:
        crypto_items.extend(parse_feed(url, limit=15))
    for it in crypto_items:
        title_lower = it["title"].lower()
        # Word-boundary match to avoid short tickers (link, dot, eth...) matching inside
        # unrelated words like "linking", "adopt", "weather".
        matched = [kw for kw in CRYPTO_WATCHLIST if re.search(r'\b' + re.escape(kw) + r'\b', title_lower)]
        if matched:
            it["symbol"] = matched[0].upper()
            it["asset_name"] = matched[0].title()
            items.append(it)

    # de-dupe by title, cap at 30
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
        # Strip tags to plain text so we don't depend on exact HTML structure/classes.
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


def fetch_ishares_fund_facts(product_url):
    """
    Scrapes the 'Key Facts' block of the fund's ishares.com product page: management
    company, benchmark, Morningstar category, fund/class size, launch date, and fees.
    Best-effort and resilient -- any field that isn't found (or if the page structure
    changes) is simply left out, never raises.
    """
    facts = {}
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; personal-portfolio-dashboard/1.0)"}
        res = requests.get(product_url, headers=headers, timeout=20)
        res.raise_for_status()
        plain = re.sub(r'<[^>]+>', ' ', res.text)
        plain = re.sub(r'\s+', ' ', plain)

        m = re.search(r'Inception Date\s+([\d/A-Za-z]+)', plain)
        if m: facts["class_inception_date"] = m.group(1)

        m = re.search(r'Fund Launch Date\s+([\d/A-Za-z]+)', plain)
        if m: facts["fund_launch_date"] = m.group(1)

        m = re.search(r'Net Assets\s+as of\s+[\d/A-Za-z]+\s+([A-Z]{3})\s*([\d,]+)', plain)
        if m: facts["class_aum"] = f"{m.group(1)} {int(m.group(2).replace(',', '')):,}"

        m = re.search(r'Net Assets of Fund\s+as of\s+[\d/A-Za-z]+\s+([A-Z]{3})\s*([\d,]+)', plain)
        if m: facts["fund_aum"] = f"{m.group(1)} {int(m.group(2).replace(',', '')):,}"

        m = re.search(r'Benchmark Index\s+([A-Za-z0-9 ,()%.\-]+?)\s+(?:Initial Charge|Management Fee)', plain)
        if m: facts["benchmark"] = m.group(1).strip()

        m = re.search(r'Management Company\s+([A-Za-z0-9 .,()]+?)\s+(?:Dealing Settlement|Bloomberg Ticker)', plain)
        if m: facts["management_company"] = m.group(1).strip()

        m = re.search(r'Morningstar Category\s+([A-Za-z0-9 &\-]+?)\s+Dealing Frequency', plain)
        if m: facts["morningstar_category"] = m.group(1).strip()

        m = re.search(r'Ongoing Charges Figures\s+([\d.]+)\s*%', plain)
        if m: facts["ongoing_charges_pct"] = float(m.group(1))

        m = re.search(r'Management Fee\s+([\d.]+)\s*%', plain)
        if m: facts["management_fee_pct"] = float(m.group(1))

        m = re.search(r'Performance Fee\s+([\d.]+)\s*%', plain)
        if m: facts["performance_fee_pct"] = float(m.group(1))

        m = re.search(r'Initial Charge\s+([\d.]+)\s*%', plain)
        if m: facts["initial_charge_pct"] = float(m.group(1))

    except Exception as e:
        print(f"  ! fund facts scrape failed for {product_url}: {e}", file=sys.stderr)
    return facts


def compute_fund_analytics(proxy_symbol):
    """
    Computes annual returns (per calendar year), period returns (YTD/1Y/2Y/3Y/5Y/10Y),
    and annualized volatility (12m/3y/5y) from the proxy ETF's own long-term daily
    price history. This is an approximation of your actual Class S fund (same index,
    slightly different fee drag), used because the exact fund has no yfinance ticker
    and iShares doesn't publish these tables in scrapable form (their site renders
    them client-side via JS, empty in the raw HTML).
    """
    try:
        hist = yf.Ticker(proxy_symbol).history(period="10y", interval="1d")
        if hist.empty or len(hist) < 30:
            return {}
        dates = [d.date() for d in hist.index]
        closes = [float(c) for c in hist["Close"].tolist()]

        annual_returns = {}
        for y in sorted(set(d.year for d in dates)):
            idxs = [i for i, d in enumerate(dates) if d.year == y]
            if len(idxs) < 2:
                continue
            annual_returns[str(y)] = round((closes[idxs[-1]] / closes[idxs[0]] - 1) * 100, 2)

        last_price = closes[-1]
        last_date = dates[-1]

        def nearest_price_on_or_before(target_date):
            best = None
            for i, d in enumerate(dates):
                if d <= target_date:
                    best = i
                else:
                    break
            return closes[best] if best is not None else None

        period_returns = {}
        from datetime import date as _date
        jan1 = _date(last_date.year, 1, 1)
        p = nearest_price_on_or_before(jan1) or closes[0]
        period_returns["ytd"] = round((last_price / p - 1) * 100, 2)
        for label, n in [("1y", 1), ("2y", 2), ("3y", 3), ("5y", 5), ("10y", 10)]:
            try:
                target = last_date.replace(year=last_date.year - n)
            except ValueError:
                target = last_date.replace(year=last_date.year - n, day=28)
            p = nearest_price_on_or_before(target)
            if p:
                period_returns[label] = round((last_price / p - 1) * 100, 2)

        def volatility_over(days_back):
            sub = closes[-days_back:] if len(closes) > days_back else closes
            if len(sub) < 10:
                return None
            rets = [math.log(sub[i] / sub[i-1]) for i in range(1, len(sub))]
            mean = sum(rets) / len(rets)
            variance = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
            return round((variance ** 0.5) * (252 ** 0.5) * 100, 2)

        volatility = {"12m": volatility_over(252), "3y": volatility_over(252*3), "5y": volatility_over(252*5)}

        return {"annual_returns": annual_returns, "period_returns": period_returns, "volatility": volatility}
    except Exception as e:
        print(f"  ! fund analytics failed for {proxy_symbol}: {e}", file=sys.stderr)
        return {}


def build_your_funds():
    """
    Your actual fund holdings. Tries the exact scraped Class S NAV first; if that
    fails for any reason, falls back to applying the proxy ETF's daily % change to
    the last scraped/known NAV, so the dashboard never just breaks silently. Also
    attaches fund facts (scraped) and computed analytics (annual/period returns,
    volatility -- from the proxy ETF's history, see compute_fund_analytics).
    """
    out = []
    for fund in YOUR_FUNDS:
        scraped = fetch_ishares_nav(fund["product_url"], fund["isin"])
        facts = fetch_ishares_fund_facts(fund["product_url"])
        analytics = compute_fund_analytics(fund["proxy_symbol"])
        entry = {
            "isin": fund["isin"],
            "name": fund["name"],
            "facts": facts,
            "analytics": analytics,
            "history": fetch_full_history(fund["proxy_symbol"]),  # approximate price chart via the proxy ETF
        }
        if scraped:
            entry.update({
                "price": scraped["price"],
                "change_pct": scraped.get("change_pct"),
                "nav_date": scraped.get("nav_date"),
                "wk52_low": scraped.get("wk52_low"),
                "wk52_high": scraped.get("wk52_high"),
                "proxy_symbol": fund["proxy_symbol"],
                "source": "ishares_nav_scrape",
            })
        else:
            # Fallback: approximate using the proxy ETF's daily % change.
            proxy_data = fetch_symbol(fund["proxy_symbol"])
            entry.update({
                "price": None,  # dashboard keeps the user's last manual NAV and applies change_pct itself
                "change_pct": proxy_data["change_pct"] if proxy_data else None,
                "proxy_symbol": fund["proxy_symbol"],
                "source": "proxy_approx",
            })
        out.append(entry)
    return out


def build_market_mood():
    """VIX (volatility) + its 50-day moving average via yfinance, the crypto Fear & Greed
    Index via alternative.me (free, public, no key), and CNN's stock-market Fear & Greed
    Index (including its sub-components: momentum, breadth/strength, put/call, junk bond
    and safe-haven demand) via CNN's own public dataviz endpoint that powers their site's
    widget. That last one is undocumented/unofficial -- best-effort, and simply omitted
    if CNN ever changes it, same as everything else scraped in this script."""
    mood = {}
    try:
        hist = yf.Ticker("^VIX").history(period="3mo", interval="1d")
        if not hist.empty:
            closes = hist["Close"].tolist()
            last = float(closes[-1])
            prev = float(closes[-2]) if len(closes) > 1 else last
            change_pct = (last / prev - 1) * 100 if prev else 0.0
            sma_window = closes[-50:] if len(closes) >= 50 else closes
            sma50 = sum(float(c) for c in sma_window) / len(sma_window)
            mood["vix"] = {"value": round(last, 2), "change_pct": round(change_pct, 2)}
            mood["vix_50ma"] = {"value": round(sma50, 2)}
    except Exception as e:
        print(f"  ! VIX fetch failed: {e}", file=sys.stderr)

    try:
        res = requests.get("https://api.alternative.me/fng/?limit=1", timeout=15)
        res.raise_for_status()
        d = res.json()["data"][0]
        mood["crypto_fear_greed"] = {"value": int(d["value"]), "classification": d["value_classification"]}
    except Exception as e:
        print(f"  ! fear & greed fetch failed: {e}", file=sys.stderr)

    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; personal-portfolio-dashboard/1.0)"}
        res = requests.get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata", headers=headers, timeout=15)
        res.raise_for_status()
        d = res.json()
        def sub(key):
            try:
                v = d[key]
                return {"value": round(float(v.get("score")), 1), "rating": v.get("rating")}
            except Exception:
                return None
        fg = sub("fear_and_greed")
        if fg: mood["stock_fear_greed"] = fg
        mom = sub("market_momentum_sp500")
        if mom: mood["market_momentum"] = mom
        pc = sub("put_call_options")
        if pc: mood["put_call"] = pc
        junk = sub("junk_bond_demand")
        if junk: mood["junk_bond_demand"] = junk
        safe = sub("safe_haven_demand")
        if safe: mood["safe_haven_demand"] = safe
    except Exception as e:
        print(f"  ! CNN fear & greed fetch failed (unofficial endpoint, best-effort): {e}", file=sys.stderr)

    return mood


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


def sanitize_for_json(obj):
    """
    Recursively replaces NaN/Infinity floats with None (JSON null).
    Python's json.dump writes bare NaN/Infinity tokens by default, which is
    NOT valid JSON per spec -- browsers' JSON.parse() rejects it outright,
    silently breaking the whole feed for a single bad value anywhere in it.
    """
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_for_json(v) for v in obj]
    return obj


def main():
    print("Fetching indices...")
    indices = build_list(INDICES)
    print("Fetching full ('max') + intraday history for each index...")
    indices = attach_histories(indices)
    print("Fetching stocks...")
    stocks = build_list(STOCKS)
    print("Fetching full ('max') + intraday history for each stock (for the detail chart)...")
    stocks = attach_histories(stocks)
    print("Fetching funds...")
    funds = build_list(FUNDS)
    print("Fetching full ('max') + intraday history for each fund (Explore detail chart)...")
    funds = attach_histories(funds)
    print("Fetching your actual fund holdings (NAV scrape + fallback)...")
    your_funds = build_your_funds()
    print("Fetching commodities...")
    commodities = build_list(COMMODITIES)
    print("Fetching full ('max') + intraday history for each commodity...")
    commodities = attach_histories(commodities)
    print("Fetching forex...")
    forex = build_list(FOREX)
    print("Fetching full ('max') + intraday history for each forex pair...")
    forex = attach_histories(forex)
    print("Fetching country heatmap...")
    heatmap = build_list(COUNTRY_HEATMAP)
    print("Fetching sector heatmap...")
    sector_heatmap = build_list(SECTOR_HEATMAP)
    print("Fetching FRED rates...")
    rates = build_rates()
    print("Fetching general news...")
    general_news = build_general_news()
    print("Fetching position news...")
    position_news = build_position_news()
    print("Fetching benchmark history...")
    benchmarks = build_benchmarks()
    print("Fetching market mood (VIX, Fear & Greed)...")
    market_mood = build_market_mood()

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
        "sector_heatmap": sector_heatmap,
        "news": {
            "general": general_news,
            "positions": position_news,
        },
        "benchmarks": benchmarks,
        "market_mood": market_mood,
    }
    data = sanitize_for_json(data)

    with open("data.json", "w") as f:
        # Minified (no indent): with "max"-period history for ~40 symbols this is the
        # difference between a file jsDelivr will serve and one it silently rejects for
        # being over its 20MB cap. Paste into any JSON formatter if you need to read it
        # by eye -- see downsample_history()'s docstring for the other half of the fix.
        json.dump(data, f, separators=(",", ":"))

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
