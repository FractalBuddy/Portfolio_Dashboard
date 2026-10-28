"""Feed v2, paso 5: datos propios del dueño en Supabase. Servidor HTTP LOCAL que simula Supabase (ninguna conexión real). TODOS los ISIN, símbolos,
nombres y tokens de este fichero son FALSOS."""
import base64
import json
import os
import stat
import threading
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import feed_v2_common as c
import personal_v2 as pv

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
TOKEN = "tok_ABCDEFGHIJKLMNOP0123456789"
PUB_KEY = "sb_publishable_FAKEKEY0123456789"
ISIN_A, ISIN_B = "IE0FAKE00001", "IE0FAKE00002"             # ISIN inventados (12 caracteres, formato válido)
URL_A = "https://www.ishares.com/ch/individual/en/products/000001/fake-fund-a"
URL_B = "https://www.ishares.com/uk/individual/en/products/000002/fake-fund-b"


class FakeSupabase:
    def __init__(self, watch=None, token=TOKEN, key=PUB_KEY):
        self.watch, self.token, self.key = watch, token, key
        self.reqs = []                                       # (ruta, cabeceras en minúscula, cuerpo)
        self.publish_status = 200
        self.watch_status = 200
        self.redirect_to = None
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                try:
                    parsed = json.loads(body)
                except ValueError:
                    parsed = None
                outer.reqs.append((self.path, {k.lower(): v for k, v in self.headers.items()}, parsed))
                if outer.redirect_to:
                    self.send_response(302)
                    self.send_header("Location", outer.redirect_to)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                ok = self.headers.get("apikey") == outer.key and isinstance(parsed, dict) and parsed.get("p_token") == outer.token
                fn = self.path.rsplit("/", 1)[-1]
                status = 401 if not ok else outer.watch_status if fn == pv.RPC_WATCH else outer.publish_status
                out = json.dumps(outer.watch if fn == pv.RPC_WATCH else None).encode() if status == 200 else b'{"message":"secreto del servidor 12345"}'
                self.send_response(status)
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.srv.server_address[1]
        threading.Thread(target=lambda: self.srv.serve_forever(poll_interval=0.01), daemon=True).start()

    def env(self, **over):
        e = {"SUPABASE_URL": self.url, "SUPABASE_PUBLISHABLE_KEY": self.key, "PERSONAL_FEED_TOKEN": self.token}
        e.update(over)
        return {k: v for k, v in e.items() if v is not None}

    def calls(self, fn=None):
        return [r for r in self.reqs if fn is None or r[0].endswith("/" + fn)]

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture(autouse=True)
def _sin_esperas(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)


@pytest.fixture()
def sb():
    s = FakeSupabase()
    yield s
    s.close()


def bars(n=300, start=100.0, end=date(2026, 10, 23)):
    from datetime import timedelta
    out, d = [], end
    for i in range(n):
        out.append({"d": d.isoformat(), "o": 1, "h": 1, "l": 1, "c": round(start + i * 0.01, 4), "v": 0})
        d -= timedelta(days=1)
    return list(reversed(out))


def long_bars(years):
    """Serie diaria de `years` años (solo días laborables) terminada en 2026-10-23; cierres crecientes y > 0."""
    from datetime import timedelta
    out, d, i = [], date(2026, 10, 23), 0
    while d > date(2026, 10, 23) - timedelta(days=int(years * 365.25)):
        if d.weekday() < 5:
            out.append({"d": d.isoformat(), "o": 1, "h": 1, "l": 1, "c": round(200.0 - i * 0.01, 4), "v": 0})
            i += 1
        d -= timedelta(days=1)
    return list(reversed(out))


class Spy:
    """Fuentes externas simuladas; guardan qué se pidió."""
    def __init__(self, nav_price=25.5, series_currency="USD"):
        self.nav_calls, self.facts_calls, self.series_calls = [], [], []
        self.nav_price, self.series_currency = nav_price, series_currency
        self.noise = False
        self.periods = {}
        self.long = None

    def nav(self, url, isin):
        self.nav_calls.append((url, isin))
        if self.noise:
            import sys
            print("  ! could not find expected NAV pattern for %s" % isin, file=sys.stderr)
        return {"price": self.nav_price, "nav_date": "23/Oct/2026", "nav_date_iso": "2026-10-23", "nav_stale": False, "change_pct": 0.4,
                "wk52_low": 20.0, "wk52_high": 30.0}

    def facts(self, url):
        self.facts_calls.append(url)
        return {"ongoing_charges_pct": 0.1, "domicile": "Ireland", "evil_key": "x"}

    def series(self, sym, period="2y"):
        self.series_calls.append(sym)
        self.periods[sym] = period
        if sym == "BOOM":
            raise RuntimeError("falla")
        if self.long is not None and period == "max":
            return self.long, self.series_currency
        return bars(), self.series_currency

    def fetchers(self):
        return pv.Fetchers(self.nav, self.facts, self.series)


def watch_row(user="usuario-1", funds=None, symbols=None):
    return {"user_id": user, "funds": funds if funds is not None else [{"isin": ISIN_A, "name": "Fondo Falso A", "url": URL_A}],
            "symbols": symbols if symbols is not None else ["FAKE1", "AAPL"]}


