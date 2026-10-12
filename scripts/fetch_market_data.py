#!/usr/bin/env python3
"""
Descarga datos de mercado (índices, acciones, fondos, materias primas, divisas, tipos de EE. UU.,
un mapa de calor por países/sectores, IPC de España y noticias) y los escribe en data.json (+ meta.json)
en la raíz del repo. Lo ejecuta el workflow de GitHub Actions de forma programada; también se puede
lanzar en local:

    pip install -r requirements.txt
    export FRED_API_KEY=tu_clave   # opcional: necesaria para tipos + IPC
    python scripts/fetch_market_data.py

Qué contiene el feed PÚBLICO (la rama `data` de un repo público; cualquiera puede leerlo):
  * precios y series de mercado de símbolos públicos (índices, acciones, ETF, divisas...);
  * los ISIN y nombres de los fondos que sigues (YOUR_FUNDS) y su NAV exacto de ishares.com;
  * la lista de criptomonedas vigiladas (CRYPTO_WATCHLIST), visible en el código y en las noticias filtradas.
No incluye importes, cantidades, saldos ni operaciones: nada de tu cartera real más allá de qué
fondos y qué cripto sigues. Si eso es un problema, quita esas entradas o no publiques el repo.

Variable de entorno opcional PREV_FEED_PATH (por defecto `prev.json`): feed publicado anteriormente.
Si FRED falla, se reutilizan de él los tipos y el IPC marcados como `stale` (ver reuse_previous_fred).
"""
import json
import math
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import random
import time
import socket
import threading
import requests
import yfinance as yf
import feedparser

# Red de seguridad para cualquier librería que abra sockets sin timeout propio (yfinance...). Las noticias ya no
# dependen de esto: se descargan con requests (timeout + tope de tiempo total) y se pasan a feedparser como bytes.
socket.setdefaulttimeout(25)

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
# Palabras clave de cripto vigilada, para filtrar CRYPTO_NEWS_FEEDS. OJO: esta lista está en el código público y las
# palabras que coinciden con un titular salen en el feed público como `symbol`/`asset_name`.
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
    ("NOVT", "Novanta Inc."), ("AMS.SW", "ams OSRAM AG"),
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

# Los fondos que sigues (fondos indexados clase S, no cotizan en bolsa: yfinance no tiene ticker).
# OJO: el ISIN y el nombre de cada fondo salen tal cual en el feed público (`your_funds` de data.json), así que
# quien lea la rama `data` sabe qué fondos sigues (no importes ni cantidades). El dashboard los empareja por ISIN.
# Scrapeamos el NAV exacto de la clase S directamente de su
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

# FRED series for Spain's consumer price index: Eurostat HICP (IPCA), all items, monthly,
# not seasonally adjusted, "CP0000ESM086NEST" (index 2025=100, history back to 1996).
# Used by the dashboard to show net worth adjusted for inflation ("today's euros") on the
# Overview chart. Only fetched if FRED_API_KEY is set (same secret already used for the
# Rates & Bonds section above).
# NOTE: the previous series (OECD "ESPCPIALLMINMEI") froze in March 2025 because the OECD
# stopped feeding it to FRED, so it silently stopped adjusting anything. This one is
# published by Eurostat ~mid-month for the previous month. HICP differs slightly from INE's
# national IPC (typically a few tenths of a point per year); the dashboard labels it as such.
CPI_SERIES_ID = "CP0000ESM086NEST"
CPI_SOURCE_LABEL = "Eurostat HICP (IPCA) Spain, via FRED"
CPI_STALE_DAYS = 100   # last observation older than this => flagged as stale in the feed


# --- Resilience: transient Yahoo failures (rate limits, timeouts, empty answers) used to turn into
# "no data for X" straight away, and with enough of them the whole publish was blocked by the
# workflow's sanity gate. Each history call now retries with jittered exponential backoff, but a
# circuit breaker stops retrying once Yahoo is clearly down (so the 20-minute job timeout holds).
_YF_FAIL_STREAK = 0
# Presupuesto de tiempo global (el job tiene timeout de 20 min): pasado este límite se deja de llamar a Yahoo y
# se falla limpio (sanity-check) en vez de morir por timeout a mitad de publicación.
_DEADLINE = time.monotonic() + 14 * 60
# Presupuesto propio de las noticias (secundarias): main() lo fija al empezarlas como min(_DEADLINE, ahora + 5 min).
# Pasado ese límite parse_feed() devuelve [] sin tocar la red.
NEWS_BUDGET_SECONDS = 5 * 60
_NEWS_DEADLINE = None
# Un precio cuya última barra tiene más de esta antigüedad (días naturales) se publica marcado `stale` (cubre fin de semana
# largo + festivo; un valor deslistado o congelado en Yahoo deja de pasar por "de hoy").
STALE_BAR_DAYS = 7
# Una fila arrastrada del feed anterior (el símbolo falló hoy) se deja de arrastrar pasados estos días: mejor que desaparezca
# (y lo detecte el sanity-check) que enseñar un precio de hace semanas como si fuera vivo.
STALE_CARRY_MAX_DAYS = 30
# Fundamentales de Yahoo reutilizados del feed anterior (Yahoo falla): se conservan como mucho este tiempo.
PROFILE_MAX_REUSE_DAYS = 60
# Tope por feed: tiempo total (un servidor que gotea bytes no puede alargarlo) y tamaño.
FEED_MAX_SECONDS = 20
FEED_MAX_BYTES = 3_000_000


def _drop_phantom_bars(hist, max_dev=0.15):
    """Yahoo a veces devuelve una barra 'fantasma' (volumen 0, O=H=L=C) con un salto >15 % respecto a AMBOS vecinos
    (p. ej. SWDA.L 2025-10-24: +34 %/-24 %). Contamina volatilidad y gráficos; se descarta. La primera/última barra solo
    tiene un vecino: se compara con ese."""
    try:
        if hist is None or len(hist) < 3 or not {"Open", "High", "Low", "Close", "Volume"}.issubset(hist.columns):
            return hist
        c = hist["Close"].astype(float)
        keep, n = [], len(hist)
        for i in range(n):
            row = hist.iloc[i]
            flat = (float(row["Volume"] or 0) == 0 and float(row["Open"]) == float(row["High"]) == float(row["Low"]) == float(row["Close"]))
            if flat:
                # Interior bars are compared with BOTH neighbours; the first/last bar with the single one it has (a phantom
                # LAST bar would otherwise become "today's price"). A real >15 % day never has volume 0 and O=H=L=C.
                nbrs = [float(c.iloc[j]) for j in (i - 1, i + 1) if 0 <= j < n]
                if nbrs and all(a and abs(float(c.iloc[i]) / a - 1) > max_dev for a in nbrs):
                    keep.append(False)
                    continue
            keep.append(True)
        return hist[keep] if not all(keep) else hist
    except Exception:
        return hist


def yf_history(symbol, tries=3, **kw):
    """yf.Ticker(symbol).history(**kw) with retries on exceptions AND on empty results.
    Returns a DataFrame (possibly empty); raises only if every attempt raised."""
    global _YF_FAIL_STREAK
    if time.monotonic() > _DEADLINE:
        raise TimeoutError('presupuesto de tiempo de fetch agotado')
    last_exc, hist = None, None
    attempts = 1 if _YF_FAIL_STREAK >= 15 else tries
    for i in range(attempts):
        try:
            hist = yf.Ticker(symbol).history(**kw)
            hist = _drop_phantom_bars(hist.dropna(subset=["Close"]))
            if not hist.empty:
                _YF_FAIL_STREAK = 0
                return hist
        except Exception as e:  # network, rate limit, parsing...
            last_exc = e
        if i < attempts - 1:
            time.sleep(random.uniform(0.5, 1.0 * 2 ** (i + 1)))
    _YF_FAIL_STREAK += 1
    if last_exc is not None and hist is None:
        raise last_exc
    return hist


