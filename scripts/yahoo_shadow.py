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
import argparse
import json
import math
import os
import re
import sys
import traceback
import unicodedata
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

import feed_v2_common as FC
import fund_lag_common as LC
import personal_v2 as PV
from fund_probe import (FUND_TYPES, HISTORY_COST, SEARCH_COST, STOPWORDS, Gate, ProbeError, Stopped, YahooClient, _quiet, load_generic,
                        normalize_history)

SYMBOL_RE = re.compile(r"^0P[0-9A-Z]{8}\.[A-Z]{1,3}$")            # DISENO_NAV_YAHOO §2.4/§3.3: sin '/', '?', '#', '%', '@', ':' ni '..'
_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_DAY_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
HARD_MAX_REQUESTS = 80                                           # DISENO §3.2: presupuesto PROPIO de la capa Yahoo por run (configurable a la baja)
PRICE_MIN, PRICE_MAX = 0.1, 10000.0                              # mismo rango que acepta el payload para un NAV (personal_v2.FUND_SPEC)
RATIO_LIMITS = (0.5, 2.0)                                        # misma banda que personal_v2.NAV_RATIO_LIMITS
OFFICIAL_CURRENCY = "EUR"                                        # el NAV oficial del payload es siempre EUR (personal_v2.FUND_SPEC)
_CURRENCY_SHAPE = re.compile(r"^[A-Za-z]{3}$")


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


# ------------------------------------------------------------------------------------------------------------------------------
# Comparación con el NAV oficial: SOLO la barra de la fecha IDÉNTICA (nunca «la más cercana»).
# ------------------------------------------------------------------------------------------------------------------------------
def official_decimals(price, minimum):
    """Decimales con los que se compara: los que trae el NAV oficial (la ficha de iShares escribe 2) y como mínimo `minimum` (87,60 llega como 87.6)."""
    exp = Decimal(repr(float(price))).normalize().as_tuple().exponent       # 88.0 -> 0 decimales; 87.65 -> 2
    return max(int(minimum), -exp if isinstance(exp, int) and exp < 0 else 0)


def diff_bp(official, yahoo, decimals):
    """(pb tras redondear Yahoo a `decimals` con mitad-hacia-arriba, pb sin redondear), en Decimal exacto (sin errores de coma flotante):
    |y - oficial| / oficial × 10 000. Se redondea porque el oficial ya viene redondeado (2 decimales): sin redondear, 87,6512 frente a 87,65 saldría
    distinto por el redondeo de la ficha y no por el dato."""
    o, y = Decimal(repr(float(official))), Decimal(repr(float(yahoo)))
    yr = y.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    return abs(yr - o) / o * 10000, abs(y - o) / o * 10000


def _valid_price(c):
    return isinstance(c, (int, float)) and not isinstance(c, bool) and math.isfinite(c) and PRICE_MIN <= c <= PRICE_MAX


def signed_lag_bd(yahoo_last, official_day):
    """Retraso de Yahoo frente al oficial en días hábiles TARGET (fund_lag_common): > 0 si la última barra de Yahoo es ANTERIOR a la fecha oficial."""
    if yahoo_last < official_day:
        return LC.business_days_between(yahoo_last, official_day)
    return -LC.business_days_between(official_day, yahoo_last)


