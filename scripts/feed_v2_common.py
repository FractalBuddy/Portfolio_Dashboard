"""
Piezas compartidas por el generador del feed v2 (`feed_v2.py`), su validador (`sanity_check_v2.py`), el descargador del sitio publicado
(`site_download.py`) y el módulo de datos propios (`personal_v2.py`).

SOLO biblioteca estándar, a propósito: el validador y el descargador se ejecutan en el job `publish` (el único con permisos de
publicación), que no instala dependencias de terceros.

Contiene: el contrato de rutas (`/v2/...`), la validación del símbolo (BE-04), las unidades de precio (BE-01/BE-02: «GBp» ≠ «GBP»),
la carga y validación de `registry.json` y las constantes de tamaño.
"""
import datetime
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
# Metadatos OPCIONALES de una fila (no se publican en el feed; sirven para documentar de dónde sale y para ampliar el universo por listas):
#   country: país de la empresa/emisor, ISO 3166-1 alfa-2 en mayúsculas ("US", "SA", "KR").
#   lists:   {"<lista>@<AAAA-MM-DD de corte>": rango (entero >= 1) o null si el rango exacto no está confirmado}, p. ej. {"top50-mcap@2026-11-13": 46}.
# Cualquier otro campo sigue siendo un error (nada de ISIN, URL, ids de ficha ni datos del dueño).
OPTIONAL_REGISTRY_FIELDS = frozenset({"country", "lists"})
COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
LIST_ID_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{0,39}@(\d{4}-\d{2}-\d{2})$")
MAX_LISTS_PER_SYMBOL = 10
MAX_LIST_RANK = 100_000
MAX_NAME_LEN = 80
MAX_REGISTRY_SYMBOLS = 400
# Listas PUBLICADAS (F5): clave opcional `lists` en la RAÍZ del registro, {"<lista>@<AAAA-MM-DD>": {"source": "https://...", "as_of": "AAAA-MM-DD",
# "size": N, "symbols": [N símbolos del registro, en orden de rango]}}. Solo se define una lista cuando está COMPLETA (len(symbols) == size), con fuente
# https y fecha igual a la del id; si no, el registro entero es inválido (no hay publicación a medias). Lo que valida se publica tal cual en
# `index.json.lists` (ver index_lists). Las anotaciones por fila (`symbols[i].lists`) siguen siendo metadatos no publicados y tienen que coincidir
# con la definición de la raíz cuando esta existe.
MAX_LISTS = 10
MAX_LIST_SIZE = MAX_REGISTRY_SYMBOLS           # una lista no puede tener más símbolos que el registro
MAX_LIST_SOURCE_LEN = 300
LIST_FIELDS = frozenset({"source", "as_of", "size", "symbols"})
_SOURCE_RE = re.compile(r"^https://[A-Za-z0-9.\-]+(?::\d{1,5})?(?:/[^\s<>\x00-\x1f\x7f]*)?$")
_NAME_BAD_RE = re.compile(r"[<>\x00-\x1f\x7f]")

# Frecuencia mínima entre dos refrescos de un símbolo según su nivel. hot = en cada run (AU01-F1: el disparador externo lanza el workflow cada ~60 min en
# sesión; 45 min deja 15 min de margen para el desfase de arranque entre dos runs y queda 5 min por encima de la puerta de frescura de 40 min),
# warm = ~1 vez al día, cold = ~1 vez por semana.
TIER_MIN_INTERVAL_HOURS = {"hot": 0.75, "warm": 20, "cold": 6 * 24}
# Un símbolo `invalid`/`delisted` se reintenta como mucho una vez a la semana; `dormant` no se pide nunca.
RETRY_INVALID_HOURS = 7 * 24
FAIL_STREAK_INVALID = 5


class RegistryError(ValueError):
    """registry.json inválido; el mensaje lista TODOS los problemas."""


