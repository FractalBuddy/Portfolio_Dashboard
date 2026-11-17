#!/usr/bin/env python3
"""
MEDICIÓN DEL RETRASO REAL de los fondos «0P» (Morningstar) en Yahoo Finance (solo lectura; solo corre desde GitHub Actions: .github/workflows/fund-lag.yml).

Para cada ticker de config/fund_lag_tickers.json (lista FIJA y pública: fondos 0P y ETF cotizados de control) pide el histórico de 5 días con yfinance y
escribe UNA LÍNEA JSON por ticker (ver scripts/fund_lag_common.py: esquema y regla de días hábiles). Este script NO publica nada: deja las líneas en un fichero local
(--out); las empuja a la rama huérfana `probe-data` otro job aparte. No usa secretos ni datos de nadie.

Autolímite: si la primera línea del registro (la rama `probe-data`) tiene más de 7 días, no hace nada y sale con 0 (sin tocar Yahoo).
Reutiliza el ritmo y la parada de la sonda de fondos (Gate): pausa >= 1 s entre peticiones, tope total de peticiones y parada limpia, SIN reintentos, ante un 429.
"""
import argparse
import math
import os
import sys
from datetime import date, datetime, timezone

import fund_lag_common as C
from fund_probe import Gate, ProbeError, Stopped, YahooClient

PERIOD = "5d"
HISTORY_COST = 2             # igual que la sonda de fondos: un histórico cuenta como hasta 2 peticiones (zona horaria + serie)
HARD_MAX_REQUESTS = 30       # 10 tickers x 2 = 20; el tope duro deja margen pero no deja crecer


def valid_dates(history):
    """{"rows": [(date, close)]} -> fechas de las barras válidas (cierre finito y > 0). Misma regla que fund_probe.measure."""
    out = []
    for d, c in (history or {}).get("rows") or []:
        if isinstance(d, datetime):
            d = d.date()
        if isinstance(d, date) and isinstance(c, (int, float)) and not isinstance(c, bool) and math.isfinite(c) and c > 0:
            out.append(d)
    return out


def measure_all(tickers, client, gate, now):
    """-> lista de registros, uno por ticker, en el orden de la lista. `now()` da el instante UTC de cada medición."""
    recs = []
    for t in tickers:
        ticker = t["ticker"]
        if gate.stopped:
            recs.append(C.make_record(now(), ticker, "no_probado"))
            continue
        try:
            h = gate.call(lambda s=ticker: client.history(s, period=PERIOD), HISTORY_COST)
        except Stopped:
            recs.append(C.make_record(now(), ticker, "no_probado"))
            continue
        except ProbeError as pe:
            recs.append(C.make_record(now(), ticker, pe.cls if pe.cls in C.STATUSES else "parseo"))
            continue
        at = now()
        ds = valid_dates(h)
        recs.append(C.make_record(at, ticker, "ok", ds, at.date()) if ds else C.make_record(at, ticker, "vacio" if not (h or {}).get("rows") else "parseo"))
    return recs


def _cell(v):
    return "—" if v is None or v == "" else str(v)