def compare(official, official_day, history, cfg, today):
    """Un fondo con NAV oficial `official` del día `official_day` frente a su histórico de Yahoo ({"rows": [(date, cierre)], "currency"}).
    -> {"kind": ..., ...}. kind:
      comparable          hay barra válida de la MISMA fecha: bp (redondeado), raw_bp, equal (bp ≤ equal_bp), out_of_band (fuera de ×0,5-×2)
      yahoo_por_detras    la última barra válida de Yahoo es anterior a la fecha oficial (no se compara con la anterior)
      sin_fecha_comun     Yahoo tiene barras posteriores pero no la de esa fecha (no se compara con la más cercana)
      dato_invalido       hay filas pero ninguna barra válida, o la de esa fecha no lo es (precio no finito, ≤ 0 o fuera de 0,1-10 000)
      distinta_divisa     la divisa de Yahoo no es la del oficial (EUR): no se compara (D5 B: en la sombra solo se cuenta); subunit=True si es una
                          subunidad (GBp, ZAc...): NUNCA se divide ni se multiplica por 100 en silencio
      sin_divisa          Yahoo no da una divisa reconocible: no se compara
      vacio               sin filas
    Las barras de hoy o posteriores (barra abierta o fecha imposible) se ignoran. `lag_bd`: retraso de Yahoo en días hábiles (si hay barras válidas)."""
    rows = (history or {}).get("rows") if isinstance(history, dict) else None
    if not rows:
        return {"kind": "vacio"}
    cur = history.get("currency")
    if cur != OFFICIAL_CURRENCY:
        if isinstance(cur, str) and (_CURRENCY_SHAPE.match(cur) or cur in FC.SUBUNIT_FAMILIES):
            return {"kind": "distinta_divisa", "subunit": cur in FC.SUBUNIT_FAMILIES}
        return {"kind": "sin_divisa"}
    valid, exact_rows = {}, 0
    for row in rows:
        try:
            d, c = row
        except (TypeError, ValueError):
            continue
        if not isinstance(d, date) or d >= today:
            continue
        if d == official_day:
            exact_rows += 1
        if _valid_price(c):
            valid[d] = float(c)
    if not valid:
        return {"kind": "dato_invalido"}
    out = {"lag_bd": signed_lag_bd(max(valid), official_day)}
    if official_day not in valid:
        if exact_rows:
            out["kind"] = "dato_invalido"
        else:
            out["kind"] = "yahoo_por_detras" if max(valid) < official_day else "sin_fecha_comun"
        return out
    y = valid[official_day]
    bp, raw = diff_bp(official, y, official_decimals(official, cfg["min_official_decimals"]))
    out.update(kind="comparable", bp=float(round(bp, 4)), raw_bp=float(round(raw, 4)), equal=bp <= Decimal(repr(float(cfg["equal_bp"]))),
               out_of_band=not (RATIO_LIMITS[0] < y / official < RATIO_LIMITS[1]))
    return out


# ------------------------------------------------------------------------------------------------------------------------------
# Cliente real de Yahoo: el de la sonda de fondos, pero con el histórico SIN AJUSTAR (el NAV es el precio publicado; el cierre ajustado por
# dividendos de una clase de reparto no lo es) y con el periodo de la configuración.
# ------------------------------------------------------------------------------------------------------------------------------
class ShadowYahooClient(YahooClient):
    def history(self, symbol, period="5d"):
        t = self.yf.Ticker(symbol)
        df = t.history(period=period, interval="1d", actions=False, auto_adjust=False)
        try:
            meta = t.history_metadata
        except Exception:                               # noqa: BLE001 - sin metadatos: divisa desconocida («sin divisa»: no se compara)
            meta = {}
        return normalize_history(df, meta)


# ------------------------------------------------------------------------------------------------------------------------------
# Entradas: lo que dejaron watch y build de personal_v2.py en la carpeta PRIVADA del run (nunca un artefacto)
# ------------------------------------------------------------------------------------------------------------------------------
def collect_funds(directory):
    """-> (fondos, contadores, isin_a_enmascarar). fondos = [{isin, name, price, day}] ÚNICOS por ISIN (si dos usuarios siguen el mismo fondo se pide
    una vez), solo los que tienen ficha de iShares Y NAV oficial con fecha en este run (source 'ishares_nav_scrape'). Ordenados por ISIN (determinista).
    contadores: usuarios, con_ficha (ISIN únicos con ficha), sin_nav_oficial. None si faltan los ficheros (no hubo watch/build)."""
    wpath, ppath = os.path.join(directory, "watch.json"), os.path.join(directory, "payloads.json")
    if not (os.path.isfile(wpath) and os.path.isfile(ppath)):
        return None
    with open(wpath, encoding="utf-8") as f:
        raw = json.load(f)
    with open(ppath, encoding="utf-8") as f:
        payloads = json.load(f)
    users, _dropped = PV.parse_watch(raw, PV._plain)
    masks = sorted({f["isin"] for u in users for f in u["funds"]})
    official = {}
    for item in payloads if isinstance(payloads, list) else []:
        funds = item.get("payload", {}).get("funds") if isinstance(item, dict) and isinstance(item.get("payload"), dict) else None
        for f in funds if isinstance(funds, list) else []:
            if not (isinstance(f, dict) and f.get("source") == "ishares_nav_scrape" and isinstance(f.get("isin"), str)):
                continue
            day = PV._iso_day(f.get("nav_date_iso"))
            if day is not None and _valid_price(f.get("price")) and f["isin"] not in official:
                official[f["isin"]] = (float(f["price"]), day)
    with_url, names = [], {}
    for u in users:
        for f in u["funds"]:
            if f.get("url") and f["isin"] not in names:
                names[f["isin"]] = f["name"]
                with_url.append(f["isin"])
    funds = [{"isin": i, "name": names[i], "price": official[i][0], "day": official[i][1]} for i in sorted(with_url) if i in official]
    counts = {"usuarios": len(users), "con_ficha": len(with_url), "sin_nav_oficial": len(with_url) - len(funds)}
    return funds, counts, masks