def registry_problems(doc):
    """Lista de textos con lo que está mal en un registro ya parseado (vacía = válido)."""
    out = []
    if not isinstance(doc, dict) or set(doc) - {"schema", "symbols", "lists"}:
        return ["la raíz debe ser un objeto con solo `schema`, `symbols` y, opcional, `lists`"]
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
        extra = sorted(set(r) - REGISTRY_FIELDS - OPTIONAL_REGISTRY_FIELDS)
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
        if "country" in r and not (isinstance(r["country"], str) and COUNTRY_RE.fullmatch(r["country"])):
            out.append("%s: country inválido (ISO 3166-1 alfa-2 en mayúsculas)" % where)
        if "lists" in r and not _lists_ok(r["lists"]):
            out.append("%s: lists inválido ({\"<lista>@AAAA-MM-DD\": rango >= 1 o null}, de 1 a %d listas)" % (where, MAX_LISTS_PER_SYMBOL))
    if "lists" in doc:
        out += list_problems(doc["lists"], rows)
    return out


def _list_id_ok(k):
    m = LIST_ID_RE.fullmatch(k) if isinstance(k, str) else None
    if not m:
        return None
    try:
        return datetime.date.fromisoformat(m.group(1))
    except ValueError:
        return None


def list_problems(lists, rows):
    """Problemas de la definición de listas de la raíz del registro (vacía = todas publicables). `rows`: filas del registro."""
    if not isinstance(lists, dict) or not 1 <= len(lists) <= MAX_LISTS:
        return ["lists (raíz) debe ser un objeto con 1 a %d listas" % MAX_LISTS]
    out = []
    by = {r["symbol"].upper(): r for r in rows if isinstance(r, dict) and isinstance(r.get("symbol"), str)}
    for lid, d in lists.items():
        cut = _list_id_ok(lid)
        where = "lists[%s]" % (lid if cut else "?")
        if cut is None:
            out.append("%s: id de lista inválido (<lista>@AAAA-MM-DD)" % where)
            continue
        if not isinstance(d, dict):
            out.append("%s: no es un objeto" % where)
            continue
        if set(d) != LIST_FIELDS:
            out.append("%s: campos %s (se esperan %s)" % (where, sorted(map(str, d)), sorted(LIST_FIELDS)))
        src = d.get("source")
        if not (isinstance(src, str) and len(src) <= MAX_LIST_SOURCE_LEN and _SOURCE_RE.fullmatch(src)):
            out.append("%s: source ausente o no es una URL https" % where)
        if d.get("as_of") != cut.isoformat():
            out.append("%s: as_of ausente o distinta de la fecha del id" % where)
        size = d.get("size")
        if not (type(size) is int and 1 <= size <= MAX_LIST_SIZE):
            out.append("%s: size inválido (entero de 1 a %d)" % (where, MAX_LIST_SIZE))
            size = None
        syms = d.get("symbols")
        if not isinstance(syms, list):
            out.append("%s: symbols no es una lista" % where)
            continue
        if size is not None and len(syms) != size:
            out.append("%s: lista incompleta (%d de %d símbolos)" % (where, len(syms), size))
        seen = set()
        members = {x.upper() for x in syms if isinstance(x, str)}
        for pos, s in enumerate(syms, 1):
            key = s.upper() if isinstance(s, str) else None
            if key is not None and key in seen:
                out.append("%s: símbolo repetido %s" % (where, s if safe_symbol(s) else "?"))
                continue
            if key is None or key not in by or by[key]["symbol"] != s:
                out.append("%s: el símbolo %s no está en el registro" % (where, s if safe_symbol(s) else "?"))
                continue
            seen.add(key)
            ann = by[key].get("lists")
            if not isinstance(ann, dict) or lid not in ann:
                out.append("%s: %s no lleva la anotación de la lista en su fila" % (where, s))
            elif ann[lid] is not None and ann[lid] != pos:
                out.append("%s: %s tiene rango %r en su fila y posición %d en la lista" % (where, s, ann[lid], pos))
        for key, r in by.items():
            if isinstance(r.get("lists"), dict) and lid in r["lists"] and key not in members:
                out.append("%s: %s lleva la anotación de la lista pero no está en ella" % (where, r["symbol"]))
    return out


def index_lists(lists):
    """Lo que se publica en `index.json.lists` (None = no se publica la clave). `lists` ya validado por registry_problems."""
    if not lists:
        return None
    return {lid: {"id": lid, "source": d["source"], "as_of": d["as_of"], "symbols": list(d["symbols"])} for lid, d in lists.items()}


