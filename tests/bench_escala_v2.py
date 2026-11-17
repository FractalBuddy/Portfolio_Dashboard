"""Banco de pruebas de ESCALA del feed v2 (solo medición: no cambia ningún tope ni ningún script).

Genera registros sintéticos de N símbolos (mismo reparto de niveles, tipos y unidades que el registro actual, con nombres inventados SYN...),
ejecuta el generador (scripts/feed_v2.py) y el validador (scripts/sanity_check_v2.py) en local con Yahoo y CoinGecko SIMULADOS y la red bloqueada,
y mide: bytes y ficheros del sitio por tipo, tamaño de index.json y manifest.json, tiempos, memoria máxima y qué tope se rompe.

Cada fase corre en un SUBPROCESO propio (la memoria máxima, ru_maxrss, es la de esa fase y no se mezcla con las demás):
  gen1      primer run sin sitio anterior: alta de los N símbolos con 10 años (BACKFILL_MAX_PER_RUN se levanta SOLO aquí, en memoria, para medir el
            sitio en régimen; en real ese alta se reparte en ceil(N/80) runs)
  gen2      run del día siguiente con el sitio anterior (hot y warm tocan; cold se copia): es el run diario típico
  val1      validación de gen1 sin sitio anterior
  val2      validación de gen2 contra gen1 (EXPECT_PREV, como en el job publish)
Con --history-years 5 se repiten gen1/val1 con HISTORY_YEARS=5 (parcheado en memoria, no en el código).

Uso:
  python tests/bench_escala_v2.py run --n 200 500 1000 5000 --work /ruta/temporal --out resultados.json
"""
import argparse
import datetime as dt
import json
import math
import os
import resource
import socket
import subprocess
import sys
import time
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

NOW1 = dt.datetime(2026, 10, 26, 12, 0, tzinfo=dt.timezone.utc)     # lunes
NOW2 = NOW1 + dt.timedelta(days=1)
END1, END2 = dt.date(2026, 10, 23), dt.date(2026, 10, 26)
PERIOD_DAYS = {"1mo": 30, "3mo": 92, "6mo": 183, "1y": 365, "2y": 730, "5y": 1826, "10y": 3652}


# ------------------------------------------------------------------------------------------------------------------------------
# Sin red: cualquier resolución de nombres que no sea local revienta (además de que todas las funciones de red están sustituidas).
# ------------------------------------------------------------------------------------------------------------------------------
def block_network():
    real = socket.getaddrinfo

    def guard(host, *a, **k):
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise OSError("bench sin red: intento de resolver %r" % (host,))
        return real(host, *a, **k)
    socket.getaddrinfo = guard


# ------------------------------------------------------------------------------------------------------------------------------
# Registro sintético: reparto del registro actual (21 % hot, 70 % warm, 9 % cold; 7 % cripto; unidades 70/10/10/10).
# ------------------------------------------------------------------------------------------------------------------------------
def make_registry(n):
    rows = []
    for i in range(n):
        b = i % 100
        tier = "hot" if b < 21 else "warm" if b < 91 else "cold"
        if i % 14 == 7:
            rows.append({"symbol": "SYC%04d" % i, "kind": "crypto", "name": "Synthetic coin %d" % i, "currency": "USD", "unit": "currency",
                         "tier": tier, "status": "active", "fail_streak": 0})
            continue
        u = i % 10
        if u == 7:
            sym, cur, unit = "^SYN%04d" % i, "USD", "points"
        elif u == 8:
            sym, cur, unit = "SYN%04d=X" % i, "USD", "rate"
        elif u == 9:
            sym, cur, unit = "SYN%04d.L" % i, "GBp", "subunit"
        else:
            sym, cur, unit = "SYN%04d" % i, "USD", "currency"
        rows.append({"symbol": sym, "kind": "yahoo", "name": "Synthetic instrument number %d (bench)" % i, "currency": cur, "unit": unit,
                     "tier": tier, "status": "active", "fail_streak": 0})
    return rows