# ------------------------------------------------------------------------------------------------------------------------------
# El recorrido: canario y fondos. Solo CONTADORES salen de aquí (nunca el ISIN, el símbolo, el nombre ni el precio).
# ------------------------------------------------------------------------------------------------------------------------------
COUNTER_KEYS = ("en_sombra", "comparables", "iguales", "fuera_de_banda", "sin_0p", "sin_emparejar", "ambiguos", "distinta_divisa", "subunidad",
                "sin_divisa", "sin_fecha_comun", "yahoo_por_detras", "dato_invalido", "vacios", "no_probados", "lag_n")
ERROR_KEYS = ("429", "sin_red", "parseo")
CANARY_STOP = frozenset({"429", "sin_red", "parseo", "vacio", "dato_invalido", "no_probado"})     # canario mal = problema de Yahoo: no se pide nada más
CANARY_CONFIG = frozenset({"sin_0p", "sin_emparejar", "ambiguo"})                                 # Yahoo responde, pero el canario configurado no sirve


def new_counters():
    c = {k: 0 for k in COUNTER_KEYS}
    c.update(errores={k: 0 for k in ERROR_KEYS}, max_bp=None, max_bp_raw=None, lag_sum=0, by_date={}, canario="no_probado", canario_lag=None)
    return c


def _error(c, cls):
    if cls == "vacio":
        c["vacios"] += 1
    else:
        c["errores"][cls if cls in ERROR_KEYS else "parseo"] += 1


def _history(client, gate, symbol, period):
    with _quiet():
        return gate.call(lambda: client.history(symbol, period=period), HISTORY_COST)


def _canary(cfg, client, gate, mask, today):
    """Canario: un fondo PÚBLICO de la lista genérica (no de nadie), primero en cada run. -> estado: ok | sin_0p | sin_emparejar | ambiguo | vacio |
    dato_invalido | 429 | sin_red | parseo | no_probado. Con «ok», el retraso de su última barra en días hábiles hasta hoy."""
    mask(cfg["canary_isin"])
    try:
        status, sym = resolve(cfg["canary_isin"], cfg["canary_name"], client, gate, mask, cfg["name_min_coverage"])
        if status != "ok":
            return status, None
        h = _history(client, gate, sym, cfg["history_period"])
    except Stopped:
        return "no_probado", None
    except ProbeError as pe:
        return pe.cls, None
    rows = [(d, cl) for d, cl in (h or {}).get("rows") or [] if isinstance(d, date) and d < today and _valid_price(cl)]
    if not (h or {}).get("rows"):
        return "vacio", None
    if not rows:
        return "dato_invalido", None
    return "ok", LC.business_days_between(max(d for d, _ in rows), today)


def run_shadow(funds, cfg, client, gate, mask, today):
    """Canario primero; luego, por fondo: búsqueda por ISIN -> `0P…` validado -> histórico corto -> comparación de la misma fecha. -> contadores."""
    c = new_counters()
    c["en_sombra"] = len(funds)
    c["canario"], c["canario_lag"] = _canary(cfg, client, gate, mask, today)
    stop = c["canario"] in CANARY_STOP
    for f in funds:
        if stop or gate.stopped:
            c["no_probados"] += 1
            continue
        try:
            status, sym = resolve(f["isin"], f["name"], client, gate, mask, cfg["name_min_coverage"])
            if status != "ok":
                c[{"sin_0p": "sin_0p", "sin_emparejar": "sin_emparejar", "ambiguo": "ambiguos"}[status]] += 1
                continue
            h = _history(client, gate, sym, cfg["history_period"])
        except Stopped:
            c["no_probados"] += 1
            continue
        except ProbeError as pe:
            _error(c, pe.cls)
            continue
        except Exception:                               # noqa: BLE001 - clase cerrada, sin el texto (podría citar el símbolo)
            _error(c, "parseo")
            continue
        out = compare(f["price"], f["day"], h, cfg, today)
        kind = out["kind"]
        if "lag_bd" in out:
            c["lag_sum"] += out["lag_bd"]
            c["lag_n"] += 1
        if kind == "comparable":
            c["comparables"] += 1
            c["iguales"] += bool(out["equal"])
            c["fuera_de_banda"] += bool(out["out_of_band"])
            c["max_bp"] = max(c["max_bp"] or 0.0, out["bp"])
            c["max_bp_raw"] = max(c["max_bp_raw"] or 0.0, out["raw_bp"])
            day = f["day"].isoformat()
            b = c["by_date"].setdefault(day, [0, 0, 0.0])
            b[0] += 1
            b[1] += bool(out["equal"])
            b[2] = max(b[2], out["bp"])
        elif kind == "distinta_divisa":
            c["distinta_divisa"] += 1
            c["subunidad"] += bool(out.get("subunit"))
        elif kind == "vacio":
            c["vacios"] += 1
        else:
            c[kind] += 1
    c["peticiones"], c["tope"], c["parada"] = gate.used, gate.max_requests, gate.stopped or ("canario" if stop else None)
    return c


