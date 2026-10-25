#!/usr/bin/env python3
"""
Descarga por HTTP el sitio v2 publicado anteriormente en GitHub Pages (histórico incremental y reglas de regresión).

    python3 scripts/site_download.py --base-url https://usuario.github.io/repo/v2 --dest prev_site/v2 --mode full|validate [--github-env]

Solo biblioteca estándar: lo ejecutan tanto el job `fetch` (modo `full`: todo el sitio, para reconstruir un sitio completo con los años
cerrados) como el job `publish` (modo `validate`: manifest + latest.json + diario de los dos últimos años; el job de publicación no confía en
lo que le pase `fetch`).

FAIL-CLOSED (BE-06): la ÚNICA situación que se trata como «primer run» es un 404 en manifest.json. Cualquier otro fallo (red, 5xx tras reintentos,
JSON inválido, un fichero listado que da 404, tamaño o sha256 distintos de los del manifiesto) termina con código 1 y NO se publica a ciegas.

Con --github-env escribe `EXPECT_PREV=true|false` en $GITHUB_ENV para que el validador sepa si debe exigir un sitio anterior.
"""
import argparse
import gzip
import hashlib
import io
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import feed_v2_common as c

USER_AGENT = "portfolio-feed-v2-ci/1.0 (+https://github.com)"
TRIES = 4
BACKOFF = 1.5
TIMEOUT = 30
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
RETRY_CODES = {408, 425, 429, 500, 502, 503, 504}


class NotFound(Exception):
    pass


class DownloadError(Exception):
    pass


def _check_base(url):
    p = urllib.parse.urlsplit(url)
    host = p.hostname or ""
    if p.scheme != "https" and not (p.scheme == "http" and host in LOOPBACK):
        raise DownloadError("la URL base debe ser https://")
    if not host or p.username or p.password or p.query or p.fragment:
        raise DownloadError("URL base inválida")
    return url.rstrip("/")


def _read_limited(resp, limit):
    raw = resp.read(limit + 1)
    if len(raw) > limit:
        raise DownloadError("respuesta demasiado grande")
    if resp.headers.get("Content-Encoding", "").lower() == "gzip":
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as g:
            data = g.read(limit + 1)
        if len(data) > limit:
            raise DownloadError("respuesta descomprimida demasiado grande")
        return data
    return raw


