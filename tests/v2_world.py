"""Utilidades compartidas por los tests del feed v2: un Yahoo simulado, un registro pequeño y atajos para generar y leer sitios."""
import hashlib
import json
import math
import os
import zlib
from datetime import date, datetime, timedelta, timezone

import pandas as pd

import feed_v2 as f2
import fetch_market_data as fm

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)            # lunes
END = date(2026, 10, 23)                                              # último viernes: última barra «de ayer»
PERIOD_DAYS = {"1mo": 30, "3mo": 92, "6mo": 183, "1y": 365, "5y": 1826, "10y": 3652}


def mk(sym, unit="currency", cur="USD", tier="hot", kind="yahoo", name=None, status="active"):
    return {"symbol": sym, "kind": kind, "name": name or sym, "currency": cur, "unit": unit, "tier": tier, "status": status, "fail_streak": 0}


REG = [mk("^GSPC", "points", "USD"), mk("AAPL"), mk("SWDA.L", "subunit", "GBp"), mk("EIMI.L", "currency", "USD", "warm"),
       mk("EURUSD=X", "rate", "USD"), mk("USDJPY=X", "rate", "JPY", "warm"), mk("GC=F", "currency", "USD"),
       mk("BTC", "currency", "USD", "hot", "crypto", "Bitcoin"), mk("ETH", "currency", "USD", "warm", "crypto", "Ethereum")]


class World:
    """Yahoo simulado: la serie de un símbolo es una función de la FECHA (así dos descargas de ventanas distintas casan)."""
    def __init__(self):
        self.calls = []
        self.fail = set()
        self.currency = {}                  # símbolo -> divisa que «informa» Yahoo (por defecto, la del registro)
        self.scale = {}                     # símbolo -> factor (simula un rescalado: split, x100)
        self.end = END
        self.stop_after = None
        self.decl = {e["symbol"]: e for e in REG}

    def price(self, sym, d):
        base = 20 + zlib.crc32(sym.encode()) % 400
        return base * (1 + 0.1 * math.sin(d.toordinal() / 37)) * self.scale.get(sym, 1.0)

    def __call__(self, sym, tries=3, **kw):
        self.calls.append((sym, kw.get("period"), kw.get("interval")))
        if self.stop_after is not None and len(self.calls) > self.stop_after:
            raise fm.YahooStop("429 sostenido")
        if sym in self.fail:
            raise RuntimeError("boom")
        if kw.get("interval") == "5m":
            idx = pd.date_range("2026-10-23 09:30", periods=20, freq="5min", tz="America/New_York")
            df = pd.DataFrame({"Close": [100 + i for i in range(20)]}, index=idx)
        else:
            days = []
            d = self.end
            for _ in range(PERIOD_DAYS[kw["period"]]):
                if d.weekday() < 5:
                    days.append(d)
                d -= timedelta(days=1)
            days.reverse()
            p = [self.price(sym, x) for x in days]
            df = pd.DataFrame({"Open": [x * 0.998 for x in p], "High": [x * 1.01 for x in p], "Low": [x * 0.99 for x in p], "Close": p,
                               "Volume": [1_000_000 + x.toordinal() % 1000 for x in days]}, index=pd.DatetimeIndex([pd.Timestamp(x) for x in days]))
        cur = self.currency.get(sym, self.decl[sym]["currency"] if sym in self.decl else "USD")
        if cur:
            df.attrs["currency"] = cur
        return df


def default_coins(n=30):
    return [{"id": "coin-%d" % i, "symbol": "c%d" % i, "name": "Coin %d" % i, "current_price": 1.5 + i, "market_cap": 1e9 - i,
             "market_cap_rank": i + 1, "total_volume": 5e6, "price_change_percentage_24h_in_currency": 1.234,
             "price_change_percentage_7d_in_currency": -2.5, "last_updated": "2026-10-26T11:58:00.000Z"} for i in range(n)]


def install_world(monkeypatch):
    w = World()
    monkeypatch.setattr(fm, "yf_history", w)
    monkeypatch.setattr(fm, "_DEADLINE", 1e12)
    monkeypatch.setattr(fm, "_HARD_DEADLINE", 1e12)
    monkeypatch.setattr(fm, "_nap", lambda s: None)
    monkeypatch.setattr(fm, "_cg_markets", lambda params, tries=3: default_coins())      # CoinGecko responde con 30 monedas; cada test lo cambia si lo necesita
    monkeypatch.setattr(fm, "parse_feed", lambda url, limit=8: [])
    monkeypatch.setattr(fm, "build_rates", lambda: [])
    monkeypatch.setattr(fm, "fetch_cpi_history", lambda: ([], {"source": "x", "series": "x", "last_date": None, "stale": True}))
    monkeypatch.setattr(fm, "build_market_mood", lambda prev_feed=None, include_altcoin=True: {"vix": {"value": 15.0, "change_pct": 1.0}})
    monkeypatch.setattr(fm, "FRED_API_KEY", "")
    return w


def gen(tmp_path, name, prev=None, now=NOW, registry=REG, **kw):
    out = tmp_path / name
    run = f2.generate(registry, str(out / "v2"), str(prev / "v2") if prev else None, now=now, **kw)
    return out, run


def jl(base, rel):
    with open(os.path.join(base, "v2", *rel.split("/")), encoding="utf-8") as fh:
        return json.load(fh)


def exists(base, rel):
    return os.path.isfile(os.path.join(base, "v2", *rel.split("/")))


def sha(base, rel):
    with open(os.path.join(base, "v2", *rel.split("/")), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()