def fetch_symbol(symbol):
    """Returns price / 1-day change / 7-day change / volume ratio for a yfinance symbol."""
    try:
        hist = yf_history(symbol, period="8d", interval="1d")
        hist = hist.dropna(subset=["Close"])  # a NaN last close would become null in the feed and break the charts
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
        out = {
            "price": round(last, 4),
            "change_pct": round(change_pct, 2),
            "change_pct_7d": round(change_pct_7d, 2),
            "volume_ratio": round(vol_ratio, 2) if vol_ratio else None,
            "sparkline": [round(float(c), 4) for c in hist["Close"].tolist()],
        }
        try:  # fecha de la última barra: permite detectar precios congelados / símbolos deslistados
            as_of = hist.index[-1].strftime("%Y-%m-%d")
            out["as_of"] = as_of
            age = (datetime.now(timezone.utc).date() - datetime.strptime(as_of, "%Y-%m-%d").date()).days
            if age > STALE_BAR_DAYS:
                out["stale"] = True
                out["stale_since"] = as_of
                print(f"  ! {symbol}: la última barra es del {as_of} ({age} días): se publica marcada como antigua", file=sys.stderr)
        except Exception:
            pass
        return out
    except Exception as e:
        print(f"  ! failed {symbol}: {e}", file=sys.stderr)
        return None


def _carry_previous_row(symbol, name, extra, old, today=None):
    """Fila del feed anterior para un símbolo que hoy ha fallado, marcada `stale` (+ `stale_since`). None si no hay fila
    anterior utilizable o lleva más de STALE_CARRY_MAX_DAYS arrastrándose. Antes el símbolo simplemente desaparecía del
    feed: el dashboard perdía la acción (y sus posiciones/Explorar) sin ningún aviso."""
    if not isinstance(old, dict) or not isinstance(old.get("price"), (int, float)) or isinstance(old.get("price"), bool):
        return None
    today = today or datetime.now(timezone.utc).date()
    since = str(old.get("stale_since") or old.get("as_of") or today.isoformat())[:10]
    try:
        if (today - datetime.strptime(since, "%Y-%m-%d").date()).days > STALE_CARRY_MAX_DAYS:
            return None
    except ValueError:
        since = today.isoformat()
    row = dict(old)
    row["name"] = name
    if extra:
        row["region"] = extra[0]
    row["stale"] = True
    row["stale_since"] = since
    row.pop("intraday", None)  # el intradía de ayer engañaría en el gráfico 1D
    return row


def build_list(items, prev_rows=None):
    prev_by = {r.get("symbol"): r for r in (prev_rows or []) if isinstance(r, dict)}
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
        else:
            row = _carry_previous_row(symbol, name, extra, prev_by.get(symbol))
            if row:
                print(f"  ! {symbol}: sin datos hoy, se conserva la fila del feed anterior marcada como antigua (desde {row['stale_since']})", file=sys.stderr)
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
    via downsample_history() before being returned -- see that function's docstring.

    Each point also carries open/high/low/volume alongside close (added so the
    dashboard's chart tooltip can show a real OHLC breakdown instead of just the
    close) -- purely additive, so older code that only reads `close` is unaffected.
    Volume is occasionally NaN for some symbols/venues; left as-is and cleaned up by
    sanitize_for_json() below rather than special-cased here."""
    try:
        hist = yf_history(symbol, period=period, interval="1d")
        hist = hist.dropna(subset=["Close"])
        if hist.empty:
            return []
        points = [
            {
                "date": idx.strftime("%Y-%m-%d"),
                "close": round(float(row["Close"]), 4),
                "open": round(float(row["Open"]), 4),
                "high": round(float(row["High"]), 4),
                "low": round(float(row["Low"]), 4),
                "volume": float(row["Volume"]) if row.get("Volume") is not None else None,
            }
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
        hist = yf_history(symbol, tries=2, period="2d", interval="5m", prepost=True)
        hist = hist.dropna(subset=["Close"])
        if hist.empty:
            return []
        return [
            {"time": idx.strftime("%Y-%m-%d %H:%M"), "close": round(float(row["Close"]), 4)}
            for idx, row in hist.iterrows()
        ]
    except Exception as e:
        print(f"  ! intraday history failed for {symbol}: {e}", file=sys.stderr)
        return []


def attach_histories(items, prev_rows=None):
    """Attaches full ('max') daily history + intraday 5-min bars to any list of items
    that have a 'symbol' key (stocks, indices, forex, commodities, funds).
    If Yahoo returns no daily history for a symbol today, the previous feed's history is reused (flagged
    `history_stale`) instead of publishing an empty chart: it only lacks the latest days. Intraday is never reused
    (yesterday's 5-minute bars would pass for today's session)."""
    prev_by = {r.get("symbol"): r for r in (prev_rows or []) if isinstance(r, dict)}
    for s in items:
        h = fetch_full_history(s["symbol"])
        if not h:
            old_h = (prev_by.get(s["symbol"]) or {}).get("history")
            if isinstance(old_h, list) and old_h:
                h = old_h
                s["history_stale"] = True
                print(f"  ! {s['symbol']}: sin histórico hoy, se reutiliza el del feed anterior (history_stale)", file=sys.stderr)
        s["history"] = h
        s["intraday"] = fetch_intraday_history(s["symbol"])
    return items


PROFILE_FIELDS = ("sector", "industry", "employees", "market_cap", "enterprise_value", "cap_currency",
                  "pe_trailing", "pe_forward", "ev_ebitda", "profit_margin", "operating_margin", "beta",
                  "insiders_pct", "dividend_yield", "dividend_rate", "dividend_5y_avg",
                  "ebitda", "quarterly_earnings_growth", "total_cash", "total_debt", "financial_currency")


def parse_stock_profile(info):
    """Pure parser of yfinance's Ticker.info (the data behind Yahoo's Profile / Statistics pages).
    Returns {sector, industry, employees, market_cap, enterprise_value, cap_currency} with ONLY the fields that
    are present and plausible (a missing/garbage field is simply omitted, never null-filled), or {} if info is
    not a dict. Market cap and EV keep Yahoo's own figures (the dashboard formats them like Yahoo: 3.21T, 52.37B)."""
    out = {}
    if not isinstance(info, dict):
        return out

    def _num(v, positive=True):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            return None
        if positive and v <= 0:
            return None
        return v

    for key, src in (("sector", "sector"), ("industry", "industry")):
        v = info.get(src)
        if isinstance(v, str) and v.strip() and len(v.strip()) <= 80:
            out[key] = v.strip()
    emp = _num(info.get("fullTimeEmployees"))
    if emp is not None and emp < 5_000_000:
        out["employees"] = int(emp)
    cap = _num(info.get("marketCap"))
    if cap is not None:
        out["market_cap"] = int(cap)
        ev = _num(info.get("enterpriseValue"), positive=False)  # a net-cash giant can have a (rare) negative EV
        if ev is not None and ev != 0:
            out["enterprise_value"] = int(ev)
    cur = info.get("currency")
    if "market_cap" in out and isinstance(cur, str) and re.fullmatch(r"[A-Za-z]{3}", cur.strip()):
        out["cap_currency"] = cur.strip().upper()

    # --- valuation / profitability / risk / ownership (Yahoo "Statistics" page). Ratios are published as Yahoo
    # shows them: multiples as plain numbers, margins / insiders / yields as PERCENT (27.04 = 27.04 %).
    # Loss-making companies have no P/E or EV/EBITDA on Yahoo: those fields are simply omitted (the dashboard shows "—").
    for key, src in (("pe_trailing", "trailingPE"), ("pe_forward", "forwardPE"), ("ev_ebitda", "enterpriseToEbitda")):
        v = _num(info.get(src))  # "Infinity" strings (a known Yahoo quirk) and non-positive values are dropped
        if v is not None and v < 10000:
            out[key] = round(float(v), 2)
    for key, src in (("profit_margin", "profitMargins"), ("operating_margin", "operatingMargins")):
        v = _num(info.get(src), positive=False)  # a fraction: 0.27 -> 27 %; negative margins are legitimate
        if v is not None and -50 <= v <= 5:
            out[key] = round(float(v) * 100, 2)
    b = _num(info.get("beta"), positive=False)
    if b is not None and abs(b) < 20:
        out["beta"] = round(float(b), 2)
    ins = _num(info.get("heldPercentInsiders"), positive=False)  # fraction 0-1
    if ins is not None and 0 <= ins <= 1:
        out["insiders_pct"] = round(float(ins) * 100, 2)

    # Income statement / balance sheet (Yahoo "Financial Highlights"). These are expressed in the company's REPORTING
    # currency (`financialCurrency`, e.g. TWD for TSM, EUR for ASML), which can differ from the quote currency, so it is
    # published next to them. EBITDA may be negative (legitimate); cash and debt are >= 0.
    eb = _num(info.get("ebitda"), positive=False)
    if eb is not None and eb != 0:
        out["ebitda"] = int(eb)
    qg = _num(info.get("earningsQuarterlyGrowth"), positive=False)  # fraction: 0.12 -> +12 % year over year
    if qg is not None and -10 <= qg <= 100:
        out["quarterly_earnings_growth"] = round(float(qg) * 100, 2)
    for key, src in (("total_cash", "totalCash"), ("total_debt", "totalDebt")):
        v = _num(info.get(src), positive=False)
        if v is not None and v >= 0:
            out[key] = int(v)
    fcur = info.get("financialCurrency")
    if any(k in out for k in ("ebitda", "total_cash", "total_debt")) and isinstance(fcur, str) and re.fullmatch(r"[A-Za-z]{3}", fcur.strip()):
        out["financial_currency"] = fcur.strip().upper()

    # Dividends. Yahoo/yfinance have changed the scale of `dividendYield` (fraction vs percent) in the past, so the
    # current yield is derived from the annual rate and the price first (scale-proof), then from the trailing
    # fraction, and only then from `dividendYield` itself.
    rate = _num(info.get("dividendRate"))
    price = _num(info.get("currentPrice")) or _num(info.get("regularMarketPrice")) or _num(info.get("previousClose"))
    cur_yield = None
    if rate is not None and price is not None:
        cur_yield = rate / price * 100
    if cur_yield is None:
        tay = _num(info.get("trailingAnnualDividendYield"))
        if tay is not None:
            cur_yield = tay * 100
    if cur_yield is None:
        dy = _num(info.get("dividendYield"))
        if dy is not None:
            cur_yield = dy * 100 if dy <= 0.02 else dy
    if cur_yield is not None and 0 < cur_yield <= 30:
        out["dividend_yield"] = round(float(cur_yield), 2)
        if rate is not None:
            out["dividend_rate"] = round(float(rate), 4)
    elif "market_cap" in out and cur_yield is None:
        out["dividend_yield"] = 0.0  # Yahoo omits every dividend key for non-payers
    avg5 = _num(info.get("fiveYearAvgDividendYield"))  # already a percent on Yahoo (0.52 = 0.52 %)
    if avg5 is not None and avg5 <= 30:
        out["dividend_5y_avg"] = round(float(avg5), 2)
    return out


