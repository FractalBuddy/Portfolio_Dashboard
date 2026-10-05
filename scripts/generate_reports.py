#!/usr/bin/env python3
"""Genera informes (fiscal, mensual, anual) y registra el patrimonio diario SIN abrir el dashboard.

Flujo (la copia va cifrada: GitHub/Gist solo ven texto cifrado; OJO: este proceso recibe SYNC_PASSPHRASE
 como secreto de Actions para poder descifrar, así que confías en tu repo/GitHub para este paso):
  1. Lee del Gist privado el estado cifrado de la cartera (portfolio-state.enc.json), una copia
     cifrada del propio dashboard (dashboard.enc.json) y el histórico diario del propio proceso
     (job-history.enc.json).
  2. Descifra con SYNC_PASSPHRASE (secreto del repo) y abre el dashboard en un Chromium sin
     cabeza: exactamente el mismo código que ves en el navegador calcula intereses, valora la
     cartera con el feed de mercado y construye los informes.
  3. Anota el patrimonio de hoy (huecos de días sin abrir el dashboard) y sube al Gist, cifrados,
     los informes que toquen: report-<tipo>-<periodo>.enc.json.
Nada de lo descifrado se escribe en el log. Requiere: cryptography, playwright (chromium).
"""
import base64, datetime as dt, gzip, json, os, re, sys, tempfile, time, urllib.request, urllib.error, urllib.parse
from zoneinfo import ZoneInfo
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

API = os.environ.get("GIST_API", "https://api.github.com").rstrip("/")


def _need(name: str) -> str:
    v = os.environ.get(name, "")
    if not v.strip():
        print(f"ERROR: el secret {name} está vacío o no existe. Créalo en Settings > Secrets and variables > "
              f"Actions > Repository secrets (el nombre debe ser exactamente {name}).", flush=True)
        sys.exit(3)
    return v


GIST_ID = _need("GIST_ID").strip()
TOKEN = _need("GIST_TOKEN").strip()
PASS = _need("SYNC_PASSPHRASE").rstrip("\r\n")   # un salto de línea final al pegar el secret rompería el descifrado
TZ = ZoneInfo(os.environ.get("REPORT_TZ", "Europe/Madrid"))
FORCE_DATE = os.environ.get("FORCE_DATE", "").strip()          # solo para pruebas: AAAA-MM-DD
ALLOW_STALE_FEED = os.environ.get("ALLOW_STALE_FEED") == "1"    # solo para pruebas
STATE_FILE, DASH_FILE, HIST_FILE = "portfolio-state.enc.json", "dashboard.enc.json", "job-history.enc.json"
STORAGE_KEY = "portfolio_dashboard_v2"
ITER = 310000


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- cripto (mismo formato que WebCrypto)
def _key(salt: bytes, iters: int) -> bytes:
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iters).derive(PASS.encode())


def decrypt_env(env: dict) -> bytes:
    try:
        iters = int(env.get("iter", ITER))
    except (TypeError, ValueError):
        iters = ITER
    if not 100_000 <= iters <= 2_000_000:   # el sobre no está autenticado: sin tope, un Gist hostil podría colgar el job o rebajar el KDF
        iters = ITER
    key = _key(base64.b64decode(env["salt"]), iters)
    return AESGCM(key).decrypt(base64.b64decode(env["iv"]), base64.b64decode(env["ct"]), None)


def encrypt_text(text: str, meta: dict) -> dict:
    salt, iv = os.urandom(16), os.urandom(12)
    ct = AESGCM(_key(salt, ITER)).encrypt(iv, text.encode(), None)
    env = {"v": 1, "iter": ITER, "salt": base64.b64encode(salt).decode(), "iv": base64.b64encode(iv).decode(),
           "ct": base64.b64encode(ct).decode()}
    env.update(meta)
    return env


# ---------------------------------------------------------------- Gist
def gh(method: str, path: str, body=None):
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "token " + TOKEN, "Accept": "application/vnd.github+json",
                                          "Content-Type": "application/json", "User-Agent": "portfolio-reports"})
    return json.loads(_open(req).decode())