def run_all(sb, tmp_path, spy=None, **over):
    spy = spy or Spy()
    reg = str(tmp_path / "registry.json")
    open(reg, "w").write(json.dumps({"schema": 1, "symbols": [
        {"symbol": "AAPL", "kind": "yahoo", "name": "Apple", "currency": "USD", "unit": "currency", "tier": "hot", "status": "active", "fail_streak": 0}]}))
    rc = pv.main(["all", "--dir", str(tmp_path / "work"), "--registry", reg], fetchers=spy.fetchers(), env=sb.env(**over))
    return rc, spy


# ------------------------------------------------------------------ configuración
@pytest.mark.parametrize("var", ["SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "PERSONAL_FEED_TOKEN"])
def test_si_falta_algo_solo_hay_un_warning_y_ninguna_peticion(sb, tmp_path, capsys, var):
    rc, spy = run_all(sb, tmp_path, **{var: None})
    out = capsys.readouterr().out
    assert rc == 0 and out.count("::warning::") == 1 and var in out and "::error::" not in out
    assert sb.reqs == [] and spy.nav_calls == [] and spy.series_calls == []


def test_sin_ninguna_variable_el_run_no_falla(tmp_path, capsys):
    assert pv.main(["all", "--dir", str(tmp_path / "w")], env={}) == 0
    assert "::warning::" in capsys.readouterr().out


@pytest.mark.parametrize("key", [
    "sb_secret_FAKE0123456789", "xx_service_role_yy",
    "eyJhbGciOiJIUzI1NiJ9." + base64.urlsafe_b64encode(b'{"role":"service_role"}').decode().rstrip("=") + ".firma",
])
def test_clave_secreta_aborta_sin_enviar_nada(sb, tmp_path, capsys, key):
    rc, spy = run_all(sb, tmp_path, SUPABASE_PUBLISHABLE_KEY=key)
    out = capsys.readouterr().out
    assert rc == 1 and "::error::" in out and "SECRETA" in out and key not in out
    assert sb.reqs == [] and spy.nav_calls == []


def test_clave_publicable_y_jwt_anon_se_aceptan(sb, tmp_path):
    anon = "eyJhbGciOiJIUzI1NiJ9." + base64.urlsafe_b64encode(b'{"role":"anon"}').decode().rstrip("=") + ".firma"
    for k in (PUB_KEY, anon):
        sb.key = k
        sb.reqs.clear()
        assert run_all(sb, tmp_path, SUPABASE_PUBLISHABLE_KEY=k)[0] == 0 and sb.calls(pv.RPC_WATCH)


@pytest.mark.parametrize("url", ["http://example.com", "ftp://x", "https://user:pw@example.com", "https://example.com?x=1", "supabase.co", "https://"])
def test_url_no_https_o_con_credenciales_es_error(sb, tmp_path, capsys, url):
    rc, _ = run_all(sb, tmp_path, SUPABASE_URL=url)
    assert rc == 1 and "https://" in capsys.readouterr().out and sb.reqs == []


@pytest.mark.parametrize("token", ["corto", "con espacios en el token 123456", "tok\nsaltodelinea1234567", "x" * 201])
def test_token_con_formato_raro_es_error_y_no_se_envia(sb, tmp_path, capsys, token):
    rc, _ = run_all(sb, tmp_path, PERSONAL_FEED_TOKEN=token)
    assert rc == 1 and sb.reqs == [] and token not in capsys.readouterr().out


# ------------------------------------------------------------------ camino feliz con Supabase simulado
def test_flujo_completo_lee_la_lista_y_publica_por_usuario(sb, tmp_path, capsys):
    sb.watch = [watch_row("usuario-1"), watch_row("usuario-2", funds=[{"isin": ISIN_B, "name": "Fondo B", "url": URL_B, "prev_price": 25.0}], symbols=[])]
    rc, spy = run_all(sb, tmp_path)
    assert rc == 0
    w = sb.calls(pv.RPC_WATCH)
    assert len(w) == 1 and w[0][0] == "/rest/v1/rpc/" + pv.RPC_WATCH and w[0][2] == {"p_token": TOKEN}
    pubs = sb.calls(pv.RPC_PUBLISH)
    assert [p[2]["p_user"] for p in pubs] == ["usuario-1", "usuario-2"]
    for _path, headers, body in w + pubs:
        assert headers["apikey"] == PUB_KEY and "authorization" not in headers                 # SOLO la clave publicable en `apikey`
        assert body["p_token"] == TOKEN and set(body) <= {"p_token", "p_user", "p_payload"}
    p1 = pubs[0][2]["p_payload"]
    assert pv.payload_problems(p1, datetime.now(timezone.utc).date()) == []       # (build usa el reloj real: la fecha fija de NOW caducaba)
    assert set(p1) == {"schema", "generated_at", "funds", "series", "public"}
    assert p1["funds"][0]["isin"] == ISIN_A and p1["funds"][0]["price"] == 25.5 and p1["funds"][0]["currency"] == "EUR"
    assert p1["funds"][0]["facts"] == {"ongoing_charges_pct": 0.1, "domicile": "Ireland"}      # claves fuera de la lista blanca, descartadas
    assert [s["symbol"] for s in p1["series"]] == ["FAKE1"] and p1["public"] == ["AAPL"]       # AAPL está en el universo público: no se repite
    assert spy.series_calls == ["FAKE1"] and spy.nav_calls == [(URL_A, ISIN_A), (URL_B, ISIN_B)]
    s = p1["series"][0]
    assert len(s["d"]) <= 2 * 366 and s["d"] == sorted(s["d"]) and s["unit"] == "currency" and s["price_unit_divisor"] == 1


def test_el_log_no_contiene_ni_isin_ni_simbolos_ni_nombres_ni_url_ni_clave_ni_token(sb, tmp_path, capsys):
    sb.watch = [watch_row()]
    spy = Spy()
    spy.noise = True                                            # las funciones reutilizadas imprimen el ISIN en stderr: debe descartarse
    run_all(sb, tmp_path, spy)
    cap = capsys.readouterr()
    text = cap.out + cap.err
    for secret in (ISIN_A, "FAKE1", "Fondo Falso", "ishares.com", TOKEN, PUB_KEY, sb.url, "usuario-1"):
        assert secret not in text.replace("::add-mask::" + ISIN_A, ""), secret
    assert "::add-mask::" + ISIN_A in cap.out                   # lo único que cita el ISIN es la orden de enmascarado de Actions
    assert "datos propios" in text


def test_pasos_por_separado_y_ficheros_privados(sb, tmp_path):
    sb.watch = [watch_row()]
    work = tmp_path / "work"
    reg = tmp_path / "r.json"
    reg.write_text(json.dumps({"schema": 1, "symbols": [
        {"symbol": "AAPL", "kind": "yahoo", "name": "Apple", "currency": "USD", "unit": "currency", "tier": "hot", "status": "active", "fail_streak": 0}]}))
    env = sb.env()
    assert pv.main(["watch", "--dir", str(work)], env=env) == 0
    assert stat.S_IMODE(os.stat(work / "watch.json").st_mode) == 0o600 and stat.S_IMODE(os.stat(work).st_mode) == 0o700
    n = len(sb.reqs)
    assert pv.main(["build", "--dir", str(work), "--registry", str(reg)], fetchers=Spy().fetchers(), env={}) == 0      # build NO necesita ni el token
    assert len(sb.reqs) == n and (work / "payloads.json").is_file()
    assert TOKEN not in (work / "payloads.json").read_text() and TOKEN not in (work / "watch.json").read_text()
    assert pv.main(["publish", "--dir", str(work)], env=env) == 0
    assert len(sb.calls(pv.RPC_PUBLISH)) == 1


def test_build_y_publish_sin_ficheros_previos_no_hacen_nada(sb, tmp_path):
    assert pv.main(["build", "--dir", str(tmp_path / "x")], fetchers=Spy().fetchers(), env={}) == 0
    assert pv.main(["publish", "--dir", str(tmp_path / "x")], env=sb.env()) == 0 and sb.reqs == []


# ------------------------------------------------------------------ Supabase falla: el feed público no se ve afectado
def test_supabase_caido_es_un_warning(tmp_path, capsys):
    s = FakeSupabase()
    env = s.env()
    s.close()
    rc, _ = run_all(s, tmp_path, SUPABASE_URL=env["SUPABASE_URL"])
    out = capsys.readouterr().out
    assert rc == 0 and "::warning::" in out and "::error::" not in out


@pytest.mark.parametrize("status", [401, 403, 404, 500, 503])
def test_respuesta_de_error_en_la_lista_es_warning_sin_filtrar_el_cuerpo(sb, tmp_path, capsys, status, monkeypatch):
    sb.watch_status = status
    monkeypatch.setattr(pv, "TRIES", 2)
    rc, spy = run_all(sb, tmp_path)
    out = capsys.readouterr().out
    assert rc == 0 and "HTTP %d" % status in out and "secreto del servidor" not in out and spy.nav_calls == []
    assert len(sb.calls(pv.RPC_WATCH)) == (2 if status >= 500 else 1) and not sb.calls(pv.RPC_PUBLISH)


def test_token_incorrecto_da_401_y_es_solo_un_warning(sb, tmp_path, capsys):
    rc, _ = run_all(sb, tmp_path, PERSONAL_FEED_TOKEN="otro_token_incorrecto_123456")
    assert rc == 0 and "HTTP 401" in capsys.readouterr().out


def test_fallo_al_publicar_es_warning(sb, tmp_path, capsys):
    sb.watch = [watch_row()]
    sb.publish_status = 500
    rc, _ = run_all(sb, tmp_path)
    out = capsys.readouterr().out
    assert rc == 0 and "no se pudo publicar" in out and "1 fallidos" in out


def test_las_redirecciones_no_se_siguen(sb, tmp_path):
    other = FakeSupabase(watch=[watch_row()])
    try:
        sb.redirect_to = other.url + "/rest/v1/rpc/" + pv.RPC_WATCH
        assert run_all(sb, tmp_path)[0] == 0
        assert other.reqs == []                                  # ni la clave ni el token viajan a otro destino
    finally:
        other.close()


@pytest.mark.parametrize("raw", [None, "texto", 5, [], {}, [1, 2], [{"user_id": "../x"}], [{"user_id": 7}], {"funds": []}, [{"user_id": "u", "funds": "x", "symbols": 3}]])
def test_respuesta_rara_de_la_lista_no_rompe_nada(sb, tmp_path, raw):
    sb.watch = raw
    rc, spy = run_all(sb, tmp_path)
    assert rc == 0 and spy.nav_calls == [] and spy.series_calls == []


# ------------------------------------------------------------------ entradas hostiles en la lista
HOSTILE_URLS = ["http://www.ishares.com/x/y", "https://evil.com/www.ishares.com/x", "https://ishares.com.evil.com/x", "https://www.ishares.com@evil.com/x",
                "https://user:pw@www.ishares.com/x", "https://www.ishares.com:8443/x", "https://www.ishares.com/x?next=http://evil",
                "https://www.ishares.com/x#f", "file:///etc/passwd", "https://169.254.169.254/latest", "https://localhost/x", "https://www.ishares.com/x y",
                "https://www.ishares.comevil.net/x", "javascript:alert(1)", "https://www.ishares.com/" + "a" * 300, "//www.ishares.com/x", ""]


@pytest.mark.parametrize("url", HOSTILE_URLS)
def test_url_de_ficha_hostil_nunca_se_pide(sb, tmp_path, url):
    sb.watch = [watch_row(funds=[{"isin": ISIN_A, "name": "F", "url": url}], symbols=[])]
    rc, spy = run_all(sb, tmp_path)
    assert rc == 0 and spy.nav_calls == [] and spy.facts_calls == []
    pub = sb.calls(pv.RPC_PUBLISH)
    assert pub and pub[0][2]["p_payload"]["funds"][0]["price"] is None and pub[0][2]["p_payload"]["funds"][0]["source"] == "none"


@pytest.mark.parametrize("url", [URL_A, "https://ishares.com/x/y", "https://www.ishares.com/ch/individual/en/products/251882/ishares-msci-world-ucits-etf-acc-fund"])
def test_urls_de_ishares_validas(url):
    assert pv.ishares_url_ok(url)


def test_isin_simbolo_y_campos_invalidos_se_descartan(sb, tmp_path, capsys):
    sb.watch = [watch_row(funds=[
        {"isin": "no es un isin", "name": "x", "url": URL_A}, {"isin": ISIN_A, "name": "<script>alert(1)</script>Buen nombre", "url": URL_A, "extra": 1},
        {"isin": ISIN_B, "name": "ok\x00", "url": URL_B, "proxy_symbol": "../etc", "prev_price": -3}],
        symbols=["../x", "A B", "OK1", "OK1", "x" * 40, 5])]
    rc, spy = run_all(sb, tmp_path)
    assert rc == 0
    p = sb.calls(pv.RPC_PUBLISH)[0][2]["p_payload"]
    assert [f["isin"] for f in p["funds"]] == [ISIN_B] and p["funds"][0]["name"] == "ok" and "proxy_symbol" not in p["funds"][0]
    assert spy.series_calls == ["OK1"]                         # uno solo, sin repetir
    assert "elementos descartados" in capsys.readouterr().out


def test_nombre_con_marcado_se_limpia(sb, tmp_path):
    sb.watch = [watch_row(funds=[{"isin": ISIN_A, "name": "<img src=x onerror=alert(1)>Fondo", "url": URL_A}], symbols=[])]
    run_all(sb, tmp_path)
    name = sb.calls(pv.RPC_PUBLISH)[0][2]["p_payload"]["funds"][0]["name"]
    assert "<" not in name and ">" not in name and name.endswith("Fondo")


def test_limites_de_usuarios_fondos_y_simbolos(sb, tmp_path):
    funds = [{"isin": "IE0FAKE%05d" % i, "name": "F", "url": URL_A} for i in range(30)]
    sb.watch = [watch_row("u%d" % i, funds=funds if i == 0 else [], symbols=["S%d" % j for j in range(60)]) for i in range(25)]
    run_all(sb, tmp_path)
    pubs = sb.calls(pv.RPC_PUBLISH)
    assert len(pubs) == pv.MAX_USERS and len(pubs[0][2]["p_payload"]["funds"]) == pv.MAX_FUNDS


# ------------------------------------------------------------------ R4: NAV x10 respecto al anterior
@pytest.mark.parametrize("price,prev,publishes", [(255.0, 25.5, False), (2.55, 25.5, False), (51.0, 25.5, False), (52.0, 25.5 * 1.01, False),
                                                  (25.6, 25.5, True), (30.0, 25.5, True), (25.5, None, True)])
def test_R4_nav_x10_o_salto_enorme_no_se_publica(sb, tmp_path, capsys, price, prev, publishes):
    fund = {"isin": ISIN_A, "name": "F", "url": URL_A}
    if prev:
        fund["prev_price"] = prev
    sb.watch = [watch_row(funds=[fund], symbols=[])]
    run_all(sb, tmp_path, Spy(nav_price=price))
    f = sb.calls(pv.RPC_PUBLISH)[0][2]["p_payload"]["funds"][0]
    assert (f["price"] == price) == publishes and (f["source"] == "ishares_nav_scrape") == publishes
    assert ("nav_salto" in capsys.readouterr().out) == (not publishes)


def test_R4_validador_final_rechaza_nav_implausible():
    base = {"schema": 2, "generated_at": "2026-10-26T12:00:00Z", "series": [], "public": [],
            "funds": [{"isin": ISIN_A, "name": "F", "price": 25.0, "currency": "EUR", "unit": "currency", "source": "ishares_nav_scrape"}]}
    assert pv.payload_problems(base, date(2026, 10, 26)) == []
    for price in (0, -1, 0.05, 10001, float("nan"), "25", True):
        bad = json.loads(json.dumps(base))
        bad["funds"][0]["price"] = price if not isinstance(price, float) or price == price else None
        if price != price:
            bad["funds"][0]["price"] = "nan"
        assert pv.payload_problems(bad, date(2026, 10, 26)), price


# ------------------------------------------------------------------ series de lo que NO está en el universo público
def test_serie_en_subunidad_lleva_unit_subunit_y_divisor_100(sb, tmp_path):
    sb.watch = [watch_row(funds=[], symbols=["FAKE.L"])]
    run_all(sb, tmp_path, Spy(series_currency="GBp"))
    s = sb.calls(pv.RPC_PUBLISH)[0][2]["p_payload"]["series"][0]
    assert (s["currency"], s["unit"], s["price_unit_divisor"]) == ("GBp", "subunit", 100)


def test_serie_sin_divisa_conocida_no_se_publica(sb, tmp_path, capsys):
    sb.watch = [watch_row(funds=[], symbols=["FAKE1"])]
    run_all(sb, tmp_path, Spy(series_currency=None))
    assert sb.calls(pv.RPC_PUBLISH)[0][2]["p_payload"]["series"] == [] and "serie_sin_divisa_o_datos" in capsys.readouterr().out


@pytest.mark.parametrize("sym,unit", [("^FAKEX", "points"), ("FAKEUSD=X", "rate"), ("FAKE1", "currency")])
def test_unidad_inferida_para_indices_y_pares(sb, tmp_path, sym, unit):
    sb.watch = [watch_row(funds=[], symbols=[sym])]
    run_all(sb, tmp_path)
    assert sb.calls(pv.RPC_PUBLISH)[0][2]["p_payload"]["series"][0]["unit"] == unit


def test_un_simbolo_que_falla_no_tumba_el_resto(sb, tmp_path):
    sb.watch = [watch_row(funds=[], symbols=["BOOM", "FAKE1"])]
    run_all(sb, tmp_path)
    assert [s["symbol"] for s in sb.calls(pv.RPC_PUBLISH)[0][2]["p_payload"]["series"]] == ["FAKE1"]


def test_el_proxy_se_incluye_aunque_este_en_el_universo_publico(sb, tmp_path):
    """El público solo conserva HISTORY_YEARS años: la historia larga de un proxy va SIEMPRE en lo propio. Un símbolo extra público no se repite."""
    sb.watch = [watch_row(funds=[{"isin": ISIN_A, "name": "F", "url": URL_A, "proxy_symbol": "AAPL"},
                                 {"isin": ISIN_B, "name": "G", "url": URL_B, "proxy_symbol": "FAKEP"}], symbols=["AAPL", "FAKE1"])]
    spy = Spy()
    run_all(sb, tmp_path, spy)
    p = sb.calls(pv.RPC_PUBLISH)[0][2]["p_payload"]
    assert p["public"] == [] and [s["symbol"] for s in p["series"]] == ["AAPL", "FAKEP", "FAKE1"]       # proxies primero, luego los extra
    assert [f.get("proxy_symbol") for f in p["funds"]] == ["AAPL", "FAKEP"]
    assert spy.periods == {"AAPL": "max", "FAKEP": "max", "FAKE1": "2y"} and spy.series_calls.count("AAPL") == 1


def test_proxy_con_historia_larga_y_extra_recortado(sb, tmp_path):
    sb.watch = [watch_row(funds=[{"isin": ISIN_A, "name": "F", "url": URL_A, "proxy_symbol": "FAKEP"}], symbols=["FAKE1"])]
    spy = Spy()
    spy.long = long_bars(17)
    run_all(sb, tmp_path, spy)
    s = {x["symbol"]: x for x in sb.calls(pv.RPC_PUBLISH)[0][2]["p_payload"]["series"]}
    assert len(s["FAKEP"]["d"]) > 4000 and set(s["FAKEP"]) == {"symbol", "currency", "unit", "price_unit_divisor", "price", "change_pct", "as_of", "d", "c"}
    assert len(s["FAKE1"]["d"]) <= 2 * 366 and s["FAKEP"]["currency"] == "USD" and s["FAKEP"]["unit"] == "currency"


def test_historia_de_proxy_se_recorta_a_25_anos():
    b = long_bars(40)
    e = pv.series_entry("FAKEP", b, "USD", date(2026, 10, 26), long=True)
    assert e["d"][0] >= (date(2026, 10, 26) - timedelta(days=pv.PROXY_DAYS)).isoformat() and len(e["d"]) <= pv.MAX_PROXY_BARS
    assert len(pv.series_entry("FAKEP", b, "USD", date(2026, 10, 26))["d"]) <= pv.MAX_SERIES_BARS


def test_hueco_en_una_descarga_defectuosa_conserva_solo_el_tramo_final():
    b = long_bars(5)
    gap = [x for i, x in enumerate(b) if not 300 <= i < 400]                        # faltan ~100 días en mitad de la serie
    e = pv.series_entry("FAKEP", gap, "USD", date(2026, 10, 26), long=True)
    assert e["d"][0] == b[400]["d"] and e["d"][-1] == b[-1]["d"]


def test_proxy_sin_divisa_no_se_publica_y_se_cuenta(sb, tmp_path, capsys):
    sb.watch = [watch_row(funds=[{"isin": ISIN_A, "name": "F", "url": URL_A, "proxy_symbol": "FAKEP"}], symbols=[])]
    run_all(sb, tmp_path, Spy(series_currency=None))
    p = sb.calls(pv.RPC_PUBLISH)[0][2]["p_payload"]
    assert p["series"] == [] and p["funds"][0]["proxy_symbol"] == "FAKEP" and "proxy_sin_divisa_o_datos" in capsys.readouterr().out


def test_proxy_que_falla_no_tumba_el_nav(sb, tmp_path, capsys):
    sb.watch = [watch_row(funds=[{"isin": ISIN_A, "name": "F", "url": URL_A, "proxy_symbol": "BOOM"}], symbols=[])]
    run_all(sb, tmp_path)
    p = sb.calls(pv.RPC_PUBLISH)[0][2]["p_payload"]
    assert p["series"] == [] and p["funds"][0]["price"] == 25.5 and "proxy_fallo" in capsys.readouterr().out


def test_dos_usuarios_con_el_mismo_proxy_descargan_una_vez(sb, tmp_path):
    f = [{"isin": ISIN_A, "name": "F", "url": URL_A, "proxy_symbol": "FAKEP"}]
    sb.watch = [watch_row("u1", funds=f, symbols=["FAKEP"]), watch_row("u2", funds=f, symbols=["FAKEP", "FAKE1"]), watch_row("u3", funds=[], symbols=["FAKE1"])]
    spy = Spy()
    run_all(sb, tmp_path, spy)
    assert sorted(spy.series_calls) == ["FAKE1", "FAKEP"] and len(sb.calls(pv.RPC_PUBLISH)) == 3


# ------------------------------------------------------------------ tope de 1 MB
def _serie(sym, n, price=100.0, cur="USD"):
    b = long_bars(n / 252.0 + 0.2)[-n:]
    return {"symbol": sym, "currency": cur, "unit": "currency", "price_unit_divisor": 1, "price": b[-1]["c"], "change_pct": 0.1, "as_of": b[-1]["d"],
            "d": [x["d"] for x in b], "c": [x["c"] for x in b]}


def _fund(i, proxy=None):
    f = {"isin": "IE0FAKE%05d" % i, "name": "F", "price": 25.0, "currency": "EUR", "unit": "currency", "source": "none"}
    if proxy:
        f["proxy_symbol"] = proxy
    return f


def _worst_case(n_proxy_bars=6300, n_funds=20, n_symbols=40):
    """20 fondos con proxy distinto + 20 símbolos extra (40 series en total: el tope de símbolos por usuario)."""
    funds = [_fund(i, "P%d" % i) for i in range(n_funds)]
    series = [_serie("P%d" % i, n_proxy_bars) for i in range(n_funds)] + [_serie("X%d" % i, 500) for i in range(n_symbols - n_funds)]
    return {"schema": 2, "generated_at": "2026-10-26T12:00:00Z", "funds": funds, "series": series, "public": []}


def test_peor_caso_real_se_recorta_de_forma_determinista_y_conserva_antes_los_proxies():
    p1, p2 = _worst_case(), _worst_case()
    assert len(c.dumps(p1)) > pv.MAX_PAYLOAD_BYTES                          # el caso máximo SIN recortar no cabe en 1 MB
    o1, o2 = pv.fit_payload(p1), pv.fit_payload(p2)
    assert o1 is not None and c.dumps(o1) == c.dumps(o2)                    # determinista
    assert len(c.dumps(o1)) <= pv.MAX_PAYLOAD_BYTES and pv.payload_problems(o1, date(2026, 10, 26)) == []
    prox = [len(s["d"]) for s in o1["series"] if s["symbol"].startswith("P")]
    extra = [len(s["d"]) for s in o1["series"] if s["symbol"].startswith("X")]
    assert len(set(prox)) == 1 and prox[0] in pv.PROXY_KEEP and all(n == 1 for n in extra) and prox[0] > 1     # los extra caen primero y al mínimo
    assert [s["symbol"] for s in o1["series"]] == [s["symbol"] for s in _worst_case()["series"]]               # mismo orden


def test_fit_payload_no_toca_lo_que_ya_cabe_y_recorta_extra_antes_que_proxies():
    ok = _worst_case(n_funds=2, n_symbols=4)
    before = c.dumps(ok)
    assert c.dumps(pv.fit_payload(ok)) == before
    # con un tope que obliga a recortar solo los extra, los proxies quedan íntegros
    p = _worst_case(n_funds=2, n_symbols=40)
    proxies_bytes = len(c.dumps({"x": [s for s in p["series"] if s["symbol"].startswith("P")]}))
    old = pv.MAX_PAYLOAD_BYTES
    pv.MAX_PAYLOAD_BYTES = proxies_bytes + 20_000
    try:
        out = pv.fit_payload(p)
    finally:
        pv.MAX_PAYLOAD_BYTES = old
    assert out and all(len(s["d"]) == 6300 for s in out["series"] if s["symbol"].startswith("P"))
    assert all(len(s["d"]) < 500 for s in out["series"] if s["symbol"].startswith("X"))


def test_tamano_real_con_dos_fondos_de_17_anos():
    p = {"schema": 2, "generated_at": "2026-10-26T12:00:00Z", "funds": [_fund(1, "P1"), _fund(2, "P2")], "series": [_serie("P1", 4300), _serie("P2", 4300)], "public": []}
    size = len(c.dumps(p))
    assert 150_000 < size < 400_000 and pv.fit_payload(p) is p and pv.payload_problems(p, date(2026, 10, 26)) == []


def test_payload_que_no_cabe_ni_recortado_se_descarta(monkeypatch):
    p = {"schema": 2, "generated_at": "2026-10-26T12:00:00Z", "series": [], "public": [],
         "funds": [{"isin": ISIN_A, "name": "F", "price": 25.0, "currency": "EUR", "unit": "currency", "source": "none"}]}
    monkeypatch.setattr(pv, "MAX_PAYLOAD_BYTES", 10)
    assert pv.fit_payload(p) is None


def test_el_tope_real_es_un_megabyte():
    assert pv.MAX_PAYLOAD_BYTES == 1_000_000


# ------------------------------------------------------------------ esquema del payload
def _payload(**over):
    p = {"schema": 2, "generated_at": "2026-10-26T12:00:00Z", "series": [], "public": ["AAPL"],
         "funds": [{"isin": ISIN_A, "name": "F", "price": 25.0, "currency": "EUR", "unit": "currency", "source": "none"}]}
    p.update(over)
    return p


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(extra="x"), lambda p: p["funds"][0].update(extra=1), lambda p: p["funds"][0].update(name="<b>x</b>"),
    lambda p: p["funds"][0].update(currency="USD"), lambda p: p["funds"][0].update(facts={"evil": "x"}),
    lambda p: p["funds"][0].update(facts={"domicile": "<x>"}), lambda p: p["funds"][0].update(isin="IE0FAKE"),
    lambda p: p.update(public=["../x"]), lambda p: p.update(schema=3),
    lambda p: p.update(series=[{"symbol": "X", "currency": "USD", "unit": "currency", "price_unit_divisor": 1, "price": 1.0, "change_pct": 0, "as_of": "2026-10-23",
                                "d": ["2026-10-23", "2026-10-22"], "c": [1.0, 1.0]}]),
    lambda p: p.update(series=[{"symbol": "X", "currency": "USD", "unit": "currency", "price_unit_divisor": 1, "price": 1.0, "change_pct": 0, "as_of": "2026-10-23",
                                "d": ["2026-10-23"], "c": [2.0]}]),
])
def test_payload_invalido_no_se_envia(mutate):
    p = _payload()
    mutate(p)
    assert pv.payload_problems(p, date(2026, 10, 26))