def _lists_ok(v):
    if not isinstance(v, dict) or not 1 <= len(v) <= MAX_LISTS_PER_SYMBOL:
        return False
    for k, rank in v.items():
        m = LIST_ID_RE.fullmatch(k) if isinstance(k, str) else None
        if not m:
            return False
        try:
            datetime.date.fromisoformat(m.group(1))
        except ValueError:
            return False
        if rank is not None and not (type(rank) is int and 1 <= rank <= MAX_LIST_RANK):
            return False
    return True


def load_registry(path):
    """Lee y valida registry.json. Devuelve la lista de entradas (dicts). Lanza RegistryError si algo está mal."""
    return load_registry_doc(path)[0]


def load_registry_doc(path):
    """Como load_registry, pero devuelve (filas, listas validadas de la raíz; {} si no hay)."""
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError) as e:
        raise RegistryError("registry ilegible: %s" % type(e).__name__)
    problems = registry_problems(doc)
    if problems:
        raise RegistryError("; ".join(problems[:12]) + (" (+%d más)" % (len(problems) - 12) if len(problems) > 12 else ""))
    return doc["symbols"], doc.get("lists", {})


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


def shard_path(sym):
    """Trozo del manifiesto de un símbolo (formato de manifiesto 2)."""
    assert safe_symbol(sym)
    return "s/%s/manifest.json" % sym


# Manifiesto (MANIFEST_ESCALA). Dos formatos que conviven, los dos dentro de schema 2:
#   formato 1  `manifest.json`: ruta -> bytes + sha256 de TODOS los ficheros de datos. Lo que lee hoy el cliente; crece ~1,3 KB por símbolo.
#   formato 2  `manifest-root.json` (ficheros globales + huella del trozo de cada símbolo, ~0,1 KB por símbolo) y `s/<SYM>/manifest.json` (los ficheros de
#              ese símbolo, SIN generated_at: si no cambian sus ficheros, el trozo es idéntico byte a byte). Es aditivo: el formato 1 no cambia.
# Ningún manifiesto se lista a sí mismo ni a los otros: los dos formatos describen el mismo conjunto de ficheros de DATOS.
MANIFEST_FORMAT = 2
MANIFEST_ROOT = "manifest-root.json"
MANIFEST_KINDS = ("manifest", "manifest_root", "manifest_shard")

_SYM_PART = r"[A-Za-z0-9.^=\-]{1,15}"
PATH_RES = {
    "index": re.compile(r"^index\.json$"),
    "manifest": re.compile(r"^manifest\.json$"),
    "manifest_root": re.compile(r"^manifest-root\.json$"),
    "manifest_shard": re.compile(r"^s/(%s)/manifest\.json$" % _SYM_PART),
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
        sym = m.group(1) if kind in ("latest", "daily", "intraday", "manifest_shard") else None
        if sym is not None and not safe_symbol(sym):
            return None
        year = int(m.group(2)) if kind == "daily" else None
        if year is not None and not (YEAR_MIN <= year <= YEAR_MAX):
            return None
        return kind, sym, year
    return None


# Tope de tamaño por tipo de fichero (bytes, sin comprimir) y total del sitio. Los topes salen de las mediciones de `tests/test_feed_v2.py`
# con holgura; el validador los hace cumplir.
# manifest_root: ~105 B por símbolo (huella del trozo) + los globales: 600 KB dan para ~5.500 símbolos. manifest_shard: ≤ 12 ficheros por símbolo
# (10 años + latest + intradía) a ~120 B cada uno (~1,5 KB); 4 KB deja margen sin dejar pasar un trozo desbocado.
MAX_BYTES = {
    "index": 120_000, "manifest": 600_000, "latest": 8_000, "daily": 90_000, "intraday": 60_000,
    "crypto": 400_000, "macro": 250_000, "news": 120_000, "health": 200_000,
    "manifest_root": 600_000, "manifest_shard": 4_000,
}
MAX_SITE_BYTES = 120_000_000
MAX_SITE_FILES = 12_000
# El validador avisa (::warning:: en el log, no bloquea ni manda /fail a Healthchecks) cuando un tope pasa de esta fracción: MAX_BYTES de cada tipo (el
# fichero más grande), MAX_SITE_FILES, MAX_SITE_BYTES y MAX_REGISTRY_SYMBOLS.
HEADROOM_WARN_FRACTION = 0.5
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