def _fmt_bp(v):
    return "—" if v is None else "%.2f" % v


def counters_line(c, counts, cfg, now):
    errs = sum(c["errores"].values())
    lag = "—" if not c["lag_n"] else "%+.2f" % (c["lag_sum"] / c["lag_n"])
    return ("sombra Yahoo: %d fondos con NAV oficial (de %d con ficha; %d sin NAV oficial; %d usuarios) · comparables %d (iguales a ≤ %g pb %d; dif. máx. %s pb, "
            "sin redondear %s pb; fuera de banda %d) · sin 0P %d · sin emparejar %d · ambiguos %d · distinta divisa %d (subunidad %d) · sin divisa %d · "
            "sin dato de la misma fecha %d (Yahoo por detrás %d) · dato inválido %d · vacíos %d · errores de Yahoo %d%s · sin probar %d · "
            "retraso medio de Yahoo frente a iShares %s d hábiles (n=%d) · canario %s%s · peticiones %d/%d · parada %s [run %s]") % (
        c["en_sombra"], counts["con_ficha"], counts["sin_nav_oficial"], counts["usuarios"], c["comparables"], cfg["equal_bp"], c["iguales"],
        _fmt_bp(c["max_bp"]), _fmt_bp(c["max_bp_raw"]), c["fuera_de_banda"], c["sin_0p"], c["sin_emparejar"], c["ambiguos"], c["distinta_divisa"],
        c["subunidad"], c["sin_divisa"], c["sin_fecha_comun"] + c["yahoo_por_detras"], c["yahoo_por_detras"], c["dato_invalido"], c["vacios"], errs,
        (" (" + ", ".join("%s %d" % (k, n) for k, n in c["errores"].items() if n) + ")") if errs else "", c["no_probados"], lag, c["lag_n"],
        c["canario"], "" if c["canario_lag"] is None else " (última barra a %d d hábiles)" % c["canario_lag"], c["peticiones"], c["tope"],
        c["parada"] or "ninguna", now.strftime("%Y-%m-%dT%H:%MZ"))


# ------------------------------------------------------------------------------------------------------------------------------
# Libro de contadores (persistencia para evaluar la puerta de 10 días hábiles). SOLO números, fechas de NAV/run y estados de listas cerradas: ni ISIN,
# ni símbolos, ni nombres, ni precios. El workflow lo guarda en la caché de Actions (sin commits, sin artefactos, sin permisos de escritura).
# Una entrada por LECTURA: {day, slot, at, official_max, c: {contadores}, by_date: [[fecha NAV, comparables, iguales, máx. pb], ...]}.
# ------------------------------------------------------------------------------------------------------------------------------
LEDGER_SCHEMA = 1
LEDGER_MAX_READS = 500
LEDGER_C_KEYS = ("en_sombra", "comparables", "iguales", "max_bp", "fuera_de_banda", "sin_0p", "sin_emparejar", "ambiguos", "distinta_divisa", "subunidad",
                 "sin_divisa", "sin_fecha_comun", "yahoo_por_detras", "dato_invalido", "vacios", "errores", "no_probados", "lag_sum", "lag_n", "canario",
                 "peticiones", "pendientes")
CANARY_STATES = ("ok",) + tuple(sorted(CANARY_STOP | CANARY_CONFIG))
_AT_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}Z$")


