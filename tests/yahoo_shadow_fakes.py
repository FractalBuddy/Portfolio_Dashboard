"""Simuladores SINTÉTICOS para la sombra NAV Yahoo (F1). Ningún ISIN, símbolo, nombre ni precio es real: los ISIN son los inventados de
tests/privacy_guard.py (SINTETICOS_VALIDOS) y los `0P…` los inventados de ZERO_P_SINTETICOS. Reutiliza el reloj falso y la forma de las respuestas de
búsqueda de la sonda de fondos (tests/fund_probe_fakes.py)."""
import json
import os
from datetime import date, datetime, timedelta, timezone

from fund_probe_fakes import FakeTime, quote  # noqa: F401  (se reexportan para los tests)

# Distintivos a propósito: si aparecen en CUALQUIER salida, la prueba de privacidad lo detecta.
ISIN_A, ISIN_B, ISIN_C = "IE000SYNA013", "IE000SYNB011", "IE000SYNC019"
SYM_A, SYM_B, SYM_C = "0PFAKE0001.F", "0PFAKE0002.F", "0PFAKE0003.F"
SYM_OTRA_CLASE = "0PFAKE0004.F"
SYM_CANARIO = "0PCANARY01.F"
NAME_A = "Zeta Quimera Developed World Index Fund (IE) S Acc EUR"
NAME_B = "Zeta Quimera Emerging Markets Index Fund (IE) S Acc EUR"
NAME_C = "Zeta Quimera Euro Government Bond Index Fund (IE) S Acc EUR"
URL_A = "https://www.ishares.com/es/individual/es/products/000001/zeta-quimera-a"
URL_B = "https://www.ishares.com/es/individual/es/products/000002/zeta-quimera-b"
URL_C = "https://www.ishares.com/es/individual/es/products/100001/zeta-quimera-c"
PRICE_A, PRICE_B, PRICE_C = 87.65, 43.21, 9.87                  # NAV oficiales inventados (2 decimales, como la ficha)
CANARY_ISIN = "IE00B03HD191"                                     # el de config/yahoo_shadow.json (lista genérica PÚBLICA)
CANARY_NAME = "Vanguard Global Stock Index Fund EUR Acc"

NOW = datetime(2026, 11, 24, 6, 40, tzinfo=timezone.utc)         # martes 06:40 UTC (franja de mañana)
NAV_DAY = date(2026, 11, 20)                                     # viernes: fecha del NAV oficial


def hist(points, currency="EUR"):
    """{"rows": [(date, close)], "currency"}: la forma que devuelve fund_probe.normalize_history / YahooClient.history."""
    return {"rows": [(d, c) for d, c in points], "currency": currency}


def bars(last=NAV_DAY, n=4, close=10.0, step=0.0):
    """n barras hábiles (L-V) terminando en `last`; la última vale `close`."""
    out, d = [], last
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    out.reverse()
    return [(d, round(close - step * (len(out) - 1 - i), 6)) for i, d in enumerate(out)]


class FakeYahoo:
    """Mismo contrato que yahoo_shadow.ShadowYahooClient: search(q) -> quotes; history(sym, period) -> hist. Una EXCEPCIÓN en la tabla se lanza.
    `noisy=True` imprime en stdout/stderr lo mismo que haría yfinance (cita el símbolo y la consulta) para comprobar que se descarta."""

    def __init__(self, searches=None, histories=None, noisy=True):
        self.searches, self.histories, self.noisy = searches or {}, histories or {}, noisy
        self.calls = []

    def _get(self, table, key, default):
        v = table.get(key, default)
        if isinstance(v, BaseException):
            raise v
        return v

    def search(self, query):
        self.calls.append(("search", query))
        if self.noisy:
            import sys
            print("yfinance: buscando %s" % query)
            print("aviso %s" % query, file=sys.stderr)
        return self._get(self.searches, query, [])

    def history(self, symbol, period="5d"):
        self.calls.append(("history", symbol, period))
        if self.noisy:
            import sys
            import warnings
            print("%s: possibly delisted" % symbol, file=sys.stderr)
            warnings.warn("precio de %s" % symbol)
        return self._get(self.histories, symbol, hist([]))


def canary_ok(searches, histories, last=NAV_DAY):
    searches[CANARY_ISIN] = [quote(SYM_CANARIO, CANARY_NAME, longname=CANARY_NAME)]
    histories[SYM_CANARIO] = hist(bars(last, 4, 77.0))


def write_inputs(directory, funds, users=1):
    """watch.json + payloads.json como los deja personal_v2.py (watch y build) en $RUNNER_TEMP/personal.
    funds = [(isin, name, url, price|None, nav_date_iso|None)]; price None => la ficha no dio NAV (source 'none')."""
    os.makedirs(directory, exist_ok=True)
    watch, payloads = [], []
    for u in range(users):
        uid = "user%02d" % u
        watch.append({"user_id": uid, "funds": [{"isin": i, "name": n, "url": url} for i, n, url, _p, _d in funds], "symbols": []})
        pf = []
        for i, n, _url, p, d in funds:
            f = {"isin": i, "name": n, "price": p, "currency": "EUR", "unit": "currency", "source": "ishares_nav_scrape" if p is not None else "none"}
            if p is not None and d:
                f.update(nav_date_iso=d, nav_date=d)
            pf.append(f)
        payloads.append({"user": uid, "payload": {"schema": 2, "generated_at": "2026-11-24T06:38:00Z", "funds": pf, "series": [], "public": []}})
    with open(os.path.join(directory, "watch.json"), "w", encoding="utf-8") as f:
        json.dump(watch, f)
    with open(os.path.join(directory, "payloads.json"), "w", encoding="utf-8") as f:
        json.dump(payloads, f)