def _open(req) -> bytes:
    """urlopen con 3 intentos ante 429/5xx y errores de red; los 4xx (token, permisos, 404) fallan a la primera."""
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == 2:
                raise
        time.sleep(3 * (attempt + 1))


def file_text(f: dict) -> str:
    if f.get("truncated") or f.get("content") is None:
        raw = str(f["raw_url"])
        host = urllib.parse.urlparse(raw).hostname or ""
        if not (raw.startswith(API + "/") or host == "gist.githubusercontent.com"):   # el token solo viaja a GitHub
            raise RuntimeError("raw_url con host inesperado: " + host)
        return _open(urllib.request.Request(raw, headers={"Authorization": "token " + TOKEN})).decode()
    return f["content"]


# ---------------------------------------------------------------- qué toca generar
def add_months(y, m, d):
    m += d
    while m < 1:
        y, m = y - 1, m + 12
    while m > 12:
        y, m = y + 1, m - 12
    return y, m


def plan(today: dt.date, existing: dict, first_date: str):
    """existing: nombre de fichero -> meta del sobre. first_date: primer día con datos. Devuelve [(tipo, periodo, kind)]."""
    tasks = []
    py, pm = add_months(today.year, today.month, -1)
    pmk = f"{py}-{pm:02d}"
    last_day_prev_month = (dt.date(today.year, today.month, 1) - dt.timedelta(days=1)).isoformat()
    if last_day_prev_month >= first_date and f"report-monthly-{pmk}.enc.json" not in existing:
        tasks.append(("monthly", pmk, "final"))
    # anual y fiscal: solo en la ventana posterior al cierre del año (enero-marzo) y si hay datos de ese año
    ay = str(today.year - 1)
    if today.month <= 3 and ay >= first_date[:4]:
        if f"report-annual-{ay}.enc.json" not in existing:
            tasks.append(("annual", ay, "final"))
        meta = existing.get(f"report-tax-{ay}.enc.json")
        if not meta or meta.get("kind") != "final":
            tasks.append(("tax", ay, "final"))
    return tasks


