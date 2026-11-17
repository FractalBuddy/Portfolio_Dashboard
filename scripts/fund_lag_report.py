#!/usr/bin/env python3
"""
INFORME de la medición del retraso de los fondos «0P» (lee lag/fund_lag.jsonl de la rama `probe-data`; solo biblioteca estándar).

Uso:  python scripts/fund_lag_report.py lag/fund_lag.jsonl [--slot-hours 3] [--calendar target|weekdays] [--json]

Por ticker y FRANJA HORARIA (hora UTC de la medición, en bloques de --slot-hours horas: con 3, «06-08» recoge lo medido entre 06:00 y 08:59):
  retraso mínimo, mediano y máximo observado (en días hábiles; `target` = lunes-viernes salvo festivos TARGET, `weekdays` = solo lunes-viernes).
Por ticker y día de barra: PRIMERA aparición (la primera medición en la que la fecha D figura entre las barras devueltas), con
  * `absent_at`: la última medición anterior en la que D aún NO estaba -> Yahoo publicó D entre `absent_at` y `first_seen` (la resolución es el intervalo de muestreo);
  * `bd_after`: días hábiles entre D y el día de la primera aparición (1 = la barra de D ya estaba durante el día hábil siguiente);
  * `censored`: la fecha ya estaba en la PRIMERA medición del ticker: solo se sabe que apareció a esa hora o antes (cota superior, no una medida).
Solo cuentan las mediciones con estado «ok»; el resto (429, vacío, no probado...) se cuentan aparte. Las líneas ilegibles se saltan y se cuentan.
"""
import argparse
import json
import statistics
import sys
from datetime import date

import fund_lag_common as C

FIELDS = {"target": "lag_bd", "weekdays": "lag_wd"}


def load_records(text):
    """Texto .jsonl -> (registros válidos, nº de líneas saltadas). Valida igual que el job de escritura (sin restringir la lista de tickers)."""
    recs, skipped = [], 0
    for ln in text.splitlines():
        if not ln.strip():
            continue
        try:
            rec = json.loads(ln)
        except ValueError:
            skipped += 1
            continue
        if C.check_record(rec):
            skipped += 1
        else:
            rec["_t"] = C.parse_instant(rec["measured_at"])
            recs.append(rec)
    recs.sort(key=lambda r: (r["_t"], r["ticker"]))
    return recs, skipped


def slot_label(hour, slot_hours):
    lo = (hour // slot_hours) * slot_hours
    return "%02d-%02d" % (lo, min(23, lo + slot_hours - 1))


def lag_stats(recs, slot_hours=3, calendar="target"):
    """-> {ticker: {franja: {"n", "min", "median", "max"}}} con las mediciones «ok»."""
    field = FIELDS[calendar]
    groups = {}
    for r in recs:
        if r["status"] == "ok":
            groups.setdefault(r["ticker"], {}).setdefault(slot_label(r["_t"].hour, slot_hours), []).append(r[field])
    return {t: {s: {"n": len(v), "min": min(v), "median": statistics.median(v), "max": max(v)} for s, v in sorted(slots.items())} for t, slots in sorted(groups.items())}


def first_seen(recs, calendar="target"):
    """-> {ticker: [{"date", "first_seen", "absent_at", "censored", "bd_after", "days_after"}]} ordenado por fecha de barra."""
    out = {}
    by_ticker = {}
    for r in recs:
        if r["status"] == "ok":
            by_ticker.setdefault(r["ticker"], []).append(r)
    for t, rs in sorted(by_ticker.items()):
        seen = {}
        prev = None
        for i, r in enumerate(rs):                       # rs ya está ordenado por instante
            for d in r["dates"]:
                if d not in seen:
                    seen[d] = {"date": d, "first_seen": r["measured_at"], "absent_at": prev["measured_at"] if prev else None, "censored": i == 0}
            prev = r
        rows = []
        for d, e in sorted(seen.items()):
            day = e["first_seen"][:10]
            e["bd_after"] = C.business_days_between(date.fromisoformat(d), date.fromisoformat(day), calendar)
            e["days_after"] = (date.fromisoformat(day) - date.fromisoformat(d)).days
            rows.append(e)
        out[t] = rows
    return out


def build_report(recs, skipped=0, slot_hours=3, calendar="target"):
    status = {}
    for r in recs:
        status[r["status"]] = status.get(r["status"], 0) + 1
    ok = [r for r in recs if r["status"] == "ok"]
    return {"records": len(recs), "skipped_lines": skipped, "by_status": status, "slot_hours": slot_hours, "calendar": calendar,
            "first_measurement": recs[0]["measured_at"] if recs else None, "last_measurement": recs[-1]["measured_at"] if recs else None,
            "ok": len(ok), "lag": lag_stats(recs, slot_hours, calendar), "first_seen": first_seen(recs, calendar)}


def render_markdown(rep):
    L = ["# Retraso de los fondos 0P en Yahoo: informe", "",
         f"Registros: {rep['records']} (líneas saltadas por ilegibles: {rep['skipped_lines']}) · primera medición: {rep['first_measurement'] or '—'} · última: {rep['last_measurement'] or '—'}",
         "Estados: " + (", ".join(f"{k} {v}" for k, v in sorted(rep["by_status"].items())) or "—"),
         f"Calendario: {rep['calendar']} · franja de {rep['slot_hours']} h (hora UTC de la medición)", "",
         "## Retraso por ticker y franja (días hábiles)", "", "| Ticker | Franja UTC | Mediciones | Mín | Mediana | Máx |", "|---|---|---|---|---|---|"]
    for t, slots in rep["lag"].items():
        for s, v in slots.items():
            L.append(f"| {t} | {s} | {v['n']} | {v['min']} | {v['median']:g} | {v['max']} |")
    L += ["", "## Primera aparición de cada barra", "",
          "`≤` = ya estaba en la primera medición del ticker (solo cota superior). «Ausente en» = última medición anterior sin esa barra.", "",
          "| Ticker | Barra | Primera aparición (UTC) | Ausente en (UTC) | Días hábiles tras la barra | Días naturales tras la barra |", "|---|---|---|---|---|---|"]
    for t, rows in rep["first_seen"].items():
        for e in rows:
            L.append(f"| {t} | {e['date']} | {'≤ ' if e['censored'] else ''}{e['first_seen']} | {e['absent_at'] or '—'} | {e['bd_after']} | {e['days_after']} |")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Informe del retraso de los fondos 0P en Yahoo.")
    ap.add_argument("registry", help="lag/fund_lag.jsonl")
    ap.add_argument("--slot-hours", type=int, default=3)
    ap.add_argument("--calendar", choices=tuple(FIELDS), default="target")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not 1 <= args.slot_hours <= 24:
        print("--slot-hours debe estar entre 1 y 24", file=sys.stderr)
        return 2
    try:
        text = open(args.registry, encoding="utf-8").read()
    except (OSError, UnicodeDecodeError):
        print("no se pudo leer el registro", file=sys.stderr)
        return 2
    recs, skipped = load_records(text)
    rep = build_report(recs, skipped, args.slot_hours, args.calendar)
    print(json.dumps(rep, ensure_ascii=False, indent=1) if args.json else render_markdown(rep), end="" if not args.json else "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
