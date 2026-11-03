"""
Piezas compartidas por el generador del feed v2 (`feed_v2.py`), su validador (`sanity_check_v2.py`), el descargador del sitio publicado
(`site_download.py`) y el módulo de datos propios (`personal_v2.py`).

SOLO biblioteca estándar, a propósito: el validador y el descargador se ejecutan en el job `publish` (el único con permisos de
publicación), que no instala dependencias de terceros.

Contiene: el contrato de rutas (`/v2/...`), la validación del símbolo (BE-04), las unidades de precio (BE-01/BE-02: «GBp» ≠ «GBP»),
la carga y validación de `registry.json` y las constantes de tamaño.
"""
import json
import math
import os
import re
from urllib.parse import quote

SCHEMA = 2

# ------------------------------------------------------------------------------------------------------------------------------
# Símbolos (BE-04). El feed v2 es genérico: el símbolo acaba en una ruta de fichero y en una URL de Yahoo/RSS, así que se valida SIEMPRE con
# esta regex (la del contrato) y, además, se rechazan los que serían peligrosos como nombre de directorio (`.`, `..`, `.DJI`): la regex los
# admite porque el punto es legítimo en `BRK.B` o `SAN.MC`, pero un símbolo que empieza por punto sería un directorio oculto (Jekyll lo
# ignoraría) o un recorrido de ruta.
# ------------------------------------------------------------------------------------------------------------------------------
SYMBOL_RE = re.compile(r"^[A-Za-z0-9.^=\-]{1,15}$")


def safe_symbol(sym):
    """True si `sym` es un símbolo admisible para el feed v2 (regex del contrato, sin punto inicial ni `..`)."""
    return isinstance(sym, str) and SYMBOL_RE.fullmatch(sym) is not None and not sym.startswith(".") and ".." not in sym


def quote_symbol(sym):
    """Símbolo codificado para ir en una URL (`^GSPC` -> `%5EGSPC`). Lanza ValueError si no es admisible: nunca se interpola sin validar."""
    if not safe_symbol(sym):
        raise ValueError("símbolo no admisible")
    return quote(sym, safe="")


# ------------------------------------------------------------------------------------------------------------------------------
# Divisa y unidad (BE-01). `currency` se publica TAL CUAL (distingue mayúsculas: «GBp» = peniques, «GBP» = libras). `unit` dice qué es el
# número: `currency` (importe en esa divisa por acción/participación/contrato), `subunit` (importe en la SUBUNIDAD: peniques, centavos; el
# precio en divisa mayor es price / 100), `points` (nivel de un índice, no es un importe) o `rate` (tipo de cambio: unidades de `currency` por
# unidad de la divisa base del par).
# ------------------------------------------------------------------------------------------------------------------------------
UNITS = ("currency", "subunit", "points", "rate")
# Divisas cotizadas en subunidad -> (divisa mayor, divisor). Varias grafías son la MISMA unidad (GBp y GBX son peniques).
SUBUNIT_FAMILIES = {
    "GBp": ("GBP", 100), "GBX": ("GBP", 100),
    "ZAc": ("ZAR", 100), "ZAC": ("ZAR", 100),
    "ILA": ("ILS", 100), "ILa": ("ILS", 100),
    "USd": ("USD", 100), "USX": ("USD", 100),
}
CURRENCY_RE = re.compile(r"^[A-Za-z]{3,4}$")


def currency_family(cur):
    """(divisa mayor en mayúsculas, divisor) de una divisa de cotización. `GBp` -> ('GBP', 100); `GBP` -> ('GBP', 1)."""
    if cur in SUBUNIT_FAMILIES:
        return SUBUNIT_FAMILIES[cur]
    return (str(cur).upper(), 1)


def price_unit_divisor(currency, unit):
    """100 si la unidad es la subunidad de la divisa; 1 en el resto de casos."""
    return currency_family(currency)[1] if unit == "subunit" else 1


def same_unit(a, b):
    """¿Son la misma divisa Y la misma escala? `GBp`/`GBX` sí; `GBp`/`GBP` no (×100)."""
    return currency_family(a) == currency_family(b)


# ------------------------------------------------------------------------------------------------------------------------------
# Registro (registry.json)
# ------------------------------------------------------------------------------------------------------------------------------
KINDS = ("yahoo", "crypto")
TIERS = ("hot", "warm", "cold")
STATUSES = ("active", "dormant", "invalid", "delisted")
REGISTRY_FIELDS = frozenset({"symbol", "kind", "name", "currency", "unit", "tier", "status", "fail_streak"})
MAX_NAME_LEN = 80
MAX_REGISTRY_SYMBOLS = 400
_NAME_BAD_RE = re.compile(r"[<>\x00-\x1f\x7f]")