# ------------------------------------------------------------------------------------------------------------------------------
# Yahoo simulado: precio = función determinista de (símbolo, fecha) (dos descargas de ventanas distintas casan, como en tests/v2_world.py) con
# ruido por día para que los números tengan todos sus decimales (lo realista para el tamaño del JSON) y variaciones diarias < 2 %.
# ------------------------------------------------------------------------------------------------------------------------------
class FakeYahoo:
    def __init__(self, registry, end):
        self.decl = {e["symbol"]: e for e in registry}
        self.end = end
        self.calls = 0

    @staticmethod
    def price(sym, d, unit):
        h = zlib.crc32(sym.encode())
        base = {"rate": 0.5 + h % 150 / 100, "subunit": 200 + h % 5000, "points": 1000 + h % 30000}.get(unit, 15 + h % 600)
        o = d.toordinal()
        noise = (zlib.crc32(("%s%d" % (sym, o)).encode()) / 4294967295.0 - 0.5) * 0.012
        return base * (1 + 0.25 * math.sin(o / 97 + h % 7) + 0.05 * math.sin(o / 11)) * (1 + noise)

    def __call__(self, sym, tries=3, **kw):
        import pandas as pd
        self.calls += 1
        unit = self.decl[sym]["unit"]
        if kw.get("interval") == "5m":
            # 2 días de 5 min con pre/post (04:00-20:00): 192 barras por día, lo que pide fetch_intraday_history.
            idx = list(pd.date_range("%s 04:00" % (self.end - dt.timedelta(days=1)), periods=192, freq="5min", tz="America/New_York")) + \
                list(pd.date_range("%s 04:00" % self.end, periods=192, freq="5min", tz="America/New_York"))
            p = self.price(sym, self.end, unit)
            df = pd.DataFrame({"Close": [p * (1 + 0.001 * math.sin(k / 7)) for k in range(len(idx))]}, index=pd.DatetimeIndex(idx))
        else:
            days, d = [], self.end
            for _ in range(PERIOD_DAYS[kw["period"]]):
                if d.weekday() < 5:
                    days.append(d)
                d -= dt.timedelta(days=1)
            days.reverse()
            p = [self.price(sym, x, unit) for x in days]
            df = pd.DataFrame({"Open": [x * 0.9981 for x in p], "High": [x * 1.0093 for x in p], "Low": [x * 0.9907 for x in p], "Close": p,
                               "Volume": [1_000_000 + zlib.crc32(("v%s%d" % (sym, x.toordinal())).encode()) % 90_000_000 for x in days]},
                              index=pd.DatetimeIndex([pd.Timestamp(x) for x in days]))
        df.attrs["currency"] = self.decl[sym]["currency"]
        return df


def coins_for(registry):
    """Lote de CoinGecko de 250 monedas que incluye las criptos del registro (hasta 250: CRYPTO_TOP_N)."""
    mine = [e["symbol"].lower() for e in registry if e["kind"] == "crypto"]
    syms = (mine + ["zz%d" % i for i in range(250)])[:250]
    return [{"id": "coin-%s" % s, "symbol": s, "name": "Coin %s" % s, "current_price": 1.5 + i, "market_cap": 1e10 - i * 1e6, "market_cap_rank": i + 1,
             "total_volume": 5e6, "price_change_percentage_24h_in_currency": 1.234, "price_change_percentage_7d_in_currency": -2.5,
             "last_updated": "2026-10-26T11:58:00.000Z"} for i, s in enumerate(syms)]


def install_fakes(registry, end):
    import feed_v2 as f2
    import fetch_market_data as fm
    y = FakeYahoo(registry, end)
    fm.yf_history = y
    fm._DEADLINE = fm._HARD_DEADLINE = 1e12
    fm._nap = lambda s: None
    fm._cg_markets = lambda params, tries=3: coins_for(registry)
    fm.parse_feed = lambda url, limit=8: []
    fm.build_rates = lambda: []
    fm.fetch_cpi_history = lambda: ([], {"source": "x", "series": "x", "last_date": None, "stale": True})
    fm.build_market_mood = lambda prev_feed=None, include_altcoin=True: {"vix": {"value": 15.0, "change_pct": 1.0}}
    fm.FRED_API_KEY = ""
    f2.BACKFILL_MAX_PER_RUN = 10 ** 9            # SOLO medición del sitio en régimen (ver cabecera)
    return y


