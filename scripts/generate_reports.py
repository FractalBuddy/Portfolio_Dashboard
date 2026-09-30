#!/usr/bin/env python3
"""Genera informes (fiscal, mensual, anual) y registra el patrimonio diario SIN abrir el dashboard.

Flujo (todo cifrado extremo a extremo; GitHub/Gist solo ven texto cifrado):
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
import base64, datetime as dt, gzip, json, os, re, sys, tempfile, urllib.request, urllib.error
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
PASS = _need("SYNC_PASSPHRASE")
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
    key = _key(base64.b64decode(env["salt"]), int(env.get("iter", ITER)))
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
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def file_text(f: dict) -> str:
    if f.get("truncated") or f.get("content") is None:
        with urllib.request.urlopen(urllib.request.Request(f["raw_url"], headers={"Authorization": "token " + TOKEN}), timeout=60) as r:
            return r.read().decode()
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
        log("No hay estado cifrado en el Gist (activa «Cifrar la copia» en el dashboard). Nada que hacer.")
        return 0
    state = json.loads(decrypt_env(json.loads(file_text(files[STATE_FILE]))).decode())
    if DASH_FILE not in files:
        log("Falta dashboard.enc.json: abre el dashboard una vez con la copia cifrada activada.")
        return 1
    dash_env = json.loads(file_text(files[DASH_FILE]))
    dash_html = gzip.decompress(base64.b64decode(decrypt_env(dash_env).decode())).decode()
    job_hist = {"snapshots": []}
    if HIST_FILE in files:
        try:
            job_hist = json.loads(decrypt_env(json.loads(file_text(files[HIST_FILE]))).decode())
        except Exception as e:  # noqa: BLE001
            log("job-history ilegible, se reinicia:", type(e).__name__)

    existing = {}
    for name, f in files.items():
        if re.match(r"^report-.*\.enc\.json$", name):
            try:
                existing[name] = json.loads(file_text(f))
            except Exception:  # noqa: BLE001
                pass

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
            browser = p.chromium.launch()
            ctx = browser.new_context(timezone_id=str(TZ), locale="es-ES", viewport={"width": 1300, "height": 900})
            page = ctx.new_page()
            page.route("**/api.github.com/**", lambda r: r.abort())      # el dashboard no toca GitHub aquí
            page.add_init_script("localStorage.setItem(%s, %s);" % (json.dumps(STORAGE_KEY), json.dumps(json.dumps(state))))
            if FORCE_DATE:
                page.clock.install(time=FORCE_DATE + "T08:00:00")
            page.goto("file://" + path)
            page.wait_for_function("typeof state !== 'undefined' && typeof buildTaxReportHTML === 'function'", timeout=60000)
            # espera al feed de mercado (precios) hasta 60 s
            for _ in range(30):
                if page.evaluate("!marketFeedLoading && !!(state.marketFeed && state.marketFeed.generated_at)"):
                    break
                page.wait_for_timeout(2000)
            feed_ok = page.evaluate("""() => { const g = state.marketFeed && state.marketFeed.generated_at; if(!g) return false;
                return (Date.now() - new Date(g).getTime()) < 36*3600*1000; }""")
            if FORCE_DATE:
                feed_ok = feed_ok or ALLOW_STALE_FEED
            page.evaluate("() => { compoundSavings(); snapshotHistory(); captureYearEndSnapshot(); }")
            snap = page.evaluate("state.history.find(h => h.date === todayISO()) || null")
            if snap and feed_ok:
                job_hist["snapshots"] = [s for s in job_hist["snapshots"] if s.get("date") != snap["date"]] + [snap]
                job_hist["snapshots"] = sorted(job_hist["snapshots"], key=lambda s: s["date"])[-900:]
                out_files[HIST_FILE] = {"content": json.dumps(encrypt_text(json.dumps(job_hist), {"savedAt": dt.datetime.utcnow().isoformat() + "Z"}))}
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
                meta = {"type": typ, "period": period, "kind": kind, "generatedAt": dt.datetime.utcnow().isoformat() + "Z"}
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
    except InvalidTag:
        log("ERROR: no se puede descifrar. SYNC_PASSPHRASE no coincide con la contraseña usada en el "
            "dashboard (o el Gist está corrupto). Comprueba mayúsculas, espacios y símbolos.")
        sys.exit(4)