# Frecuencia mínima entre dos refrescos de un símbolo según su nivel (el cron es 4 veces al día: hot = en cada run, warm = ~1 vez al día).
TIER_MIN_INTERVAL_HOURS = {"hot": 3, "warm": 20, "cold": 6 * 24}
# Un símbolo `invalid`/`delisted` se reintenta como mucho una vez a la semana; `dormant` no se pide nunca.
RETRY_INVALID_HOURS = 7 * 24
FAIL_STREAK_INVALID = 5


class RegistryError(ValueError):
    """registry.json inválido; el mensaje lista TODOS los problemas."""


def registry_problems(doc):
    """Lista de textos con lo que está mal en un registro ya parseado (vacía = válido)."""
    out = []
    if not isinstance(doc, dict) or set(doc) - {"schema", "symbols"}:
        return ["la raíz debe ser un objeto con solo `schema` y `symbols`"]
    if doc.get("schema") != 1:
        out.append("schema del registro != 1")
    rows = doc.get("symbols")
    if not isinstance(rows, list) or not rows:
        return out + ["`symbols` debe ser una lista no vacía"]
    if len(rows) > MAX_REGISTRY_SYMBOLS:
        out.append("demasiados símbolos (%d > %d)" % (len(rows), MAX_REGISTRY_SYMBOLS))
    seen = set()
    for i, r in enumerate(rows):
        where = "symbols[%d]" % i
        if not isinstance(r, dict):
            out.append("%s no es un objeto" % where)
            continue
        extra = sorted(set(r) - REGISTRY_FIELDS)
        missing = sorted(REGISTRY_FIELDS - set(r))
        if extra:
            out.append("%s: campos no permitidos %s" % (where, extra))
        if missing:
            out.append("%s: faltan campos %s" % (where, missing))
        if missing:
            continue
        sym = r["symbol"]
        where = "%s (%s)" % (where, sym if isinstance(sym, str) and len(sym) < 20 else "?")
        if not safe_symbol(sym):
            out.append("%s: símbolo no admisible" % where)
        elif sym.upper() in seen:
            out.append("%s: símbolo repetido" % where)
        else:
            seen.add(sym.upper())
        if r["kind"] not in KINDS:
            out.append("%s: kind %r" % (where, r["kind"]))
        name = r["name"]
        if not (isinstance(name, str) and 1 <= len(name) <= MAX_NAME_LEN and not _NAME_BAD_RE.search(name)):
            out.append("%s: name inválido" % where)
        cur, unit = r["currency"], r["unit"]
        if not (isinstance(cur, str) and CURRENCY_RE.fullmatch(cur)):
            out.append("%s: currency inválida" % where)
        elif r["unit"] not in UNITS:
            out.append("%s: unit %r" % (where, unit))
        elif (unit == "subunit") != (cur in SUBUNIT_FAMILIES):
            # «GBp» declarada como `currency` (o «GBP» como `subunit`) es exactamente el error ×100 que este contrato quiere impedir.
            out.append("%s: currency %r no es coherente con unit %r" % (where, cur, unit))
        if r["tier"] not in TIERS:
            out.append("%s: tier %r" % (where, r["tier"]))
        if r["status"] not in STATUSES:
            out.append("%s: status %r" % (where, r["status"]))
        fs = r["fail_streak"]
        if not (type(fs) is int and 0 <= fs <= 1000):
            out.append("%s: fail_streak inválido" % where)
    return out


def load_registry(path):
    """Lee y valida registry.json. Devuelve la lista de entradas (dicts). Lanza RegistryError si algo está mal."""
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError) as e:
        raise RegistryError("registry ilegible: %s" % type(e).__name__)
    problems = registry_problems(doc)
    if problems:
        raise RegistryError("; ".join(problems[:12]) + (" (+%d más)" % (len(problems) - 12) if len(problems) > 12 else ""))
    return doc["symbols"]


# ------------------------------------------------------------------------------------------------------------------------------
# Rutas del sitio publicado (todo bajo /v2/). Las rutas se construyen SOLO con símbolos validados.
# ------------------------------------------------------------------------------------------------------------------------------
YEAR_MIN, YEAR_MAX = 1900, 2200


def latest_path(sym):
    assert safe_symbol(sym)
    return "s/%s/latest.json" % sym


def year_path(sym, year):
    assert safe_symbol(sym) and YEAR_MIN <= int(year) <= YEAR_MAX
    return "s/%s/d/%04d.json" % (sym, int(year))


def intraday_path(sym):
    assert safe_symbol(sym)
    return "s/%s/i.json" % sym


_SYM_PART = r"[A-Za-z0-9.^=\-]{1,15}"
PATH_RES = {
    "index": re.compile(r"^index\.json$"),
    "manifest": re.compile(r"^manifest\.json$"),
    "latest": re.compile(r"^s/(%s)/latest\.json$" % _SYM_PART),
    "daily": re.compile(r"^s/(%s)/d/(\d{4})\.json$" % _SYM_PART),
    "intraday": re.compile(r"^s/(%s)/i\.json$" % _SYM_PART),
    "crypto": re.compile(r"^crypto/latest\.json$"),
    "macro": re.compile(r"^macro\.json$"),
    "news": re.compile(r"^news\.json$"),
    "health": re.compile(r"^health\.json$"),
}


