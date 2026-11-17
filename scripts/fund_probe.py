#!/usr/bin/env python3
"""
SONDA DE COBERTURA DE FONDOS EUROPEOS EN YAHOO FINANCE (diagnóstico manual, solo lectura).

¿Qué fondos UCITS (indexados y de gestión activa, no solo ETFs cotizados) sirve Yahoo vía yfinance, con qué divisa y cuántos días de retraso trae su última barra?
Se ejecuta SOLO desde GitHub Actions (.github/workflows/fund-probe.yml, workflow_dispatch). No escribe en ningún sitio ni toca la cartera del dueño.

  * Sección NOMBRES: lista genérica de fondos populares (config/fund_probe_names.json) -> resultados completos (resumen y JSON).
  * Sección ISIN (opcional, secreto PROBE_ISINS): SOLO salen agregados; jamás un ISIN, ticker, nombre ni nada que permita deducir cuál es cuál.

El repo es PÚBLICO: cada línea que imprime este script es pública.
"""
import json
import math
import re
import time
import unicodedata
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
RECENT_DAYS = 3                  # «dato reciente»: la última barra tiene como mucho 3 días (cubre viernes -> lunes)
MIN_COVERAGE = 0.6               # un candidato solo vale si contiene >= 60 % de las palabras significativas del nombre buscado
STRONG_DELTA = 0.1               # «candidato fuerte»: cobertura a menos de 0.1 de la del mejor
MAX_ALTS = 3                     # como mucho 3 candidatos fuertes (el mejor + 2 alternativas) se miden por nombre
FUND_TYPES = frozenset({"MUTUALFUND", "ETF"})
STOPWORDS = frozenset({"fund", "funds", "ucits", "the", "of", "and"})
_ISIN_SHAPE = re.compile(r"\b[A-Z]{2}(?=[A-Z0-9]*\d)[A-Z0-9]{10}\b")      # forma de ISIN, sin exigir el dígito de control: más estricta = más privada


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


# ------------------------------------------------------------------------------------------------------------------------------
# Sección NOMBRES
# ------------------------------------------------------------------------------------------------------------------------------
def new_error_counter():
    return {c: 0 for c in ERROR_CLASSES}


def load_names(path):
    """Lista [{"manager", "name"}] del repo. ValueError si la estructura no es válida o algún nombre parece un ISIN."""
    try:
        data = json.loads(open(path, encoding="utf-8").read())
    except (OSError, ValueError) as exc:
        raise ValueError("lista de nombres ilegible") from exc
    if not isinstance(data, list) or not data:
        raise ValueError("la lista de nombres debe ser una lista no vacía")
    for e in data:
        if (not isinstance(e, dict) or set(e) != {"manager", "name"} or not all(isinstance(e[k], str) and e[k].strip() for k in e)
                or _ISIN_SHAPE.search(e["name"]) or _ISIN_SHAPE.search(e["manager"])):
            raise ValueError("entrada de la lista de nombres no válida")
    return data


def norm_tokens(text):
    """Palabras significativas: minúsculas, sin acentos ni puntuación, sin palabras vacías (fund, funds, ucits, the, of, and)."""
    t = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii").lower()
    return {w for w in re.findall(r"[a-z0-9]+", t) if w not in STOPWORDS}


def fund_candidates(query, quotes):
    """REGLA DE SELECCIÓN (1/2): de los resultados de búsqueda de Yahoo, solo MUTUALFUND/ETF con >= MIN_COVERAGE de las palabras del nombre buscado
    (en nombre corto o largo). Sin duplicados por símbolo. Conserva el orden de Yahoo (rank)."""
    q = norm_tokens(query)
    out, seen = [], set()
    for rank, qt in enumerate(quotes or []):
        if not isinstance(qt, dict) or qt.get("quoteType") not in FUND_TYPES or not isinstance(qt.get("symbol"), str) or qt["symbol"] in seen:
            continue
        cov = len(q & (norm_tokens(qt.get("shortname")) | norm_tokens(qt.get("longname")))) / len(q) if q else 0.0
        if cov + 1e-9 < MIN_COVERAGE:
            continue
        seen.add(qt["symbol"])
        out.append({"symbol": qt["symbol"], "exchange": qt.get("exchange") or qt.get("exchDisp"), "shortname": qt.get("shortname") or qt.get("longname"),
                    "quote_type": qt["quoteType"], "coverage": cov, "rank": rank})
    return out


def _ordered(cands):
    return sorted(cands, key=lambda c: (-c["coverage"], c["rank"]))


def pick_best(cands):
    """REGLA DE SELECCIÓN (2/2): mayor cobertura; a igualdad, el que Yahoo puso antes."""
    o = _ordered(cands)
    return o[0] if o else None


