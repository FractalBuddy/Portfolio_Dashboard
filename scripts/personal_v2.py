#!/usr/bin/env python3
"""
Datos PROPIOS del dueño del dashboard, FUERA del repo: la lista a seguir vive en Supabase; este script la lee, recoge lo que el feed público no
cubre y lo publica de vuelta en Supabase. En el repo (público) no hay ISIN, listas ni fixtures del dueño; los tests usan ISIN y símbolos FALSOS.

    python3 scripts/personal_v2.py watch   --out  $RUNNER_TEMP/personal      # lee la lista (RPC personal_watch_for_backend)   [con token; solo stdlib]
    python3 scripts/personal_v2.py build   --dir  $RUNNER_TEMP/personal      # NAV de iShares, facts y series (código de terceros) [SIN token]
    python3 scripts/personal_v2.py publish --dir  $RUNNER_TEMP/personal      # publica (RPC publish_personal_feed)              [con token; solo stdlib]
    python3 scripts/personal_v2.py all     --dir  $RUNNER_TEMP/personal      # los tres pasos en un proceso (tests y uso local)

Contrato con Supabase (lo define el dueño en SQL; ver docs/CONTRATO_FEED_V2.md):
  * Solo se usa la clave PUBLICABLE (`sb_publishable_...`), en la cabecera `apikey`, y un token aleatorio (secreto de Actions PERSONAL_FEED_TOKEN) en el
    cuerpo de la RPC. NUNCA service_role ni sb_secret_: si la clave lo parece, se aborta SIN enviar nada (igual que supabase-keepalive.yml).
  * personal_watch_for_backend(p_token)           -> lista de usuarios con lo que siguen.
  * publish_personal_feed(p_token, p_user, p_payload) -> guarda el payload (jsonb) de ese usuario.

Es OPCIONAL: si Supabase o el token no están configurados, o algo falla, se emite un `::warning::` y el run publica el feed público igualmente (salida 0).
Solo es un error (salida 1) una configuración peligrosa: clave secreta o URL que no sea https.

PRIVACIDAD: los logs de Actions de un repo público son públicos. Aquí solo se escriben contadores: nunca ISIN, símbolos, nombres, URL, clave ni token. Todo
lo que imprimen las funciones reutilizadas de fetch_market_data (que citan ISIN en sus avisos) se descarta. El payload nunca va en un artefacto.
"""
import argparse
import base64
import contextlib
import io
import json
import os
import re
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import feed_v2_common as c
import sanity_check_v2 as v

RPC_WATCH = "personal_watch_for_backend"
RPC_PUBLISH = "publish_personal_feed"
MAX_PAYLOAD_BYTES = 1_000_000
MAX_USERS, MAX_FUNDS, MAX_SYMBOLS = 20, 20, 40
SERIES_DAYS = 2 * 366                       # ≤ 2 años de cierres diarios
TIMEOUT = 20
TRIES = 2
LOOPBACK = {"127.0.0.1", "localhost", "::1"}

_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_USER_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
_URL_PATH_RE = re.compile(r"^/[A-Za-z0-9/_.\-%]{1,200}$")
_TOKEN_RE = re.compile(r"^[\x21-\x7e]{16,200}$")
FACTS_KEYS = frozenset({
    "class_launch_date", "class_inception_date", "fund_launch_date", "class_aum", "fund_aum", "benchmark", "management_company",
    "morningstar_category", "ongoing_charges_pct", "management_fee_pct", "performance_fee_pct", "initial_charge_pct", "base_currency",
    "share_class_currency", "use_of_income", "structure", "methodology", "holdings", "domicile"})
# NAV: un salto mayor entre dos runs no es un movimiento real de un fondo indexado: parseo erróneo o cambio de página (R4 del apéndice A).
NAV_RATIO_LIMITS = (0.5, 2.0)