# ------------------------------------------------------------------------------------------------------------------------------
# Fases (subproceso)
# ------------------------------------------------------------------------------------------------------------------------------
def phase(args):
    block_network()
    import feed_v2 as f2
    import feed_v2_common as c
    import sanity_check_v2 as sc
    if args.history_years:
        c.HISTORY_YEARS = args.history_years
    reg = make_registry(args.n)
    base = os.path.join(args.work, "n%d%s" % (args.n, "_h%d" % args.history_years if args.history_years else ""))
    out = {"phase": args.phase, "n": args.n}
    t0 = time.perf_counter()
    if args.phase in ("gen1", "gen2"):
        first = args.phase == "gen1"
        install_fakes(reg, END1 if first else END2)
        run = f2.generate(reg, os.path.join(base, "run1" if first else "run2", "v2"), None if first else os.path.join(base, "run1", "v2"),
                          now=NOW1 if first else NOW2)
        out.update({"stats": dict(run.stats), "backfills": run.backfills, "yahoo_calls": run.yahoo_calls})
    else:
        first = args.phase == "val1"
        site = os.path.join(base, "run1" if first else "run2", "v2")
        errors, warnings = sc.check_site(site, reg, None if first else os.path.join(base, "run1", "v2"),
                                         today=(NOW1 if first else NOW2).date(), expect_prev=not first)
        out.update({"errors": len(errors), "errors_sample": [str(e)[:200] for e in errors[:12]], "warnings": len(warnings),
                    "warnings_sample": [str(w)[:160] for w in warnings[:5]]})
    out["seconds"] = round(time.perf_counter() - t0, 2)
    out["max_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    print(json.dumps(out))


# ------------------------------------------------------------------------------------------------------------------------------
# Medición del sitio y topes
# ------------------------------------------------------------------------------------------------------------------------------
def measure_site(site, registry, today_year, history_years_projection=5):
    import feed_v2_common as c
    by_kind, files, total = {}, 0, 0
    biggest = {}
    tier = {e["symbol"]: e["tier"] for e in registry}
    proj = {"files": 0, "bytes": 0}
    cutoff_proj = today_year - history_years_projection + 1
    for root, _dirs, names in os.walk(site):
        for nm in names:
            p = os.path.join(root, nm)
            rel = os.path.relpath(p, site).replace(os.sep, "/")
            size = os.path.getsize(p)
            cl = c.classify_path(rel)
            kind = cl[0] if cl else "other"
            k = by_kind.setdefault(kind, {"files": 0, "bytes": 0})
            k["files"] += 1
            k["bytes"] += size
            biggest[kind] = max(biggest.get(kind, 0), size)
            files += 1
            total += size
            if kind == "daily" and tier.get(cl[1]) in ("warm", "cold") and cl[2] < cutoff_proj:
                proj["files"] += 1
                proj["bytes"] += size
    caps = []
    if len(registry) > c.MAX_REGISTRY_SYMBOLS:
        caps.append("MAX_REGISTRY_SYMBOLS (%d > %d)" % (len(registry), c.MAX_REGISTRY_SYMBOLS))
    for kind, mx in biggest.items():
        if kind in c.MAX_BYTES and mx > c.MAX_BYTES[kind]:
            caps.append("MAX_BYTES[%s] (%d > %d)" % (kind, mx, c.MAX_BYTES[kind]))
    if total > c.MAX_SITE_BYTES:
        caps.append("MAX_SITE_BYTES (%d > %d)" % (total, c.MAX_SITE_BYTES))
    if files > c.MAX_SITE_FILES:
        caps.append("MAX_SITE_FILES (%d > %d)" % (files, c.MAX_SITE_FILES))
    return {"total_bytes": total, "files": files, "by_kind": by_kind, "max_file_bytes": biggest, "caps_broken": caps,
            "index_bytes": by_kind.get("index", {}).get("bytes", 0), "manifest_bytes": by_kind.get("manifest", {}).get("bytes", 0),
            "health_bytes": by_kind.get("health", {}).get("bytes", 0),
            "projection_warm_cold_5y": proj}


def run_phase(n, ph, work, hy=None):
    cmd = [sys.executable, os.path.abspath(__file__), "phase", "--phase", ph, "--n", str(n), "--work", work]
    if hy:
        cmd += ["--history-years", str(hy)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("fase %s n=%d falló:\n%s" % (ph, n, r.stderr[-3000:]))
    return json.loads(r.stdout.strip().splitlines()[-1])


def main_run(args):
    os.makedirs(args.work, exist_ok=True)
    results = []
    for n in args.n:
        reg = make_registry(n)
        res = {"n": n, "registry": {"hot": sum(e["tier"] == "hot" for e in reg), "warm": sum(e["tier"] == "warm" for e in reg),
                                    "cold": sum(e["tier"] == "cold" for e in reg), "crypto": sum(e["kind"] == "crypto" for e in reg)}}
        import feed_v2_common as c
        res["registry_problems"] = c.registry_problems({"schema": 1, "symbols": reg})[:3]
        phases = ["gen1", "val1"] + ([] if args.only_first else ["gen2", "val2"])
        for ph in phases:
            res[ph] = run_phase(n, ph, args.work)
            print("n=%d %s %.1fs %.0f MB" % (n, ph, res[ph]["seconds"], res[ph]["max_rss_mb"]), file=sys.stderr, flush=True)
        base = os.path.join(args.work, "n%d" % n)
        res["site1"] = measure_site(os.path.join(base, "run1", "v2"), reg, NOW1.year)
        if not args.only_first:
            res["site2"] = measure_site(os.path.join(base, "run2", "v2"), reg, NOW2.year)
        if n in args.history5:
            for ph in ("gen1", "val1"):
                res[ph + "_h5"] = run_phase(n, ph, args.work, 5)
                print("n=%d %s (HISTORY_YEARS=5) %.1fs" % (n, ph, res[ph + "_h5"]["seconds"]), file=sys.stderr, flush=True)
            res["site1_h5"] = measure_site(os.path.join(args.work, "n%d_h5" % n, "run1", "v2"), reg, NOW1.year)
        results.append(res)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=1)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Banco de escala del feed v2 (solo medición, sin red)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("phase")
    p.add_argument("--phase", choices=["gen1", "gen2", "val1", "val2"], required=True)
    p.add_argument("--n", type=int, required=True)
    p.add_argument("--work", required=True)
    p.add_argument("--history-years", type=int, default=None)
    r = sub.add_parser("run")
    r.add_argument("--n", type=int, nargs="+", required=True)
    r.add_argument("--work", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--history5", type=int, nargs="*", default=[], help="N para los que repetir gen1/val1 con HISTORY_YEARS=5")
    r.add_argument("--only-first", action="store_true", help="solo gen1/val1 (para N grandes)")
    a = ap.parse_args(argv)
    if a.cmd == "phase":
        phase(a)
        return 0
    return main_run(a)


if __name__ == "__main__":
    sys.exit(main())
