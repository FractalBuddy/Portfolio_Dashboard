#!/usr/bin/env python3
"""
DIAGNÓSTICO MANUAL del desfase de fecha del NAV: ¿por qué la ficha de iShares enseña «NAV as of 28/Oct/2026» en el navegador y el scraper (requests) ve 27/Oct?

    python3 -S scripts/personal_v2.py watch --dir $RUNNER_TEMP/personal       # lee las fichas del dueño (con token; solo stdlib)
    python  scripts/nav_probe.py --dir $RUNNER_TEMP/personal                  # este script (SIN token; solo LEE iShares; no escribe en ningún sitio)

Para cada ficha del dueño (etiquetada #1, #2... en el orden de la lista; nunca ISIN, nombre ni URL) pide la página con varias variantes de la petición y escribe, por
variante, la fecha del NAV y el valor que lee el parser. Variantes (ver fetch_market_data.ishares_request): base (la petición de siempre), nocache (Cache-Control/Pragma),
bust (parámetro único), fresh (ambas; es la que usa el scraper junto a base), browser / browser_fresh (User-Agent y cabeceras de navegador), passthrough
(siteEntryPassthrough=true), base_again (la base repetida: ¿cambia entre dos peticiones seguidas?) y las MISMAS fichas en otros sitios de iShares (alt:uk, alt:de...).
Además busca en el HTML de la ficha las peticiones XHR/JSON (*.ajax, *.json), las describe SIN identificadores (ruta sin id ni nombre de producto) y prueba si traen un NAV
más fresco que el HTML (xhr1, xhr2...). Con las cabeceras de caché de cada respuesta (Age, Cache-Control...: no identifican nada) se ve si la copia viene de una caché.

PRIVACIDAD: los logs de un repo público son públicos. Se imprimen etiquetas, códigos HTTP, tamaños, fechas y valores de NAV, y nombres de campos JSON de las XHR; nunca ISIN,
nombres, URL, token ni clave. Todo lo que imprimen las funciones reutilizadas (citan ISIN) se descarta.
"""
import argparse
import contextlib
import io
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import date, datetime, timezone
from urllib.parse import parse_qsl, urlsplit

import fetch_market_data as fm
import personal_v2 as pv

MAX_BODY = 4_000_000
KEEP_HEADERS = ("age", "date", "last-modified", "cache-control", "expires", "x-cache")
SAFE_QUERY_VALUES = {"filetype", "datatype", "tab", "type", "view"}        # el resto de valores de la query (p. ej. fileName, que lleva el ticker) se oculta
_SITE_RE = re.compile(r"^(/)([a-z]{2})/(individual|professional|institutions?)/([a-z]{2})(/products/\d+/.+)$")
_XHR_RE = re.compile(r"""["'`]((?:https://[A-Za-z0-9.\-]*ishares\.com)?/[^"'`\s<>\\]{2,300}?\.(?:ajax|json)(?:\?[^"'`\s<>\\]{0,300})?|\d{8,}\.ajax(?:\?[^"'`\s<>\\]{0,300})?)["'`]""")
_NAV_TOKEN_RE = re.compile(r"NAV as of\s+(\d{1,2}[/ \-][A-Za-z]{3,10}[/ \-]\d{4}|\d{4}-\d{2}-\d{2})")


def say(msg):
    print(msg)
    sys.stdout.flush()


# ------------------------------------------------------------------------------------------------------------------------------
# HTTP (inyectable en los tests)
# ------------------------------------------------------------------------------------------------------------------------------
def real_get(url, headers):
    """(status, cabeceras en minúscula, texto). No lanza por códigos HTTP; sí por fallos de red (el llamador imprime solo el nombre de la excepción)."""
    import requests
    r = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
    return r.status_code, {k.lower(): v for k, v in r.headers.items()}, r.text[:MAX_BODY]


def host_ok(url):
    """Solo https a *.ishares.com, sin credenciales (el diagnóstico no debe poder ser dirigido a otro sitio)."""
    try:
        p = urlsplit(url)
        host = (p.hostname or "").lower()
        host.encode("ascii")
        return p.scheme == "https" and (host == "ishares.com" or host.endswith(".ishares.com")) and not p.username and not p.password
    except (ValueError, UnicodeError, AttributeError):
        return False