def classify_path(rel):
    """('latest'|'daily'|..., símbolo|None, año|None) de una ruta relativa permitida; None si la ruta no es del contrato."""
    if not isinstance(rel, str) or len(rel) > 120 or "\\" in rel or rel.startswith("/"):
        return None
    for kind, rx in PATH_RES.items():
        m = rx.fullmatch(rel)
        if not m:
            continue
        sym = m.group(1) if kind in ("latest", "daily", "intraday") else None
        if sym is not None and not safe_symbol(sym):
            return None
        year = int(m.group(2)) if kind == "daily" else None
        if year is not None and not (YEAR_MIN <= year <= YEAR_MAX):
            return None
        return kind, sym, year
    return None


# Tope de tamaño por tipo de fichero (bytes, sin comprimir) y total del sitio. Los topes salen de las mediciones de `tests/test_feed_v2.py`
# con holgura; el validador los hace cumplir.
MAX_BYTES = {
    "index": 120_000, "manifest": 600_000, "latest": 8_000, "daily": 90_000, "intraday": 60_000,
    "crypto": 400_000, "macro": 250_000, "news": 120_000, "health": 200_000,
}
MAX_SITE_BYTES = 120_000_000
MAX_SITE_FILES = 12_000
# Años de histórico diario que se conservan por símbolo (los más antiguos se podan: cada año es un fichero que hay que volver a incluir
# en el artefacto de Pages en cada run).
HISTORY_YEARS = 10
LATEST_SPARKLINE_BARS = 30


# ------------------------------------------------------------------------------------------------------------------------------
# JSON determinista
# ------------------------------------------------------------------------------------------------------------------------------
def dumps(obj):
    """JSON compacto y estable (mismos datos => mismos bytes => mismo hash). Sin NaN/Infinity (no son JSON válido)."""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = dumps(obj).encode("ascii")
    with open(path, "wb") as f:
        f.write(data)
    return len(data)


def finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


# ------------------------------------------------------------------------------------------------------------------------------
# Calidad de las series (AU-06). Funciones puras, solo biblioteca estándar: las usan el generador y el validador del job `publish`.
# ------------------------------------------------------------------------------------------------------------------------------
SPIKE_UP = 1.5               # un cierre que sube más de un 50 % (x > 1,5) o baja más de un 50 % (x < 0,5) respecto a sus dos vecinos, que se parecen entre sí,
SPIKE_DOWN = 0.5             # ...es un pico aislado (tick erróneo)
NEIGHBOR_SPREAD = 1.5        # «se parecen»: el mayor de los dos vecinos no supera 1,5 veces el menor (si no, es una tendencia, no un pico que vuelve)
MAX_GAP_DAYS = 370           # más de un año natural sin una sola barra dentro de la serie = hueco (falta un fichero de año): el símbolo es sospechoso
PLAUSIBLE_PRICE = {          # (mínimo, máximo) razonables del cierre por unit; el esquema solo acota 1e-9..1e9. Holgados a propósito: atrapan un tick roto, no un dato raro
    "currency": (1e-4, 1e7),     # acciones/ETF/materias primas en divisa (Berkshire A ~ 7e5)
    "subunit": (1e-2, 1e8),      # peniques/centavos (GBp, ZAc...): x100 respecto a la divisa
    "points": (1e-2, 1e7),       # índices y tipos de interés
    "rate": (1e-5, 1e6),         # tipos de cambio (USD/IDR ~ 1,6e4)
}


def price_plausible(unit, price):
    """¿El precio entra en el rango plausible de su unit? Falso para no finitos, <= 0 y units desconocidas."""
    rng = PLAUSIBLE_PRICE.get(unit)
    return bool(rng) and finite(price) and rng[0] <= price <= rng[1]


def spike_indices(closes):
    """Posiciones de los cierres que son un PICO AISLADO: saltan más de ±50 % respecto a su barra anterior y a la siguiente, que se parecen entre sí
    (la serie vuelve al nivel de antes). Una tendencia o un crash que se mantiene NO es un pico (la barra siguiente ya está en el nuevo nivel); tampoco
    dos barras malas seguidas, ni la primera o la última de la serie (sin vecino a un lado no se puede juzgar)."""
    out = []
    for i in range(1, len(closes) - 1):
        p, x, q = closes[i - 1], closes[i], closes[i + 1]
        if not (finite(p) and finite(x) and finite(q) and p > 0 and x > 0 and q > 0):
            continue
        lo, hi = min(p, q), max(p, q)
        if hi <= NEIGHBOR_SPREAD * lo and (x > SPIKE_UP * hi or x < SPIKE_DOWN * lo):
            out.append(i)
    return out