def render_summary(recs, kinds, started, gate):
    L = ["## Retraso de los fondos 0P en Yahoo (medición)", "",
         f"Instante (UTC): {C.fmt_instant(started)} · peticiones: {gate.used}/{gate.max_requests} · parada: {gate.stopped or 'ninguna'}", "",
         "| Ticker | Tipo | Estado | Última barra | Barras (5 d) | Retraso (días hábiles TARGET) | Retraso (hábiles L-V) | Retraso (días naturales) |", "|---|---|---|---|---|---|---|---|"]
    for r in recs:
        L.append("| " + " | ".join(_cell(x) for x in (r["ticker"], kinds.get(r["ticker"]), r["status"], r["last_bar"], r["bars"], r["lag_bd"], r["lag_wd"], r["lag_days"])) + " |")
    ok = [r for r in recs if r["status"] == "ok"]
    fondos = [r["lag_bd"] for r in ok if kinds.get(r["ticker"]) == "fondo"]
    ctrl = [r["lag_bd"] for r in ok if kinds.get(r["ticker"]) == "control_etf"]
    L += ["", f"- Fondos 0P medidos: {len(fondos)} (retraso hábil mín/máx: {min(fondos) if fondos else '—'}/{max(fondos) if fondos else '—'}) · "
              f"ETF de control medidos: {len(ctrl)} (mín/máx: {min(ctrl) if ctrl else '—'}/{max(ctrl) if ctrl else '—'})",
          "- Estados: " + ", ".join(f"{s} {sum(r['status'] == s for r in recs)}" for s in C.STATUSES),
          "- Regla: retraso = días hábiles en (última barra, hoy UTC]; hoy cuenta aunque su sesión no haya terminado (ver scripts/fund_lag_common.py)."]
    return "\n".join(L) + "\n"


def _write(path, text, mode="a"):
    if path:
        with open(path, mode, encoding="utf-8") as f:
            f.write(text)


def main(argv=None, env=None, client=None, now=None, clock=None, sleep=None):
    env = os.environ if env is None else env
    ap = argparse.ArgumentParser(description="Mide el retraso de los fondos 0P de Yahoo (solo lectura).")
    ap.add_argument("--tickers", default=C.DEFAULT_TICKERS)
    ap.add_argument("--out", required=True, help="fichero donde se dejan las líneas nuevas (.jsonl)")
    ap.add_argument("--registry-head", default=None, help="fichero con la PRIMERA línea del registro actual (vacío o ausente = no hay registro)")
    ap.add_argument("--head-state", choices=("found", "missing", "error"), default="missing")
    ap.add_argument("--max-requests", type=int, default=HARD_MAX_REQUESTS)
    ap.add_argument("--max-age-days", type=int, default=C.MAX_AGE_DAYS)
    args = ap.parse_args(argv)
    now = now or (lambda: datetime.now(timezone.utc).replace(microsecond=0))
    out_gh, summary = env.get("GITHUB_OUTPUT"), env.get("GITHUB_STEP_SUMMARY")
    try:
        tickers = C.load_tickers(args.tickers)
    except ValueError:
        print("::error::La lista de tickers no es válida.")
        return 2
    head = ""
    if args.registry_head and args.head_state == "found":
        try:
            head = open(args.registry_head, encoding="utf-8").readline()
        except OSError:
            args.head_state = "error"
    started = now()
    decision, why = C.decide_run(args.head_state, head, started, args.max_age_days)
    _write(args.out, "", "w")
    if decision != "medir":
        msg = {"caducado": "Registro con más de %d días: el experimento ha terminado, no se mide (%s)." % (args.max_age_days, why),
               "sin_registro_legible": "No se pudo leer el registro de la rama probe-data: no se mide en esta ejecución (%s)." % why}[decision]
        print(("::notice::" if decision == "caducado" else "::warning::") + msg)
        _write(summary, "## Retraso de los fondos 0P en Yahoo\n\n" + msg + "\n")
        _write(out_gh, "append=false\nexpired=%s\n" % ("true" if decision == "caducado" else "false"))
        return 0
    gate = Gate(max_requests=min(max(0, args.max_requests), HARD_MAX_REQUESTS), clock=clock, sleep=sleep)
    client = client or YahooClient()
    recs = measure_all(tickers, client, gate, now)
    _write(args.out, "".join(C.dumps_record(r) + "\n" for r in recs))
    _write(summary, render_summary(recs, {t["ticker"]: t["kind"] for t in tickers}, started, gate))
    if gate.stopped:
        print(f"::warning::Medición parada limpiamente ({gate.stopped}): resultados parciales, sin reintentos.")
    print(f"Medición terminada: {len(recs)} líneas, {gate.used}/{gate.max_requests} peticiones.")
    _write(out_gh, "append=true\nexpired=false\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
