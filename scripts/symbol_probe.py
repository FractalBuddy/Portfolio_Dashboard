#!/usr/bin/env python3
"""
Sonda de símbolos del registro público: ¿sirve Yahoo este símbolo con la divisa y la unidad que declara `registry.json`? SOLO LEE: no genera el feed,
no escribe en el sitio publicado, no usa secretos. La lanza a mano el workflow `symbol-probe.yml`; también se puede correr en local.

    python scripts/symbol_probe.py --registry registry.json                       # todas las filas yahoo en `dormant` (las pendientes de activar)
    python scripts/symbol_probe.py --registry registry.json --symbols SPCX,PM     # solo esas (tienen que estar en el registro)
    python scripts/symbol_probe.py --registry registry.json --select all          # todas las filas yahoo (para auditar el universo entero)

Por cada símbolo: UNA petición (histórico diario de 1 mes, `auto_adjust=False`, como el generador), con >= 1 s entre peticiones, tope de símbolos por run
y parada limpia (sin reintentos) ante un 429 o tres fallos de red seguidos. Aplica las MISMAS comprobaciones que `feed_v2.py` (divisa/escala con
`check_unit`, barras con `split_bars`, congelado a FROZEN_DAYS, rango plausible del precio por `unit`) y una más estricta: en `currency`/`subunit` Yahoo
TIENE que informar la divisa (el generador, en `currency`, publica aunque Yahoo calle; para ACTIVAR un símbolo se exige verla).

Salida: tabla en Markdown (--summary-out, p. ej. $GITHUB_STEP_SUMMARY) y JSON (--json-out). Solo datos públicos: símbolo, nombre del registro, divisa y
unidad declaradas y observadas, nombre/bolsa/tipo que da Yahoo (saneados), última fecha y último cierre. Código de salida: 0 = todos OK; 1 = alguno
falla o no se pudo probar; 2 = argumentos o registro inválidos.

El veredicto OK NO comprueba que el símbolo sea la EMPRESA correcta: eso lo mira una persona comparando «nombre en el registro» con «nombre en Yahoo».
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import feed_v2 as f2
import feed_v2_common as c
import fetch_market_data as fm

MAX_SYMBOLS_DEFAULT = 60
MAX_SYMBOLS_HARD = 300             # ~5-6 min a >= 1 s por petición: cabe de sobra en el timeout del job (20 min)
MIN_GAP_SECONDS = 1.0
MAX_NET_FAILS_IN_A_ROW = 3
PERIOD = "1mo"
VERDICTS = ("ok", "empty", "frozen", "unit", "currency_missing", "price_range", "http_error", "not_tried")
_SAFE_TEXT_RE = re.compile(r"[^A-Za-z0-9 .,&()'/+\-]")


def clean(v, maxlen=60):
    """Texto de Yahoo apto para Markdown y JSON públicos: solo caracteres inocuos (nada de |, <, >, `, saltos de línea), recortado."""
    if not isinstance(v, str):
        return None
    s = _SAFE_TEXT_RE.sub("", v).strip()
    return s[:maxlen] or None


def select(registry, symbols=None, mode="dormant"):
    """Lista de entradas a probar. `symbols` (texto separado por comas) manda sobre `mode`; cada símbolo tiene que ser válido y estar en el registro."""
    yahoo = [e for e in registry if e["kind"] == "yahoo"]
    if symbols:
        want = [s.strip() for s in symbols.split(",") if s.strip()]
        by = {e["symbol"].upper(): e for e in yahoo}
        bad = [s for s in want if not c.safe_symbol(s) or s.upper() not in by]
        if bad:
            raise ValueError("símbolos que no son válidos o no están en el registro como yahoo: %d" % len(bad))
        seen, out = set(), []
        for s in want:
            if s.upper() not in seen:
                seen.add(s.upper())
                out.append(by[s.upper()])
        return out
    if mode == "all":
        return yahoo
    if mode == "dormant":
        return [e for e in yahoo if e["status"] == "dormant"]
    raise ValueError("--select debe ser dormant o all")


def judge(entry, df, meta, today):
    """Veredicto de una descarga: (veredicto, detalle) con las reglas del generador + divisa obligatoria."""
    observed = df.attrs.get("currency") if df is not None else None
    info = {"yahoo_currency": observed if isinstance(observed, str) and c.CURRENCY_RE.fullmatch(observed) else None,
            "yahoo_name": clean((meta or {}).get("longName") or (meta or {}).get("shortName")),
            "yahoo_exchange": clean((meta or {}).get("fullExchangeName") or (meta or {}).get("exchangeName"), 40),
            "yahoo_type": clean((meta or {}).get("instrumentType"), 20), "last_date": None, "last_close": None}
    if info["yahoo_currency"] is not None and f2.check_unit(entry, observed):
        return "unit", info                                                # divisa o escala distinta (GBP frente a GBp): antes que nada
    bars, _dropped = f2.split_bars(df, today, entry["unit"]) if df is not None and len(df) else ([], {})
    if bars:
        info["last_date"], info["last_close"] = bars[-1]["d"], bars[-1]["c"]
    if len(bars) < 2:
        return "empty", info                                               # Yahoo no lo sirve (o casi nada)
    if entry["unit"] in ("currency", "subunit") and info["yahoo_currency"] is None:
        return "currency_missing", info                                    # más estricto que el generador: para activar hay que VER la divisa
    if (today - f2.parse_day(bars[-1]["d"])).days > f2.FROZEN_DAYS:
        return "frozen", info
    if not c.price_plausible(entry["unit"], bars[-1]["c"]):
        return "price_range", info
    return "ok", info


def fetch(sym):
    """Una petición a Yahoo: (DataFrame, metadatos). Sin reintentos (es una sonda)."""
    tk = fm.yf.Ticker(sym)
    df = tk.history(period=PERIOD, interval="1d", auto_adjust=False)
    df = df.dropna(subset=["Close"]) if "Close" in df.columns else df
    meta = {}
    try:
        meta = dict(tk.history_metadata or {})
    except Exception:                                                     # noqa: BLE001
        meta = {}
    cur = meta.get("currency")
    if isinstance(cur, str) and c.CURRENCY_RE.fullmatch(cur):
        df.attrs["currency"] = cur
    return df, meta


def probe(entries, max_symbols, fetcher=fetch, sleep=time.sleep, clock=time.monotonic, today=None):
    """Prueba hasta `max_symbols` entradas. Devuelve la lista de resultados (uno por entrada, en orden; las que no se prueban, `not_tried`)."""
    today = today or datetime.now(timezone.utc).date()
    results, last, net_fails, stop = [], None, 0, None
    for i, e in enumerate(entries):
        base = {"symbol": e["symbol"], "name": e["name"], "currency": e["currency"], "unit": e["unit"], "status": e["status"]}
        if stop or i >= max_symbols:
            results.append({**base, "verdict": "not_tried", "why": stop or "tope de símbolos por run"})
            continue
        if last is not None:
            wait = MIN_GAP_SECONDS - (clock() - last)
            if wait > 0:
                sleep(wait)
        last = clock()
        try:
            df, meta = fetcher(e["symbol"])
            net_fails = 0
        except Exception as exc:                                          # noqa: BLE001 - cualquier fallo de Yahoo = este símbolo no se pudo probar
            if fm._is_rate_limit(exc):
                stop = "Yahoo respondió 429: parada limpia"
                results.append({**base, "verdict": "not_tried", "why": stop})
                continue
            net_fails += 1
            results.append({**base, "verdict": "http_error", "why": type(exc).__name__})
            if net_fails >= MAX_NET_FAILS_IN_A_ROW:
                stop = "%d fallos de red seguidos: parada limpia" % net_fails
            continue
        try:
            verdict, info = judge(e, df, meta, today)
        except Exception as exc:                                          # noqa: BLE001
            verdict, info = "empty", {"why": type(exc).__name__}
        results.append({**base, "verdict": verdict, **info})
    return results


def _cell(v):
    return "—" if v is None else str(v).replace("|", "/")


def summary_md(results, generated_at):
    ok = sum(r["verdict"] == "ok" for r in results)
    lines = ["## Sonda de símbolos (solo lectura, no publica nada)", "",
             "%s · %d símbolos · **%d OK** · %d con problemas" % (generated_at, len(results), ok, len(results) - ok), "",
             "Antes de activar un OK, compara a ojo «nombre en el registro» con «nombre en Yahoo» (la sonda no sabe si es la empresa correcta).", "",
             "| Símbolo | Veredicto | Registro (nombre · divisa/unidad) | Yahoo (nombre · bolsa · tipo · divisa) | Última barra |", "|---|---|---|---|---|"]
    for r in results:
        yahoo = " · ".join(_cell(r.get(k)) for k in ("yahoo_name", "yahoo_exchange", "yahoo_type", "yahoo_currency")) if "yahoo_name" in r else _cell(r.get("why"))
        last = "%s %s" % (_cell(r.get("last_date")), _cell(r.get("last_close"))) if r.get("last_date") else "—"
        lines.append("| `%s` | %s | %s · %s/%s | %s | %s |" % (r["symbol"], r["verdict"].upper() if r["verdict"] != "ok" else "OK",
                                                         _cell(r["name"]), r["currency"], r["unit"], yahoo, last))
    return "\n".join(lines) + "\n"


def main(argv=None, fetcher=fetch):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--registry", default="registry.json")
    ap.add_argument("--symbols", default="", help="símbolos separados por comas (deben estar en el registro); vacío = según --select")
    ap.add_argument("--select", default="dormant", choices=("dormant", "all"))
    ap.add_argument("--max-symbols", default=str(MAX_SYMBOLS_DEFAULT), help="tope de símbolos por run (1-%d)" % MAX_SYMBOLS_HARD)
    ap.add_argument("--json-out", default=None)
    ap.add_argument("--summary-out", default=None)
    a = ap.parse_args(argv)
    try:
        n = int(a.max_symbols)
        if not 1 <= n <= MAX_SYMBOLS_HARD:
            raise ValueError
    except ValueError:
        print("::error::--max-symbols debe ser un entero entre 1 y %d" % MAX_SYMBOLS_HARD)
        return 2
    if len(a.symbols) > 2000:
        print("::error::--symbols demasiado largo")
        return 2
    try:
        entries = select(c.load_registry(a.registry), a.symbols, a.select)
    except (c.RegistryError, ValueError) as e:
        print("::error::%s" % str(e).replace("\n", " ")[:300])
        return 2
    if not entries:
        print("sonda: nada que probar (ninguna fila yahoo en %s)" % a.select)
        return 0
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    results = probe(entries, n, fetcher=fetcher)
    md = summary_md(results, generated_at)
    if a.summary_out:
        with open(a.summary_out, "a", encoding="utf-8") as f:
            f.write(md)
    if a.json_out:
        os.makedirs(os.path.dirname(os.path.abspath(a.json_out)), exist_ok=True)
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump({"schema": 1, "generated_at": generated_at, "period": PERIOD, "results": results}, f, ensure_ascii=False, indent=1, allow_nan=False)
    counts = {v: sum(r["verdict"] == v for r in results) for v in VERDICTS}
    print("sonda: %d símbolos; %s" % (len(results), ", ".join("%s %d" % (k, v) for k, v in counts.items() if v)))
    return 0 if counts["ok"] == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
