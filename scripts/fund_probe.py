#!/usr/bin/env python3
"""
SONDA DE COBERTURA DE FONDOS EUROPEOS EN YAHOO FINANCE (diagnóstico manual, solo lectura).

¿Qué fondos UCITS (indexados y de gestión activa, no solo ETFs cotizados) sirve Yahoo vía yfinance, con qué divisa y cuántos días de retraso trae su última barra?
Se ejecuta SOLO desde GitHub Actions (.github/workflows/fund-probe.yml, workflow_dispatch). No escribe en ningún sitio ni toca la cartera del dueño.

  * Sección NOMBRES: lista genérica de fondos populares (config/fund_probe_names.json) -> resultados completos (resumen y JSON).
  * Sección ISIN (opcional, secreto PROBE_ISINS): SOLO salen agregados; jamás un ISIN, ticker, nombre ni nada que permita deducir cuál es cuál.

El repo es PÚBLICO: cada línea que imprime este script es pública.
"""
import math
import time
from datetime import date, datetime

from yfinance import exceptions as yf_exceptions

# ------------------------------------------------------------------------------------------------------------------------------
# Constantes (la regla de cada una está explicada en auditorias/FIXES_SONDA_FONDOS.md)
# ------------------------------------------------------------------------------------------------------------------------------
MIN_PAUSE = 1.0                  # segundos mínimos entre llamadas a Yahoo; no se puede bajar
HARD_MAX_REQUESTS = 250          # tope duro de peticiones por run
SEARCH_COST = 1                  # una búsqueda = 1 petición
HISTORY_COST = 2                 # un histórico = hasta 2: yfinance pide primero la zona horaria del ticker y luego la serie (peor caso)
MAX_NET_STREAK = 3               # fallos de red seguidos que paran la sonda
ERROR_CLASSES = ("sin_red", "429", "vacio", "parseo", "divisa_rara")
KNOWN_CURRENCIES = frozenset({"EUR", "USD", "GBP", "GBp", "CHF", "SEK", "NOK", "DKK", "PLN", "CZK", "HUF", "JPY", "CAD", "AUD", "NZD", "HKD", "SGD", "CNY", "ZAR"})
HISTORY_PERIOD = "1mo"


class ProbeError(Exception):
    """Error con clase CERRADA (ERROR_CLASSES). El mensaje es siempre la clase: nunca arrastra texto de la excepción original (podría citar un ISIN)."""

    def __init__(self, cls):
        super().__init__(cls)
        self.cls = cls