# ------------------------------------------------------------------------------------------------------------------------------
# Lectura del NAV
# ------------------------------------------------------------------------------------------------------------------------------
def to_plain(text):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text))


def read_html_nav(text, isin, today):
    """Lo que leería el scraper: (resultado de parse_ishares_nav | None, nº de «NAV as of» con fecha, fechas distintas vistas, ISIN presente)."""
    plain = to_plain(text)
    with contextlib.redirect_stderr(io.StringIO()):
        r = fm.parse_ishares_nav(plain, isin, today=today)
    dates = Counter(_NAV_TOKEN_RE.findall(plain))
    isin_in = bool(re.search(r"(?<![A-Za-z0-9])" + re.escape(isin) + r"(?![A-Za-z0-9])", plain))
    return r, sum(dates.values()), dates, isin_in


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        return fm._parse_num(re.sub(r"^[A-Z]{3}\s*", "", v.strip()))
    if isinstance(v, dict):
        for k in ("raw", "value", "display", "displayValue"):
            if k in v and _num(v[k]) is not None:
                return _num(v[k])
    return None


def _date(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)) and v > 1e9:
        try:
            return datetime.fromtimestamp(v / 1000.0 if v > 1e11 else v, tz=timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(v, str):
        return fm._parse_nav_date(v) or (fm._parse_nav_date(v[:10]) if re.match(r"\d{4}-\d{2}-\d{2}", v) else None)
    if isinstance(v, dict):
        for k in ("raw", "value", "display", "displayValue"):
            if k in v and _date(v[k]):
                return _date(v[k])
    return None


def json_nav_candidates(doc, limit=5):
    """Pares (fecha|None, valor) de claves que parecen un NAV (nombre con «nav», sin change/return/total...) en cualquier JSON. Heurística: solo orienta."""
    out = []

    def walk(o, depth):
        if len(out) >= limit or depth > 8:
            return
        if isinstance(o, dict):
            when = next((d for k, v in o.items() if re.search(r"date|as_?of", k, re.I) for d in [_date(v)] if d), None)
            for k, v in o.items():
                if re.search(r"nav", k, re.I) and not re.search(r"change|return|percent|pct|total|aum|asset", k, re.I):
                    n = _num(v)
                    if n is not None and n > 0:
                        out.append((when or (_date(v) if isinstance(v, dict) else None), n))
            for v in o.values():
                walk(v, depth + 1)
        elif isinstance(o, list):
            for v in o[:50]:
                walk(v, depth + 1)
    walk(doc, 0)
    return out[:limit]


def csv_latest(text):
    """Última fila fechada de un CSV (fecha, primer número) por si una descarga trae el histórico de NAV. None si no hay."""
    best = None
    for line in text.splitlines()[:5000]:
        m = re.match(r'^\W*(\d{1,2}[/ \-][A-Za-z]{3,10}[/ \-]\d{4}|\d{4}-\d{2}-\d{2})\W+(?:[^,;\n]*[,;])?\W*([\d.,]+)', line)
        if m:
            d, n = fm._parse_nav_date(m.group(1)), fm._parse_num(m.group(2))
            if d and n and (best is None or d > best[0]):
                best = (d, n)
    return best


def read_xhr_nav(text, isin, today):
    """Candidatos (fecha|None, valor) de una respuesta XHR: JSON, HTML/texto con «NAV as of» o CSV. Más los nombres de campo de primer nivel (para el siguiente paso)."""
    keys = []
    try:
        doc = json.loads(text)
    except ValueError:
        r, _n, _dates, _isin = read_html_nav(text, isin, today)
        if r:
            return [(date.fromisoformat(r["nav_date_iso"]) if r.get("nav_date_iso") else None, r["price"])], keys
        c = csv_latest(text)
        return ([c] if c else []), keys
    if isinstance(doc, dict):
        keys = sorted(str(k)[:30] for k in doc)[:25]
    return json_nav_candidates(doc), keys


# ------------------------------------------------------------------------------------------------------------------------------
# Descripción de URLs SIN identificadores
# ------------------------------------------------------------------------------------------------------------------------------
def shape(url):
    """Ruta de una XHR sin el id ni el nombre del producto y sin valores de la query que puedan identificar (fileName lleva el ticker)."""
    p = urlsplit(url)
    path = re.sub(r"/products/\d+/[^/?#]+", "/products/*/*", p.path)
    q = "&".join("%s=%s" % (k, v if k.lower() in SAFE_QUERY_VALUES else "*") for k, v in parse_qsl(p.query, keep_blank_values=True))
    return path + ("?" + q if q else "")


def alt_urls(url):
    """La MISMA ficha en otros sitios de iShares: [(etiqueta, url)]. Solo si la ruta tiene la forma /<país>/<público>/<idioma>/products/<id>/<nombre>."""
    m = _SITE_RE.match(urlsplit(url).path)
    if not m:
        return []
    _s, country, audience, lang, rest = m.groups()
    host = urlsplit(url).netloc
    base = "https://%s" % host
    out = []
    for c in ("uk", "de", "ie", "es"):
        if c != country:
            out.append(("alt:" + c, "%s/%s/%s/%s%s" % (base, c, audience, lang, rest)))
    if audience != "professional":
        out.append(("alt:pro", "%s/%s/professional/%s%s" % (base, country, lang, rest)))
    return out


def discover_xhr(raw_html, product_url, limit=8):
    """URLs *.ajax / *.json mencionadas en el HTML de la ficha (https a iShares), sin duplicados y sin descargas de Excel/PDF."""
    seen, out = set(), []
    for m in _XHR_RE.finditer(raw_html):
        ref = m.group(1)
        if ref.startswith("https://"):
            url = ref
        elif ref.startswith("/"):
            p = urlsplit(product_url)
            url = "%s://%s%s" % (p.scheme, p.netloc, ref)
        else:
            url = product_url.rstrip("/") + "/" + ref
        if not host_ok(url) or re.search(r"filetype=(xls|xlsx|pdf)", url, re.I) or url in seen:
            continue
        seen.add(url)
        out.append(url)
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------------------------------------------------------------------------
# El diagnóstico
# ------------------------------------------------------------------------------------------------------------------------------
def fmt_date(d):
    return d.strftime("%d/%b/%Y") if d else "-"


def header_note(headers):
    got = ["%s=%s" % (k, headers[k][:48]) for k in KEEP_HEADERS if headers.get(k)]
    return ("  [" + " ".join(got) + "]") if got else ""


def run(directory, max_funds=4, show_headers=True, try_xhr=True, http_get=real_get, sleep=time.sleep, now=None, pause=1.0):
    now = now or datetime.now(timezone.utc)
    today = now.date()
    path = os.path.join(directory, "watch.json")
    if not os.path.isfile(path):
        say("::warning::nav_probe: no hay lista de fichas (watch.json): ¿falló el paso de lectura de la lista?")
        return 0
    with open(path, encoding="utf-8") as f:
        users, _dropped = pv.parse_watch(json.load(f), pv._plain)
    funds, seen = [], set()
    for u in users:
        for f in u["funds"]:
            if f["url"] and f["url"] not in seen:
                seen.add(f["url"])
                funds.append(f)
    say("nav_probe: %d fichas distintas en la lista; se prueban las %d primeras [run %s]" % (len(funds), min(len(funds), max_funds), now.strftime("%Y-%m-%dT%H:%MZ")))
    wins = Counter()
    for i, f in enumerate(funds[:max_funds], 1):
        tag = "#%d" % i
        results = {}                                           # etiqueta -> fecha de NAV (date) leída; para el resumen
        raw_base = None
        plan = [(v, "base" if v == "base_again" else v, f["url"]) for v in list(fm.ISHARES_VARIANTS) + ["base_again"]]
        plan += [(label, "base", u) for label, u in alt_urls(f["url"])]
        for label, variant, base_url in plan:
            url, headers = fm.ishares_request(base_url, variant)
            try:
                status, rh, text = http_get(url, headers)
            except Exception as e:                             # noqa: BLE001 - solo el nombre de la excepción (el mensaje puede llevar la URL)
                say("%s %-14s ERR %s" % (tag, label, type(e).__name__))
                sleep(pause)
                continue
            if label == "base":
                raw_base = text
            r, n_tok, dates, isin_in = read_html_nav(text, f["isin"], today)
            if r:
                results[label] = date.fromisoformat(r["nav_date_iso"]) if r.get("nav_date_iso") else None
                line = "NAV %s = %s" % (r["nav_date"], r["price"])
            else:
                line = "NAV - (sin NAV legible; ISIN en la página: %s; «NAV as of» con fecha: %d)" % ("sí" if isin_in else "no", n_tok)
            extra = ""
            if len(dates) > 1:
                extra = "  fechas «NAV as of» distintas en la página: " + ", ".join("%s x%d" % kv for kv in sorted(dates.items()))
            say("%s %-14s HTTP %s %6.0f KB  %s%s%s" % (tag, label, status, len(text) / 1024.0, line, header_note(rh) if show_headers else "", extra))
            sleep(pause)
        if try_xhr and raw_base:
            cands = discover_xhr(raw_base, f["url"])
            say("%s XHR/JSON citados en el HTML de la ficha: %d%s" % (tag, len(cands), "" if cands else " (el NAV viaja en el propio HTML o se carga por un medio que este diagnóstico no ve)"))
            for k, xurl in enumerate(cands, 1):
                label = "xhr%d" % k
                headers = {"User-Agent": fm.ISHARES_UA, "Accept": "application/json, text/plain, */*", "X-Requested-With": "XMLHttpRequest", "Cache-Control": "no-cache"}
                try:
                    status, rh, text = http_get(xurl, headers)
                except Exception as e:                         # noqa: BLE001
                    say("%s %-14s %s ERR %s" % (tag, label, shape(xurl), type(e).__name__))
                    sleep(pause)
                    continue
                cand, keys = read_xhr_nav(text, f["isin"], today)
                best = max((c for c in cand if c[0]), default=None, key=lambda c: c[0])
                if best:
                    results[label] = best[0]
                found = ", ".join("%s = %s" % (fmt_date(d), n) for d, n in cand[:3]) or "sin NAV reconocible"
                say("%s %-14s %s HTTP %s %6.0f KB  NAV %s%s%s" % (tag, label, shape(xurl), status, len(text) / 1024.0, found,
                                                                   ("  campos: " + ",".join(keys)) if keys and not cand else "", header_note(rh) if show_headers else ""))
                sleep(pause)
        newest = max((d for d in results.values() if d), default=None)
        if newest:
            top = [k for k, d in results.items() if d == newest]
            base_d = results.get("base")
            say("%s RESUMEN: fecha de NAV más reciente %s; la dan: %s; la base da %s%s" % (
                tag, fmt_date(newest), " ".join(top), fmt_date(base_d), "" if base_d == newest else "  <-- la base va por detrás"))
            if base_d != newest:
                for k in top:
                    wins[k] += 1
        else:
            say("%s RESUMEN: ninguna variante dio un NAV con fecha legible" % tag)
    if wins:
        say("VEREDICTO: variantes que dan una fecha más reciente que la base (nº de fichas): " + ", ".join("%s x%d" % kv for kv in wins.most_common()))
    else:
        say("VEREDICTO: ninguna variante supera a la base en las fichas probadas (o no hubo NAV legible). Si el navegador enseña una fecha más reciente, la causa no es la caché del HTML "
            "que este diagnóstico puede variar: mira las XHR, o si la ficha del navegador depende de cookies/sesión.")
    return 0


def main(argv=None, **kw):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--dir", required=True, help="carpeta de trabajo privada con watch.json (la deja `personal_v2.py watch`)")
    ap.add_argument("--max-funds", type=int, default=4, help="nº máximo de fichas a probar (cada una hace ~20 peticiones)")
    ap.add_argument("--no-headers", action="store_true", help="no imprimir las cabeceras de caché de cada respuesta")
    ap.add_argument("--no-xhr", action="store_true", help="no buscar ni probar peticiones XHR/JSON")
    a = ap.parse_args(argv)
    return run(a.dir, max(1, min(a.max_funds, 10)), not a.no_headers, not a.no_xhr, **kw)


if __name__ == "__main__":
    sys.exit(main())