def main():
    today = dt.date.fromisoformat(FORCE_DATE) if FORCE_DATE else dt.datetime.now(TZ).date()
    gist = gh("GET", f"/gists/{GIST_ID}")
    files = gist.get("files", {})
    if STATE_FILE not in files:
        log("ERROR: no hay estado cifrado en el Gist (¿GIST_ID equivocado, o falta activar «Cifrar la copia» en el dashboard?).")
        return 1
    state = json.loads(decrypt_env(json.loads(file_text(files[STATE_FILE]))).decode())
    if DASH_FILE not in files:
        log("ERROR: falta dashboard.enc.json: abre el dashboard una vez con la copia cifrada activada y pulsa «Backup ahora».")
        return 1
    dash_env = json.loads(file_text(files[DASH_FILE]))
    dash_html = gzip.decompress(base64.b64decode(decrypt_env(dash_env).decode())).decode()
    job_hist = {"snapshots": []}
    if HIST_FILE in files:
        try:
            job_hist = json.loads(decrypt_env(json.loads(file_text(files[HIST_FILE]))).decode())
        except InvalidTag:
            raise
        except Exception as e:  # noqa: BLE001
            # no se reinicia en silencio: sobrescribir el histórico del proceso perdería días ya registrados
            log("ERROR: job-history ilegible (", type(e).__name__, "): se aborta para no sobrescribirlo.")
            return 1

    existing = {}
    for name, f in files.items():
        if re.match(r"^report-.*\.enc\.json$", name):
            try:
                existing[name] = json.loads(file_text(f))
            except Exception as e:  # noqa: BLE001
                # un fallo transitorio aquí haría regenerar y sobrescribir un informe ya generado
                log("ERROR: no se pudo leer el informe existente", name, "(", type(e).__name__, "): se aborta para no sobrescribirlo.")
                return 1

    # Fusiona los días registrados por este proceso en el histórico (solo fechas que faltan).
    have = {h.get("date") for h in state.get("history", [])}
    for s in job_hist.get("snapshots", []):
        if s.get("date") not in have:
            state.setdefault("history", []).append(s)
    state["history"] = sorted(state.get("history", []), key=lambda h: h.get("date", ""))
    st = state.setdefault("settings", {})
    st["cloudEncrypt"] = False           # el proceso nunca sincroniza por su cuenta
    st["githubGistToken"], st["githubGistTokenEnc"] = "", None

    first_date = min([h.get('date', '9999') for h in state.get('history', [])] or ['9999-12-31'])
    tasks = plan(today, existing, first_date)
    # Ya existe el snapshot de hoy en el histórico registrado por el proceso?
    need_snapshot = not any(s.get("date") == today.isoformat() for s in job_hist.get("snapshots", []))
    if not tasks and not need_snapshot:
        log("Nada que generar hoy.")
        return 0

    from playwright.sync_api import sync_playwright
    out_files, new_reports = {}, []
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "dashboard.html")
        open(path, "w", encoding="utf-8").write(dash_html)
        with sync_playwright() as p:
            # El dashboard descifrado se ejecuta en Chromium: no hereda los secretos del job (token, contraseña).
            _drop = {"GIST_TOKEN", "SYNC_PASSPHRASE", "GIST_ID", "GITHUB_TOKEN"}
            browser = p.chromium.launch(env={k: v for k, v in os.environ.items() if k not in _drop and not k.startswith(("ACTIONS_", "GITHUB_"))})
            ctx = browser.new_context(timezone_id=str(TZ), locale="es-ES", viewport={"width": 1300, "height": 900})
            page = ctx.new_page()
            # Lista blanca de red: el dashboard descifrado solo puede hablar con los orígenes que necesita para precios/FX/feed
            # (y file:// para sí mismo). Todo lo demás (GitHub, Google Fonts, imágenes de terceros...) se aborta.
            _ALLOW = {"cdn.jsdelivr.net", "api.coingecko.com", "open.er-api.com", "api.frankfurter.app"}
            def _gate(route):
                u = route.request.url
                if u.startswith(("file://", "data:", "blob:", "about:")) or urllib.parse.urlparse(u).hostname in _ALLOW:
                    return route.continue_()
                return route.abort()
            page.route("**/*", _gate)
            page.add_init_script("localStorage.setItem(%s, %s);" % (json.dumps(STORAGE_KEY), json.dumps(json.dumps(state))))
            if FORCE_DATE:
                page.clock.install(time=FORCE_DATE + "T08:00:00")
            page.goto("file://" + path)
            # wait_for_function evalúa una cadena con eval() y la CSP del dashboard (sin 'unsafe-eval') lo bloquea:
            # se consulta con page.evaluate (vía CDP), que sí funciona, y se da un mensaje claro si el dashboard es antiguo.
            ready = False
            for _ in range(30):
                try:
                    ready = page.evaluate("typeof state !== 'undefined' && typeof buildTaxReportHTML === 'function' && typeof buildPerformanceReportHTML === 'function'")
                except Exception:  # noqa: BLE001
                    ready = False
                if ready:
                    break
                page.wait_for_timeout(2000)
            if not ready:
                log("ERROR: dashboard.enc.json está desactualizado o no arranca (faltan las funciones de informes). "
                    "Abre el dashboard actual y pulsa «Backup ahora» para volver a subirlo.")
                browser.close()
                return 1
            t0 = page.evaluate("new Date().toISOString()")
            # espera al feed de mercado (precios) hasta 60 s
            for _ in range(30):
                if page.evaluate("!marketFeedLoading && !!(state.marketFeed && state.marketFeed.generated_at)"):
                    break
                page.wait_for_timeout(2000)
            feed_ok = page.evaluate("""() => { const g = state.marketFeed && state.marketFeed.generated_at; if(!g) return false;
                return (Date.now() - new Date(g).getTime()) < 36*3600*1000; }""")
            # Los precios cripto vienen de CoinGecko, no del feed de Yahoo: si hay posiciones cripto se espera a que la
            # descarga de ESTA ejecución termine; sin ella el patrimonio saldría con precios viejos y se guardaría como bueno.
            has_crypto = page.evaluate("state.positions.some(p => p.category === 'crypto' && p.coingeckoId)")
            if has_crypto:
                crypto_ok = False
                for _ in range(30):
                    if page.evaluate("(t0) => !!state.lastPriceFetch && state.lastPriceFetch >= t0", t0):
                        crypto_ok = True
                        break
                    page.wait_for_timeout(2000)
                if not crypto_ok:
                    log("Precios cripto no actualizados en esta ejecución (¿CoinGecko limitado?): se trata como datos no frescos.")
                    feed_ok = False
            if FORCE_DATE:
                feed_ok = feed_ok or ALLOW_STALE_FEED
            page.evaluate("() => { compoundSavings(); snapshotHistory(); captureYearEndSnapshot(); }")
            snap = page.evaluate("state.history.find(h => h.date === todayISO()) || null")
            if snap and feed_ok:
                job_hist["snapshots"] = [s for s in job_hist.get("snapshots", []) if s.get("date") != snap["date"]] + [snap]
                job_hist["snapshots"] = sorted(job_hist["snapshots"], key=lambda s: s["date"])[-900:]
                out_files[HIST_FILE] = {"content": json.dumps(encrypt_text(json.dumps(job_hist), {"savedAt": dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f') + "Z"}))}
                log("Patrimonio de hoy registrado.")
            elif snap:
                log("Feed de mercado no fresco: no se registra el patrimonio de hoy (se evita guardar un valor erróneo).")
            for typ, period, kind in tasks:
                if not feed_ok and not ALLOW_STALE_FEED:
                    log(f"Se pospone {typ} {period}: feed no fresco.")
                    continue
                if typ == "tax":
                    html = page.evaluate("y => buildTaxReportHTML(y)", period)
                else:
                    html = page.evaluate("([t, p]) => buildPerformanceReportHTML(t, p)", [typ, period])
                name = f"report-{typ}-{period}.enc.json"
                meta = {"type": typ, "period": period, "kind": kind, "generatedAt": dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f') + "Z"}
                out_files[name] = {"content": json.dumps(encrypt_text(html, meta))}
                new_reports.append(name)
                log("Informe generado:", typ, period)
            browser.close()

    if out_files:
        gh("PATCH", f"/gists/{GIST_ID}", {"files": out_files})
        log("Subido al Gist:", ", ".join(sorted(out_files)))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.HTTPError as e:
        log("Error HTTP con GitHub:", e.code)   # sin cuerpo: podría eco de datos
        log({401: "-> GIST_TOKEN no es válido o ha caducado (usa un token *classic* con permiso 'gist').",
             403: "-> GIST_TOKEN sin permisos o límite de peticiones alcanzado.",
             404: "-> GIST_ID incorrecto, o el token es de otra cuenta/sin permiso 'gist' "
                  "(un Gist secreto solo se ve con el token de su dueño)."}.get(e.code, "-> Reintenta más tarde."))
        sys.exit(2)
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        log("ERROR de red hablando con GitHub:", type(e).__name__, "-> reintenta más tarde (el próximo cron lo volverá a intentar).")
        sys.exit(2)
    except InvalidTag:
        log("ERROR: no se puede descifrar. SYNC_PASSPHRASE no coincide con la contraseña usada en el "
            "dashboard (o el Gist está corrupto). Comprueba mayúsculas, espacios y símbolos.")
        sys.exit(4)
    except Exception as e:  # noqa: BLE001
        # Los logs de un repo público los ve cualquiera: solo el tipo de error (el detalle, únicamente con DEBUG=1 en una ejecución manual).
        log("ERROR inesperado:", type(e).__name__, ("- " + str(e)[:200]) if os.environ.get("DEBUG") == "1" else "")
        sys.exit(1)