# Yahoo's own "Valuation Measures" table (Statistics page). yfinance >= 1.7 exposes it as Ticker.valuation / get_valuation_measures():
# it comes from the fundamentals-timeseries API and its "Current" column is exactly what the website shows. `Ticker.info`
# (quoteSummary) is NOT the same source: for ADRs it returns an Enterprise Value in the wrong currency (ASML 37.98T, TSM 16.4T
# vs. the 660B / 1.93T the website shows) and P/E / market cap computed at a different moment.
_VALUATION_LABELS = (("market_cap", "Market Cap"), ("enterprise_value", "Enterprise Value"), ("pe_trailing", "Trailing P/E"),
                     ("pe_forward", "Forward P/E"), ("ev_ebitda", "Enterprise Value/EBITDA"))


def parse_valuation_measures(df):
    """Pure parser of Ticker.get_valuation_measures(): the "Current" column -> {market_cap, enterprise_value, pe_trailing,
    pe_forward, ev_ebitda} with ONLY the plausible values present. {} when the table is missing/empty or has no market cap
    (then the caller keeps what `info` said). Never raises."""
    out = {}
    try:
        if df is None or getattr(df, "empty", True) or "Current" not in df.columns:
            return out
        col = df["Current"]

        def num(label):
            if label not in df.index:
                return None
            try:
                v = float(col[label])
            except (TypeError, ValueError):
                return None
            return v if math.isfinite(v) else None

        mc = num("Market Cap")
        if mc is None or mc <= 0:
            return out
        out["market_cap"] = int(mc)
        ev = num("Enterprise Value")
        if ev is not None and ev != 0:
            out["enterprise_value"] = int(ev)
        for key, label in (("pe_trailing", "Trailing P/E"), ("pe_forward", "Forward P/E"), ("ev_ebitda", "Enterprise Value/EBITDA")):
            v = num(label)
            if v is not None and 0 < v < 10000:
                out[key] = round(v, 2)
    except Exception:
        return {}
    return out


def merge_valuation(prof, val):
    """Valuation-table figures take precedence over `info` for the five measures Yahoo shows in that table. A measure the table
    lacks (loss-makers have no P/E) is removed so the dashboard shows "—" like Yahoo, instead of a stale `info` figure.
    Without a usable table (val empty) the `info` profile is returned untouched."""
    if not val or "market_cap" not in val:
        return prof
    merged = dict(prof)
    for key, _ in _VALUATION_LABELS:
        if key in val:
            merged[key] = val[key]
        else:
            merged.pop(key, None)
    return merged


def fetch_stock_profile(symbol):
    """Yahoo: Ticker.info (profile, margins, balance sheet...) + the Valuation Measures table (market cap, EV, P/Es, EV/EBITDA)
    -> parse + merge. {} on total failure (caller falls back to the previous feed). The valuation table failing alone is not
    fatal: `info` figures are used and the `_valuation` marker says so (popped by attach_profiles, logged, never published)."""
    if time.monotonic() > _DEADLINE:
        return {}
    for i in range(2):
        try:
            t = yf.Ticker(symbol)
            prof = parse_stock_profile(t.info)
            if prof:
                val = {}
                try:
                    val = parse_valuation_measures(t.get_valuation_measures(freq="trailing", periods=0))
                except Exception as e:
                    print(f"  ! valuation table failed for {symbol}: {type(e).__name__}: {e}", file=sys.stderr)
                if val:
                    old_ev, old_mc = prof.get("enterprise_value"), prof.get("market_cap")
                    new_ev, new_mc = val.get("enterprise_value"), val.get("market_cap")
                    if old_ev and new_ev and not (0.8 < old_ev / new_ev < 1.25):
                        print(f"  ~ {symbol}: EV de info ({old_ev:,}) != tabla de valoración ({new_ev:,}): se usa la tabla (la que muestra Yahoo)", file=sys.stderr)
                    if old_mc and new_mc and not (0.9 < old_mc / new_mc < 1.1):
                        print(f"  ~ {symbol}: capitalización de info ({old_mc:,}) != tabla de valoración ({new_mc:,}): se usa la tabla", file=sys.stderr)
                prof = merge_valuation(prof, val)
                prof["_valuation"] = bool(val)
                return prof
        except Exception as e:
            print(f"  ! profile attempt {i + 1} failed for {symbol}: {type(e).__name__}: {e}", file=sys.stderr)
        if i == 0:
            time.sleep(random.uniform(0.5, 1.5))
    return {}


_PROFILE_CORE = tuple(k for k in PROFILE_FIELDS if k not in ("cap_currency", "financial_currency"))


def _profile_degraded(new, old):
    """True when a fresh profile has lost more than half of the metrics the previous one had (Yahoo sometimes answers a
    throttled/partial `info`: keeping it would silently erase good data). Only judged when the previous profile was rich."""
    n_old = sum(1 for k in _PROFILE_CORE if k in old)
    n_new = sum(1 for k in _PROFILE_CORE if k in new)
    return n_old >= 8 and n_new < 0.5 * n_old


def _profile_warnings(symbol, new, old):
    """Plausibility notes for the log (never blocks): a market cap that moved >60 % between two runs, or an EV that is
    not coherent with the market cap (Yahoo's currency mix-ups on ADRs)."""
    msgs = []
    try:
        mc, ev = new.get("market_cap"), new.get("enterprise_value")
        if mc and ev and not (0.25 < ev / mc < 4):
            msgs.append(f"EV/MarketCap = {ev / mc:.1f}x: EV y EV/EBITDA no son fiables (el dashboard los oculta)")
        omc = (old or {}).get("market_cap")
        if mc and omc and not (0.625 < mc / omc < 1.6):
            msgs.append(f"capitalización {mc / omc - 1:+.0%} respecto al perfil anterior: comprueba que no es un error de Yahoo")
    except Exception:
        pass
    for m in msgs:
        print(f"  ! {symbol}: {m}", file=sys.stderr)
    return msgs