def http_get(base, rel, limit, sleep=None):
    """GET base/rel con reintentos. Lanza NotFound (404) o DownloadError. Cada intento lleva un parámetro aleatorio para saltarse cachés."""
    url = "%s/%s" % (base, urllib.parse.quote(rel, safe="/"))
    last = "sin respuesta"
    for i in range(TRIES):
        req = urllib.request.Request("%s?cb=%d" % (url, random.getrandbits(40)), headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return _read_limited(resp, limit)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise NotFound(rel)
            last = "HTTP %d" % e.code
            if e.code not in RETRY_CODES:
                break
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last = type(e).__name__
        if i < TRIES - 1:
            (sleep or time.sleep)(BACKOFF * (2 ** i) + random.random())
    raise DownloadError("%s: %s tras %d intentos" % (rel, last, TRIES))


def parse_manifest(data):
    """Valida mínimamente el manifiesto descargado (viene de Internet): devuelve {ruta: {bytes, sha256}}."""
    def no_const(name):
        raise ValueError(name)
    try:
        m = json.loads(data.decode("utf-8"), parse_constant=no_const)
    except (ValueError, UnicodeDecodeError):
        raise DownloadError("manifest.json no es JSON válido")
    files = m.get("files") if isinstance(m, dict) else None
    if not isinstance(files, dict) or m.get("schema") != c.SCHEMA or not files or len(files) > c.MAX_SITE_FILES:
        raise DownloadError("manifest.json con formato inesperado")
    out = {}
    for rel, v in files.items():
        cls = c.classify_path(rel)
        if cls is None or cls[0] == "manifest":
            raise DownloadError("manifest.json lista una ruta fuera del contrato")
        if not (isinstance(v, dict) and type(v.get("bytes")) is int and 0 <= v["bytes"] <= c.MAX_BYTES[cls[0]]
                and isinstance(v.get("sha256"), str) and len(v["sha256"]) == 64):
            raise DownloadError("manifest.json con una entrada inválida")
        out[rel] = v
    return out


def wanted(files, mode, today):
    """Rutas a bajar. full: todas. validate: latest.json de cada símbolo y el diario del año actual y el anterior (lo que compara el validador)."""
    if mode == "full":
        return sorted(files)
    keep = []
    for rel in files:
        kind, _sym, year = c.classify_path(rel)
        if kind == "latest" or (kind == "daily" and year >= today.year - 1):
            keep.append(rel)
    return sorted(keep)


def fetch_verified(base, rel, meta, dest, sleep=None):
    """Baja un fichero listado en el manifiesto y comprueba bytes y sha256. Un desajuste se reintenta una vez (borde de CDN con la versión
    anterior) y después es un error."""
    for attempt in range(2):
        data = http_get(base, rel, c.MAX_BYTES[c.classify_path(rel)[0]], sleep)
        if len(data) == meta["bytes"] and hashlib.sha256(data).hexdigest() == meta["sha256"]:
            p = os.path.join(dest, *rel.split("/"))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(data)
            return len(data)
        if attempt == 0:
            (sleep or time.sleep)(2 + random.random())
    raise DownloadError("%s no coincide con el manifiesto (bytes o sha256)" % rel)


def download(base_url, dest, mode="full", workers=16, today=None, sleep=None):
    """Devuelve (estado, ficheros, bytes). estado: 'first_run' (404 en manifest.json) o 'ok'. Lanza DownloadError en cualquier otro caso."""
    base = _check_base(base_url)
    today = today or datetime.now(timezone.utc).date()
    try:
        raw = http_get(base, "manifest.json", c.MAX_BYTES["manifest"], sleep)
    except NotFound:
        return "first_run", 0, 0
    files = parse_manifest(raw)
    os.makedirs(dest, exist_ok=True)
    with open(os.path.join(dest, "manifest.json"), "wb") as f:
        f.write(raw)
    todo = wanted(files, mode, today)
    total = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = {pool.submit(fetch_verified, base, rel, files[rel], dest, sleep): rel for rel in todo}
        try:
            for fut in as_completed(futs):
                total += fut.result()
        except NotFound as e:
            raise DownloadError("%s figura en el manifiesto pero da 404: sitio inconsistente" % e)
        finally:
            for f in futs:
                f.cancel()
    return "ok", len(todo) + 1, total + len(raw)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--base-url", required=True, help="URL de la carpeta v2 publicada (sin barra final)")
    ap.add_argument("--dest", required=True)
    ap.add_argument("--mode", choices=("full", "validate"), default="full")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--github-env", action="store_true", help="escribe EXPECT_PREV en $GITHUB_ENV")
    a = ap.parse_args(argv)
    t0 = time.monotonic()
    try:
        state, n, size = download(a.base_url, a.dest, a.mode, a.workers)
    except DownloadError as e:
        print("::error::no se pudo bajar el sitio anterior: %s" % str(e).replace("\n", " ")[:300])
        print("No se publica a ciegas: un fallo de descarga no es un primer run (BE-06).")
        return 1
    expect = state == "ok"
    if state == "first_run":
        print("::notice::sin sitio anterior (HTTP 404 en manifest.json): primer run")
    else:
        print("sitio anterior: %d ficheros, %d bytes, %.1f s (modo %s)" % (n, size, time.monotonic() - t0, a.mode))
    env = os.environ.get("GITHUB_ENV")
    if a.github_env and env:
        with open(env, "a", encoding="utf-8") as f:
            f.write("EXPECT_PREV=%s\n" % ("true" if expect else "false"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