def _day_ok(v):
    if not (isinstance(v, str) and _DAY_RE.match(v)):
        return False
    try:
        date.fromisoformat(v)
    except ValueError:
        return False
    return True


def _read_ok(r):
    if not (isinstance(r, dict) and set(r) == {"day", "slot", "at", "official_max", "c", "by_date"}):
        return False
    if not (_day_ok(r["day"]) and _int(0, 3)(r["slot"]) and isinstance(r["at"], str) and _AT_RE.match(r["at"]) and r["at"][:10] == r["day"]
            and (r["official_max"] is None or _day_ok(r["official_max"]))):
        return False
    c = r["c"]
    if not (isinstance(c, dict) and set(c) == set(LEDGER_C_KEYS)):
        return False
    for k in LEDGER_C_KEYS:
        v = c[k]
        if k == "canario":
            ok = v in CANARY_STATES
        elif k == "max_bp":
            ok = v is None or _num(0, 1e6)(v)
        elif k == "lag_sum":
            ok = _int(-100000, 100000)(v)
        else:
            ok = _int(0, 100000)(v)
        if not ok:
            return False
    bd = r["by_date"]
    return isinstance(bd, list) and len(bd) <= 60 and all(
        isinstance(e, list) and len(e) == 4 and _day_ok(e[0]) and _int(0, 100000)(e[1]) and _int(0, 100000)(e[2]) and e[2] <= e[1] and _num(0, 1e6)(e[3])
        for e in bd)


def load_ledger(path):
    """-> (lecturas, problema). Sin fichero = libro vacío (primer run o caché perdida). Cualquier cosa que no cumpla el esquema = libro vacío y un
    problema de texto FIJO (nunca se cita el contenido)."""
    if not path or not os.path.isfile(path):
        return [], None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return [], "ilegible"
    if not (isinstance(data, dict) and set(data) == {"schema", "reads"} and data["schema"] == LEDGER_SCHEMA and isinstance(data["reads"], list)
            and len(data["reads"]) <= LEDGER_MAX_READS and all(_read_ok(r) for r in data["reads"])):
        return [], "ilegible"
    return data["reads"], None


def ledger_entry(c, now, slot, official_max):
    errs = sum(c["errores"].values())
    cc = {k: c[k] for k in LEDGER_C_KEYS if k not in ("errores", "pendientes", "max_bp")}
    cc.update(errores=errs, max_bp=c["max_bp"], pendientes=c["yahoo_por_detras"] + c["vacios"] + errs + c["no_probados"])
    return {"day": now.date().isoformat(), "slot": slot, "at": now.strftime("%Y-%m-%dT%H:%MZ"), "official_max": official_max, "c": cc,
            "by_date": [[d, v[0], v[1], v[2]] for d, v in sorted(c["by_date"].items())]}


def save_ledger(path, reads, now, keep_days):
    cutoff = date.fromordinal(now.date().toordinal() - keep_days).isoformat()
    reads = [r for r in reads if r["day"] >= cutoff][-LEDGER_MAX_READS:]
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"schema": LEDGER_SCHEMA, "reads": reads}, f, separators=(",", ":"), allow_nan=False)
    os.replace(tmp, path)
    return reads


def decide(now, cfg, reads, n_funds, official_max):
    """(leer, motivo, franja). Frecuencia (D6 A): como mucho una lectura por franja UTC de `slots_utc` y `max_reads_per_day` al día, con PARADA
    TEMPRANA: no se vuelve a leer si una lectura anterior de hoy ya lo midió todo (nada pendiente, mismos fondos, misma fecha oficial, canario bien)."""
    slot = next((i for i, (a, b) in enumerate(cfg["slots_utc"]) if a <= now.hour < b), None)
    if slot is None:
        return False, "fuera de franja", None
    today = [r for r in reads if r["day"] == now.date().isoformat()]
    if any(r["slot"] == slot for r in today):
        return False, "franja ya leída hoy", slot
    if len(today) >= cfg["max_reads_per_day"]:
        return False, "tope de lecturas del día", slot
    for r in today:
        if (r["c"]["pendientes"] == 0 and r["c"]["canario"] == "ok" and r["c"]["en_sombra"] == n_funds and r["official_max"] == official_max):
            return False, "todo medido en la franja anterior", slot
    return True, None, slot