def attach_profiles(stocks, prev=None, fetch=fetch_stock_profile, max_fail_streak=5):
    """Adds sector / industry / employees / market cap / EV (+ `profile_as_of`) to every stock. These move slowly:
    when Yahoo fails for a symbol, the previous feed's values are kept (with their OLD `profile_as_of`) instead of
    blanking the dashboard. After `max_fail_streak` consecutive failures Yahoo is skipped for the rest of the run
    (rate limit): everything else still falls back to the previous values. Never raises."""
    prev_by_sym = {s.get("symbol"): s for s in ((prev or {}).get("stocks") or []) if isinstance(s, dict)}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    streak, fresh, kept, val_ok = 0, 0, 0, 0
    for s in stocks:
        prof = {}
        old = prev_by_sym.get(s.get("symbol")) or {}
        if streak < max_fail_streak:
            try:
                prof = fetch(s["symbol"])
            except Exception as e:
                print(f"  ! profile failed for {s.get('symbol')}: {e}", file=sys.stderr)
            if prof and _profile_degraded(prof, old):
                print(f"  ! {s.get('symbol')}: Yahoo devolvió un perfil incompleto ({sum(1 for k in _PROFILE_CORE if k in prof)} de "
                      f"{sum(1 for k in _PROFILE_CORE if k in old)} métricas): se descarta y se conserva el anterior", file=sys.stderr)
                prof = {}
            streak = 0 if prof else streak + 1
            time.sleep(0.25)
        if prof:
            if prof.pop("_valuation", None):
                val_ok += 1
            _profile_warnings(s.get("symbol"), prof, old)
            s.update(prof)
            s["profile_as_of"] = today
            fresh += 1
            continue
        got = {k: old[k] for k in PROFILE_FIELDS if k in old}
        if got:
            try:  # unos fundamentales de hace meses no se presentan como vigentes: pasado el tope se omiten
                age = (datetime.now(timezone.utc).date() - datetime.strptime(str(old.get("profile_as_of"))[:10], "%Y-%m-%d").date()).days
            except ValueError:
                age = None
            if age is None or age > PROFILE_MAX_REUSE_DAYS:
                print(f"  ! {s.get('symbol')}: el perfil anterior tiene {age if age is not None else '?'} días: no se reutiliza", file=sys.stderr)
                continue
            s.update(got)
            if isinstance(old.get("profile_as_of"), str):
                s["profile_as_of"] = old["profile_as_of"]
            kept += 1
    print(f"  profiles: {fresh} fresh, {kept} reused from the previous feed, {len(stocks) - fresh - kept} without data")
    print(f"  valuation table (Yahoo's Statistics page: market cap, EV, P/E, EV/EBITDA): {val_ok} of {fresh} fresh profiles" + ("" if val_ok == fresh else "  <-- the rest use Ticker.info figures, which can be wrong for ADRs"))
    # Per-field coverage, so the log shows at a glance whether Yahoo is returning each metric (a field that drops to 0
    # across the board means Yahoo renamed it). Missing values are normal for banks / loss-makers / foreign listings.
    cov = ", ".join(f"{k} {sum(1 for s in stocks if k in s)}" for k in PROFILE_FIELDS if k not in ("cap_currency", "financial_currency"))
    print(f"  profile coverage (of {len(stocks)} stocks): {cov}")
    return stocks


def _fred_get(params, tries=3):
    """GET a FRED observations con reintentos (5xx/429/timeout transitorios). Lanza la última excepción si todos fallan."""
    last = None
    for i in range(tries):
        try:
            res = requests.get("https://api.stlouisfed.org/fred/series/observations", params=params, timeout=15)
            if res.status_code in (429, 500, 502, 503, 504) and i < tries - 1:
                time.sleep(2 * (i + 1)); continue
            res.raise_for_status()
            return res
        except requests.RequestException as e:
            last = e
            if i < tries - 1:
                time.sleep(2 * (i + 1))
    raise last


def fetch_fred_series(series_id):
    if not FRED_API_KEY:
        return None
    try:
        res = _fred_get({"series_id": series_id, "api_key": FRED_API_KEY, "file_type": "json", "sort_order": "desc", "limit": 10})
        obs = [o for o in res.json().get("observations", []) if o["value"] not in (".", "")]
        if not obs:
            return None
        latest = float(obs[0]["value"])
        prev = float(obs[1]["value"]) if len(obs) > 1 else latest
        return {"value": latest, "prev": prev, "date": obs[0]["date"]}
    except Exception as e:
        print(f"  ! FRED {series_id} failed: {type(e).__name__}", file=sys.stderr)  # sin {e}: la URL de la excepción lleva la api_key
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


def fetch_cpi_history():
    """Full monthly history of Spain's HICP index (FRED series CP0000ESM086NEST, base
    2025=100), used to show net worth in inflation-adjusted ("today's euros") terms on the
    dashboard's Overview chart. Only fetched if FRED_API_KEY is set -- returns ([], meta)
    otherwise, same graceful-degradation pattern as the Rates & Bonds section above.

    Returns (points, meta). meta = {source, series, last_date, stale} so the dashboard can
    tell the user when the data stops being fresh instead of silently using an old value."""
    meta = {"source": CPI_SOURCE_LABEL, "series": CPI_SERIES_ID, "last_date": None, "stale": True}
    if not FRED_API_KEY:
        print("  (FRED_API_KEY not set -- skipping CPI/inflation section)", file=sys.stderr)
        return [], meta
    try:
        res = _fred_get({"series_id": CPI_SERIES_ID, "api_key": FRED_API_KEY, "file_type": "json", "sort_order": "asc"})
        obs = res.json().get("observations", [])
        pts = []
        for o in obs:
            v = _parse_num(str(o.get("value") or ""))
            # plausibility: a consumer price index level; discards "." and junk
            if v is None or not (10 < v < 1000):
                continue
            pts.append({"date": o["date"], "value": v})
        if pts:
            meta["last_date"] = pts[-1]["date"]
            try:
                age = (datetime.now(timezone.utc).date() - datetime.strptime(pts[-1]["date"], "%Y-%m-%d").date()).days
                meta["stale"] = age > CPI_STALE_DAYS
                if meta["stale"]:
                    print(f"  ! CPI series looks STALE: last observation {pts[-1]['date']} ({age} days old)", file=sys.stderr)
            except Exception:
                pass
        return pts, meta
    except Exception as e:
        print(f"  ! CPI history fetch failed: {type(e).__name__}", file=sys.stderr)  # sin {e}: la URL lleva la api_key
        return [], meta


def _download_feed(url, stop):
    """Descarga con requests (timeout=(5, 10)) con tope de tamaño. `stop` (threading.Event) corta el bucle si el
    llamador ya se ha rendido."""
    headers = {"User-Agent": getattr(feedparser, "USER_AGENT", "feedparser"),  # el mismo UA que usaba feedparser.parse(url)
               "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.1"}
    with requests.get(url, headers=headers, timeout=(5, 10), stream=True) as res:
        res.raise_for_status()
        chunks, size = [], 0
        for chunk in res.iter_content(chunk_size=16384):
            if stop.is_set():
                raise TimeoutError("descarga abandonada")
            chunks.append(chunk)
            size += len(chunk)
            if size > FEED_MAX_BYTES:
                raise ValueError("feed demasiado grande")
        return b"".join(chunks)


def _fetch_feed_bytes(url):
    """Descarga un feed RSS con un tope de tiempo TOTAL (FEED_MAX_SECONDS) y de tamaño (FEED_MAX_BYTES).
    El timeout de requests es por operación de socket: un servidor que gotea bytes lo esquiva (y iter_content
    espera a llenar el chunk), así que la descarga corre en un hilo daemon al que se espera como máximo
    FEED_MAX_SECONDS; si no termina, se abandona (el hilo muere solo al cortar el socket o al acabar el proceso).
    Lanza una excepción si falla o se pasa de tiempo/tamaño."""
    box, stop = {}, threading.Event()

    def work():
        try:
            box["data"] = _download_feed(url, stop)
        except Exception as e:  # noqa: BLE001 - se relanza en el hilo principal
            box["err"] = e

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(FEED_MAX_SECONDS)
    if t.is_alive():
        stop.set()
        raise TimeoutError("feed demasiado lento (>%d s)" % FEED_MAX_SECONDS)
    if "err" in box:
        raise box["err"]
    return box["data"]


def _load_prev_feed():
    """Feed publicado anteriormente (PREV_FEED_PATH, por defecto prev.json). {} si no existe o no es un JSON válido."""
    path = os.environ.get("PREV_FEED_PATH", "prev.json")
    try:
        with open(path, encoding="utf-8") as f:
            prev = json.load(f)
        return prev if isinstance(prev, dict) else {}
    except Exception:
        return {}