def test_payload_valido_pasa():
    assert pv.payload_problems(_payload(), date(2026, 10, 26)) == []


def test_key_looks_secret_casos():
    assert pv.key_looks_secret("sb_secret_x") and pv.key_looks_secret("a.service_role.b") and not pv.key_looks_secret(PUB_KEY) and not pv.key_looks_secret("")
    assert not pv.key_looks_secret("a.b")                       # JWT ilegible: no se declara secreta (la publicable no es JWT)


def test_el_codigo_publico_no_contiene_datos_del_dueno():
    """Este módulo, el workflow y la documentación no pueden llevar ISIN, URL de fichas ni listas del dueño. Los identificadores se leen del código
    v1 EN EL TEST (no se repiten aquí)."""
    import fetch_market_data as fm
    root = os.path.join(os.path.dirname(__file__), "..")
    paths = ["scripts/personal_v2.py", "scripts/feed_v2.py", "scripts/feed_v2_common.py", "scripts/sanity_check_v2.py", "scripts/site_download.py",
             "registry.json", ".github/workflows/publish-feed-v2.yml", "tests/test_personal_v2.py", "tests/test_feed_v2.py", "tests/v2_world.py",
             "tests/test_sanity_check_v2.py", "tests/test_site_download.py", "tests/test_registry_v2.py"]
    for rel in ("README.md", "docs/CONTRATO_FEED_V2.md", "auditorias/FIXES_FEED_V2.md"):
        if os.path.exists(os.path.join(root, rel)):
            paths.append(rel)
    for rel in paths:
        text = open(os.path.join(root, rel), encoding="utf-8").read()
        for f in fm.YOUR_FUNDS:
            for secret in (f["isin"], f["product_url"], f["name"], f["proxy_symbol"]):
                assert secret not in text, (rel, "dato del dueño")


