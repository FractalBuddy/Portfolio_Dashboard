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
# Tope por feed: tiempo total (un servidor que gotea bytes no puede alargarlo) y tamaño.
FEED_MAX_SECONDS = 20
FEED_MAX_BYTES = 3_000_000


def _drop_phantom_bars(hist, max_dev=0.15):
    """Yahoo a veces devuelve una barra 'fantasma' (volumen 0, O=H=L=C) con un salto >15 % respecto a AMBOS vecinos
    (p. ej. SWDA.L 2025-10-24: +34 %/-24 %). Contamina volatilidad y gráficos; se descarta (solo barras interiores)."""
    try:
        if hist is None or len(hist) < 3 or not {"Open", "High", "Low", "Close", "Volume"}.issubset(hist.columns):
            return hist
        c = hist["Close"].astype(float)
        keep = []
        for i in range(len(hist)):
            row = hist.iloc[i]
            flat = (float(row["Volume"] or 0) == 0 and float(row["Open"]) == float(row["High"]) == float(row["Low"]) == float(row["Close"]))
            if flat and 0 < i < len(hist) - 1:
                a, b = float(c.iloc[i - 1]), float(c.iloc[i + 1])
                if a and b and abs(float(c.iloc[i]) / a - 1) > max_dev and abs(float(c.iloc[i]) / b - 1) > max_dev:
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


def attach_histories(items):
    """Attaches full ('max') daily history + intraday 5-min bars to any list of items
    that have a 'symbol' key (stocks, indices, forex, commodities, funds)."""
    for s in items:
        s["history"] = fetch_full_history(s["symbol"])
        s["intraday"] = fetch_intraday_history(s["symbol"])
    return items


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
    print("Fetching Spain CPI (inflation) history...")
    cpi, cpi_meta = fetch_cpi_history()
    if FRED_API_KEY:
        rates, cpi, cpi_meta = reuse_previous_fred(rates, cpi, cpi_meta, _load_prev_feed())
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
    }
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
