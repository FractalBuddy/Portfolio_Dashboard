"""Medición del MANIFIESTO del feed v2 a escala (solo medición: no cambia ningún tope ni ningún script).

Reutiliza el banco de escala (tests/bench_escala_v2.py: registro sintético SYN..., Yahoo y CoinGecko simulados, red bloqueada) y añade:
  * desglose de manifest.json por tipo de entrada (latest, daily, intraday, ficheros globales) y por campo (ruta, bytes, sha256, puntuación);
  * tamaño del manifiesto por trozos (manifest-root.json + s/<SYM>/manifest.json), si el generador lo escribe;
  * peticiones a Yahoo del run (simuladas: cuenta las llamadas del generador, no la latencia real);
  * tiempo de la descarga COMPLETA del sitio anterior (scripts/site_download.py, modo full, 16 hilos) contra un servidor HTTP LOCAL: es una cota
    inferior (sin la latencia ni el ancho de banda de Pages);
  * topes que se rompen (los del validador) y los que pasan del 50 %.

Uso:
  python tests/bench_manifest_escala.py --n 249 400 800 1500 3000 --work /ruta/temporal --out resultados.json [--no-download]
"""
import argparse
import functools
import http.server
import json
import os
import shutil
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import bench_escala_v2 as b          # noqa: E402
import feed_v2_common as c           # noqa: E402

SHA_FIELD = len('"sha256":""') + 64
SEP = len(",")


def manifest_breakdown(path, n_yahoo):
    """Desglose de un manifest.json (formato clásico): bytes por tipo de entrada y por campo, y bytes por símbolo de Yahoo."""
    with open(path, "rb") as f:
        raw = f.read()
    m = json.loads(raw)
    kinds, fields = {}, {"ruta": 0, "bytes": 0, "sha256": 0, "puntuacion": 0}
    for rel, v in m["files"].items():
        entry = len(c.dumps({rel: v})) - 2 + SEP                 # '"ruta":{"bytes":N,"sha256":"..."}' + coma
        kind = (c.classify_path(rel) or ("otro",))[0]
        k = kinds.setdefault(kind, {"entradas": 0, "bytes": 0})
        k["entradas"] += 1
        k["bytes"] += entry
        path_b = len(c.dumps(rel))
        bytes_b = len('"bytes":') + len(str(v["bytes"]))
        fields["ruta"] += path_b
        fields["bytes"] += bytes_b
        fields["sha256"] += SHA_FIELD
        fields["puntuacion"] += entry - path_b - bytes_b - SHA_FIELD
    cab = len(raw) - sum(k["bytes"] for k in kinds.values()) + SEP
    per_sym = {k: round(v["bytes"] / n_yahoo, 1) for k, v in kinds.items() if k in ("latest", "daily", "intraday")}
    return {"total": len(raw), "cabecera": cab, "por_tipo": kinds, "por_campo": fields, "por_simbolo_yahoo": per_sym,
            "por_simbolo_yahoo_total": round(len(raw) / n_yahoo, 1), "entradas": len(m["files"])}


def sharded_sizes(site):
    root = os.path.join(site, "manifest-root.json")
    if not os.path.isfile(root):
        return None
    shards = []
    for sym in os.listdir(os.path.join(site, "s")):
        p = os.path.join(site, "s", sym, "manifest.json")
        if os.path.isfile(p):
            shards.append(os.path.getsize(p))
    return {"root_bytes": os.path.getsize(root), "shards": len(shards), "shard_max": max(shards) if shards else 0,
            "shard_media": round(sum(shards) / len(shards), 1) if shards else 0}


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def timed_download(site, dest):
    """Descarga completa (modo full, 16 hilos) del sitio servido por HTTP local. Devuelve (segundos, ficheros, bytes)."""
    import site_download as sd
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Quiet, directory=site))
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        if os.path.exists(dest):
            shutil.rmtree(dest)
        t0 = time.perf_counter()
        state, nfiles, nbytes = sd.download("http://127.0.0.1:%d" % srv.server_address[1], dest, mode="full", workers=16, today=b.NOW2.date())
        secs = time.perf_counter() - t0
    finally:
        srv.shutdown()
    assert state == "ok"
    return round(secs, 2), nfiles, nbytes


def headroom(site_m, n):
    """Topes rotos y topes por encima del 50 % (mismos topes que el validador)."""
    over, warn = list(site_m["caps_broken"]), []
    pairs = [("MAX_REGISTRY_SYMBOLS", n, c.MAX_REGISTRY_SYMBOLS), ("MAX_SITE_FILES", site_m["files"], c.MAX_SITE_FILES),
             ("MAX_SITE_BYTES", site_m["total_bytes"], c.MAX_SITE_BYTES)]
    pairs += [("MAX_BYTES[%s]" % k, v, c.MAX_BYTES[k]) for k, v in site_m["max_file_bytes"].items() if k in c.MAX_BYTES]
    for name, val, cap in pairs:
        if 0.5 * cap <= val <= cap:
            warn.append("%s %d %%" % (name, round(100 * val / cap)))
    return over, warn


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, nargs="+", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-download", action="store_true")
    a = ap.parse_args(argv)
    os.makedirs(a.work, exist_ok=True)
    results = []
    for n in a.n:
        reg = b.make_registry(n)
        n_yahoo = sum(e["kind"] == "yahoo" for e in reg)
        res = {"n": n, "yahoo": n_yahoo, "hot": sum(e["tier"] == "hot" and e["kind"] == "yahoo" for e in reg),
               "warm": sum(e["tier"] == "warm" and e["kind"] == "yahoo" for e in reg), "cold": sum(e["tier"] == "cold" and e["kind"] == "yahoo" for e in reg)}
        for ph in ("gen1", "gen2", "val2"):
            res[ph] = b.run_phase(n, ph, a.work)
            print("n=%d %s %.1fs" % (n, ph, res[ph]["seconds"]), file=sys.stderr, flush=True)
        base = os.path.join(a.work, "n%d" % n)
        site2 = os.path.join(base, "run2", "v2")
        m = b.measure_site(site2, reg, b.NOW2.year)
        res["site2"] = {k: m[k] for k in ("total_bytes", "files", "index_bytes", "manifest_bytes", "health_bytes", "caps_broken", "max_file_bytes")}
        res["manifest"] = manifest_breakdown(os.path.join(site2, "manifest.json"), n_yahoo)
        res["sharded"] = sharded_sizes(site2)
        res["topes_rotos"], res["topes_50"] = headroom(m, n)
        if not a.no_download:
            import site_download as sd
            try:
                res["descarga_local_full"] = dict(zip(("segundos", "ficheros", "bytes"), timed_download(os.path.join(base, "run1", "v2"), os.path.join(base, "dl"))))
            except sd.DownloadError as e:                          # p. ej. manifest.json por encima de MAX_BYTES["manifest"]: el job fetch no arranca
                res["descarga_local_full"] = {"error": str(e)[:200]}
            shutil.rmtree(os.path.join(base, "dl"), ignore_errors=True)
        results.append(res)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=1)
        shutil.rmtree(base, ignore_errors=True)                     # disco: cada N ocupa hasta ~1 GB (dos sitios)
    return 0


if __name__ == "__main__":
    sys.exit(main())