def evaluate_gate(reads, cfg):
    """Puerta D7 con los datos del libro. Comparables ÚNICOS por fecha de NAV oficial: de todas las lecturas que midieron una misma fecha se toma la
    que más comparables tuvo (a igualdad, la de peor diferencia); así una fecha leída por la mañana y otra vez al día siguiente no cuenta doble.
    La diferencia máxima es la peor de TODAS las lecturas (conservador). Días hábiles = días TARGET (L-V sin festivos TARGET2) con alguna lectura con
    comparables. Veredicto: NO PASA en cuanto hay uno > max_bp; EN CURSO hasta min_business_days; luego PASA si iguales/comparables ≥ gate_equal_pct."""
    best = {}
    worst = None
    for r in reads:
        for d, comp, eq, mx in r["by_date"]:
            cur = best.get(d)
            if cur is None or (comp, mx) > (cur[0], cur[2]):
                best[d] = (comp, eq, mx)
            worst = mx if worst is None else max(worst, mx)
    comp = sum(v[0] for v in best.values())
    eq = sum(v[1] for v in best.values())
    is_bd = lambda d: LC.is_business_day(date.fromisoformat(d))          # noqa: E731
    days_read = {r["day"] for r in reads if is_bd(r["day"])}
    days_comp = {r["day"] for r in reads if r["c"]["comparables"] and is_bd(r["day"])}
    pct = 100.0 * eq / comp if comp else None
    if worst is not None and worst > cfg["max_bp"]:
        verdict = "NO PASA"
    elif len(days_comp) < cfg["min_business_days"]:
        verdict = "EN CURSO"
    elif pct + 1e-9 >= cfg["gate_equal_pct"]:
        verdict = "PASA"
    else:
        verdict = "NO PASA"
    return {"verdict": verdict, "days_comp": len(days_comp), "days_read": len(days_read), "comparables": comp, "iguales": eq, "pct": pct, "max_bp": worst,
            "dates": len(best)}


def gate_line(g, cfg):
    return ("sombra Yahoo: puerta D7: %s · %d de %d días hábiles con comparables (%d con lectura) · %d comparables únicos (%d fechas de NAV) · "
            "%d iguales a ≤ %g pb (%s) · dif. máx. %s pb · umbral: ≥ %g %% iguales y ninguno > %g pb") % (
        g["verdict"], g["days_comp"], cfg["min_business_days"], g["days_read"], g["comparables"], g["dates"], g["iguales"], cfg["equal_bp"],
        "—" if g["pct"] is None else "%.1f %%" % g["pct"], _fmt_bp(g["max_bp"]), cfg["gate_equal_pct"], cfg["max_bp"])


def render_summary(g, cfg, this_run, reads):
    L = ["## Sombra NAV Yahoo (F1): solo contadores", "", "**Puerta D7: %s**" % g["verdict"], "", gate_line(g, cfg).replace("sombra Yahoo: ", ""), "",
         "**Este run:** " + this_run.replace("sombra Yahoo: ", ""), "",
         "### Lecturas (las 30 más recientes)", "",
         "| Lectura (UTC) | Fondos | Comparables | Iguales | Máx. pb | Sin 0P | Sin emparejar | Distinta divisa | Sin dato misma fecha | Retraso medio (d háb.) | Canario | Peticiones |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in reversed(reads[-30:]):
        c = r["c"]
        lag = "—" if not c["lag_n"] else "%+.2f" % (c["lag_sum"] / c["lag_n"])
        L.append("| %s | %d | %d | %d | %s | %d | %d | %d | %d | %s | %s | %d |" % (
            r["at"].replace("T", " ").rstrip("Z"), c["en_sombra"], c["comparables"], c["iguales"], _fmt_bp(c["max_bp"]), c["sin_0p"], c["sin_emparejar"],
            c["distinta_divisa"], c["sin_fecha_comun"] + c["yahoo_por_detras"], lag, c["canario"], c["peticiones"]))
    L += ["", "Cómo leerlo: «PASA» = ≥ %g días hábiles con comparables, ≥ %g %% de comparables iguales a ≤ %g pb y ninguno > %g pb. «Retraso medio» > 0 = "
          "Yahoo va por detrás de iShares. Solo contadores: ningún ISIN, símbolo, nombre ni precio." % (cfg["min_business_days"], cfg["gate_equal_pct"],
                                                                                                       cfg["equal_bp"], cfg["max_bp"]), ""]
    return "\n".join(L)


# ------------------------------------------------------------------------------------------------------------------------------
# Programa
# ------------------------------------------------------------------------------------------------------------------------------
def _append(path, text):
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)


