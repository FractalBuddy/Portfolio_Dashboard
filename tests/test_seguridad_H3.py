"""Bloque 3 (seguridad del backend): AU-04 (SSRF: redirecciones y tamaño del cuerpo), AU-05 (ReDoS), AU-15 (caracteres Unicode
de formato, prev_price) y AU-14 (repr acotado en el log del NAV). Servidores LOCALES reales; sin red externa."""
import http.server
import threading
import time

import pytest

import fetch_market_data as fm


@pytest.fixture(autouse=True)
def _limpio(monkeypatch):
    monkeypatch.setattr(fm, "_ISHARES_PAGES", {})
    monkeypatch.setattr(fm, "_nap", lambda s: None)


class _Servidor:
    """Servidor HTTP local en un hilo. `handler(self)` atiende cada GET; `hits` registra las rutas pedidas."""

    def __init__(self, handler):
        self.hits = []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                outer.hits.append(self.path)
                handler(self)

            def log_message(self, *a):
                pass

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def url(self, path="/"):
        return "http://127.0.0.1:%d%s" % (self.port, path)

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def servidores():
    abiertos = []

    def crea(handler):
        s = _Servidor(handler)
        abiertos.append(s)
        return s
    yield crea
    for s in abiertos:
        s.close()


# ----------------------------------------------------------------------------------------------------------------- AU-04
def test_AU04_una_redireccion_a_otro_puerto_no_se_sigue(servidores):
    destino = servidores(lambda h: (h.send_response(200), h.send_header("Content-Length", "2"), h.end_headers(), h.wfile.write(b"ok")))

    def redirige(h):
        h.send_response(302)
        h.send_header("Location", destino.url("/secreto"))
        h.send_header("Content-Length", "0")
        h.end_headers()
    origen = servidores(redirige)
    with pytest.raises(Exception):
        fm._ishares_plain(origen.url("/ficha"))
    assert origen.hits == ["/ficha"]
    assert destino.hits == [], "el runner siguió la redirección a otro puerto (SSRF)"