# ------------------------------------------------------------------ controles negativos del validador de series largas
def _with(series, **fund_over):
    f = _fund(1, "P1")
    f.update(fund_over)
    return {"schema": 2, "generated_at": "2026-10-26T12:00:00Z", "funds": [f], "series": series, "public": []}


def _problems(p):
    return pv.payload_problems(p, date(2026, 10, 26))


def test_serie_larga_valida_pasa():
    assert _problems(_with([_serie("P1", 6300)])) == []


def test_serie_truncada_o_con_columnas_desiguales_se_rechaza():
    s = _serie("P1", 4000)
    s["c"] = s["c"][:-100]                                                  # la columna de cierres quedó más corta que la de fechas
    assert _problems(_with([s]))
    s = _serie("P1", 4000)
    s["d"], s["c"] = s["d"][:-100], s["c"][:-100]                           # cortada por el final: as_of y price ya no son el último cierre
    assert "serie incoherente" in _problems(_with([s]))


def test_fechas_desordenadas_o_repetidas_se_rechazan():
    s = _serie("P1", 4000)
    s["d"][1000], s["d"][1001] = s["d"][1001], s["d"][1000]
    assert "serie con fechas no crecientes" in _problems(_with([s]))
    s = _serie("P1", 4000)
    s["d"][1001] = s["d"][1000]
    assert "serie con fechas no crecientes" in _problems(_with([s]))


