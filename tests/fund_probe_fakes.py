"""Simuladores y fixtures SINTÉTICOS de la sonda de fondos (ningún ticker, ISIN ni nombre es real)."""
from datetime import date

import pandas as pd

TODAY = date(2026, 11, 16)                                    # lunes


class FakeTime:
    """Reloj y sleep falsos: sleep avanza el reloj; guarda los instantes de cada petición para comprobar el ritmo."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, s):
        self.sleeps.append(s)
        self.now += s


def quote(symbol, shortname, qtype="MUTUALFUND", exch="FRA", longname=None):
    """Forma real de un elemento de yfinance.Search(...).quotes (sin divisa: Yahoo no la da en la búsqueda)."""
    return {"exchange": exch, "shortname": shortname, "quoteType": qtype, "symbol": symbol, "index": "quotes", "score": 20000.0,
            "typeDisp": "Fund" if qtype == "MUTUALFUND" else "ETF", "longname": longname or shortname, "exchDisp": "Frankfurt",
            "isYahooFinance": True}


def hist_df(last=date(2026, 11, 13), n=21, currency="EUR", tz="UTC"):
    """DataFrame con la forma real de Ticker.history(): índice DatetimeIndex tz-aware por fecha, columnas OHLCV."""
    idx = pd.bdate_range(end=pd.Timestamp(last), periods=n, tz=tz)
    return pd.DataFrame({"Open": 10.0, "High": 10.5, "Low": 9.5, "Close": [10.0 + i * 0.01 for i in range(n)], "Volume": 0}, index=idx)


def rows(last=date(2026, 11, 13), n=21):
    return [(ts.date(), float(c)) for ts, c in zip(hist_df(last, n).index, hist_df(last, n)["Close"])]


class FakeClient:
    """Cliente falso con el mismo contrato que YahooClient: search(q) -> lista de quotes; history(sym) -> {"rows": [...], "currency": str}.
    `searches`/`histories`: dict clave -> lista de quotes / dict historia, o una EXCEPCIÓN (se lanza)."""

    def __init__(self, searches=None, histories=None, default_history=None):
        self.searches, self.histories, self.default_history = searches or {}, histories or {}, default_history
        self.calls = []
        self.periods = []                      # periodo pedido en cada history() (None = el de por defecto de la sonda)

    def _get(self, table, key, default):
        v = table.get(key, default)
        if isinstance(v, BaseException):
            raise v
        return v

    def search(self, query):
        self.calls.append(("search", query))
        return self._get(self.searches, query, [])

    def history(self, symbol, period=None):
        self.calls.append(("history", symbol))
        self.periods.append(period)
        return self._get(self.histories, symbol, self.default_history)