def reuse_previous_fred(rates, cpi, cpi_meta, prev):
    """
    Si FRED falla (clave caducada, caída, serie cambiada) NO se bloquea la publicación de los precios de bolsa:
    se reutilizan del feed anterior los tipos que falten y, si no hay IPC, el IPC y su cpi_meta, todo marcado
    `stale: true` (+ `stale_since`: desde cuándo no se refresca; se conserva entre reutilizaciones sucesivas).
    Sin feed anterior no hay nada que reutilizar y todo queda como estaba (el sanity-check exige lo mínimo).
    Solo se llama con FRED_API_KEY definida. Devuelve (rates, cpi, cpi_meta).
    """
    if not isinstance(prev, dict):
        return rates, cpi, cpi_meta
    since_default = str(prev.get("generated_at", ""))[:10] or None

    # Tipos: por serie, conservando el orden de RATES.
    try:
        have = {r.get("series") for r in rates}
        prev_rates = {r.get("series"): r for r in prev.get("rates", []) if isinstance(r, dict)}
        merged = []
        for series_id, _name in RATES:
            if series_id in have:
                merged.extend(r for r in rates if r.get("series") == series_id)
            elif isinstance(prev_rates.get(series_id), dict) and isinstance(prev_rates[series_id].get("value"), (int, float)):
                r = dict(prev_rates[series_id])
                r["stale"] = True
                r["stale_since"] = r.get("stale_since") or since_default
                merged.append(r)
                print(f"  ! FRED {series_id}: se reutiliza el valor del feed anterior (stale)", file=sys.stderr)
        rates = merged
    except Exception as e:
        print(f"  ! no se pudieron reutilizar los tipos anteriores: {type(e).__name__}", file=sys.stderr)

    # IPC: solo si la descarga nueva ha salido vacía.
    try:
        if not cpi:
            pts = [p for p in prev.get("cpi", []) if isinstance(p, dict) and isinstance(p.get("value"), (int, float)) and p.get("date")]
            if len(pts) >= 12:
                pm = prev.get("cpi_meta") if isinstance(prev.get("cpi_meta"), dict) else {}
                cpi = pts
                cpi_meta = dict(cpi_meta)
                cpi_meta.update({k: pm[k] for k in ("source", "series") if k in pm})
                cpi_meta["last_date"] = pts[-1]["date"]
                cpi_meta["stale"] = True
                cpi_meta["reused_from_previous"] = True
                cpi_meta["stale_since"] = pm.get("stale_since") or since_default
                print("  ! IPC de FRED no disponible: se reutiliza el del feed anterior (stale)", file=sys.stderr)
    except Exception as e:
        print(f"  ! no se pudo reutilizar el IPC anterior: {type(e).__name__}", file=sys.stderr)
    return rates, cpi, cpi_meta


