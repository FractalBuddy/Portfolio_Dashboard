#!/usr/bin/env python3
"""
Núcleo SIN dependencias (solo biblioteca estándar) de la medición del retraso de los fondos «0P» de Yahoo Finance:
calendario de días hábiles, esquema del registro (una línea JSON por ticker y ejecución), validación y autolímite.

Lo usan scripts/fund_lag.py (medir), scripts/fund_lag_report.py (informe) y el job de escritura del workflow (validar lo que se va a publicar:
ese job no instala nada de terceros, solo ejecuta este fichero con el python del runner).

REGLA DE DÍAS HÁBILES (la única definición de «retraso» de todo el experimento):
  retraso = número de días hábiles d con  última_barra < d <= hoy,  donde «hoy» es la fecha UTC del instante de la medición.
  * Hoy cuenta aunque su sesión no haya terminado: un ETF con la barra de hoy da 0; un fondo cuya última barra es la del día hábil anterior da 1.
  * Si hoy no es hábil (sábado, domingo, festivo) no suma: viernes con la barra del viernes, medido el sábado, da 0.
  * Calendario «target» (por defecto, el campo `lag_bd`): lunes a viernes salvo los 6 festivos TARGET2 (1 ene, Viernes Santo, Lunes de Pascua, 1 may, 25 y 26 dic).
  * Calendario «weekdays» (campo `lag_wd`): solo lunes a viernes. Se guardan los dos para no tener que decidir antes de ver los datos.
  * `lag_days` = días naturales (hoy - última barra), por si se quiere otra lectura.
  Limitación: los festivos de la bolsa de Frankfurt (24 y 31 de diciembre, por ejemplo) no son festivos TARGET; los de Londres tampoco coinciden.
"""
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone

STATUSES = ("ok", "vacio", "429", "sin_red", "parseo", "no_probado")
RECORD_KEYS = ("measured_at", "ticker", "status", "last_bar", "bars", "dates", "lag_bd", "lag_wd", "lag_days")
MAX_AGE_DAYS = 7                      # autolímite: la primera línea del registro más vieja que esto => el job sale en verde sin medir
MAX_LINE_CHARS = 1000
MAX_NEW_LINES = 30                    # una ejecución escribe una línea por ticker (la lista tiene 10)
MAX_TICKERS = 20
_TICKER = re.compile(r"^[A-Z0-9][A-Z0-9.^=-]{0,19}$")
_INSTANT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_ISIN_SHAPE = re.compile(r"\b[A-Z]{2}(?=[A-Z0-9]*\d)[A-Z0-9]{10}\b")
DEFAULT_TICKERS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "fund_lag_tickers.json")