class Stopped(Exception):
    """La sonda ya está parada (429, sin red o tope de peticiones): no se hace ninguna petición más."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def classify_exception(exc):
    """Excepción -> una de ERROR_CLASSES. Solo mira el TIPO (y, para el 429, el texto), nunca lo devuelve."""
    if isinstance(exc, ProbeError):
        return exc.cls
    if isinstance(exc, yf_exceptions.YFRateLimitError):
        return "429"
    text = str(exc).lower()
    if "429" in text or "too many requests" in text or "rate limit" in text:
        return "429"
    if isinstance(exc, yf_exceptions.YFTickerMissingError):          # incluye YFPricesMissingError y YFTzMissingError
        return "vacio"
    if isinstance(exc, (yf_exceptions.YFDataException, OSError)):    # ConnectionError y TimeoutError son OSError; Yahoo caído también cuenta como «sin red»
        return "sin_red"
    mod = type(exc).__module__ or ""
    if mod.startswith("curl_cffi") or mod.startswith("requests.exceptions") or mod.startswith("urllib3"):
        return "sin_red"
    return "parseo"


class Gate:
    """Todas las peticiones a Yahoo pasan por aquí: pausa mínima, tope total y parada limpia (sin reintentos) ante 429 o caída de red."""

    def __init__(self, max_requests=HARD_MAX_REQUESTS, min_pause=MIN_PAUSE, clock=None, sleep=None):
        self.max_requests = max(0, min(int(max_requests), HARD_MAX_REQUESTS))
        self.min_pause = max(MIN_PAUSE, float(min_pause))
        self.clock = clock or time.monotonic
        self.sleep = sleep or time.sleep
        self.used = 0
        self.stopped = None            # None | "429" | "sin_red" | "presupuesto"
        self._last = None
        self._net_streak = 0

    def remaining(self):
        return self.max_requests - self.used

    def can_afford(self, cost):
        return self.stopped is None and self.used + cost <= self.max_requests

    def call(self, fn, cost=SEARCH_COST):
        if self.stopped:
            raise Stopped(self.stopped)
        if self.used + cost > self.max_requests:
            self.stopped = "presupuesto"
            raise Stopped("presupuesto")
        if self._last is not None:
            wait = self.min_pause - (self.clock() - self._last)
            if wait > 0:
                self.sleep(wait)
        self._last = self.clock()
        self.used += cost
        try:
            out = fn()
        except Exception as exc:               # noqa: BLE001 - se clasifica y se relanza con clase cerrada, sin texto
            cls = classify_exception(exc)
            if cls == "429":
                self.stopped = "429"
            elif cls == "sin_red":
                self._net_streak += 1
                if self._net_streak >= MAX_NET_STREAK:
                    self.stopped = "sin_red"
            else:
                self._net_streak = 0
            raise ProbeError(cls) from None
        self._net_streak = 0
        return out


# ------------------------------------------------------------------------------------------------------------------------------
# Medida de una serie
# ------------------------------------------------------------------------------------------------------------------------------
def measure(history, today):
    """history = {"rows": [(date, close)], "currency": str|None} -> {last_date, lag_days, bars, currency, flags}. Lanza ProbeError("vacio"|"parseo")."""
    rows = history.get("rows") or []
    if not rows:
        raise ProbeError("vacio")
    good = []
    for d, c in rows:
        try:
            if isinstance(d, datetime):
                d = d.date()
            if isinstance(d, date) and isinstance(c, (int, float)) and not isinstance(c, bool) and math.isfinite(c) and c > 0:
                good.append(d)
        except (TypeError, ValueError):
            continue
    if not good:
        raise ProbeError("parseo")
    last = max(good)
    cur = history.get("currency")
    flags = [] if isinstance(cur, str) and cur in KNOWN_CURRENCIES else ["divisa_rara"]
    return {"last_date": last.isoformat(), "lag_days": max(0, (today - last).days), "bars": len(good),
            "currency": cur if isinstance(cur, str) and cur else None, "flags": flags}


# ------------------------------------------------------------------------------------------------------------------------------
# Cliente real de Yahoo (yfinance). Las pruebas lo sustituyen por simuladores; solo corre de verdad en Actions.
# ------------------------------------------------------------------------------------------------------------------------------
def normalize_history(df, meta):
    """DataFrame de Ticker.history() (índice por fecha, tz-aware o no) -> {"rows": [(date, close)], "currency": str|None}."""
    if df is None or len(df) == 0:
        return {"rows": [], "currency": (meta or {}).get("currency")}
    try:
        closes = df["Close"]
        rows = [(ts.date(), float(c)) for ts, c in zip(df.index, closes)]        # el índice ya viene en la zona horaria del mercado: .date() es la fecha local de la barra
    except (KeyError, AttributeError, TypeError, ValueError):
        raise ProbeError("parseo") from None
    return {"rows": rows, "currency": (meta or {}).get("currency")}


class YahooClient:
    def __init__(self):
        import yfinance as yf
        self.yf = yf
        yf.config.debug.hide_exceptions = False        # por defecto yfinance se traga los errores (incluso un 429 sale como «vacío»): así llegan como excepciones

    def search(self, query):
        s = self.yf.Search(query, max_results=8, news_count=0, lists_count=0, include_cb=False)
        return list(s.quotes)

    def history(self, symbol):
        t = self.yf.Ticker(symbol)
        df = t.history(period=HISTORY_PERIOD, interval="1d", actions=False)
        try:
            meta = t.history_metadata
        except Exception:                               # noqa: BLE001 - sin metadatos: divisa desconocida (se marca divisa_rara), la serie sigue valiendo
            meta = {}
        return normalize_history(df, meta)