def _plain(text, limit):
    """Texto plano por defecto (si no se inyecta fetch_market_data._plain_text): sin '<', '>' ni caracteres de control, recortado."""
    return re.sub(r"[<>\x00-\x1f\x7f]", "", str(text or "")).strip()[:limit]


def say(msg):
    """Única vía de salida de texto: solo contadores y códigos, nunca datos del dueño."""
    print(msg)


# ------------------------------------------------------------------------------------------------------------------------------
# Configuración y claves
# ------------------------------------------------------------------------------------------------------------------------------
def key_looks_secret(key):
    """True si la clave parece una clave de servidor (sb_secret_, service_role, JWT con rol service_role)."""
    if key.startswith("sb_secret_") or "service_role" in key:
        return True
    try:
        part = key.split(".")[1]
        role = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))).get("role", "")
        return role == "service_role"
    except (IndexError, ValueError, TypeError, AttributeError):
        return False


def read_config(env=None):
    """(config|None, problema|None, fatal). Falta algo => (None, texto, False); configuración peligrosa => (None, texto, True)."""
    env = os.environ if env is None else env
    url, key, token = (env.get(k, "").strip() for k in ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "PERSONAL_FEED_TOKEN"))
    missing = [n for n, val in (("SUPABASE_URL", url), ("SUPABASE_PUBLISHABLE_KEY", key), ("PERSONAL_FEED_TOKEN", token)) if not val]
    if missing:
        return None, "faltan %s: no se publican datos propios" % ", ".join(missing), False
    if key_looks_secret(key):
        return None, "la clave configurada parece SECRETA (sb_secret_ / service_role): abortado sin enviarla; usa la publicable (sb_publishable_...)", True
    p = urllib.parse.urlsplit(url)
    if not (p.scheme == "https" or (p.scheme == "http" and p.hostname in LOOPBACK)) or not p.hostname or p.username or p.password or p.query or p.fragment:
        return None, "SUPABASE_URL debe ser una URL https:// sin credenciales", True
    if not _TOKEN_RE.match(token):
        return None, "PERSONAL_FEED_TOKEN con formato inesperado (16-200 caracteres ASCII imprimibles, sin espacios)", True
    return {"url": url.rstrip("/"), "key": key, "token": token}, None, False