def _main(a, env, client, now, clock, sleep):
    cfg, problem = load_config(a.config, a.generic)
    if cfg is None:
        say("::warning::sombra Yahoo: %s; no se mide nada en este run" % problem)
        return 0
    if not cfg["enabled"]:
        say("sombra Yahoo: desactivada en config/yahoo_shadow.json (enabled=false): no se pide nada a Yahoo")
        return 0
    got = collect_funds(a.dir)
    if got is None:
        say("sombra Yahoo: sin datos de esta ejecución (no hay lista o payloads de personal_v2): no se mide nada")
        return 0
    funds, counts, masks = got
    for isin in masks:                                  # TODAS las máscaras antes de la primera petición
        say("::add-mask::" + isin)
    reads, problem = load_ledger(a.ledger)
    if problem:
        say("::warning::sombra Yahoo: libro de contadores ilegible: se empieza de cero (la puerta vuelve a contar desde hoy)")
    official_max = max((f["day"] for f in funds), default=None)
    official_max = official_max.isoformat() if official_max else None
    if not funds:
        this_run = "sombra Yahoo: 0 fondos con NAV oficial en este run (%d con ficha): no se pide nada a Yahoo" % counts["con_ficha"]
        do_read = False
    else:
        do_read, why, slot = decide(now, cfg, reads, len(funds), official_max)
        this_run = "sombra Yahoo: no se lee en este run (%s) [run %s]" % (why, now.strftime("%Y-%m-%dT%H:%MZ"))
    c = None
    if do_read:
        seen = set()

        def mask(value):
            if value not in seen:
                seen.add(value)
                say("::add-mask::" + value)
        gate = Gate(max_requests=cfg["max_requests_per_run"], clock=clock, sleep=sleep)
        if client is None:
            with _quiet():
                client = ShadowYahooClient()
        c = run_shadow(funds, cfg, client, gate, mask, now.date())
        this_run = counters_line(c, counts, cfg, now)
        if a.ledger:
            reads = save_ledger(a.ledger, reads + [ledger_entry(c, now, slot, official_max)], now, cfg["ledger_days"])
            PV._set_output(env, "ledger", "written")
    say(this_run)
    g = evaluate_gate(reads, cfg)
    say(gate_line(g, cfg))
    _append(env.get("GITHUB_STEP_SUMMARY"), render_summary(g, cfg, this_run, reads))
    if c is not None:
        if c["canario"] in CANARY_STOP:
            say("::warning::sombra Yahoo: el canario (fondo público) falla (%s): no se ha pedido nada de los fondos en este run; no afecta a lo publicado"
                % c["canario"])
        elif c["canario"] in CANARY_CONFIG:
            say("::warning::sombra Yahoo: el canario no resuelve a un 0P con su nombre (revisa canary_isin en config/yahoo_shadow.json); la medida sigue")
        if c["parada"] in ("429", "sin_red"):
            say("::warning::sombra Yahoo: parada limpia (%s) sin reintentos; no afecta a lo publicado" % c["parada"])
    return 0


def main(argv=None, env=None, client=None, now=None, clock=None, sleep=None):
    env = os.environ if env is None else env
    ap = argparse.ArgumentParser(description="Sombra NAV Yahoo (F1): mide, no publica.")
    ap.add_argument("--dir", required=True, help="carpeta PRIVADA de personal_v2 (watch.json y payloads.json)")
    ap.add_argument("--config", default="config/yahoo_shadow.json")
    ap.add_argument("--generic", default="config/fund_probe_isins_generic.json")
    ap.add_argument("--ledger", default=None, help="libro de contadores agregados (JSON; lo guarda la caché de Actions)")
    a = ap.parse_args(argv)
    now = now or datetime.now(timezone.utc)
    try:
        return _main(a, env, client, now, clock, sleep)
    except Exception as exc:                            # noqa: BLE001 - NUNCA rojo y nunca el texto de la excepción (podría citar un ISIN o un símbolo)
        tb = traceback.extract_tb(exc.__traceback__)[-1]
        say("::warning::sombra Yahoo: fallo inesperado (%s en %s:%d); no afecta a lo publicado" % (type(exc).__name__, os.path.basename(tb.filename), tb.lineno))
        return 0


if __name__ == "__main__":
    sys.exit(main())
