#!/usr/bin/env python3
"""
SOMBRA NAV Yahoo (fase F1 de auditorias/DISENO_NAV_YAHOO.md del repo privado de la app): ¿coincide el precio que da Yahoo (símbolos `0P…`, datos de
Morningstar) con el NAV OFICIAL de iShares de la MISMA fecha?

    python scripts/yahoo_shadow.py --dir $RUNNER_TEMP/personal --config config/yahoo_shadow.json --ledger $RUNNER_TEMP/yahoo-shadow/ledger.json

Corre en personal-full.yml DESPUÉS de publicar (paso con `continue-on-error`, sin token). Lee lo que ya dejaron en la carpeta privada los pasos
`watch` y `build` de personal_v2.py (watch.json y payloads.json: los ISIN con ficha de iShares y su NAV oficial con fecha), y para cada fondo con NAV
oficial: una búsqueda por ISIN en Yahoo -> `0P…` cuyo nombre cuadre (sin confirmación humana: es solo medida y NO se guarda, D1) -> histórico corto
(`5d`, sin ajustar) -> compara con el NAV oficial SOLO si hay barra de la fecha idéntica.

NO PUBLICA NADA: no llama a Supabase, no escribe en el repo, ni en artefactos, ni en el payload. No cambia lo que ve ningún usuario.

PRIVACIDAD (el repo y los logs son PÚBLICOS): solo salen CONTADORES agregados (log, resumen del job y libro de contadores). Nunca un ISIN, un `0P…`, un
nombre, un precio ni una URL. Cada ISIN y cada `0P…` se enmascaran (::add-mask::) antes de usarlos; todo lo que imprime yfinance se descarta; los errores
se reducen a una clase cerrada (sin el texto de la excepción). El libro de contadores (un JSON con números y fechas de NAV) lo guarda el workflow en la
caché de Actions; no lleva nada que identifique un fondo.

NUNCA ROJO: cualquier fallo (Yahoo caído, 429, configuración mala, libro ilegible, excepción inesperada) acaba en un aviso y salida 0.
"""
import json
import re
import sys
import unicodedata

from fund_probe import FUND_TYPES, SEARCH_COST, STOPWORDS, _quiet, load_generic

SYMBOL_RE = re.compile(r"^0P[0-9A-Z]{8}\.[A-Z]{1,3}$")            # DISENO_NAV_YAHOO §2.4/§3.3: sin '/', '?', '#', '%', '@', ':' ni '..'
_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_DAY_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
HARD_MAX_REQUESTS = 80                                           # DISENO §3.2: presupuesto PROPIO de la capa Yahoo por run (configurable a la baja)
PRICE_MIN, PRICE_MAX = 0.1, 10000.0                              # mismo rango que acepta el payload para un NAV (personal_v2.FUND_SPEC)
RATIO_LIMITS = (0.5, 2.0)                                        # misma banda que personal_v2.NAV_RATIO_LIMITS


def say(msg):
    """Única vía de salida de texto: contadores, códigos y fechas de NAV; nunca datos que identifiquen un fondo."""
    print(msg)
    sys.stdout.flush()


# ------------------------------------------------------------------------------------------------------------------------------
# Configuración versionada (config/yahoo_shadow.json): el interruptor `enabled` y los parámetros. Esquema CERRADO.
# ------------------------------------------------------------------------------------------------------------------------------
def _int(lo, hi):
    return lambda v: isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def _num(lo, hi):
    return lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi


def _slots(v):
    if not (isinstance(v, list) and 1 <= len(v) <= 4):
        return False
    for s in v:
        if not (isinstance(s, list) and len(s) == 2 and all(_int(0, 24)(h) for h in s) and s[0] < s[1]):
            return False
    flat = sorted(v)
    return all(a[1] <= b[0] for a, b in zip(flat, flat[1:]))     # franjas sin solaparse


CONFIG_SPEC = {
    "enabled": lambda v: isinstance(v, bool),
    "min_business_days": _int(1, 60),
    "gate_equal_pct": _num(50, 100),
    "equal_bp": _num(0, 100),
    "max_bp": _num(1, 1000),
    "slots_utc": _slots,
    "max_reads_per_day": _int(1, 4),
    "max_requests_per_run": _int(3, HARD_MAX_REQUESTS),
    "history_period": lambda v: v in ("5d", "1mo"),
    "min_official_decimals": _int(0, 6),
    "name_min_coverage": _num(0.3, 1),
    "canary_isin": lambda v: isinstance(v, str) and bool(_ISIN_RE.match(v)),
    "ledger_days": _int(14, 400),
}