def test_AU04_un_cuerpo_de_60_mb_se_corta_y_no_se_lee_entero(servidores):
    enviados = []

    def enorme(h):
        h.send_response(200)
        h.send_header("Content-Type", "text/html")
        h.send_header("Content-Length", str(60 * 1024 * 1024))
        h.end_headers()
        bloque = b"<p>x</p>" * 8192                                # 64 KiB
        total = 0
        try:
            for _ in range(60 * 1024 * 1024 // len(bloque)):
                h.wfile.write(bloque)
                total += len(bloque)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            enviados.append(total)
    s = servidores(enorme)
    t0 = time.monotonic()
    with pytest.raises(ValueError, match="grande"):
        fm._ishares_plain(s.url("/ficha"))
    deadline = time.monotonic() + 5
    while not enviados and time.monotonic() < deadline:
        time.sleep(0.05)
    assert enviados and enviados[0] < 20 * 1024 * 1024, "el servidor pudo enviar el cuerpo entero (%s bytes)" % enviados
    assert time.monotonic() - t0 < 20


def test_AU04_un_cuerpo_dentro_del_tope_se_lee_entero(servidores):
    cuerpo = b"<html><body>" + b"<p>NAV as of 03/Oct/2026 EUR 12.34</p>" * 1000 + b"</body></html>"

    def ok(h):
        h.send_response(200)
        h.send_header("Content-Length", str(len(cuerpo)))
        h.end_headers()
        h.wfile.write(cuerpo)
    plain = fm._ishares_plain(servidores(ok).url("/ficha"))
    assert plain.count("NAV as of") == 1000


def test_AU04_una_redireccion_a_un_destino_de_ishares_si_se_sigue_y_a_otro_no(monkeypatch):
    """Cada salto se valida: https + *.ishares.com, sin credenciales ni puerto. Se usa un requests.get falso (no hay https local)."""
    import types

    def resp(status=200, location=None, text=""):
        r = types.SimpleNamespace(status_code=status, headers={"Location": location} if location else {}, text=text, closed=False)
        r.raise_for_status = lambda: None
        r.iter_content = lambda chunk_size=16384: iter([text.encode()])
        r.close = lambda: None
        r.__enter__ = lambda: r
        return r
    visitadas = []

    def get_con(destino):
        def get(url, **kw):
            visitadas.append((url, kw.get("allow_redirects")))
            return resp(302, destino) if len(visitadas) == 1 else resp(200, text="<p>pagina</p>")
        return get

    monkeypatch.setattr(fm.requests, "get", get_con("https://www.ishares.com/uk/individual/en/products/100001/x"))
    assert fm._ishares_plain("https://www.ishares.com/ch/individual/en/products/100001/x").strip() == "pagina"
    assert [u for u, _ in visitadas] == ["https://www.ishares.com/ch/individual/en/products/100001/x",
                                         "https://www.ishares.com/uk/individual/en/products/100001/x"]
    assert all(ar is False for _, ar in visitadas)                                # nunca delega las redirecciones en requests

    for malo in ("http://www.ishares.com/x", "https://evilishares.com/x", "https://www.ishares.com.evil.test/x", "https://u:p@www.ishares.com/x",
                 "https://www.ishares.com:8443/x", "https://169.254.169.254/latest", "//evil.test/x", "https://www.ishares.com\\@evil.test/"):
        visitadas.clear()
        monkeypatch.setattr(fm, "_ISHARES_PAGES", {})
        monkeypatch.setattr(fm.requests, "get", get_con(malo))
        with pytest.raises(Exception):
            fm._ishares_plain("https://www.ishares.com/ch/individual/en/products/100001/x")
        assert len(visitadas) == 1, malo


def test_AU04_demasiadas_redirecciones_se_cortan(monkeypatch):
    import types
    n = []

    def get(url, **kw):
        n.append(url)
        r = types.SimpleNamespace(status_code=302, headers={"Location": "https://www.ishares.com/a"}, text="")
        r.raise_for_status = lambda: None
        r.iter_content = lambda chunk_size=16384: iter([])
        r.close = lambda: None
        return r
    monkeypatch.setattr(fm.requests, "get", get)
    with pytest.raises(Exception):
        fm._ishares_plain("https://www.ishares.com/ch/individual/en/products/100001/x")
    assert len(n) <= fm.ISHARES_MAX_REDIRECTS + 1


# ----------------------------------------------------------------------------------------------------------------- AU-05
_ETIQUETAS_REDOS = ["Benchmark Index A ", "Management Company A ", "Fund Manager A ", "Arranger A ", "Morningstar Category A ",
                    "Product Structure A Methodology A "]


@pytest.mark.parametrize("unidad", _ETIQUETAS_REDOS)
def test_AU05_scan_fund_facts_con_200k_caracteres_hostiles_tarda_menos_de_0_2_s(unidad):
    plain = unidad * (200_000 // len(unidad))
    t0 = time.perf_counter()
    fm.scan_fund_facts(plain)
    assert time.perf_counter() - t0 < 0.2


def test_AU05_las_cotas_no_rompen_los_valores_normales():
    plain = ("Benchmark Index MSCI World Index (Net) Initial Charge 0.00 % Management Company BlackRock Asset Management Ireland Limited "
             "Dealing Settlement T+2 Morningstar Category Global Large-Cap Blend Equity Dealing Frequency Daily")
    f = fm.scan_fund_facts(plain)
    assert f["benchmark"] == "MSCI World Index (Net)"
    assert f["management_company"] == "BlackRock Asset Management Ireland Limited"
    assert f["morningstar_category"] == "Global Large-Cap Blend Equity"


# ----------------------------------------------------------------------------------------------------------------- AU-15
@pytest.mark.parametrize("raro", ["‮", "​", " ", " ", "⁦", "﻿", "‏"])
def test_AU15_plain_text_quita_los_caracteres_unicode_de_formato(raro):
    assert fm._plain_text("Fondo %sSeguro" % raro, 120) == "Fondo Seguro"


@pytest.mark.parametrize("raro", ["‮", "​", " ", " ", "⁦", "﻿", "‏"])
def test_AU15_plain_de_personal_quita_los_caracteres_unicode_de_formato(raro):
    import personal_v2 as pv
    assert pv._plain("Fondo %sSeguro" % raro, 120) == "Fondo Seguro"


def test_AU15_el_texto_normal_con_acentos_y_emojis_se_conserva():
    assert fm._plain_text("Cotización España — 日本 ñ €", 120) == "Cotización España — 日本 ñ €"
    import personal_v2 as pv
    assert pv._plain("Cotización España — 日本 ñ €", 120) == "Cotización España — 日本 ñ €"


@pytest.mark.parametrize("pp,esperado", [(1e308, None), (1e-9, None), (0.05, None), (10001.0, None), (float("inf"), None), (0.1, 0.1), (12.34, 12.34), (10000.0, 10000.0)])
def test_AU15_prev_price_se_acota_a_un_rango_plausible(pp, esperado):
    import personal_v2 as pv
    users, _ = pv.parse_watch([{"user_id": "usuario-1", "funds": [{"isin": "IE000SYNA013", "name": "n", "url": "https://www.ishares.com/ch/individual/en/products/100001/x",
                                                                   "prev_price": pp}], "symbols": []}], fm._plain_text)
    assert users[0]["funds"][0].get("prev_price") == esperado


# ----------------------------------------------------------------------------------------------------------------- AU-14
def test_AU14_el_log_de_un_nav_inverosimil_no_vuelca_200k_digitos(capsys):
    plain = "IE000SYNA013 NAV as of 20/Oct/2026 EUR " + "9" * 200_000
    fm.parse_ishares_nav(plain, "IE000SYNA013", today=__import__("datetime").date(2026, 10, 21))
    err = capsys.readouterr().err
    assert err and max(len(linea) for linea in err.splitlines()) < 400