def strong_candidates(cands):
    """Mejor candidato + los que están a menos de STRONG_DELTA de su cobertura (como mucho MAX_ALTS). Si hay >= 2, el nombre es AMBIGUO."""
    o = _ordered(cands)
    return [c for c in o if c["coverage"] + 1e-9 >= o[0]["coverage"] - STRONG_DELTA][:MAX_ALTS] if o else []


def _status(lag):
    return "reciente" if lag <= RECENT_DAYS else "retrasado"


def _measure_symbol(client, gate, symbol, today, errors):
    """-> ("ok", medida) | ("error", clase). Lanza Stopped si la sonda ya no puede seguir."""
    try:
        h = gate.call(lambda: client.history(symbol), HISTORY_COST)
        m = measure(h, today)
    except ProbeError as pe:
        errors[pe.cls] += 1
        return "error", pe.cls
    for f in m["flags"]:
        errors[f] += 1
    return "ok", m


def _currencies(r):
    cur = {r.get("currency")} | {a.get("currency") for a in r["alternatives"]}
    r["currencies"] = sorted(c for c in cur if c)
    r["multi_currency"] = len(r["currencies"]) > 1


def _base_result(e):
    return {"manager": e["manager"], "name": e["name"], "status": "no_probado", "ticker": None, "exchange": None, "quote_type": None, "shortname": None,
            "currency": None, "last_date": None, "lag_days": None, "bars": None, "flags": [], "error": None, "candidates": 0, "ambiguous": False,
            "multi_currency": False, "currencies": [], "alternatives": []}


def probe_alternatives(results, client, gate, today, errors):
    """Mide el histórico de las alternativas pendientes (otras clases/divisas del mismo fondo). Si no hay presupuesto o la sonda paró, se quedan «pendiente»."""
    for r in results:
        for a in r["alternatives"]:
            if a["status"] != "pendiente":
                continue
            try:
                kind, m = _measure_symbol(client, gate, a["ticker"], today, errors)
            except Stopped:
                return
            if kind == "ok":
                a.update(status=_status(m["lag_days"]), currency=m["currency"], last_date=m["last_date"], lag_days=m["lag_days"], bars=m["bars"], flags=m["flags"])
            else:
                a.update(status="sin_serie", error=m)
            if gate.stopped:
                return
        _currencies(r)


def run_names(entries, client, gate, today, errors, with_alternatives=True):
    results = []
    for e in entries:
        r = _base_result(e)
        results.append(r)
        if gate.stopped:
            continue
        try:
            quotes = gate.call(lambda n=e["name"]: client.search(n), SEARCH_COST)
        except Stopped:
            continue
        except ProbeError as pe:
            errors[pe.cls] += 1
            r["error"] = pe.cls
            r["status"] = "no_probado" if gate.stopped else "error"
            continue
        strong = strong_candidates(fund_candidates(e["name"], quotes))
        if not strong:
            errors["vacio"] += 1
            r["status"] = "no_encontrado"
            continue
        best = strong[0]
        r.update(ticker=best["symbol"], exchange=best["exchange"], quote_type=best["quote_type"], shortname=best["shortname"], candidates=len(strong),
                 ambiguous=len(strong) >= 2)
        r["alternatives"] = [{"ticker": c["symbol"], "exchange": c["exchange"], "shortname": c["shortname"], "status": "pendiente", "currency": None,
                              "last_date": None, "lag_days": None, "bars": None, "flags": [], "error": None} for c in strong[1:]]
        try:
            kind, m = _measure_symbol(client, gate, best["symbol"], today, errors)
        except Stopped:
            continue
        if kind == "ok":
            r.update(status=_status(m["lag_days"]), currency=m["currency"], last_date=m["last_date"], lag_days=m["lag_days"], bars=m["bars"], flags=m["flags"])
        else:
            r.update(status="no_probado" if gate.stopped else "sin_serie", error=m)
        _currencies(r)
    if with_alternatives:
        probe_alternatives(results, client, gate, today, errors)
    return results


def summarize_by_manager(results):
    out = {}
    for r in results:
        s = out.setdefault(r["manager"], {"manager": r["manager"], "searched": 0, "found": 0, "recent": 0, "delayed": 0, "ambiguous": 0, "no_series": 0,
                                           "errors": 0, "not_probed": 0})
        st = r["status"]
        if st == "no_probado":
            s["not_probed"] += 1
            continue
        s["searched"] += 1
        s["found"] += st in ("reciente", "retrasado", "sin_serie")
        s["recent"] += st == "reciente"
        s["delayed"] += st == "retrasado"
        s["no_series"] += st == "sin_serie"
        s["errors"] += st == "error"
        s["ambiguous"] += bool(r.get("ambiguous"))
    return list(out.values())