def load_config(path, generic_path):
    """-> (config, None) o (None, problema). El problema es un texto FIJO (el fichero es público, pero el mensaje no cita valores).
    El canario TIENE que ser un ISIN de la lista genérica pública (config/fund_probe_isins_generic.json); su nombre esperado sale de esa lista."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None, "configuración de la sombra ilegible"
    if not isinstance(data, dict) or set(data) != set(CONFIG_SPEC):
        return None, "configuración de la sombra con claves de más o de menos"
    bad = sorted(k for k, ok in CONFIG_SPEC.items() if not ok(data[k]))
    if bad:
        return None, "configuración de la sombra con valores no válidos: " + ", ".join(bad)
    try:
        generic = {e["isin"]: e["name"] for e in load_generic(generic_path)}
    except ValueError:
        return None, "lista genérica pública ilegible"
    if data["canary_isin"] not in generic:
        return None, "el canario no está en la lista genérica pública"
    cfg = dict(data)
    cfg["canary_name"] = generic[data["canary_isin"]]
    return cfg, None


# ------------------------------------------------------------------------------------------------------------------------------
# Validación del nombre que devuelve Yahoo (D1 A: en la sombra no hay confirmación humana, así que la regla es CONSERVADORA:
# ante la duda, «sin emparejar» y no se compara; un falso «sin emparejar» solo resta muestras, un falso emparejamiento ensucia la medida).
# ------------------------------------------------------------------------------------------------------------------------------
ABBREV = {"idx": "index", "ind": "index", "wld": "world", "wrld": "world", "dvlpd": "developed", "devlpd": "developed", "dev": "developed",
          "gbl": "global", "glb": "global", "glbl": "global", "eq": "equity", "eqty": "equity", "equities": "equity", "mkts": "markets",
          "mkt": "market", "emerg": "emerging", "govt": "government", "gov": "government", "fd": "fund", "accum": "acc", "accumulating": "acc",
          "accumulation": "acc", "distributing": "dist", "distribution": "dist", "inc": "dist", "hdg": "hedged", "hgd": "hedged", "sml": "small"}
CURRENCIES = frozenset({"eur", "usd", "gbp", "chf", "jpy", "sek", "nok", "dkk", "pln", "czk", "huf", "cad", "aud", "nzd", "hkd", "sgd", "cny", "zar"})
SCOPE = frozenset({"small", "mid", "large", "micro", "esg", "sri", "screened", "sustainable", "climate", "paris", "pab", "ctb", "value", "growth",
                   "momentum", "quality", "dividend", "minimum", "volatility", "equal", "weight", "weighted", "islamic", "leveraged", "short",
                   "enhanced", "ex", "select", "multifactor", "factor"})


def name_tokens(text):
    """Palabras normalizadas: minúsculas, sin acentos ni puntuación, abreviaturas de Morningstar expandidas, sin palabras vacías de la sonda."""
    t = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii").lower()
    return {ABBREV.get(w, w) for w in re.findall(r"[a-z0-9]+", t)} - STOPWORDS


def name_match(expected, found, min_coverage):
    """(True, "ok") o (False, motivo). Motivos, en este orden: «divisa» (las dos traen divisa y no coinciden), «clase» (Acc frente a Dist),
    «cubierta» (solo una dice Hedged), «letra» (letras de clase distintas, p. ej. S frente a D), «alcance» (una sola dice Small, ESG, Value...),
    «cobertura» (el nombre de Yahoo no contiene al menos `min_coverage` de las palabras del esperado)."""
    e, f = name_tokens(expected), name_tokens(found)
    if not e or not f:
        return False, "cobertura"
    ce, cf = e & CURRENCIES, f & CURRENCIES
    if ce and cf and ce != cf:
        return False, "divisa"
    if {"acc", "dist"} & e and {"acc", "dist"} & f and ({"acc", "dist"} & e) != ({"acc", "dist"} & f):
        return False, "clase"
    if ("hedged" in e) != ("hedged" in f):
        return False, "cubierta"
    le, lf = {w for w in e if len(w) == 1 and w.isalpha()}, {w for w in f if len(w) == 1 and w.isalpha()}
    if le and lf and le != lf:
        return False, "letra"
    if (e ^ f) & SCOPE:
        return False, "alcance"
    if len(e & f) / len(e) + 1e-9 < min_coverage:
        return False, "cobertura"
    return True, "ok"


def _coverage(expected, found):
    e = name_tokens(expected)
    return len(e & name_tokens(found)) / len(e) if e else 0.0


def resolve(isin, expected_name, client, gate, mask, min_coverage):
    """UNA búsqueda por ISIN -> ("ok", símbolo) | ("sin_0p", None) | ("sin_emparejar", None) | ("ambiguo", None).
    Solo candidatas MUTUALFUND/ETF cuyo símbolo cumple SYMBOL_RE (D9 A: solo `0P…`). Cada `0P…` devuelto se enmascara antes de nada.
    Lanza ProbeError/Stopped (clase cerrada) si la búsqueda falla o la sombra está parada."""
    with _quiet():
        quotes = gate.call(lambda: client.search(isin), SEARCH_COST)
    cands, seen = [], set()
    for q in quotes if isinstance(quotes, list) else []:
        if not (isinstance(q, dict) and q.get("quoteType") in FUND_TYPES and isinstance(q.get("symbol"), str) and SYMBOL_RE.match(q["symbol"])):
            continue
        if q["symbol"] in seen:
            continue
        seen.add(q["symbol"])
        mask(q["symbol"])
        names = [n for n in (q.get("longname"), q.get("shortname")) if isinstance(n, str) and n]
        cands.append((q["symbol"], names))
    if not cands:
        return "sin_0p", None
    ok = []
    for sym, names in cands:
        good = [n for n in names if name_match(expected_name, n, min_coverage)[0]]
        if good:
            ok.append((max(_coverage(expected_name, n) for n in good), sym))
    if not ok:
        return "sin_emparejar", None
    ok.sort(reverse=True)
    if len(ok) >= 2 and abs(ok[0][0] - ok[1][0]) < 1e-9:
        return "ambiguo", None
    return "ok", ok[0][1]