def test_hueco_enorme_se_rechaza():
    s = _serie("P1", 4000)
    del s["d"][2000:2100], s["c"][2000:2100]
    assert any("huecos" in x for x in _problems(_with([s])))


@pytest.mark.parametrize("bad", [0, -1.5, float("nan"), float("inf"), None, "100", True])
def test_cierre_no_positivo_o_no_finito_se_rechaza(bad):
    s = _serie("P1", 4000)
    s["c"][1500] = bad
    assert _problems(_with([s]))


def test_nan_no_se_puede_serializar():
    s = _serie("P1", 10)
    s["c"][3] = float("nan")
    with pytest.raises(ValueError):
        c.dumps(s)


@pytest.mark.parametrize("over", [{"currency": None}, {"currency": ""}, {"currency": "EURO€"}, {"currency": "US"}])
def test_proxy_sin_divisa_valida_se_rechaza(over):
    s = _serie("P1", 4000)
    s.update(over)
    assert _problems(_with([s]))


def test_proxy_sin_divisa_en_la_serie_falta_la_clave():
    s = _serie("P1", 4000)
    del s["currency"]
    assert _problems(_with([s]))


@pytest.mark.parametrize("over", [{"unit": "subunit", "currency": "USD", "price_unit_divisor": 100}, {"unit": "currency", "currency": "GBp"},
                                  {"unit": "subunit", "currency": "GBp", "price_unit_divisor": 1}, {"unit": "currency", "price_unit_divisor": 100},
                                  {"unit": "points"}, {"unit": "rate"}, {"unit": "raro"}])