# ------------------------------------------------------------------------------------------------------------------------------
# Calendario
# ------------------------------------------------------------------------------------------------------------------------------
def easter_sunday(year):
    """Domingo de Pascua (calendario gregoriano; algoritmo anónimo de Meeus/Jones/Butcher)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    el = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * el) // 451
    month = (h + el - 7 * m + 114) // 31
    day = (h + el - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def target_holidays(year):
    """Festivos TARGET2 del año: 1 ene, Viernes Santo, Lunes de Pascua, 1 may, 25 dic, 26 dic."""
    easter = easter_sunday(year)
    return {date(year, 1, 1), easter - timedelta(days=2), easter + timedelta(days=1), date(year, 5, 1), date(year, 12, 25), date(year, 12, 26)}


def is_business_day(d, calendar="target"):
    if d.weekday() >= 5:
        return False
    return calendar == "weekdays" or d not in target_holidays(d.year)


def business_days_between(last, today, calendar="target"):
    """Días hábiles en el intervalo (last, today]. 0 si today <= last. Ver la REGLA en la cabecera del módulo."""
    n, d = 0, last + timedelta(days=1)
    while d <= today:
        n += is_business_day(d, calendar)
        d += timedelta(days=1)
    return n


# ------------------------------------------------------------------------------------------------------------------------------
# Instantes y tickers
# ------------------------------------------------------------------------------------------------------------------------------
def fmt_instant(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_instant(s):
    if not isinstance(s, str) or not _INSTANT.match(s):
        raise ValueError("instante no válido")
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def load_tickers(path=DEFAULT_TICKERS):
    """Lista fija del repo -> [{"ticker", "kind"}]. ValueError si la estructura no es válida (formato de ticker, duplicados, ISIN, tamaño)."""
    try:
        data = json.loads(open(path, encoding="utf-8").read())
    except (OSError, ValueError) as exc:
        raise ValueError("lista de tickers ilegible") from exc
    if not isinstance(data, list) or not data or len(data) > MAX_TICKERS:
        raise ValueError("la lista de tickers debe ser una lista no vacía de como mucho %d" % MAX_TICKERS)
    out, seen = [], set()
    for e in data:
        if not isinstance(e, dict) or set(e) != {"ticker", "kind"} or e["kind"] not in ("fondo", "control_etf") or not isinstance(e["ticker"], str):
            raise ValueError("entrada de la lista de tickers no válida")
        t = e["ticker"]
        if not _TICKER.match(t) or _ISIN_SHAPE.search(t) or t in seen:
            raise ValueError("ticker no válido o duplicado")
        seen.add(t)
        out.append({"ticker": t, "kind": e["kind"]})
    return out


# ------------------------------------------------------------------------------------------------------------------------------
# Registro (una línea JSON por ticker y ejecución)
# ------------------------------------------------------------------------------------------------------------------------------
def make_record(measured_at, ticker, status, dates=None, today=None):
    """Registro con TODAS las claves. `dates` = fechas (date) de las barras válidas; `today` = fecha UTC de la medición."""
    if status != "ok":
        return {"measured_at": fmt_instant(measured_at), "ticker": ticker, "status": status, "last_bar": None, "bars": None, "dates": [],
                "lag_bd": None, "lag_wd": None, "lag_days": None}
    today = today or measured_at.astimezone(timezone.utc).date()
    ds = sorted(set(dates))
    last = ds[-1]
    return {"measured_at": fmt_instant(measured_at), "ticker": ticker, "status": "ok", "last_bar": last.isoformat(), "bars": len(dates),
            "dates": [d.isoformat() for d in ds], "lag_bd": business_days_between(last, today, "target"),
            "lag_wd": business_days_between(last, today, "weekdays"), "lag_days": max(0, (today - last).days)}


def _is_date(s):
    if not isinstance(s, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):      # fromisoformat también acepta «2026-W01-1» y similares
        return False
    try:
        date.fromisoformat(s)
        return True
    except ValueError:
        return False


def _is_int(v, lo, hi):
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def check_record(rec, allowed_tickers=None):
    """-> lista de problemas (vacía = válido). Nunca devuelve el contenido del registro, solo el nombre de la regla incumplida."""
    if not isinstance(rec, dict) or tuple(sorted(rec)) != tuple(sorted(RECORD_KEYS)):
        return ["claves"]
    bad = []
    try:
        parse_instant(rec["measured_at"])
    except ValueError:
        bad.append("measured_at")
    if not isinstance(rec["ticker"], str) or not _TICKER.match(rec["ticker"]) or (allowed_tickers is not None and rec["ticker"] not in allowed_tickers):
        bad.append("ticker")
    if rec["status"] not in STATUSES:
        bad.append("status")
    ok = rec["status"] == "ok"
    if ok:
        if not (_is_date(rec["last_bar"]) and _is_int(rec["bars"], 1, 400) and _is_int(rec["lag_bd"], 0, 4000) and _is_int(rec["lag_wd"], 0, 4000)
                and _is_int(rec["lag_days"], 0, 4000)):
            bad.append("medida")
        if not (isinstance(rec["dates"], list) and 1 <= len(rec["dates"]) <= 400 and all(_is_date(d) for d in rec["dates"])
                and rec["dates"] == sorted(set(rec["dates"])) and rec["dates"][-1] == rec["last_bar"]):
            bad.append("dates")
    elif any(rec[k] is not None for k in ("last_bar", "bars", "lag_bd", "lag_wd", "lag_days")) or rec["dates"] != []:
        bad.append("campos_de_error")
    return bad


def check_lines(text, allowed_tickers=None, max_lines=MAX_NEW_LINES):
    """Texto .jsonl -> (registros, problemas). Cada línea: JSON válido, claves exactas, tipos y rangos, ticker de la lista, longitud acotada."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    problems, recs = [], []
    if not lines:
        return recs, ["vacio"]
    if len(lines) > max_lines:
        problems.append("demasiadas_lineas")
    for i, ln in enumerate(lines, 1):
        if len(ln) > MAX_LINE_CHARS:
            problems.append("linea %d: longitud" % i)
            continue
        try:
            rec = json.loads(ln)
        except ValueError:
            problems.append("linea %d: json" % i)
            continue
        p = check_record(rec, allowed_tickers)
        if p:
            problems.append("linea %d: %s" % (i, "+".join(p)))
        else:
            recs.append(rec)
    return recs, problems


def dumps_record(rec):
    return json.dumps(rec, ensure_ascii=True, separators=(",", ":"))


# ------------------------------------------------------------------------------------------------------------------------------
# Autolímite
# ------------------------------------------------------------------------------------------------------------------------------
def decide_run(head_state, head_line, now, max_age_days=MAX_AGE_DAYS):
    """¿Se mide en esta ejecución? -> («medir» | «caducado» | «sin_registro_legible», motivo).
    head_state: «found» (la rama existe y trae la primera línea), «missing» (no hay rama o fichero: primera ejecución) o «error» (no se pudo leer: no se mide,
    para no seguir midiendo sin fin si en realidad el registro ya caducó). Primera línea ilegible => se considera caducado (con aviso): un experimento sin
    fecha de inicio comprobable no se alarga solo."""
    if head_state == "missing":
        return "medir", "sin registro previo"
    if head_state != "found":
        return "sin_registro_legible", "no se pudo leer el registro"
    if not (head_line or "").strip():
        return "medir", "registro vacío"
    try:
        first = json.loads(head_line.strip())
        started = parse_instant(first["measured_at"])
    except (ValueError, KeyError, TypeError):
        return "caducado", "primera línea ilegible"
    age = now - started
    if age > timedelta(days=max_age_days):
        return "caducado", "la primera línea tiene %d días (> %d)" % (age.days, max_age_days)
    return "medir", "la primera línea tiene %d días" % age.days


# ------------------------------------------------------------------------------------------------------------------------------
# CLI mínima para el job de escritura: `python3 fund_lag_common.py validate FICHERO` (stdlib; no se instala nada)
# ------------------------------------------------------------------------------------------------------------------------------
def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2 or argv[0] != "validate":
        print("uso: fund_lag_common.py validate FICHERO.jsonl", file=sys.stderr)
        return 2
    try:
        allowed = {e["ticker"] for e in load_tickers()}
        text = open(argv[1], encoding="utf-8").read()
    except (OSError, ValueError, UnicodeDecodeError):
        print("::error::no se pudo leer la lista de tickers o el fichero a publicar")
        return 2
    recs, problems = check_lines(text, allowed)
    if problems:
        print("::error::el fichero a publicar no es válido: " + "; ".join(problems[:10]))
        return 1
    print("Validado: %d líneas." % len(recs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