# ------------------------------------------------------------------------------------------------------------------------------
# Supabase (RPC por HTTP, solo stdlib)
# ------------------------------------------------------------------------------------------------------------------------------
class RpcError(Exception):
    """Fallo de una RPC; el mensaje es un código corto (nunca el cuerpo de la respuesta ni la URL)."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def rpc(cfg, fn, body, limit=2_000_000, sleep=None):
    """POST {url}/rest/v1/rpc/{fn} con SOLO la cabecera `apikey` (clave publicable). Sin Authorization, sin seguir redirecciones. Devuelve el JSON o None."""
    import time
    req = urllib.request.Request(
        "%s/rest/v1/rpc/%s" % (cfg["url"], fn), data=json.dumps(body, separators=(",", ":"), allow_nan=False).encode("utf-8"), method="POST",
        headers={"apikey": cfg["key"], "Content-Type": "application/json", "Accept": "application/json", "User-Agent": "portfolio-feed-v2-ci/1.0"})
    last = "red"
    for i in range(TRIES):
        try:
            with _OPENER.open(req, timeout=TIMEOUT) as resp:
                raw = resp.read(limit + 1)
                if len(raw) > limit:
                    raise RpcError("respuesta demasiado grande")
                if not raw.strip():
                    return None
                try:
                    return json.loads(raw.decode("utf-8"))
                except ValueError:
                    raise RpcError("respuesta no JSON")
        except urllib.error.HTTPError as e:
            last = "HTTP %d" % e.code
            if e.code < 500 and e.code != 429:
                break
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            last = "red"
        if i < TRIES - 1:
            (sleep or time.sleep)(2.0)
    raise RpcError(last)


# ------------------------------------------------------------------------------------------------------------------------------
# Lo que devuelve personal_watch_for_backend
# ------------------------------------------------------------------------------------------------------------------------------
def ishares_url_ok(url):
    """Solo https a *.ishares.com, sin credenciales, puerto, consulta ni fragmento (el backend NO debe poder ser dirigido a otro sitio)."""
    try:
        p = urllib.parse.urlsplit(url)
        host = (p.hostname or "").lower()
        host.encode("ascii")
        return (p.scheme == "https" and (host == "ishares.com" or host.endswith(".ishares.com")) and not p.username and not p.password
                and p.port is None and not p.query and not p.fragment and bool(_URL_PATH_RE.match(p.path)))
    except (ValueError, UnicodeError, AttributeError):
        return False


def _clean_name(fm_text, value, limit=120):
    return fm_text(value, limit) if isinstance(value, str) else ""


def parse_watch(raw, plain_text):
    """Valida la respuesta (no fiable) de personal_watch_for_backend. Devuelve (usuarios, descartados). Cada usuario:
    {"user": id, "funds": [{isin, name, url, proxy_symbol?, prev_price?}], "symbols": [SYM, ...]}. Lo que no encaja se descarta y se cuenta."""
    dropped = 0
    rows = raw if isinstance(raw, list) else [raw] if isinstance(raw, dict) else []
    users = []
    for row in rows[:MAX_USERS]:
        if not isinstance(row, dict) or not (isinstance(row.get("user_id"), str) and _USER_RE.match(row["user_id"])):
            dropped += 1
            continue
        funds, symbols = [], []
        for f in (row.get("funds") if isinstance(row.get("funds"), list) else [])[:MAX_FUNDS]:
            ok = (isinstance(f, dict) and set(f) <= {"isin", "name", "url", "proxy_symbol", "prev_price"} and isinstance(f.get("isin"), str)
                  and _ISIN_RE.match(f["isin"]) and isinstance(f.get("url"), str))
            if not ok:
                dropped += 1
                continue
            item = {"isin": f["isin"], "name": _clean_name(plain_text, f.get("name")), "url": f["url"] if ishares_url_ok(f["url"]) else None}
            if item["url"] is None:
                dropped += 1                                        # el fondo se conserva (sin scrape), pero NUNCA se pide esa URL
            if f.get("proxy_symbol") is not None:
                if c.safe_symbol(f["proxy_symbol"]):
                    item["proxy_symbol"] = f["proxy_symbol"]
                else:
                    dropped += 1
            pp = f.get("prev_price")
            if c.finite(pp) and pp > 0:
                item["prev_price"] = float(pp)
            funds.append(item)
        for s in (row.get("symbols") if isinstance(row.get("symbols"), list) else [])[:MAX_SYMBOLS]:
            if c.safe_symbol(s):
                symbols.append(s)
            else:
                dropped += 1
        users.append({"user": row["user_id"], "funds": funds, "symbols": list(dict.fromkeys(symbols))})
    return users, dropped


# ------------------------------------------------------------------------------------------------------------------------------
# Construcción y validación del payload
# ------------------------------------------------------------------------------------------------------------------------------
_FACT_VALUE = lambda val, ctx: None if (isinstance(val, (int, float)) and not isinstance(val, bool) and c.finite(val)) else v.text(200)(val, ctx)   # noqa: E731
FUND_SPEC = v.obj({
    "isin": v.text(12, _ISIN_RE), "name": v.text(120), "price": v.number(0.1, 10000, nullable=True), "currency": v.enum("EUR"), "unit": v.enum("currency"),
    "nav_date": v.text(40), "nav_date_iso": v.day(), "nav_stale": v.boolean, "change_pct": v.number(-20, 20, nullable=True),
    "wk52_low": v.number(0, 1e6), "wk52_high": v.number(0, 1e6), "source": v.enum("ishares_nav_scrape", "none"),
    "proxy_symbol": v.symbol, "facts": v.mapping(re.compile("^(?:%s)$" % "|".join(sorted(FACTS_KEYS))), _FACT_VALUE, len(FACTS_KEYS))},
    required={"isin", "name", "price", "currency", "unit", "source"})
SERIES_SPEC = v.obj({
    "symbol": v.symbol, "currency": v.text(4, c.CURRENCY_RE), "unit": v.enum(*c.UNITS), "price_unit_divisor": v.integer(1, 100),
    "price": v.number(1e-9, 1e9, positive=True), "change_pct": v.number(-1000, 100000, nullable=True), "as_of": v.day(),
    "d": v.seq(v.day(), 600), "c": v.seq(v.number(1e-9, 1e9, positive=True), 600)})
PAYLOAD_SPEC = v.obj({
    "schema": v.SCHEMA_V, "generated_at": v.stamp, "funds": v.seq(FUND_SPEC, MAX_FUNDS), "series": v.seq(SERIES_SPEC, MAX_SYMBOLS),
    "public": v.seq(v.symbol, MAX_SYMBOLS + MAX_FUNDS)})


def payload_problems(payload, today=None):
    """Motivos por los que un payload no se envía (lista vacía = válido): esquema estricto, sin '<' ni '>', series coherentes, tope de bytes."""
    ctx = v.Ctx([], today or datetime.now(timezone.utc).date())
    bad = PAYLOAD_SPEC(payload, ctx)
    if bad:
        return [bad]
    out = []
    v._scan_strings(payload, "payload", ctx)
    out += ctx.errors
    for s in payload["series"]:
        if not (len(s["d"]) == len(s["c"]) and all(a < b for a, b in zip(s["d"], s["d"][1:])) and s["d"] and s["d"][-1] == s["as_of"]
                and abs(s["c"][-1] - s["price"]) <= 1e-6 * s["price"]):
            out.append("serie incoherente")
    if len(c.dumps(payload).encode("ascii")) > MAX_PAYLOAD_BYTES:
        out.append("payload > %d bytes" % MAX_PAYLOAD_BYTES)
    return out


def fit_payload(payload):
    """Recorta el payload hasta que quepa en MAX_PAYLOAD_BYTES: primero las series a 1 año, luego sin histórico, luego sin series. None si no cabe."""
    def size():
        return len(c.dumps(payload).encode("ascii"))
    if size() <= MAX_PAYLOAD_BYTES:
        return payload
    for keep in (260, 60, 0):
        for s in payload["series"]:
            s["d"], s["c"] = s["d"][-keep:] if keep else s["d"][-1:], s["c"][-keep:] if keep else s["c"][-1:]
        if size() <= MAX_PAYLOAD_BYTES:
            return payload
    payload["series"] = []
    return payload if size() <= MAX_PAYLOAD_BYTES else None


def nav_entry(fund, scraped, facts, warn):
    """Entrada de un fondo: NAV de iShares (si es creíble frente al anterior) + facts."""
    out = {"isin": fund["isin"], "name": fund["name"], "price": None, "currency": "EUR", "unit": "currency", "source": "none"}
    if scraped and c.finite(scraped.get("price")):
        price, prev = float(scraped["price"]), fund.get("prev_price")
        if prev and not (NAV_RATIO_LIMITS[0] < price / prev < NAV_RATIO_LIMITS[1]):
            warn("nav_salto")                                    # R4: NAV x10 respecto al anterior => no se publica ese NAV
        else:
            out.update({"price": round(price, 4), "source": "ishares_nav_scrape"})
            for k in ("nav_date", "nav_date_iso", "nav_stale", "change_pct", "wk52_low", "wk52_high"):
                if scraped.get(k) is not None:
                    out[k] = scraped[k]
    if fund.get("proxy_symbol"):
        out["proxy_symbol"] = fund["proxy_symbol"]
    if isinstance(facts, dict) and facts:
        out["facts"] = {k: val for k, val in facts.items() if k in FACTS_KEYS}
    return out


def series_entry(sym, bars, currency, today):
    """Serie recortada de un símbolo FUERA del universo público. Sin divisa conocida no se publica (None)."""
    if not (isinstance(currency, str) and c.CURRENCY_RE.match(currency)) or len(bars) < 2:
        return None
    cutoff = (today - timedelta(days=SERIES_DAYS)).isoformat()
    bars = [b for b in bars if b["d"] >= cutoff]
    if len(bars) < 2:
        return None
    unit = "points" if sym.startswith("^") else "rate" if sym.endswith("=X") else "subunit" if currency in c.SUBUNIT_FAMILIES else "currency"
    last, prev = bars[-1], bars[-2]
    return {"symbol": sym, "currency": currency, "unit": unit, "price_unit_divisor": c.price_unit_divisor(currency, unit), "price": last["c"],
            "change_pct": round((last["c"] / prev["c"] - 1) * 100, 2), "as_of": last["d"], "d": [b["d"] for b in bars], "c": [b["c"] for b in bars]}


def build_payload(user, public, fetchers, now, warn):
    """Payload de un usuario. `public`: símbolos del universo público (esos NO se incluyen: el cliente los lee del feed público)."""
    funds = []
    for f in user["funds"]:
        scraped = facts = None
        if f["url"]:
            scraped, facts = fetchers.nav(f["url"], f["isin"]), fetchers.facts(f["url"])
        funds.append(nav_entry(f, scraped, facts, warn))
    wanted = list(dict.fromkeys(user["symbols"] + [f["proxy_symbol"] for f in user["funds"] if f.get("proxy_symbol")]))
    series, pub = [], []
    for sym in wanted:
        if sym.upper() in public:
            pub.append(sym)
            continue
        try:
            bars, cur = fetchers.series(sym)
        except Exception:                                        # noqa: BLE001 - un símbolo que falla no tumba el resto
            warn("serie_fallo")
            continue
        entry = series_entry(sym, bars, cur, now.date())
        if entry:
            series.append(entry)
        else:
            warn("serie_sin_divisa_o_datos")
    payload = {"schema": c.SCHEMA, "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "funds": funds, "series": series, "public": pub}
    return fit_payload(payload)


class Fetchers:
    """Las tres fuentes de datos externas, inyectables en los tests: nav(url, isin), facts(url), series(symbol) -> (barras, divisa)."""
    def __init__(self, nav, facts, series):
        self.nav, self.facts, self.series = nav, facts, series


def default_fetchers():
    """Reutiliza la lógica probada de fetch_market_data (iShares, Yahoo). SOLO se importa aquí: los pasos con token no cargan código de terceros."""
    import feed_v2 as f2
    import fetch_market_data as fm

    def series(sym):
        df = fm.yf_history(sym, period="2y", interval="1d", auto_adjust=False)
        return f2.bars_from_df(df, datetime.now(timezone.utc).date()), df.attrs.get("currency")
    return Fetchers(fm.fetch_ishares_nav, fm.fetch_ishares_fund_facts, series), fm._plain_text


# ------------------------------------------------------------------------------------------------------------------------------
# Pasos
# ------------------------------------------------------------------------------------------------------------------------------
def _write_private(path, obj):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, separators=(",", ":"), allow_nan=False)


def _read_private(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def step_watch(cfg, directory):
    """Lee la lista de Supabase y la guarda (sin validar: lo valida `build`). Devuelve 0."""
    try:
        raw = rpc(cfg, RPC_WATCH, {"p_token": cfg["token"]})
    except RpcError as e:
        say("::warning::datos propios: no se pudo leer la lista en Supabase (%s); el feed público se publica igualmente" % e)
        return 0
    _write_private(os.path.join(directory, "watch.json"), raw)
    rows = raw if isinstance(raw, list) else [raw] if isinstance(raw, dict) else []
    for row in rows:                                              # aunque algo imprima un ISIN por error, Actions lo enmascara
        for f in (row.get("funds") if isinstance(row, dict) and isinstance(row.get("funds"), list) else []):
            if isinstance(f, dict) and isinstance(f.get("isin"), str) and _ISIN_RE.match(f["isin"]):
                say("::add-mask::" + f["isin"])
    say("datos propios: lista leída (%d usuarios)" % len(rows))
    return 0


def step_build(directory, registry_path, fetchers=None, plain_text=None, now=None):
    """Valida la lista, recoge NAV/facts/series y escribe un payload por usuario. NO usa el token. Descarta TODA la salida de las funciones
    reutilizadas (citan ISIN y símbolos)."""
    now = now or datetime.now(timezone.utc)
    path = os.path.join(directory, "watch.json")
    if not os.path.isfile(path):
        return 0
    registry = c.load_registry(registry_path)
    public = {e["symbol"].upper() for e in registry}
    sink = io.StringIO()
    warns = {}

    def warn(code):
        warns[code] = warns.get(code, 0) + 1
    if fetchers is None:
        with contextlib.redirect_stderr(sink), contextlib.redirect_stdout(sink):
            fetchers, plain_text = default_fetchers()
    with contextlib.redirect_stderr(sink), contextlib.redirect_stdout(sink):
        users, dropped = parse_watch(_read_private(path), plain_text or _plain)
        payloads = []
        for u in users:
            try:
                p = build_payload(u, public, fetchers, now, warn)
            except Exception:                                      # noqa: BLE001
                warn("usuario_fallo")
                continue
            probs = payload_problems(p, now.date()) if p else ["no cabe en %d bytes" % MAX_PAYLOAD_BYTES]
            if probs:
                warn("payload_invalido")
                continue
            payloads.append({"user": u["user"], "payload": p})
    _write_private(os.path.join(directory, "payloads.json"), payloads)
    say("datos propios: %d payloads (%d fondos, %d series), %d elementos descartados%s" % (
        len(payloads), sum(len(p["payload"]["funds"]) for p in payloads), sum(len(p["payload"]["series"]) for p in payloads), dropped,
        "".join("; %s x%d" % (k, n) for k, n in sorted(warns.items()))))
    if warns or dropped:
        say("::warning::datos propios: algunos elementos no se han podido recoger (solo contadores arriba; sin datos personales en el log)")
    return 0


def step_publish(cfg, directory):
    path = os.path.join(directory, "payloads.json")
    if not os.path.isfile(path):
        return 0
    sent = failed = 0
    for item in _read_private(path):
        if payload_problems(item["payload"]) or not _USER_RE.match(item["user"]):         # última barrera antes de enviar nada
            failed += 1
            continue
        try:
            rpc(cfg, RPC_PUBLISH, {"p_token": cfg["token"], "p_user": item["user"], "p_payload": item["payload"]})
            sent += 1
        except RpcError as e:
            failed += 1
            say("::warning::datos propios: no se pudo publicar un payload en Supabase (%s)" % e)
    say("datos propios: %d payloads publicados, %d fallidos" % (sent, failed))
    return 0


def main(argv=None, fetchers=None, plain_text=None, env=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("step", choices=("watch", "build", "publish", "all"))
    ap.add_argument("--dir", "--out", dest="dir", required=True, help="carpeta de trabajo PRIVADA (p. ej. $RUNNER_TEMP/personal); nunca un artefacto")
    ap.add_argument("--registry", default="registry.json")
    a = ap.parse_args(argv)
    if a.step in ("watch", "publish", "all"):
        cfg, problem, fatal = read_config(env)
        if cfg is None:
            say(("::error::datos propios: " if fatal else "::warning::datos propios: ") + problem)
            return 1 if fatal else 0
    else:
        cfg = None
    if a.step in ("watch", "all"):
        step_watch(cfg, a.dir)
    if a.step in ("build", "all"):
        step_build(a.dir, a.registry, fetchers, plain_text)
    if a.step in ("publish", "all"):
        step_publish(cfg, a.dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