def test_divisa_y_unidad_incoherentes_se_rechazan(over):
    s = _serie("P1", 4000)
    s.update(over)
    assert _problems(_with([s]))


def test_subunidad_coherente_pasa():
    s = _serie("P1", 4000, cur="GBp")
    s.update(unit="subunit", price_unit_divisor=100)
    assert _problems(_with([s])) == []


def test_serie_demasiado_larga_segun_su_clase():
    assert any("demasiado larga" in x for x in _problems(_with([_serie("P1", 6300), _serie("X1", 700)])))     # un extra no puede llevar historia larga
    assert any("demasiado larga" in x or "no es una lista" in x for x in _problems(_with([_serie("P1", 6700)])))


def test_simbolo_repetido_o_en_series_y_en_public_se_rechaza():
    assert "símbolo repetido en series" in _problems(_with([_serie("P1", 300), _serie("P1", 300)]))
    p = _with([_serie("P1", 300)])
    p["public"] = ["P1"]
    assert "símbolo en series y en public a la vez" in _problems(p)


def test_payload_demasiado_grande_se_rechaza_y_el_build_no_lo_envia(monkeypatch):
    p = _worst_case()
    assert any("payload >" in x for x in _problems(p))
    monkeypatch.setattr(pv, "MAX_PAYLOAD_BYTES", 10)
    assert pv.fit_payload(_with([_serie("P1", 300)])) is None


def test_payload_demasiado_grande_no_se_publica_en_el_flujo(sb, tmp_path, monkeypatch, capsys):
    sb.watch = [watch_row(funds=[{"isin": ISIN_A, "name": "F", "url": URL_A, "proxy_symbol": "FAKEP"}], symbols=[])]
    monkeypatch.setattr(pv, "MAX_PAYLOAD_BYTES", 10)
    run_all(sb, tmp_path)
    assert sb.calls(pv.RPC_PUBLISH) == [] and "payload_invalido" in capsys.readouterr().out
