#!/usr/bin/env python3
"""
Validador del sitio v2 ANTES de publicarlo (BE-05, BE-06, BE-07).

Solo usa la biblioteca estándar, a propósito: se ejecuta en el job `publish`, el único con permisos de publicación, que no instala dependencias
de terceros. El job `fetch` sí las instala (yfinance y sus transitivas), así que su salida se trata como NO fiable y se valida aquí con este
script sacado del propio repo (no del artefacto).

    python3 scripts/sanity_check_v2.py --site site/v2 --registry registry.json [--prev prev_site/v2] [--alerts-out alerts.txt]

Qué comprueba:
  * FICHEROS: solo rutas del contrato (lista blanca), símbolos que existen en registry.json, sin enlaces simbólicos, tope de bytes por fichero y
    del sitio entero, manifest.json coherente con lo que hay en disco (bytes y sha256).
  * ESQUEMA estricto por fichero: lista blanca de claves, regex y topes por campo, ningún texto con '<' ni '>' ni caracteres de control.
    `currency`, `unit` y `name` de cada serie deben COINCIDIR con el registro (un fetch comprometido no puede cambiar la unidad).
  * SERIES: fechas ISO estrictamente crecientes y no futuras, close finito > 0, low <= close <= high, latest.json coherente con la última barra.
  * CONTINUIDAD contra el sitio anterior (--prev): 0,5 < precio_nuevo/precio_anterior < 2 por símbolo, mediana de cierres nuevo/anterior en las
    fechas solapadas ≈ 1 (detecta un rescalado x100 aunque cambie todo el histórico a la vez), años cerrados sin tocar, símbolos que no
    desaparecen, el sitio no encoge, generated_at no retrocede.

Variables de entorno opcionales:
    EXPECT_PREV=true              el sitio anterior debería existir (no es el primer run): si falta o no se pudo bajar, NO se trata como primer run (BE-06).
    SANITY_ALLOW_REGRESSION=true  las reglas de CONTINUIDAD pasan a ser avisos (cambio intencionado: un split, un símbolo retirado...). Las absolutas
                                  siguen bloqueando.

Código de salida: 0 = publicable, 1 = no publicar.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import statistics
import sys
from math import ceil

import feed_v2_common as c


class Alert(str):
    """Aviso que merece que el dueño se entere (el sitio se publica, pero con datos degradados): el workflow lo manda a Healthchecks como /fail."""


MIN_PUBLISHED = 20               # símbolos de Yahoo con datos publicados, como mínimo (con registros pequeños, la mitad de los activos)
MIN_FRESH_FRACTION = 0.5         # al menos la mitad de lo publicado con última barra de los últimos MAX_BAR_AGE_DAYS
MAX_BAR_AGE_DAYS = 6             # cubre fin de semana + festivo
MAX_FUTURE_DAYS = 1              # una barra con fecha a más de «mañana» (huso horario) es imposible
MIN_DATE = dt.date(1900, 1, 1)
MAX_ABS_CHANGE_PCT = 60.0
MAX_EXTREME_CHANGE_SYMBOLS = 2
PRICE_RATIO_LIMITS = (0.5, 2.0)  # precio_nuevo / precio_anterior por símbolo
MEDIAN_RATIO_TOLERANCE = 0.05    # mediana de cierres solapados nuevo/anterior: 1 ± 5 %
MIN_OVERLAP = 3
MAX_COMPARE_GAP_DAYS = 10        # entre dos precios más separados que esto no se aplica el límite de salto (movimientos reales)
MIN_SIZE_RATIO_VS_PREV = 0.9     # el sitio nuevo debe pesar >= 90 % del anterior (solo crece; los años podados son pocos KB)
MAX_LOST_SYMBOLS_FRACTION = 0.03
MIN_LOST_SYMBOLS_TOLERATED = 2
MAX_ERRORS_SHOWN = 40
MAX_BARS_PER_YEAR_FILE = 400
MAX_INTRADAY_BARS = 800
UNPUBLISHED_ALERT_FRACTION = 0.10  # más de este % de los símbolos activos del registro sin publicar = aviso a Healthchecks (menos: solo ::warning::)
FAIL_CLASSES = ("empty", "http_error", "timeout", "parse", "unit", "suspect")      # = feed_v2.FAIL_CLASSES (el validador no importa el generador)
DROP_REASONS = ("close_missing", "close_not_finite", "close_le_0", "unknown")      # = feed_v2.DROP_REASONS

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MINUTE_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
_TZ_RE = re.compile(r"^(?:UTC|[A-Za-z_]+(?:/[A-Za-z0-9_+\-]+){1,2})$")
_COIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._\-]{0,79}$")
_COIN_SYM_RE = re.compile(r"^[A-Za-z0-9.\-]{1,15}$")
_FX_CCY_RE = re.compile(r"^[A-Z]{3}$")
_SERIES_RE = re.compile(r"^[A-Za-z0-9_]{1,30}$")
_HOST_RE = re.compile(r"^[a-z0-9.\-:]{1,60}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def _line(s):
    return str(s).replace("\r", " ").replace("\n", " ")


class Ctx:
    def __init__(self, registry, today, allow_regression=False):
        self.reg = {e["symbol"]: e for e in registry}
        self.today = today
        self.allow_regression = allow_regression
        self.errors, self.warnings, self.regressions = [], [], []

    def err(self, msg):
        if msg not in self.errors:
            self.errors.append(msg)

    def warn(self, msg, alert=False):
        msg = Alert(msg) if alert else msg
        if msg not in self.warnings:
            self.warnings.append(msg)

    def regress(self, msg):
        """Fallo de continuidad: error, salvo con allow_regression (entonces, aviso)."""
        if msg not in self.regressions:
            self.regressions.append(msg)

    @property
    def limit_day(self):
        return self.today + dt.timedelta(days=MAX_FUTURE_DAYS)


# ------------------------------------------------------------------------------------------------------------------------------
# Comprobadores de campo: devuelven None si está bien o el motivo
# ------------------------------------------------------------------------------------------------------------------------------
def text(maxlen=200, rx=None, nullable=False):
    def chk(v, ctx):
        if v is None and nullable:
            return None
        if not isinstance(v, str):
            return "no es texto"
        if len(v) > maxlen:
            return "demasiado largo (%d)" % len(v)
        if "<" in v or ">" in v:
            return "contiene '<' o '>'"
        if _CONTROL_RE.search(v):
            return "caracteres de control"
        if rx is not None and not rx.match(v):
            return "formato inválido"
        return None
    return chk


def enum(*values):
    return lambda v, ctx: None if (isinstance(v, str) and v in values) else "valor no permitido"


def number(lo=None, hi=None, nullable=False, positive=False):
    def chk(v, ctx):
        if v is None and nullable:
            return None
        if not c.finite(v):
            return "no es un número finito"
        if positive and v <= 0:
            return "debe ser > 0"
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            return "fuera de rango"
        return None
    return chk


def integer(lo=0, hi=10**9, nullable=False):
    def chk(v, ctx):
        if v is None and nullable:
            return None
        if type(v) is not int or not (lo <= v <= hi):
            return "no es un entero en rango"
        return None
    return chk


def boolean(v, ctx):
    return None if isinstance(v, bool) else "no es booleano"


def day(nullable=False, future_ok=False):
    def chk(v, ctx):
        if v is None and nullable:
            return None
        d = _parse_day(v)
        if d is None:
            return "no es una fecha YYYY-MM-DD"
        if d < MIN_DATE or (d > ctx.limit_day and not future_ok):
            return "fecha fuera de rango"
        return None
    return chk


def stamp(v, ctx):
    if not (isinstance(v, str) and _TS_RE.match(v)):
        return "no es una marca de tiempo ISO Z"
    d = _parse_day(v[:10])
    if d is None or d < MIN_DATE or d > ctx.limit_day:
        return "marca de tiempo fuera de rango"
    return None


def symbol(v, ctx):
    return None if c.safe_symbol(v) else "símbolo no admisible"


def link(v, ctx):
    bad = text(1000)(v, ctx)
    if bad:
        return bad
    return None if v.lower().startswith(("http://", "https://")) else "no es http(s)"


def seq(item, maxlen):
    def chk(v, ctx):
        if not isinstance(v, list) or len(v) > maxlen:
            return "no es una lista o es demasiado larga"
        for x in v:
            bad = item(x, ctx)
            if bad:
                return "elemento: " + bad
        return None
    return chk


def obj(spec, required=None):
    """Objeto con lista blanca de claves. `spec`: clave -> comprobador. `required`: claves obligatorias (por defecto, todas)."""
    req = set(spec) if required is None else set(required)

    def chk(v, ctx):
        if not isinstance(v, dict):
            return "no es un objeto"
        extra = sorted(str(k) for k in v if k not in spec)
        if extra:
            return "claves no permitidas %s" % extra[:4]
        missing = sorted(k for k in req if k not in v)
        if missing:
            return "faltan claves %s" % missing[:4]
        for k, f in spec.items():
            if k in v:
                bad = f(v[k], ctx)
                if bad:
                    return "%s: %s" % (k, bad)
        return None
    return chk


def mapping(key_rx, value, maxlen):
    def chk(v, ctx):
        if not isinstance(v, dict) or len(v) > maxlen:
            return "no es un objeto o es demasiado grande"
        for k, x in v.items():
            if not (isinstance(k, str) and key_rx.match(k)):
                return "clave no permitida"
            bad = value(x, ctx)
            if bad:
                return "%s: %s" % (k, bad)
        return None
    return chk


def _parse_day(s):
    if not (isinstance(s, str) and _DATE_RE.match(s)):
        return None
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        return None


# ------------------------------------------------------------------------------------------------------------------------------
# Esquemas por fichero
# ------------------------------------------------------------------------------------------------------------------------------
SCHEMA_V = lambda v, ctx: None if v == c.SCHEMA else "schema != %d" % c.SCHEMA                          # noqa: E731
_STALE = {"stale": boolean, "stale_since": day()}
_STALE_N = {"stale": boolean, "stale_since": day(nullable=True)}      # macro: fetch_market_data puede dejar stale_since a null si el feed anterior no traía fecha
_ITEM_NEWS = {"title": text(300), "link": link, "source": text(120), "published": text(100)}
_MOOD_SUB = {"value": number(-1e6, 1e6), "rating": text(60, nullable=True), "as_of": day(), **_STALE_N}

INDEX_SPEC = obj({
    "schema": SCHEMA_V, "generated_at": stamp,
    "symbols": seq(obj({"s": symbol, "kind": enum(*c.KINDS), "name": text(c.MAX_NAME_LEN), "currency": text(4, c.CURRENCY_RE),
                        "unit": enum(*c.UNITS), "tier": enum(*c.TIERS), "as_of": day(nullable=True), "status": enum(*c.STATUSES)}),
                   c.MAX_REGISTRY_SYMBOLS)})
LATEST_SPEC = obj({
    "schema": SCHEMA_V, "s": symbol, "kind": enum(*c.KINDS), "name": text(c.MAX_NAME_LEN), "currency": text(4, c.CURRENCY_RE), "unit": enum(*c.UNITS),
    "price_unit_divisor": integer(1, 100), "price": number(1e-9, 1e9, positive=True), "change_pct": number(-1000, 100000, nullable=True),
    "change_pct_7d": number(-1000, 100000, nullable=True), "volume_ratio": number(0, 1e7, nullable=True),
    "sparkline": seq(number(1e-9, 1e9, positive=True), c.LATEST_SPARKLINE_BARS), "as_of": day(), "generated_at": stamp, **_STALE},
    required={"schema", "s", "kind", "name", "currency", "unit", "price_unit_divisor", "price", "change_pct", "change_pct_7d", "volume_ratio",
              "sparkline", "as_of", "generated_at"})
CRYPTO_SPEC = obj({
    "schema": SCHEMA_V, "generated_at": stamp, "currency": enum("USD"), "unit": enum("currency"), "count": integer(0, 250),
    "coins": seq(obj({"id": text(80, _COIN_ID_RE), "symbol": text(15, _COIN_SYM_RE), "name": text(60), "rank": integer(1, 99999, nullable=True),
                      "price": number(1e-12, 1e10, positive=True), "change_24h_pct": number(-1000, 1000, nullable=True),
                      "change_7d_pct": number(-1000, 1000, nullable=True), "market_cap": number(0, 1e15, nullable=True),
                      "volume_24h": number(0, 1e15, nullable=True), "as_of": day()}), 250),
    **_STALE}, required={"schema", "generated_at", "currency", "unit", "count", "coins"})
MACRO_SPEC = obj({
    "schema": SCHEMA_V, "generated_at": stamp,
    "fx_usd": mapping(_FX_CCY_RE, number(1e-4, 1e4, positive=True), 40),
    "rates": seq(obj({"series": text(30, _SERIES_RE), "name": text(80), "value": number(-100, 1000), "prev": number(-100, 1000), "date": day(), **_STALE_N},
                     required={"series", "name", "value", "prev", "date"}), 20),
    "cpi": seq(obj({"date": day(), "value": number(0, 100000)}), 1500),
    "cpi_meta": obj({"source": text(120), "series": text(30, _SERIES_RE), "last_date": day(nullable=True), "stale": boolean,
                     "reused_from_previous": boolean, "stale_since": day(nullable=True)}, required={"source", "series", "last_date", "stale"}),
    "market_mood": obj({
        "vix": obj({"value": number(0, 1000), "change_pct": number(-1000, 100000), **_STALE_N}, required={"value"}),
        "vix_50ma": obj({"value": number(0, 1000), **_STALE_N}, required={"value"}),
        "crypto_fear_greed": obj({"value": number(0, 100), "classification": text(40, nullable=True), "timestamp": integer(0, 4_102_444_800), **_STALE_N},
                                 required={"value"}),
        "stock_fear_greed": obj(_MOOD_SUB, required={"value"}), "market_momentum": obj(_MOOD_SUB, required={"value"}),
        "junk_bond_demand": obj(_MOOD_SUB, required={"value"}), "safe_haven_demand": obj(_MOOD_SUB, required={"value"}),
        "put_call": obj({**_MOOD_SUB, "ratio": number(0, 100)}, required={"value"})}, required=set()),
    "calendar": seq(obj({"date": day(future_ok=True), "event": text(80)}), 40)})
NEWS_SPEC = obj({
    "schema": SCHEMA_V, "generated_at": stamp, "general": seq(obj(_ITEM_NEWS), 30),
    "watchlist": seq(obj({**_ITEM_NEWS, "symbol": symbol, "asset_name": text(80)}), 60)})
HEALTH_SPEC = obj({
    "schema": SCHEMA_V, "generated_at": stamp,
    "run": obj({"duration_s": number(0, 100000), "yahoo_calls": integer(0, 10**6), "yahoo_requests": integer(0, 10**6),
                "http_requests": mapping(_HOST_RE, integer(0, 10**6), 30), "yahoo_rate_limited": integer(0, 10**6),
                "yahoo_abandoned": boolean, "budget_exhausted": boolean}),
    "counts": obj({k: integer(0, 10**6) for k in ("due", "updated", "failed", "carried_stale", "deferred", "backfilled", "unit_mismatch",
                                                    "crypto", "outage", "registry", "published", "rebased", "suspect", "dropped_bars")}),
    "problems": obj({**{k: seq(symbol, 40) for k in ("rebased", "deferred", "frozen", "suspect")},
                     "unit_mismatch": mapping(c.SYMBOL_RE, obj({"registro": text(20, re.compile(r"^[A-Za-z]{3,4}/[a-z]{4,8}$")),
                                                                "yahoo": text(4, re.compile(r"^[A-Za-z]{3,4}$"), nullable=True)}), 40),
                     "failed": mapping(c.SYMBOL_RE, enum(*FAIL_CLASSES), 40),
                     "dropped_bars": mapping(c.SYMBOL_RE, obj({"n": integer(1, 10**6), "dates": seq(day(), 10),
                                                               "reasons": mapping(_DATE_RE, enum(*DROP_REASONS), 10)}), 40)}),
    "symbols": mapping(c.SYMBOL_RE, obj({"status": enum("active", "dormant", "invalid", "delisted"), "reason": enum("not_listed"), "fail_streak": integer(0, 1000),
                                          "last_ok": stamp, "last_try": stamp, "tier": enum(*c.TIERS), "dropped": seq(day(), 50),
                                          "dropped_why": mapping(_DATE_RE, enum(*DROP_REASONS), 50)},
                                         required={"status", "fail_streak", "tier"}),
                       c.MAX_REGISTRY_SYMBOLS)})
MANIFEST_SPEC = obj({
    "schema": SCHEMA_V, "generated_at": stamp, "total_bytes": integer(0, 10**10),
    "files": mapping(re.compile(r"^[A-Za-z0-9._^=/\-]{1,120}$"), obj({"bytes": integer(0, 10**9), "sha256": text(64, _SHA_RE)}), c.MAX_SITE_FILES)})
SPECS = {"index": INDEX_SPEC, "latest": LATEST_SPEC, "crypto": CRYPTO_SPEC, "macro": MACRO_SPEC, "news": NEWS_SPEC, "health": HEALTH_SPEC,
         "manifest": MANIFEST_SPEC}


def _no_constant(name):
    raise ValueError("constante no válida en JSON: %s" % name)


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f, parse_constant=_no_constant)


# ------------------------------------------------------------------------------------------------------------------------------
# Ficheros
# ------------------------------------------------------------------------------------------------------------------------------
def scan_files(site, ctx):
    """{ruta relativa: (clase, símbolo, año, tamaño)} de los ficheros del sitio. Todo lo que no sea del contrato es un error."""
    files, total = {}, 0
    for root, dirs, names in os.walk(site, followlinks=False):
        for d in list(dirs):
            if os.path.islink(os.path.join(root, d)):
                ctx.err("enlace simbólico no permitido: %s" % _line(os.path.relpath(os.path.join(root, d), site))[:80])
                dirs.remove(d)
        for n in names:
            p = os.path.join(root, n)
            rel = os.path.relpath(p, site).replace(os.sep, "/")
            cls = c.classify_path(rel)
            if os.path.islink(p):
                ctx.err("enlace simbólico no permitido: %s" % _line(rel)[:80])
                continue
            if cls is None:
                ctx.err("fichero fuera del contrato: %s" % _line(rel)[:80])
                continue
            kind, sym, year = cls
            size = os.path.getsize(p)
            total += size
            if size > c.MAX_BYTES[kind]:
                ctx.err("%s pesa %d bytes (máx. %d)" % (_line(rel)[:80], size, c.MAX_BYTES[kind]))
            if sym is not None and sym not in ctx.reg:
                ctx.err("símbolo fuera del registro: %s" % _line(sym)[:20])
            elif sym is not None and ctx.reg[sym]["kind"] != "yahoo":
                ctx.err("el símbolo %s no es de Yahoo y no lleva ficheros propios" % _line(sym)[:20])
            files[rel] = (kind, sym, year, size)
    if len(files) > c.MAX_SITE_FILES:
        ctx.err("demasiados ficheros (%d > %d)" % (len(files), c.MAX_SITE_FILES))
    if total > c.MAX_SITE_BYTES:
        ctx.err("el sitio pesa %d bytes (máx. %d)" % (total, c.MAX_SITE_BYTES))
    return files, total


def _scan_strings(v, where, ctx, depth=0):
    """Ningún string del documento (claves incluidas) con '<', '>' o caracteres de control, con independencia de su campo."""
    if depth > 8:
        ctx.err("%s: anidamiento excesivo" % where)
    elif isinstance(v, dict):
        for k, x in v.items():
            if not isinstance(k, str) or "<" in k or ">" in k or _CONTROL_RE.search(k):
                ctx.err("%s: clave ilegible o con marcado" % where)
            else:
                _scan_strings(x, where, ctx, depth + 1)
    elif isinstance(v, list):
        for x in v:
            _scan_strings(x, where, ctx, depth + 1)
    elif isinstance(v, str) and ("<" in v or ">" in v or _CONTROL_RE.search(v)):
        ctx.err("%s: texto con '<', '>' o caracteres de control" % where)


# ------------------------------------------------------------------------------------------------------------------------------
# Series
# ------------------------------------------------------------------------------------------------------------------------------
def check_year_doc(doc, sym, year, ctx, where):
    """Fichero diario de un año. Devuelve las barras (lista de tuplas) o None si el fichero no es utilizable."""
    entry = ctx.reg[sym]
    bad = obj({"schema": SCHEMA_V, "s": symbol, "y": integer(c.YEAR_MIN, c.YEAR_MAX), "currency": text(4, c.CURRENCY_RE), "unit": enum(*c.UNITS),
               "d": lambda v, x: None, "o": lambda v, x: None, "h": lambda v, x: None, "l": lambda v, x: None, "c": lambda v, x: None,
               "v": lambda v, x: None})(doc, ctx)
    if bad:
        ctx.err("%s: %s" % (where, bad))
        return None
    if doc["s"] != sym or doc["y"] != year:
        ctx.err("%s: s/y no coinciden con la ruta" % where)
    if doc["currency"] != entry["currency"] or doc["unit"] != entry["unit"]:
        ctx.err("%s: currency/unit (%s/%s) distintos de los del registro (%s/%s)" % (where, doc["currency"], doc["unit"], entry["currency"], entry["unit"]))
    cols = [doc[k] for k in "dohlcv"]
    n = len(cols[0]) if isinstance(cols[0], list) else -1
    if not all(isinstance(x, list) and len(x) == n for x in cols) or not (0 < n <= MAX_BARS_PER_YEAR_FILE):
        ctx.err("%s: columnas con longitudes distintas, vacías o demasiado largas" % where)
        return None
    bars, last, problems = [], None, set()
    for d, o, h, lo, cl, v in zip(*cols):
        dd = _parse_day(d)
        if dd is None or dd < MIN_DATE:
            problems.add("fecha ilegible")
            continue
        if dd.year != year:
            problems.add("fecha de otro año")
        if dd > ctx.limit_day:
            problems.add("fecha futura")
        if last is not None and dd <= last:
            problems.add("fechas repetidas o desordenadas")
        last = dd
        if not all(c.finite(x) and x > 0 for x in (o, h, lo, cl)):
            problems.add("precio no finito o no positivo")
            continue
        if not (lo <= cl <= h and lo <= o <= h):
            problems.add("low <= open/close <= high incumplido")
        if type(v) is not int or v < 0:
            problems.add("volumen inválido")
        bars.append((dd, cl))
    for p in sorted(problems):
        ctx.err("%s: %s" % (where, p))
    return bars


def check_intraday_doc(doc, sym, ctx, where):
    entry = ctx.reg[sym]
    bad = obj({"schema": SCHEMA_V, "s": symbol, "currency": text(4, c.CURRENCY_RE), "unit": enum(*c.UNITS),
               "t": seq(text(16, _MINUTE_RE), MAX_INTRADAY_BARS), "c": seq(number(1e-9, 1e9, positive=True), MAX_INTRADAY_BARS),
               "tz": text(40, _TZ_RE)}, required={"schema", "s", "currency", "unit", "t", "c"})(doc, ctx)
    if bad:
        ctx.err("%s: %s" % (where, bad))
        return
    if doc["s"] != sym or doc["currency"] != entry["currency"] or doc["unit"] != entry["unit"]:
        ctx.err("%s: s/currency/unit distintos de los esperados" % where)
    if len(doc["t"]) != len(doc["c"]) or len(doc["t"]) < 2:
        ctx.err("%s: t y c con longitudes distintas o demasiado cortas" % where)
    elif any(a >= b for a, b in zip(doc["t"], doc["t"][1:])):
        ctx.err("%s: horas repetidas o desordenadas" % where)
    elif max(doc["t"])[:10] > (ctx.today + dt.timedelta(days=MAX_FUTURE_DAYS)).isoformat():
        ctx.err("%s: barras con fecha futura" % where)


def check_latest_doc(doc, sym, ctx, where):
    bad = LATEST_SPEC(doc, ctx)
    if bad:
        ctx.err("%s: %s" % (where, bad))
        return False
    e = ctx.reg[sym]
    if doc["s"] != sym:
        ctx.err("%s: s no coincide con la ruta" % where)
    for k in ("kind", "name", "currency", "unit"):
        if doc[k] != e[k]:
            ctx.err("%s: %s (%s) distinto del registro (%s)" % (where, k, _line(doc[k])[:20], _line(e[k])[:20]))
    if doc["price_unit_divisor"] != c.price_unit_divisor(e["currency"], e["unit"]):
        ctx.err("%s: price_unit_divisor %s incoherente con %s/%s" % (where, doc["price_unit_divisor"], e["currency"], e["unit"]))
    if doc["sparkline"] and abs(doc["sparkline"][-1] - doc["price"]) > 1e-6 * doc["price"]:
        ctx.err("%s: la última cotización de sparkline no es el precio" % where)
    if ("stale" in doc) != ("stale_since" in doc):
        ctx.err("%s: stale y stale_since van juntos" % where)
    return True


# ------------------------------------------------------------------------------------------------------------------------------
# Continuidad con el sitio anterior
# ------------------------------------------------------------------------------------------------------------------------------
def _try_json(path):
    try:
        return read_json(path)
    except (OSError, ValueError):
        return None


def check_continuity(site, prev, new_manifest_files, latest_new, ctx):
    """Reglas de REGRESIÓN contra el sitio publicado anteriormente (carpeta v2 con manifest.json, latest.json por símbolo y el diario del año
    actual). Cada fallo va a ctx.regress (error, o aviso con allow_regression)."""
    pm = _try_json(os.path.join(prev, "manifest.json"))
    prev_files = pm.get("files", {}) if isinstance(pm, dict) and isinstance(pm.get("files"), dict) else {}
    # generated_at no retrocede
    pg = (pm or {}).get("generated_at") if isinstance(pm, dict) else None
    ng = (_try_json(os.path.join(site, "manifest.json")) or {}).get("generated_at")
    if isinstance(pg, str) and isinstance(ng, str) and ng < pg:
        ctx.regress("generated_at (%s) anterior al del sitio publicado (%s): ¿se está re-publicando un run viejo?" % (ng, pg))
    # el sitio no encoge
    new_total = sum(v["bytes"] for v in new_manifest_files.values())
    prev_total = sum(v.get("bytes", 0) for v in prev_files.values() if isinstance(v, dict))
    if prev_total and new_total < MIN_SIZE_RATIO_VS_PREV * prev_total:
        ctx.regress("el sitio pesa %d bytes, menos del %d %% del anterior (%d)" % (new_total, int(MIN_SIZE_RATIO_VS_PREV * 100), prev_total))
    # símbolos que desaparecen
    prev_syms = {m.group(1) for rel in prev_files for m in [c.PATH_RES["latest"].match(rel)] if m}
    index = _try_json(os.path.join(site, "index.json")) or {}
    status = {r.get("s"): r.get("status") for r in index.get("symbols", []) if isinstance(r, dict)}
    lost = sorted(s for s in prev_syms if s not in latest_new and status.get(s, "active") == "active")
    tol = max(MIN_LOST_SYMBOLS_TOLERATED, int(MAX_LOST_SYMBOLS_FRACTION * len(prev_syms)))
    if len(lost) > tol:
        ctx.regress("desaparecen %d símbolos activos que estaban publicados (tolerancia %d): %s" % (len(lost), tol, ", ".join(lost[:8])))
    elif lost:
        ctx.warn("desaparecen %d símbolos publicados antes: %s" % (len(lost), ", ".join(lost[:8])))
    # años cerrados sin tocar
    grace = ctx.today.month <= 2
    cutoff = ctx.today.year - c.HISTORY_YEARS + 1
    changed, gone = [], []
    for rel, v in prev_files.items():
        cls = c.classify_path(rel)
        if cls is None or cls[0] != "daily" or not isinstance(v, dict):
            continue
        year = cls[2]
        if year < cutoff or year >= ctx.today.year or (year == ctx.today.year - 1 and grace):
            continue
        nv = new_manifest_files.get(rel)
        if nv is None:
            if cls[1] in latest_new:
                gone.append(rel)
        elif nv.get("sha256") != v.get("sha256"):
            changed.append(rel)
    if changed:
        ctx.regress("años cerrados reescritos (%d): %s" % (len(changed), ", ".join(changed[:4])))
    if gone:
        ctx.regress("años cerrados desaparecidos (%d): %s" % (len(gone), ", ".join(gone[:4])))
    # saltos de nivel por símbolo y mediana de cierres solapados
    jumped, rescaled = [], []
    for sym, new in latest_new.items():
        old = _try_json(os.path.join(prev, "s", sym, "latest.json"))
        if not isinstance(old, dict) or not c.finite(old.get("price")) or old["price"] <= 0:
            continue
        nd, od = _parse_day(new.get("as_of")), _parse_day(old.get("as_of"))
        if nd and od and 0 <= (nd - od).days <= MAX_COMPARE_GAP_DAYS:
            r = new["price"] / old["price"]
            if not (PRICE_RATIO_LIMITS[0] < r < PRICE_RATIO_LIMITS[1]):
                jumped.append("%s x%.3g" % (sym, r))
        yr = ctx.today.year
        for y in (yr, yr - 1):
            rel = "s/%s/d/%04d.json" % (sym, y)
            a, b = _try_json(os.path.join(site, *rel.split("/"))), _try_json(os.path.join(prev, *rel.split("/")))
            if not (isinstance(a, dict) and isinstance(b, dict)):
                continue
            try:
                new_c = dict(zip(a["d"], a["c"]))
                ratios = [new_c[d] / pc for d, pc in zip(b["d"], b["c"]) if d in new_c and c.finite(pc) and pc > 0 and c.finite(new_c[d])]
            except (KeyError, TypeError):
                continue
            if len(ratios) >= MIN_OVERLAP:
                m = statistics.median(ratios)
                if abs(m - 1) > MEDIAN_RATIO_TOLERANCE:
                    rescaled.append("%s x%.3g" % (sym, m))
                    break
    if jumped:
        ctx.regress("salto de nivel (precio nuevo / anterior fuera de %s) en %d símbolos: %s" % (PRICE_RATIO_LIMITS, len(jumped), ", ".join(jumped[:6])))
    if rescaled:
        ctx.regress("continuidad: la mediana de cierres solapados nuevo/anterior se aleja de 1 en %d símbolos (rescalado): %s" % (len(rescaled), ", ".join(rescaled[:6])))


# ------------------------------------------------------------------------------------------------------------------------------
# Comprobación completa
# ------------------------------------------------------------------------------------------------------------------------------
def check_site(site, registry, prev=None, *, today=None, allow_regression=False, expect_prev=False):
    """Devuelve (errores, avisos): listas de textos de una línea. Sin errores = publicable."""
    ctx = Ctx(registry, today or dt.datetime.now(dt.timezone.utc).date(), allow_regression)
    if not os.path.isdir(site):
        return ["el sitio %s no existe" % _line(site)[:80]], []
    files, _total = scan_files(site, ctx)
    if not files:
        return ctx.errors + ["el sitio está vacío"], []
    for req in ("index.json", "manifest.json", "health.json", "macro.json", "news.json"):
        if req not in files:
            ctx.err("falta %s" % req)

    docs, latest_new, daily_last, daily_years = {}, {}, {}, {}
    for rel, (kind, sym, year, _size) in sorted(files.items()):
        if sym is not None and sym not in ctx.reg:
            continue                                                            # ya es un error (scan_files): no se sigue con un símbolo desconocido
        try:
            doc = read_json(os.path.join(site, *rel.split("/")))
        except (OSError, ValueError) as e:
            ctx.err("%s ilegible (%s)" % (_line(rel)[:80], type(e).__name__))
            continue
        if kind in SPECS:
            if kind == "latest":
                if check_latest_doc(doc, sym, ctx, rel):
                    latest_new[sym] = doc
            else:
                bad = SPECS[kind](doc, ctx)
                if bad:
                    ctx.err("%s: %s" % (rel, bad))
            if kind != "manifest":
                _scan_strings(doc, rel, ctx)
            docs[kind] = doc if kind not in docs else docs[kind]
        elif kind == "daily":
            bars = check_year_doc(doc, sym, year, ctx, rel)
            if bars:
                daily_years.setdefault(sym, set()).add(year)
                if year == max(daily_years[sym]):
                    daily_last[sym] = bars[-1]
        elif kind == "intraday":
            check_intraday_doc(doc, sym, ctx, rel)
            if ctx.reg[sym]["tier"] != "hot":
                ctx.err("%s: solo los símbolos hot llevan intradía" % rel)

    _check_index(docs.get("index"), ctx)
    _check_health(docs.get("health"), ctx)
    _check_manifest(docs.get("manifest"), site, files, ctx)
    _check_symbols(files, latest_new, daily_last, ctx)
    yahoo = [e for e in registry if e["kind"] == "yahoo" and e["status"] == "active"]
    # Mínimo absoluto bajo a propósito: el alta de un registro grande se reparte en varios runs (cupo de descargas largas) y Yahoo puede limitar durante
    # el primero. Con sitio anterior, que no desaparezcan símbolos lo vigilan las reglas de continuidad.
    need = min(MIN_PUBLISHED, ceil(0.5 * len(yahoo)))
    pub = [e["symbol"] for e in yahoo if e["symbol"] in latest_new]
    if len(pub) < need:
        ctx.err("solo %d de %d símbolos activos con datos publicados (mínimo %d)" % (len(pub), len(yahoo), need))
    fresh = [s for s in pub if (ctx.today - _parse_day(latest_new[s]["as_of"])).days <= MAX_BAR_AGE_DAYS]
    if pub and len(fresh) < MIN_FRESH_FRACTION * len(pub):
        ctx.err("solo %d de %d símbolos tienen una barra de los últimos %d días: histórico congelado" % (len(fresh), len(pub), MAX_BAR_AGE_DAYS))
    extreme = [s for s in pub if latest_new[s]["change_pct"] is not None and abs(latest_new[s]["change_pct"]) > MAX_ABS_CHANGE_PCT]
    if len(extreme) > MAX_EXTREME_CHANGE_SYMBOLS:
        ctx.err("variación diaria > %d %% en %d símbolos: %s" % (MAX_ABS_CHANGE_PCT, len(extreme), ", ".join(extreme[:6])))
    stale = [s for s in pub if latest_new[s].get("stale")]
    if stale:
        ctx.warn("%d de %d símbolos con precio antiguo (stale): %s" % (len(stale), len(pub), ", ".join(sorted(stale)[:8])))
    _health_alerts(docs.get("health"), ctx, latest_new)
    if any(e["kind"] == "crypto" for e in registry) and "crypto/latest.json" not in files:
        ctx.warn("sin crypto/latest.json en este run")
    elif docs.get("crypto") and docs["crypto"].get("stale"):
        ctx.warn("crypto/latest.json reutilizado del run anterior (CoinGecko no respondió)")

    prev_ok = bool(prev) and os.path.isfile(os.path.join(prev, "manifest.json"))
    if prev_ok:
        nm = docs.get("manifest")
        if isinstance(nm, dict) and isinstance(nm.get("files"), dict):
            check_continuity(site, prev, nm["files"], latest_new, ctx)
    elif expect_prev:
        ctx.regress("se esperaba un sitio anterior (EXPECT_PREV) y no hay ninguno utilizable: no se trata como primer run")
    else:
        ctx.warn("sin sitio anterior: primer run, solo reglas absolutas")

    errors = list(ctx.errors)
    warnings = list(ctx.warnings)
    for r in ctx.regressions:
        if allow_regression:
            warnings.append(Alert("[regresión permitida] " + r))
        else:
            errors.append(r)
    return errors, warnings


def _check_index(idx, ctx):
    if not isinstance(idx, dict) or not isinstance(idx.get("symbols"), list):
        return
    rows = {r.get("s"): r for r in idx["symbols"] if isinstance(r, dict)}
    if len(rows) != len(idx["symbols"]) or set(rows) != set(ctx.reg):
        ctx.err("index.json: los símbolos no coinciden con los del registro")
        return
    for s, r in rows.items():
        e = ctx.reg[s]
        for k in ("kind", "name", "currency", "unit", "tier"):
            if r.get(k) != e[k]:
                ctx.err("index.json: %s.%s distinto del registro" % (_line(s)[:15], k))
        if e["status"] != "active" and r.get("status") != e["status"]:
            ctx.err("index.json: %s tiene un estado distinto del fijado en el registro" % _line(s)[:15])


def _check_health(h, ctx):
    if not isinstance(h, dict) or not isinstance(h.get("symbols"), dict):
        return
    extra = sorted(s for s in h["symbols"] if s not in ctx.reg)
    if extra:
        ctx.err("health.json: símbolos fuera del registro %s" % extra[:4])


def _health_alerts(h, ctx, latest_new=None):
    """Avisos del run. Solo son `alert` (Healthchecks /fail) los que merecen que el dueño se entere YA:
      (ii) quedan sin publicar más del UNPUBLISHED_ALERT_FRACTION de los símbolos activos del registro, o
      (iii) caída global de Yahoo (fallan la mayoría a la vez, o Yahoo limita con 429 y se abandona el run).
    (i) —fallo de fetch, de publish o del propio validador— ya hace fallar el job. Un símbolo suelto sin publicar (unit_mismatch, failed, suspect, un solo
    cold que falla...), el presupuesto de tiempo agotado o una cripto no listada son un ::warning:: en el log; el detalle está en health.json."""
    if not isinstance(h, dict):
        return
    run, counts = h.get("run") or {}, h.get("counts") or {}
    problems = h.get("problems") if isinstance(h.get("problems"), dict) else {}
    failed = problems.get("failed") if isinstance(problems.get("failed"), dict) else {}
    unit = problems.get("unit_mismatch") if isinstance(problems.get("unit_mismatch"), dict) else {}
    syms = h.get("symbols") if isinstance(h.get("symbols"), dict) else {}
    not_listed = sorted(s for s, v in syms.items() if isinstance(v, dict) and v.get("reason") == "not_listed")

    # (iii) caída global de Yahoo
    if run.get("yahoo_abandoned"):
        ctx.warn("Yahoo limitó (429) y se abandonó en este run; los símbolos pendientes siguen con sus datos anteriores", alert=True)
    if counts.get("outage"):
        ctx.warn("fallaron la mayoría de los símbolos a la vez (caída de Yahoo): datos anteriores conservados", alert=True)

    # (ii) demasiados símbolos activos sin publicar
    active = [s for s, e in ctx.reg.items() if e["status"] == "active"]
    latest_new = latest_new if latest_new is not None else {}
    unpub = sorted(s for s in active if s in failed or s in not_listed or (ctx.reg[s]["kind"] == "yahoo" and s not in latest_new))
    n_unpub = max(len(unpub), min(int(counts.get("failed") or 0), len(active)) if isinstance(counts.get("failed"), int) else 0)
    if active and n_unpub > UNPUBLISHED_ALERT_FRACTION * len(active):
        ctx.warn("%d de %d símbolos activos del registro sin publicar (más del %d %%): %s" % (
            n_unpub, len(active), round(UNPUBLISHED_ALERT_FRACTION * 100), ", ".join(unpub[:8])), alert=True)
    elif unpub:
        why = lambda s: failed.get(s) or ("not_listed" if s in not_listed else "sin datos")      # noqa: E731
        ctx.warn("%d símbolos sin publicar en este run (detalle en health.json): %s" % (len(unpub), ", ".join("%s (%s)" % (s, why(s)) for s in unpub[:8])))

    # el resto: solo ::warning::
    if unit:
        ctx.warn("%d símbolos no publicados por divisa/unidad distinta de la del registro: %s" % (
            len(unit), ", ".join("%s (registro %s, Yahoo %s)" % (s, v.get("registro"), v.get("yahoo") or "sin divisa") for s, v in sorted(unit.items())[:8]
                                 if isinstance(v, dict))))
    if run.get("budget_exhausted"):
        ctx.warn("presupuesto de tiempo del run agotado: se saltó trabajo")
    suspect = problems.get("suspect") or []
    if suspect:
        ctx.warn("%d símbolos no publicados por tener demasiadas barras con cierre <= 0 o no finito (datos sospechosos): %s" % (
            len(suspect), ", ".join(str(s) for s in suspect[:8])))
    if not_listed:
        ctx.warn("criptos del registro que no aparecen en el lote de CoinGecko (dormant, not_listed): %s" % ", ".join(not_listed[:8]))
    rebased = problems.get("rebased") or []
    if rebased:
        ctx.warn("series bajadas enteras otra vez por rebase (split/ajuste): %s" % ", ".join(str(s) for s in rebased[:8]))


def _check_manifest(mf, site, files, ctx):
    if not isinstance(mf, dict) or not isinstance(mf.get("files"), dict):
        return
    want = {r for r in files if r != "manifest.json"}
    if set(mf["files"]) != want:
        miss, extra = sorted(want - set(mf["files"])), sorted(set(mf["files"]) - want)
        ctx.err("manifest.json no coincide con los ficheros del sitio (sin listar: %s; sobran: %s)" % (miss[:3], extra[:3]))
        return
    total = 0
    for rel, v in mf["files"].items():
        with open(os.path.join(site, *rel.split("/")), "rb") as f:
            data = f.read()
        total += len(data)
        if len(data) != v.get("bytes") or hashlib.sha256(data).hexdigest() != v.get("sha256"):
            ctx.err("manifest.json: %s no coincide (bytes o sha256)" % _line(rel)[:80])
    if mf.get("total_bytes") != total:
        ctx.err("manifest.json: total_bytes no coincide")


def _check_symbols(files, latest_new, daily_last, ctx):
    syms = {s for (_k, s, _y, _sz) in files.values() if s}
    for s in sorted(syms):
        has_daily = any(k == "daily" and sym == s for (k, sym, _y, _sz) in files.values())
        if s in latest_new and not has_daily:
            ctx.err("s/%s: latest.json sin histórico diario" % _line(s)[:15])
        if s in latest_new and s in daily_last:
            d, cl = daily_last[s]
            lat = latest_new[s]
            if lat["as_of"] != d.isoformat() or abs(lat["price"] - cl) > 1e-6 * cl:
                ctx.err("s/%s: latest.json no coincide con la última barra diaria" % _line(s)[:15])
        if s not in latest_new and has_daily and ctx.reg[s]["status"] == "active":
            ctx.warn("s/%s: histórico sin latest.json (símbolo caducado)" % _line(s)[:15])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--site", default="site/v2")
    ap.add_argument("--registry", default="registry.json")
    ap.add_argument("--prev", default=None, help="carpeta v2 del sitio publicado anteriormente (opcional)")
    ap.add_argument("--alerts-out", default=None, help="fichero donde escribir, en UNA línea, los avisos que merecen que el dueño se entere")
    ap.add_argument("--today", default=None, help=argparse.SUPPRESS)          # solo para los tests: fecha de «hoy» (YYYY-MM-DD)
    a = ap.parse_args(argv)
    try:
        registry = c.load_registry(a.registry)
    except c.RegistryError as e:
        print("::error::registro inválido: %s" % _line(e)[:300])
        return 1
    truthy = ("1", "true", "yes", "on")
    try:
        errors, warnings = check_site(
            a.site, registry, a.prev, today=dt.date.fromisoformat(a.today) if a.today else None,
            allow_regression=os.environ.get("SANITY_ALLOW_REGRESSION", "").lower() in truthy,
            expect_prev=os.environ.get("EXPECT_PREV", "").lower() in truthy)
    except Exception as e:                                                       # noqa: BLE001 - sitio con tipos inesperados = no publicable
        print("::error::sitio malformado: %s" % _line(type(e).__name__))
        return 1
    alerts = [w for w in warnings if isinstance(w, Alert)]
    for w in warnings:
        print("::warning::" + _line(w))
    if a.alerts_out:
        try:
            with open(a.alerts_out, "w", encoding="utf-8") as f:
                f.write(" | ".join(_line(w) for w in alerts)[:900])
        except OSError as e:
            print("::warning::no se pudo escribir %s (%s)" % (_line(a.alerts_out)[:80], _line(type(e).__name__)))
    for e in errors[:MAX_ERRORS_SHOWN]:
        print("::error::" + _line(e))
    if errors:
        print("Sanity-check v2 FALLIDO (%d errores%s): no se publica." % (len(errors), "" if len(errors) <= MAX_ERRORS_SHOWN else ", se muestran %d" % MAX_ERRORS_SHOWN))
        return 1
    print("OK v2: %d ficheros, %d avisos%s" % (sum(len(fs) for _r, _d, fs in os.walk(a.site)), len(warnings),
                                              "" if a.prev else " (sin comparación con sitio anterior)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