def parse_feed(url, limit=8):
    # Presupuesto global: pasado _NEWS_DEADLINE (o _DEADLINE) no se hace ninguna petición más.
    if time.monotonic() > (_NEWS_DEADLINE or _DEADLINE):
        print(f"  ! presupuesto de tiempo agotado, se omite el feed {url}", file=sys.stderr)
        return []
    try:
        parsed = feedparser.parse(_fetch_feed_bytes(url))
        items = []
        for entry in parsed.entries[:limit]:
            link = str(entry.get("link", "") or "")
            if not link.lower().startswith(("http://", "https://")):
                continue  # nunca publicar javascript:/data: etc. (el dashboard ya lo filtra; defensa en profundidad)
            items.append({
                "title": re.sub(r"<[^>]*>", "", str(entry.get("title", "") or "")).strip()[:300],
                "link": link[:1000],
                "source": str(parsed.feed.get("title", url))[:120],
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


def _parse_num(txt):
    """'1,234.56' -> 1234.56 ; '12,3000' (decimal comma) -> 12.3 ; '12.30' -> 12.3. None if ambiguous/invalid."""
    t = txt.strip()
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", t):
        return float(t.replace(",", ""))
    if re.fullmatch(r"\d+,\d+", t):
        return float(t.replace(",", "."))
    if re.fullmatch(r"\d+(\.\d+)?", t):
        return float(t)
    return None


_MESES = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
NAV_STALE_DAYS = 5          # un NAV con más antigüedad se marca nav_stale (fin de semana + festivo caben de sobra)
CHANGE_PCT_MAX = 20.0       # una variación diaria de un fondo indexado mayor que esto es un parseo erróneo


def _parse_signed(txt):
    """Como _parse_num pero acepta signo inicial: '-0.85' -> -0.85. None si no es un número limpio."""
    t = txt.strip()
    sign = -1.0 if t.startswith("-") else 1.0
    v = _parse_num(t.lstrip("+-"))
    return None if v is None else sign * v


def _parse_nav_date(txt):
    """'03/Oct/2026' -> date(2026, 10, 3) sin depender del locale (strptime %b lo hace). None si no se entiende."""
    m = re.fullmatch(r"(\d{1,2})/([A-Za-z]{3})/(\d{4})", txt.strip())
    if not m or m.group(2).lower() not in _MESES:
        return None
    try:
        return datetime(int(m.group(3)), _MESES[m.group(2).lower()], int(m.group(1))).date()
    except ValueError:
        return None


def parse_ishares_nav(plain, isin, today=None):
    """
    Extrae el NAV del texto plano (sin etiquetas) de la página de producto de iShares.
    Devuelve un dict {price, nav_date, [nav_stale], [change_pct], [wk52_low, wk52_high]} o None si no hay
    un NAV fiable (patrón ausente, ISIN ausente o precio inverosímil). Los campos secundarios
    (variación, rango 52 semanas) se parsean cada uno en su propio try: si uno viene malformado
    se descarta ese campo, pero NO se pierde el NAV válido.
    """
    nav_match = re.search(r'NAV as of\s+([\d/A-Za-z]+)\s+EUR\s*([\d.,]+)', plain)
    if not (nav_match and isin in plain):
        print(f"  ! could not find expected NAV pattern for {isin} -- falling back to proxy", file=sys.stderr)
        return None
    price = _parse_num(nav_match.group(2))
    # plausibility: a mis-parsed number (decimal comma read as thousands, 0.0001...) must not reach the dashboard
    if price is None or not (0.1 < price < 10000):
        print(f"  ! implausible NAV {nav_match.group(2)!r} for {isin} -- falling back to proxy", file=sys.stderr)
        return None
    result = {"price": round(price, 4), "nav_date": nav_match.group(1)}

    # Antigüedad del NAV: si iShares congela la página, el NAV de hace días no debe pasar por "de hoy".
    try:
        nav_day = _parse_nav_date(nav_match.group(1))
        if nav_day is not None:
            today = today or datetime.now(timezone.utc).date()
            result["nav_stale"] = (today - nav_day).days > NAV_STALE_DAYS
            if result["nav_stale"]:
                print(f"  ! NAV de {isin} con fecha {nav_match.group(1)} (> {NAV_STALE_DAYS} días)", file=sys.stderr)
    except Exception as e:
        print(f"  ! nav_date ilegible para {isin}: {e}", file=sys.stderr)

    try:
        cm = re.search(r'1 Day NAV Change.*?EUR\s*(-?[\d.,]+)\s*\(?\s*(-?[\d.,]+)%\s*\)?', plain)
        if cm:
            chg = _parse_signed(cm.group(2))
            if chg is not None and abs(chg) <= CHANGE_PCT_MAX:
                result["change_pct"] = chg
            else:
                print(f"  ! variación diaria descartada para {isin}: {cm.group(2)!r}", file=sys.stderr)
    except Exception as e:
        print(f"  ! variación diaria no parseable para {isin}: {e}", file=sys.stderr)

    try:
        wm = re.search(r'52\s*WK:\s*([\d.,]+)\s*-\s*([\d.,]+)', plain)
        if wm:
            lo, hi = _parse_num(wm.group(1)), _parse_num(wm.group(2))
            if lo is not None and hi is not None and 0 < lo <= hi and lo * 0.8 <= price <= hi * 1.2:
                result["wk52_low"], result["wk52_high"] = lo, hi
            else:
                print(f"  ! rango 52 semanas descartado para {isin}: {wm.group(0)!r}", file=sys.stderr)
    except Exception as e:
        print(f"  ! rango 52 semanas no parseable para {isin}: {e}", file=sys.stderr)
    return result


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
        # Strip tags to plain text so we don't depend on exact HTML structure/classes.
        plain = re.sub(r'<[^>]+>', ' ', res.text)
        plain = re.sub(r'\s+', ' ', plain)
        return parse_ishares_nav(plain, isin)
    except Exception as e:
        print(f"  ! ishares scrape failed for {isin}: {e} -- falling back to proxy", file=sys.stderr)
        return None


# La unidad viene en cualquier capitalización ("EUR 1.20 B", "USD 29.5 bn", "EUR 820.50 M"): antes el regex solo aceptaba
# minúsculas, no capturaba la unidad y _fmt_aum truncaba a "EUR 1" / "EUR 820" (orden de magnitud falso).
_AUM_RE = r'([A-Z]{3})\s*([\d,]+(?:\.\d+)?)\s*((?i:bn|mn|mm|k|m|b))?\b'
_AUM_U = {"k": "k", "m": "m", "mn": "m", "mm": "m", "bn": "bn", "b": "bn"}


def _fmt_aum(m):
    """'EUR', '809.12', 'M' -> 'EUR 809.12 m'; sin unidad se agrupa el número ('EUR 1,234,567'); sin unidad y < 1000 es ambiguo -> None."""
    ccy, num, unit = m.group(1), m.group(2), (m.group(3) or "").lower()
    if unit:
        return f"{ccy} {num} {_AUM_U[unit]}"
    try:
        v = float(num.replace(',', ''))
    except ValueError:
        return None
    return f"{ccy} {v:,.0f}" if v >= 1000 else None


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

        # number WITH decimals and its unit (k / m / bn): "EUR 809.12 m", not "EUR 809"
        m = re.search(r'Net Assets\s+as of\s+[\d/A-Za-z]+\s+' + _AUM_RE, plain)
        if m:
            _v = _fmt_aum(m)
            if _v: facts["class_aum"] = _v

        m = re.search(r'Net Assets of Fund\s+as of\s+[\d/A-Za-z]+\s+' + _AUM_RE, plain)
        if m:
            _v = _fmt_aum(m)
            if _v: facts["fund_aum"] = _v

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
        hist = yf_history(proxy_symbol, period="10y", interval="1d")
        hist = hist.dropna(subset=["Close"])
        if hist.empty or len(hist) < 30:
            return {}
        dates = [d.date() for d in hist.index]
        closes = [float(c) for c in hist["Close"].tolist()]

        annual_returns = {}
        for y in sorted(set(d.year for d in dates)):
            idxs = [i for i, d in enumerate(dates) if d.year == y]
            prev = [i for i, d in enumerate(dates) if d.year == y - 1]
            if not idxs or not prev:
                continue  # sin cierre del año anterior (primer año parcial) no hay rentabilidad anual real
            annual_returns[str(y)] = round((closes[idxs[-1]] / closes[prev[-1]] - 1) * 100, 2)

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
            # The history is the PROXY ETF's series (other currency / scale). Rescale it so its last close equals the
            # scraped NAV: the dashboard labels it EUR and compares it with the NAV, so without this a what-if on the fund
            # read -69 %. The shape (returns) is unchanged.
            try:
                hh = entry["history"]
                if hh and scraped.get("price") and hh[-1].get("close"):
                    k = float(scraped["price"]) / float(hh[-1]["close"])
                    for p_ in hh:
                        for f_ in ("close", "open", "high", "low"):
                            if p_.get(f_) is not None:
                                p_[f_] = round(p_[f_] * k, 4)
                    entry["history_scaled"] = True
            except Exception:
                pass
            entry.update({
                "price": scraped["price"],
                "change_pct": scraped.get("change_pct"),
                "nav_date": scraped.get("nav_date"),
                "nav_stale": scraped.get("nav_stale"),
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


# ---------------------------------------------------------------------------
# Altcoin Season Index: % of the top-N altcoins that beat Bitcoin over the last 90 days.
# Same rule as blockchaincenter.net: stablecoins and wrapped / staked / asset-backed tokens
# are NOT altcoins for this purpose (they track something else, so counting them skews it).
# Computed here (not in the browser) because CoinGecko's API has no 90-day change field:
# `price_change_percentage` only accepts 1h/24h/7d/14d/30d/200d/1y, so the old client-side
# calculation always came back empty. The coin list + market caps come from CoinGecko
# (1 + a few category calls, no key), the 90-day prices from Yahoo (`SYM-USD`), which the
# rest of this script already uses.
# ---------------------------------------------------------------------------
ALT_WINDOW_DAYS = 90
ALT_TOP_N = 50            # altcoins counted (BTC is the yardstick, not part of the count)
ALT_MIN_COINS = 30        # fewer usable coins than this => no index (better "—" than a noisy number)
ALT_MAX_TRIES = 90        # cap on Yahoo lookups (each failed ticker costs a request)
ALT_PRICE_TOLERANCE = 0.15  # Yahoo's last close must be within 15 % of CoinGecko's price, else the ticker is a different coin
ALT_EXCLUDE_CATEGORIES = ("stablecoins", "wrapped-tokens", "liquid-staking-tokens", "tokenized-gold")
_ALT_EXCLUDE_SYMBOLS = {
    # stablecoins
    "USDT", "USDC", "DAI", "USDE", "USDS", "FDUSD", "PYUSD", "TUSD", "USD1", "USDD", "USDG", "RLUSD", "FRAX",
    "GUSD", "USDP", "LUSD", "USDTB", "BUIDL", "USYC", "BFUSD", "USDF", "USDY", "EURC", "EURS", "CRVUSD", "GHO",
    "SUSDS", "SUSDE", "USDC.E", "BSC-USD", "USDX", "USDT0", "USD0",
    # wrapped / staked / bridged / asset-backed
    "WBTC", "WETH", "STETH", "WSTETH", "WEETH", "CBBTC", "CBETH", "RETH", "WBETH", "BTCB", "EETH", "LBTC",
    "SOLVBTC", "XAUT", "PAXG", "JITOSOL", "MSOL", "BNSOL", "WSOL", "METH", "OSETH", "ETHX", "SWETH", "TBTC",
}
_ALT_EXCLUDE_NAME_RE = re.compile(r"\b(wrapped|staked|bridged|restaked)\b|stablecoin", re.I)
_CG_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; personal-portfolio-dashboard/1.0)", "Accept": "application/json"}


def _cg_markets(params, tries=3):
    """GET CoinGecko /coins/markets (public, no key) with retries on 429/5xx. Returns the JSON list or raises."""
    last = None
    for i in range(tries):
        try:
            res = requests.get("https://api.coingecko.com/api/v3/coins/markets", params=params,
                               headers=_CG_HEADERS, timeout=20)
            if res.status_code in (429, 500, 502, 503, 504) and i < tries - 1:
                time.sleep(8 * (i + 1)); continue
            res.raise_for_status()
            data = res.json()
            if not isinstance(data, list):
                raise ValueError("respuesta inesperada de CoinGecko")
            return data
        except (requests.RequestException, ValueError) as e:
            last = e
            if i < tries - 1:
                time.sleep(4 * (i + 1))
    raise last


def _alt_excluded_ids():
    """Ids of coins CoinGecko files under stablecoins / wrapped / liquid-staking / tokenized-gold. Best-effort:
    a category call that fails just contributes nothing (the static symbol list still covers the big ones)."""
    ids = set()
    for cat in ALT_EXCLUDE_CATEGORIES:
        try:
            rows = _cg_markets({"vs_currency": "usd", "category": cat, "order": "market_cap_desc", "per_page": 250, "page": 1})
            ids.update(str(r.get("id")) for r in rows if isinstance(r, dict) and r.get("id"))
        except Exception as e:
            print(f"  ! altcoin season: categoría {cat} no disponible ({type(e).__name__})", file=sys.stderr)
        time.sleep(2.5)  # sin clave el límite de CoinGecko es bajo: no se le martillea
    return ids


def _alt_is_excluded(coin, excluded_ids):
    sym = str(coin.get("symbol") or "").upper()
    return (str(coin.get("id")) in excluded_ids or sym in _ALT_EXCLUDE_SYMBOLS
            or bool(_ALT_EXCLUDE_NAME_RE.search(str(coin.get("name") or ""))))


def _alt_change_pct(symbol, cg_price=None, window=ALT_WINDOW_DAYS):
    """% change of `symbol`-USD over `window` days from Yahoo daily closes, or None when it can't be trusted:
    no data, listed less than `window` days ago, or the Yahoo ticker is a different coin than CoinGecko's
    (last close further than ALT_PRICE_TOLERANCE from CoinGecko's current price)."""
    hist = yf_history(f"{symbol.upper()}-USD", tries=1, period="130d", interval="1d")
    hist = hist.dropna(subset=["Close"]) if hist is not None else None
    if hist is None or hist.empty or len(hist) < window // 2:
        return None
    last = float(hist["Close"].iloc[-1])
    if last <= 0:
        return None
    if cg_price:
        ratio = last / float(cg_price)
        if not (1 - ALT_PRICE_TOLERANCE <= ratio <= 1 + ALT_PRICE_TOLERANCE):
            return None
    cutoff = hist.index[-1] - timedelta(days=window)
    ref = hist[hist.index <= cutoff]
    if ref.empty:
        return None
    if (cutoff - ref.index[-1]).days > 5:  # the nearest bar is too far from the cutoff: gap in the data
        return None
    base = float(ref["Close"].iloc[-1])
    return (last / base - 1) * 100 if base > 0 else None


def compute_altcoin_season(coins, excluded_ids, change_fn=_alt_change_pct):
    """coins: CoinGecko /coins/markets rows (market-cap order). Returns the index dict or None.
    Bitcoin is the yardstick; the first ALT_TOP_N non-excluded coins with a trustworthy 90-day change are counted
    (a coin whose Yahoo ticker doesn't check out is skipped and the next one takes its place)."""
    btc = next((c for c in coins if str(c.get("id")) == "bitcoin"), None)
    if not btc:
        return None
    btc_change = change_fn("BTC", btc.get("current_price"))
    if btc_change is None:
        return None
    changes, tries = [], 0
    for c in coins:
        if len(changes) >= ALT_TOP_N or tries >= ALT_MAX_TRIES:
            break
        if str(c.get("id")) == "bitcoin" or _alt_is_excluded(c, excluded_ids):
            continue
        sym = str(c.get("symbol") or "").strip()
        if not sym:
            continue
        tries += 1
        try:
            ch = change_fn(sym, c.get("current_price"))
        except TimeoutError:
            break  # presupuesto de tiempo agotado: con lo que haya
        except Exception:
            ch = None
        if ch is not None and math.isfinite(ch):
            changes.append(ch)
    if len(changes) < ALT_MIN_COINS:
        print(f"  ! altcoin season: solo {len(changes)} monedas utilizables (mínimo {ALT_MIN_COINS}); no se publica", file=sys.stderr)
        return None
    beating = sum(1 for ch in changes if ch > btc_change)
    return {
        "value": int(round(beating / len(changes) * 100)),
        "outperforming": beating,
        "total": len(changes),
        "window_days": ALT_WINDOW_DAYS,
        "btc_change_pct": round(btc_change, 1),
        "source": "coingecko+yahoo",
    }


def build_altcoin_season(prev=None):
    """Altcoin Season Index for the feed, or None. If today's calculation fails and the previous feed has one, that
    one is reused flagged `stale` (it moves slowly; better than blanking the card)."""
    global _YF_FAIL_STREAK
    streak = _YF_FAIL_STREAK  # many unmapped tickers would trip the Yahoo circuit breaker for the rest of the run
    try:
        coins = _cg_markets({"vs_currency": "usd", "order": "market_cap_desc", "per_page": 100, "page": 1})
        time.sleep(2.5)
        result = compute_altcoin_season(coins, _alt_excluded_ids())
        if result:
            return result
    except Exception as e:
        print(f"  ! altcoin season failed: {type(e).__name__}: {e}", file=sys.stderr)
    finally:
        _YF_FAIL_STREAK = streak
    try:
        old = (prev or {}).get("market_mood", {}).get("altcoin_season")
        if isinstance(old, dict) and isinstance(old.get("value"), (int, float)):
            old = dict(old)
            old["stale"] = True
            old["stale_since"] = old.get("stale_since") or (str((prev or {}).get("generated_at", ""))[:10] or None)
            print("  ! altcoin season: se reutiliza el valor del feed anterior (stale)", file=sys.stderr)
            return old
    except Exception:
        pass
    return None


def cnn_put_call_ratio(block):
    """CNN's `put_call_options` block carries a 0-100 score (what `value` holds) AND a daily series whose `y` is the
    actual put/call ratio (~0.5-1.5). Returns the latest ratio, or None if the series is missing/implausible
    (undocumented endpoint: best-effort, never raises)."""
    try:
        y = float(block["data"][-1]["y"])
        return round(y, 3) if 0 < y < 5 else None
    except Exception:
        return None


LIDO_API = "https://eth-api.lido.fi/v1/protocol/steth/apr"
_STAKING_HEADERS = {"User-Agent": "personal-portfolio-dashboard/1.0"}


def parse_lido_apr(last_json, sma_json):
    """Lido's /apr/last and /apr/sma responses -> feed dict, or None if `last` is missing/implausible.
    `apr` is Lido's own figure (linear annualisation of the latest oracle rebase), in %; the SMA is the 7-day mean.
    Pure function (no network) so it can be tested; never raises."""
    try:
        d = last_json["data"]
        apr = float(d["apr"])
        if not (0 < apr < 50):
            return None
        out = {"apr": round(apr, 3), "as_of": int(d["timeUnix"]), "source": "lido"}
    except Exception:
        return None
    try:
        sma = float(sma_json["data"]["smaApr"])
        if 0 < sma < 50:
            out["apr_sma7"] = round(sma, 3)
    except Exception:
        pass
    return out


def fetch_lido_apr():
    """Keyless public API. Returns the parsed dict or None (the caller handles the stale fallback)."""
    last = requests.get(f"{LIDO_API}/last", headers=_STAKING_HEADERS, timeout=15)
    last.raise_for_status()
    sma_json = None
    try:
        sma = requests.get(f"{LIDO_API}/sma", headers=_STAKING_HEADERS, timeout=15)
        sma.raise_for_status()
        sma_json = sma.json()
    except Exception as e:
        print(f"  ! lido apr/sma failed (se sigue solo con apr/last): {e}", file=sys.stderr)
    parsed = parse_lido_apr(last.json(), sma_json)
    if not parsed:
        print("  ! lido apr: respuesta inesperada o fuera de rango", file=sys.stderr)
    return parsed


# Staking rates published in the public feed (rates only: NEVER amounts or balances), keyed by the name the
# dashboard looks up (see STAKING_FEED in portfolio.html). To add another coin with a keyless public API: write a
# `fetch_xxx()` returning {"apr": <percent>, "as_of": <unix>, "source": "<name>", "apr_sma7": <optional>} or None,
# register it here, and map the coin's CoinGecko id to the key in the dashboard. Coins without a public source
# are handled in the dashboard with a manual APR instead.
STAKING_PROVIDERS = {
    "eth_lido": fetch_lido_apr,
}


def build_staking(prev=None):
    """`staking` section of the feed: {key: rate dict}. A provider that fails keeps the previous feed's value
    flagged `stale` (it moves slowly; better than blanking the panel); with no previous value it is omitted."""
    staking = {}
    for key, fetch in STAKING_PROVIDERS.items():
        try:
            parsed = fetch()
            if parsed:
                staking[key] = parsed
        except Exception as e:
            print(f"  ! staking {key} failed: {type(e).__name__}: {e}", file=sys.stderr)
        if key in staking:
            continue
        try:
            old = ((prev or {}).get("staking") or {}).get(key)
            if isinstance(old, dict) and isinstance(old.get("apr"), (int, float)):
                old = dict(old)
                old["stale"] = True
                old["stale_since"] = old.get("stale_since") or (str((prev or {}).get("generated_at", ""))[:10] or None)
                staking[key] = old
                print(f"  ! staking {key}: se reutiliza el valor del feed anterior (stale)", file=sys.stderr)
        except Exception:
            pass
    return staking


def build_market_mood():
    """VIX (volatility) + its 50-day moving average via yfinance, the crypto Fear & Greed
    Index via alternative.me (free, public, no key), and CNN's stock-market Fear & Greed
    Index (including its sub-components: momentum, breadth/strength, put/call, junk bond
    and safe-haven demand) via CNN's own public dataviz endpoint that powers their site's
    widget. That last one is undocumented/unofficial -- best-effort, and simply omitted
    if CNN ever changes it, same as everything else scraped in this script."""
    mood = {}
    try:
        hist = yf_history("^VIX", period="3mo", interval="1d")
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
        try:
            mood["crypto_fear_greed"]["timestamp"] = int(d["timestamp"])  # unix, 00:00 UTC del día de la lectura (alternative.me)
        except (KeyError, TypeError, ValueError):
            pass
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
        if pc:
            ratio = cnn_put_call_ratio(d.get("put_call_options"))
            if ratio is not None:
                pc["ratio"] = ratio  # the real put/call ratio; `value` is CNN's 0-100 score for that component
            mood["put_call"] = pc
        junk = sub("junk_bond_demand")
        if junk: mood["junk_bond_demand"] = junk
        safe = sub("safe_haven_demand")
        if safe: mood["safe_haven_demand"] = safe
    except Exception as e:
        print(f"  ! CNN fear & greed fetch failed (unofficial endpoint, best-effort): {e}", file=sys.stderr)

    alt = build_altcoin_season(_load_prev_feed())
    if alt:
        mood["altcoin_season"] = alt

    return mood


def build_benchmarks():
    """90-day daily closes for S&P 500 and an MSCI World proxy, for portfolio comparison charts."""
    benchmarks = {}
    for key, symbol in [("sp500", "^GSPC"), ("msci_world", "URTH")]:
        try:
            hist = yf_history(symbol, period="90d", interval="1d").dropna(subset=["Close"])
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


def compute_health(data, today=None):
    """Resumen de fiabilidad del feed, para el log y para el dashboard (`health` en data.json): qué símbolos llevan precio
    antiguo (Yahoo falló hoy y se conserva la fila anterior, o su última barra es vieja), qué históricos se han reutilizado
    y cuántos perfiles de fundamentales están al día. Pura (no toca la red) y nunca lanza."""
    today = today or datetime.now(timezone.utc).date()
    data = data if isinstance(data, dict) else {}
    stale, hist_reused = [], []
    try:
        for key in ("indices", "stocks", "funds", "commodities", "forex", "heatmap", "sector_heatmap"):
            for r in (data.get(key) or []):
                if not isinstance(r, dict):
                    continue
                if r.get("stale"):
                    stale.append(str(r.get("symbol")))
                if r.get("history_stale"):
                    hist_reused.append(str(r.get("symbol")))
    except Exception:
        pass
    ages = []
    with_profile = 0
    stocks = [r for r in (data.get("stocks") if isinstance(data.get("stocks"), list) else []) if isinstance(r, dict)]
    for r in stocks:
        if "market_cap" in r or "pe_forward" in r:
            with_profile += 1
        try:
            ages.append((today - datetime.strptime(str(r.get("profile_as_of"))[:10], "%Y-%m-%d").date()).days)
        except ValueError:
            pass
    return {
        "stale_symbols": sorted(set(stale))[:40],
        "history_reused": sorted(set(hist_reused))[:40],
        "profiles": {"stocks": len(stocks), "with_data": with_profile,
                     "oldest_days": max(ages) if ages else None,
                     "older_than_7d": sum(1 for a in ages if a > 7)},
    }


def main():
    prev = _load_prev_feed()
    print("Fetching indices...")
    indices = build_list(INDICES, prev.get("indices"))
    print("Fetching full ('max') + intraday history for each index...")
    indices = attach_histories(indices, prev.get("indices"))
    print("Fetching stocks...")
    stocks = build_list(STOCKS, prev.get("stocks"))
    print("Fetching full ('max') + intraday history for each stock (for the detail chart)...")
    stocks = attach_histories(stocks, prev.get("stocks"))
    print("Fetching company profile + market cap / EV for each stock...")
    stocks = attach_profiles(stocks, prev)
    print("Fetching funds...")
    funds = build_list(FUNDS, prev.get("funds"))
    print("Fetching full ('max') + intraday history for each fund (Explore detail chart)...")
    funds = attach_histories(funds, prev.get("funds"))
    print("Fetching your actual fund holdings (NAV scrape + fallback)...")
    your_funds = build_your_funds()
    print("Fetching commodities...")
    commodities = build_list(COMMODITIES, prev.get("commodities"))
    print("Fetching full ('max') + intraday history for each commodity...")
    commodities = attach_histories(commodities, prev.get("commodities"))
    print("Fetching forex...")
    forex = build_list(FOREX, prev.get("forex"))
    print("Fetching full ('max') + intraday history for each forex pair...")
    forex = attach_histories(forex, prev.get("forex"))
    print("Fetching country heatmap...")
    heatmap = build_list(COUNTRY_HEATMAP, prev.get("heatmap"))
    print("Fetching sector heatmap...")
    sector_heatmap = build_list(SECTOR_HEATMAP, prev.get("sector_heatmap"))
    print("Fetching FRED rates...")
    rates = build_rates()
    print("Fetching Spain CPI (inflation) history...")
    cpi, cpi_meta = fetch_cpi_history()
    if FRED_API_KEY:
        rates, cpi, cpi_meta = reuse_previous_fred(rates, cpi, cpi_meta, prev)
    global _NEWS_DEADLINE
    _NEWS_DEADLINE = min(_DEADLINE, time.monotonic() + NEWS_BUDGET_SECONDS)
    print("Fetching general news...")
    general_news = build_general_news()
    print("Fetching position news...")
    position_news = build_position_news()
    print("Fetching benchmark history...")
    benchmarks = build_benchmarks()
    print("Fetching market mood (VIX, Fear & Greed)...")
    market_mood = build_market_mood()
    print("Fetching staking rates (Lido)...")
    staking = build_staking(prev)

    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "indices": indices,
        "stocks": stocks,
        "funds": funds,
        "your_funds": your_funds,
        "commodities": commodities,
        "forex": forex,
        "rates": rates,
        "cpi": cpi,
        "cpi_meta": cpi_meta,
        "heatmap": heatmap,
        "sector_heatmap": sector_heatmap,
        "news": {
            "general": general_news,
            "positions": position_news,
        },
        "benchmarks": benchmarks,
        "market_mood": market_mood,
        "staking": staking,
    }
    data["health"] = compute_health(data)
    h = data["health"]
    print(f"HEALTH: {len(h['stale_symbols'])} símbolos con precio antiguo {h['stale_symbols'] or ''}; "
          f"{len(h['history_reused'])} con histórico reutilizado {h['history_reused'] or ''}; "
          f"perfiles: {h['profiles']['with_data']}/{h['profiles']['stocks']} con datos, el más antiguo de {h['profiles']['oldest_days']} días, "
          f"{h['profiles']['older_than_7d']} con más de 7 días")
    data = sanitize_for_json(data)

    with open("data.json", "w") as f:
        # Minified (no indent): with "max"-period history for ~40 symbols this is the
        # difference between a file jsDelivr will serve and one it silently rejects for
        # being over its 20MB cap. Paste into any JSON formatter if you need to read it
        # by eye -- see downsample_history()'s docstring for the other half of the fix.
        json.dump(data, f, separators=(",", ":"), allow_nan=False)

    # Tiny sidecar the dashboard polls every few minutes instead of re-downloading the
    # whole (~10MB, ~2MB gzipped) data.json: it only fetches the big file when
    # `generated_at` here differs from the one it already has. Written AFTER data.json
    # so a crash halfway can never advertise a data.json that doesn't exist yet.
    data_bytes = os.path.getsize("data.json")
    with open("meta.json", "w") as f:
        json.dump({"generated_at": data["generated_at"], "data_bytes": data_bytes, "schema": 1}, f, separators=(",", ":"))

    print(
        f"Wrote data.json: {len(indices)} indices, {len(stocks)} stocks, {len(funds)} funds, "
        f"{len(your_funds)} of your own funds ({sum(1 for f in your_funds if f['source']=='ishares_nav_scrape')} scraped exact, "
        f"{sum(1 for f in your_funds if f['source']=='proxy_approx')} fell back to proxy approx), "
        f"{len(commodities)} commodities, {len(forex)} forex, {len(rates)} rates, {len(cpi)} CPI points, "
        f"{len(heatmap)} heatmap entries, {len(general_news)} general news, "
        f"{len(position_news)} position news, "
        f"{len(benchmarks.get('sp500',[]))} sp500 / {len(benchmarks.get('msci_world',[]))} msci_world benchmark points."
    )


if __name__ == "__main__":
    main()
